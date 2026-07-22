import frappe
from frappe import _
from frappe.utils import flt, nowdate, nowtime


COMPANY = "Aceaviasion Solution Private Limited"
WAREHOUSES = []

PROJECT_FIELD = "project_aa"
BIN_FIELD = "bin_location"
TO_PROJECT_FIELD = "to_project_aa"
TO_BIN_FIELD = "to_bin_location"

STOCK_ENTRY_TYPE = "Material Transfer"
PURPOSE = "Material Transfer"

MIN_QTY = 0.000001
SKIP_SERIAL_AND_BATCH_ITEMS = True
ALLOW_DIFFERENT_PROJECT_LAST_RESORT = True
PATCH_MARKER = "ace-negative-inventory-dimension-correction-v1"


def execute_dimension_stock_correction():
	"""
	Create and submit a same-warehouse Project/Bin correction Stock Entry.

	The correction moves quantity from positive dimension buckets to matching
	negative dimension buckets for the same Item and Warehouse. Warehouse-level
	quantity stays unchanged; only Project/Bin allocation is reclassified.
	"""
	lock = frappe.cache.lock("ace-dimension-stock-correction-submit", timeout=900)

	with lock:
		validate_setup()

		existing = get_existing_correction_entry()
		if existing:
			return submit_existing_or_report(existing)

		balances = get_grouped_balances()
		plan, unresolved = build_transfer_plan(balances)
		item_details = get_item_details({row["item_code"] for row in plan})

		skipped = []
		if SKIP_SERIAL_AND_BATCH_ITEMS:
			plan, skipped = filter_unsupported_items(plan, item_details)

		if unresolved:
			frappe.throw(
				_("Cannot create correction Stock Entry. Unresolved rows: {0}").format(
					frappe.as_json(unresolved[:50])
				)
			)

		if not plan:
			frappe.logger().info("No negative inventory dimension correction rows found.")
			return {"status": "no_rows"}

		stock_entry = create_stock_entry(plan, item_details, skipped)
		validate_dimension_only_entry(stock_entry)

		return submit_with_temporary_negative_stock(stock_entry)


def clean_dimension(value):
	if value is None:
		return ""
	return str(value).strip()


def ensure_field(doctype, fieldname):
	if not frappe.get_meta(doctype).has_field(fieldname):
		frappe.throw(_("Missing field {0} on {1}").format(fieldname, doctype))


def validate_setup():
	ensure_field("Stock Ledger Entry", PROJECT_FIELD)
	ensure_field("Stock Ledger Entry", BIN_FIELD)
	ensure_field("Stock Entry Detail", PROJECT_FIELD)
	ensure_field("Stock Entry Detail", BIN_FIELD)
	ensure_field("Stock Entry Detail", TO_PROJECT_FIELD)
	ensure_field("Stock Entry Detail", TO_BIN_FIELD)


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
		return {
			"status": "already_submitted",
			"stock_entry": doc.name,
		}

	if doc.docstatus != 0:
		frappe.throw(_("Stock Entry {0} must be Draft or Submitted.").format(doc.name))

	validate_dimension_only_entry(doc)
	prepare_correction_rows(doc)
	return submit_with_temporary_negative_stock(doc)


def get_grouped_balances():
	conditions = ["is_cancelled = 0", "company = %(company)s"]
	params = {"company": COMPANY, "min_qty": MIN_QTY}

	if WAREHOUSES:
		conditions.append("warehouse in %(warehouses)s")
		params["warehouses"] = tuple(WAREHOUSES)

	query = """
		select
			item_code,
			warehouse,
			coalesce({project_field}, '') as project_value,
			coalesce({bin_field}, '') as bin_value,
			sum(actual_qty) as qty
		from `tabStock Ledger Entry`
		where {conditions}
		group by item_code, warehouse, coalesce({project_field}, ''), coalesce({bin_field}, '')
		having abs(sum(actual_qty)) > %(min_qty)s
	""".format(
		project_field=PROJECT_FIELD,
		bin_field=BIN_FIELD,
		conditions=" and ".join(conditions),
	)

	return frappe.db.sql(query, params, as_dict=True)


def get_item_details(item_codes):
	if not item_codes:
		return {}

	rows = frappe.get_all(
		"Item",
		filters={"name": ["in", list(item_codes)]},
		fields=["name", "stock_uom", "has_serial_no", "has_batch_no", "is_stock_item"],
	)
	return {row.name: row for row in rows}


def rank_source(source, target):
	same_project = source["project"] == target["project"]

	if same_project and source["bin"] and source["bin"] != target["bin"]:
		return 0
	if same_project and not source["bin"]:
		return 1
	if not source["project"]:
		return 2
	if ALLOW_DIFFERENT_PROJECT_LAST_RESORT:
		return 3
	return 99


