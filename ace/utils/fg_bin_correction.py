import frappe
from frappe import _
from frappe.utils import flt, nowdate, nowtime

from ace.stock.stock_entry import (
	DEFAULT_FG_BIN_FIELD,
	ensure_default_bin_client_script,
	ensure_seed_default_bin,
	get_default_bin_for_field,
)
from ace.utils.dimension_stock_correction import (
	BIN_FIELD,
	COMPANY,
	MIN_QTY,
	PROJECT_FIELD,
	PURPOSE,
	STOCK_ENTRY_TYPE,
	TO_BIN_FIELD,
	TO_PROJECT_FIELD,
	filter_unsupported_items,
	get_item_details,
	submit_with_temporary_negative_stock,
)


PATCH_MARKER = "ace-fg-default-bin-correction-v1"
INITIAL_FG_BIN = "ACE - FG"
INITIAL_FG_WAREHOUSE = "Finished Goods - ACE"


def execute_fg_bin_correction():
	lock = frappe.cache.lock("ace-fg-default-bin-correction", timeout=900)

	with lock:
		ensure_seed_default_bin(INITIAL_FG_BIN, INITIAL_FG_WAREHOUSE, DEFAULT_FG_BIN_FIELD)
		ensure_default_bin_client_script()

		existing = get_existing_correction_entry()
		if existing:
			return submit_existing_or_report(existing)

		plan = get_fg_blank_bin_balances()
		item_details = get_item_details({row["item_code"] for row in plan})
		plan, skipped = filter_unsupported_items(plan, item_details)

		if not plan:
			frappe.logger().info("No Finished Goods blank-bin balances found for correction.")
			return {"status": "no_rows", "skipped": skipped}

		stock_entry = create_fg_bin_correction_entry(plan, item_details, skipped)
		return submit_with_temporary_negative_stock(stock_entry)


def get_existing_correction_entry():
	return frappe.db.exists(
		"Stock Entry",
		{
			"company": COMPANY,
			"remarks": ["like", "%" + PATCH_MARKER + "%"],
			"docstatus": ["<", 2],
		},
	)


def submit_existing_or_report(stock_entry_name):
	doc = frappe.get_doc("Stock Entry", stock_entry_name)

	if doc.docstatus == 1:
		return {"status": "already_submitted", "stock_entry": doc.name}

	if doc.docstatus != 0:
		frappe.throw(_("Stock Entry {0} must be Draft or Submitted.").format(doc.name))

	return submit_with_temporary_negative_stock(doc)


def get_fg_blank_bin_balances():
	default_bin = get_default_bin_for_field(DEFAULT_FG_BIN_FIELD)
	if not default_bin:
		return []

	query = """
		select
			item_code,
			coalesce({project_field}, '') as project_value,
			sum(actual_qty) as qty
		from `tabStock Ledger Entry`
		where
			is_cancelled = 0
			and company = %(company)s
			and warehouse = %(warehouse)s
			and coalesce({bin_field}, '') = ''
		group by item_code, coalesce({project_field}, '')
		having sum(actual_qty) > %(min_qty)s
	""".format(
		project_field=PROJECT_FIELD,
		bin_field=BIN_FIELD,
	)

	rows = frappe.db.sql(
		query,
		{
			"company": COMPANY,
			"warehouse": default_bin.warehouse,
			"min_qty": MIN_QTY,
		},
		as_dict=True,
	)

	return [
		{
			"item_code": row.item_code,
			"warehouse": default_bin.warehouse,
			"from_project": row.project_value or "",
			"from_bin": "",
			"to_project": row.project_value or "",
			"to_bin": default_bin.bin_location,
			"qty": flt(row.qty, 6),
		}
		for row in rows
	]


def create_fg_bin_correction_entry(plan, item_details, skipped):
	default_bin = get_default_bin_for_field(DEFAULT_FG_BIN_FIELD)

	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = COMPANY
	stock_entry.stock_entry_type = STOCK_ENTRY_TYPE
	stock_entry.purpose = PURPOSE
	stock_entry.posting_date = nowdate()
	stock_entry.posting_time = nowtime()
	stock_entry.set_posting_time = 1
	stock_entry.remarks = (
		PATCH_MARKER
		+ "\nMoves existing Finished Goods stock from blank bin to "
		+ (default_bin.bin_location if default_bin else "")
		+ "."
		+ "\nWarehouse and Project are unchanged on every row."
		+ "\nBatch/serial skipped rows: "
		+ str(len(skipped))
	)

	for row in plan:
		item = item_details[row["item_code"]]
		child = stock_entry.append(
			"items",
			{
				"item_code": row["item_code"],
				"s_warehouse": row["warehouse"],
				"t_warehouse": row["warehouse"],
				"qty": row["qty"],
				"transfer_qty": row["qty"],
				"uom": item.stock_uom,
				"stock_uom": item.stock_uom,
				"conversion_factor": 1,
				"allow_zero_valuation_rate": 1,
			},
		)
		child.set(PROJECT_FIELD, row["from_project"] or None)
		child.set(BIN_FIELD, row["from_bin"] or None)
		child.set(TO_PROJECT_FIELD, row["to_project"] or None)
		child.set(TO_BIN_FIELD, row["to_bin"] or None)

	stock_entry.flags.ignore_permissions = True
	stock_entry.insert()
	return stock_entry
