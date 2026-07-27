import frappe
from frappe import _
from frappe.utils import flt

from ace.stock.stock_entry import (
	DEFAULT_FG_BIN_FIELD,
	DEFAULT_WIP_BIN_FIELD,
	ensure_default_bin_client_script,
	ensure_seed_default_bin,
	get_default_bin_for_field,
)
from ace.utils.dimension_stock_correction import (
	MIN_QTY,
	create_stock_entry,
	filter_unsupported_items,
	get_item_details,
	submit_with_temporary_negative_stock,
)

PATCH_MARKER = "ace-wip-fg-dimension-backfill-v2"
WAREHOUSE_DEFAULTS = (
	("Work In Progress - ACE", "ACE - WIP", DEFAULT_WIP_BIN_FIELD),
	("Finished Goods - ACE", "ACE - FG", DEFAULT_FG_BIN_FIELD),
)


def execute_warehouse_dimension_backfill():
	lock = frappe.cache.lock("ace-wip-fg-dimension-backfill", timeout=900)

	with lock:
		normalize_blank_stock_dimensions()

		for warehouse, bin_location, fieldname in WAREHOUSE_DEFAULTS:
			ensure_seed_default_bin(bin_location, warehouse, fieldname)
		ensure_default_bin_client_script()

		existing = frappe.db.exists(
			"Stock Entry",
			{
				"remarks": ["like", "%" + PATCH_MARKER + "%"],
				"docstatus": ["<", 2],
			},
		)
		if existing:
			doc = frappe.get_doc("Stock Entry", existing)
			if doc.docstatus == 1:
				return {"status": "already_submitted", "stock_entry": doc.name}
			return submit_with_temporary_negative_stock(doc)

		plan = []
		for warehouse, _bin_location, fieldname in WAREHOUSE_DEFAULTS:
			default_bin = get_default_bin_for_field(fieldname)
			if default_bin:
				plan.extend(get_dimension_backfill_plan(warehouse, default_bin.bin_location))

		if not plan:
			return {"status": "no_rows"}

		item_details = get_item_details({row["item_code"] for row in plan})
		plan, skipped = filter_unsupported_items(plan, item_details)
		if skipped:
			frappe.throw(
				_("Cannot backfill serialized or unsupported WIP/FG rows: {0}").format(
					frappe.as_json(skipped[:50])
				)
			)

		stock_entry = create_stock_entry(plan, item_details, skipped, marker=PATCH_MARKER)
		stock_entry.remarks = (
			PATCH_MARKER
			+ "\nMoves current WIP/FG balances to their configured default bins."
			+ "\nBlank projects are attributed from the originating Stock Entry or Work Order."
		)
		stock_entry.flags.ignore_permissions = True
		stock_entry.save()
		return submit_with_temporary_negative_stock(stock_entry)


def normalize_blank_stock_dimensions():
	"""Treat legacy empty strings as the NULL value used by current stock posting."""
	frappe.db.sql(
		"""update `tabStock Ledger Entry`
		set project_aa = null
		where project_aa = ''"""
	)
	frappe.db.sql(
		"""update `tabStock Ledger Entry`
		set bin_location = null
		where bin_location = ''"""
	)


def get_dimension_backfill_plan(warehouse, default_bin):
	balances = frappe.db.sql(
		"""
		select
			item_code,
			coalesce(project_aa, '') as project,
			coalesce(bin_location, '') as bin_location,
			sum(actual_qty) as qty
		from `tabStock Ledger Entry`
		where is_cancelled = 0 and warehouse = %(warehouse)s
		group by item_code, coalesce(project_aa, ''), coalesce(bin_location, '')
		having sum(actual_qty) > %(min_qty)s
		""",
		{"warehouse": warehouse, "min_qty": MIN_QTY},
		as_dict=True,
	)

	plan = []
	for balance in balances:
		if balance.project:
			if balance.bin_location != default_bin:
				plan.append(make_plan_row(balance, warehouse, default_bin, balance.project, balance.qty))
			continue

		allocations = get_blank_project_allocations(
			balance.item_code,
			warehouse,
			balance.bin_location,
		)
		allocated_qty = sum(flt(row.qty, 6) for row in allocations)
		if abs(flt(balance.qty, 6) - allocated_qty) > MIN_QTY:
			frappe.throw(
				_("Cannot attribute {0} in {1}. Balance is {2}, attributed quantity is {3}.").format(
					balance.item_code, warehouse, balance.qty, allocated_qty
				)
			)

		for allocation in allocations:
			if allocation.qty > MIN_QTY:
				plan.append(
					make_plan_row(
						balance,
						warehouse,
						default_bin,
						allocation.project,
						allocation.qty,
					)
				)

	return plan


def get_blank_project_allocations(item_code, warehouse, bin_location):
	return frappe.db.sql(
		"""
		select
			coalesce(stock_entry.project, work_order.project, '') as project,
			sum(sle.actual_qty) as qty
		from `tabStock Ledger Entry` sle
		left join `tabStock Entry` stock_entry
			on sle.voucher_type = 'Stock Entry' and stock_entry.name = sle.voucher_no
		left join `tabWork Order` work_order on work_order.name = stock_entry.work_order
		where
			sle.is_cancelled = 0
			and sle.item_code = %(item_code)s
			and sle.warehouse = %(warehouse)s
			and coalesce(sle.project_aa, '') = ''
			and coalesce(sle.bin_location, '') = %(bin_location)s
		group by coalesce(stock_entry.project, work_order.project, '')
		having abs(sum(sle.actual_qty)) > %(min_qty)s
		""",
		{
			"item_code": item_code,
			"warehouse": warehouse,
			"bin_location": bin_location,
			"min_qty": MIN_QTY,
		},
		as_dict=True,
	)


def make_plan_row(balance, warehouse, default_bin, target_project, qty):
	if not target_project:
		frappe.throw(_("Cannot determine a project for {0} in {1}.").format(balance.item_code, warehouse))

	return {
		"item_code": balance.item_code,
		"warehouse": warehouse,
		"from_project": balance.project or "",
		"from_bin": balance.bin_location or "",
		"to_project": target_project,
		"to_bin": default_bin,
		"batch_no": "",
		"qty": flt(qty, 6),
	}