def build_transfer_plan(balances):
	positives_by_key = {}
	negatives_by_key = {}

	for row in balances:
		qty = flt(row.qty, 6)
		key = (row.item_code, row.warehouse)
		entry = {
			"item_code": row.item_code,
			"warehouse": row.warehouse,
			"project": clean_dimension(row.project_value),
			"bin": clean_dimension(row.bin_value),
			"qty": qty,
		}

		if qty > MIN_QTY:
			positives_by_key.setdefault(key, []).append(entry)
		elif qty < -MIN_QTY:
			entry["need"] = flt(-qty, 6)
			negatives_by_key.setdefault(key, []).append(entry)

	plan = []
	unresolved = []

	for key in negatives_by_key:
		sources = positives_by_key.get(key, [])
		negatives = sorted(
			negatives_by_key[key],
			key=lambda target: (target["project"], target["bin"], -target["need"]),
		)

		for target in negatives:
			candidates = [
				source
				for source in sources
				if source["qty"] > MIN_QTY
				and not (source["project"] == target["project"] and source["bin"] == target["bin"])
				and rank_source(source, target) < 99
			]
			candidates = sorted(
				candidates,
				key=lambda source: (
					rank_source(source, target),
					source["project"] != target["project"],
					-source["qty"],
					source["project"],
					source["bin"],
				),
			)

			remaining = target["need"]
			for source in candidates:
				if remaining <= MIN_QTY:
					break

				qty = flt(min(source["qty"], remaining), 6)
				if qty <= MIN_QTY:
					continue

				plan.append(
					{
						"item_code": target["item_code"],
						"warehouse": target["warehouse"],
						"from_project": source["project"],
						"from_bin": source["bin"],
						"to_project": target["project"],
						"to_bin": target["bin"],
						"qty": qty,
					}
				)
				source["qty"] = flt(source["qty"] - qty, 6)
				remaining = flt(remaining - qty, 6)

			if remaining > MIN_QTY:
				unresolved.append(
					{
						"item_code": target["item_code"],
						"warehouse": target["warehouse"],
						"to_project": target["project"],
						"to_bin": target["bin"],
						"uncovered_qty": remaining,
					}
				)

	return plan, unresolved


def filter_unsupported_items(plan, item_details):
	supported = []
	skipped = []

	for row in plan:
		item = item_details.get(row["item_code"])
		reason = ""

		if not item:
			reason = "Item not found"
		elif not item.is_stock_item:
			reason = "Not a stock item"
		elif item.has_serial_no:
			reason = "Serialized item"
		elif item.has_batch_no:
			reason = "Batch item"

		if reason:
			skipped.append(dict(row, reason=reason))
		else:
			supported.append(row)

	return supported, skipped


def create_stock_entry(plan, item_details, skipped):
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = COMPANY
	stock_entry.stock_entry_type = STOCK_ENTRY_TYPE
	stock_entry.purpose = PURPOSE
	stock_entry.posting_date = nowdate()
	stock_entry.posting_time = nowtime()
	stock_entry.set_posting_time = 1
	stock_entry.remarks = (
		PATCH_MARKER
		+ "\nCorrection for negative Project/Bin inventory dimension balances."
		+ "\nWarehouse is unchanged on every row."
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


def validate_dimension_only_entry(doc):
	if not doc.items:
		frappe.throw(_("Stock Entry {0} has no item rows.").format(doc.name))

	for row in doc.items:
		if not row.item_code:
			frappe.throw(_("Row {0}: Item Code is required.").format(row.idx))

		if not row.s_warehouse or not row.t_warehouse:
			frappe.throw(_("Row {0}: Source and target warehouses are required.").format(row.idx))

		if row.s_warehouse != row.t_warehouse:
			frappe.throw(
				_("Row {0}: Warehouse movement is not allowed. Source is {1}, target is {2}.").format(
					row.idx,
					row.s_warehouse,
					row.t_warehouse,
				)
			)

		if not row.qty or row.qty <= 0:
			frappe.throw(_("Row {0}: Quantity must be greater than zero.").format(row.idx))

		if not dimensions_changed(row):
			frappe.throw(_("Row {0}: Source and target dimensions are identical.").format(row.idx))


def dimensions_changed(row):
	source = (row.get(PROJECT_FIELD), row.get(BIN_FIELD))
	target = (row.get(TO_PROJECT_FIELD), row.get(TO_BIN_FIELD))
	return source != target


def prepare_correction_rows(doc):
	for row in doc.items:
		if hasattr(row, "allow_zero_valuation_rate"):
			row.allow_zero_valuation_rate = 1


def submit_with_temporary_negative_stock(doc):
	prepare_correction_rows(doc)

	stock_settings = frappe.get_single("Stock Settings")
	original_allow_negative_stock = stock_settings.allow_negative_stock or 0

	try:
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
		frappe.clear_cache(doctype="Stock Settings")

		doc.flags.ignore_permissions = True
		doc.flags.ignore_mandatory = True
		doc.submit()

		if doc.docstatus != 1:
			frappe.throw(_("Stock Entry {0} was not submitted.").format(doc.name))

		frappe.db.commit()

		return {
			"status": "submitted",
			"stock_entry": doc.name,
			"rows": len(doc.items),
			"value_difference": doc.value_difference,
		}

	except Exception:
		frappe.db.rollback()
		raise

	finally:
		frappe.db.set_single_value(
			"Stock Settings",
			"allow_negative_stock",
			original_allow_negative_stock,
		)
		frappe.clear_cache(doctype="Stock Settings")
		frappe.db.commit()
