import hashlib

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, get_datetime, now_datetime

COMPANY = "Aceaviasion Solution Private Limited"
WAREHOUSES = []

PROJECT_FIELD = "project_aa"
BIN_FIELD = "bin_location"
TO_PROJECT_FIELD = "to_project_aa"
TO_BIN_FIELD = "to_bin_location"

STOCK_ENTRY_TYPE = "Material Transfer"
PURPOSE = "Material Transfer"

MIN_QTY = 0.000001
SKIP_SERIAL_ITEMS = True
ALLOW_DIFFERENT_PROJECT_LAST_RESORT = True
PATCH_MARKER = "ace-negative-inventory-dimension-correction-v2"


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

		balances = get_grouped_balances()
		plan, unresolved = build_transfer_plan(balances)
		item_details = get_item_details({row["item_code"] for row in plan})

		skipped = []
		if SKIP_SERIAL_ITEMS:
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

		batch_plan = [row for row in plan if row.get("batch_no")]
		regular_plan = [row for row in plan if not row.get("batch_no")]
		results = []

		for row in batch_plan:
			marker = get_batch_patch_marker(row)
			results.append(
				submit_plan(
					[row],
					item_details,
					skipped,
					marker,
					posting_datetime=get_batch_correction_posting_datetime(row),
				)
			)

		if regular_plan:
			results.append(
				submit_plan(
					regular_plan,
					item_details,
					skipped,
					PATCH_MARKER + "-regular",
				)
			)

		return {"status": "completed", "results": results}


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


def get_existing_correction_entry(marker=PATCH_MARKER):
	return frappe.db.exists(
		"Stock Entry",
		{
			"company": COMPANY,
			"remarks": ["like", "%" + marker + "%"],
			"docstatus": ["<", 2],
		},
	)


def submit_plan(plan, item_details, skipped, marker, posting_datetime=None):
	plan, identical_plan_rows = validate_transfer_plan(plan)
	if identical_plan_rows:
		frappe.logger("ace").warning(
			"Skipped identical transfer-plan rows: %s",
			frappe.as_json(identical_plan_rows),
		)

	if not plan:
		return {
			"status": "no_actionable_rows",
			"skipped_identical_rows": identical_plan_rows,
		}

	existing = get_existing_correction_entry(marker)
	if existing:
		return submit_existing_or_report(existing)

	stock_entry = create_stock_entry(
		plan,
		item_details,
		skipped,
		marker=marker,
		posting_datetime=posting_datetime,
	)
	removed_noop_rows = remove_noop_document_rows(stock_entry)
	if removed_noop_rows:
		frappe.logger("ace").warning(
			"Removed no-op Stock Entry rows: %s",
			frappe.as_json(removed_noop_rows),
		)

	if not stock_entry.items:
		stock_entry.delete(ignore_permissions=True)
		return {
			"status": "no_actionable_rows",
			"removed_noop_rows": removed_noop_rows,
		}

	validate_dimension_only_entry(stock_entry)
	return submit_with_temporary_negative_stock(stock_entry)


def validate_transfer_plan(plan):
	valid_rows = []
	skipped_rows = []

	for index, row in enumerate(plan, start=1):
		source = (
			clean_dimension(row.get("from_project")),
			clean_dimension(row.get("from_bin")),
		)
		target = (
			clean_dimension(row.get("to_project")),
			clean_dimension(row.get("to_bin")),
		)

		if source == target:
			skipped_rows.append(
				{
					"plan_row": index,
					"item_code": row.get("item_code"),
					"warehouse": row.get("warehouse"),
					"batch_no": row.get("batch_no"),
					"source_project": source[0],
					"source_bin": source[1],
					"target_project": target[0],
					"target_bin": target[1],
					"reason": "Source and target dimensions are identical",
				}
			)
			continue

		valid_rows.append(row)

	return valid_rows, skipped_rows


def get_batch_patch_marker(row):
	identity = "|".join(
		str(row.get(field) or "")
		for field in (
			"item_code",
			"warehouse",
			"batch_no",
			"from_project",
			"from_bin",
			"to_project",
			"to_bin",
		)
	)
	return PATCH_MARKER + "-batch-" + hashlib.sha1(identity.encode()).hexdigest()[:12]


def get_batch_correction_posting_datetime(row):
	posting_datetime = frappe.db.sql(
		"""
		select min(sle.posting_datetime)
		from `tabStock Ledger Entry` sle
		inner join `tabSerial and Batch Entry` batch
			on batch.parent = sle.serial_and_batch_bundle
		where
			sle.is_cancelled = 0
			and sle.item_code = %(item_code)s
			and sle.warehouse = %(warehouse)s
			and coalesce(sle.project_aa, '') = %(project)s
			and coalesce(sle.bin_location, '') = %(bin)s
			and batch.batch_no = %(batch_no)s
			and batch.qty < 0
		""",
		{
			"item_code": row["item_code"],
			"warehouse": row["warehouse"],
			"project": row["to_project"],
			"bin": row["to_bin"],
			"batch_no": row["batch_no"],
		},
	)[0][0]

	if not posting_datetime:
		frappe.throw(
			_("Could not find the negative batch transaction for {0}, batch {1}.").format(
				row["item_code"], row["batch_no"]
			)
		)

	return add_to_date(get_datetime(posting_datetime), seconds=-1)


def submit_existing_or_report(stock_entry_name):
	doc = frappe.get_doc("Stock Entry", stock_entry_name)

	if doc.docstatus == 1:
		return {
			"status": "already_submitted",
			"stock_entry": doc.name,
		}

	if doc.docstatus != 0:
		frappe.throw(_("Stock Entry {0} must be Draft or Submitted.").format(doc.name))

	removed_noop_rows = remove_noop_document_rows(doc)
	if removed_noop_rows:
		frappe.logger("ace").warning(
			"Removed no-op Stock Entry rows: %s",
			frappe.as_json(removed_noop_rows),
		)

	if not doc.items:
		doc.delete(ignore_permissions=True)
		return {
			"status": "no_actionable_rows",
			"removed_noop_rows": removed_noop_rows,
		}

	validate_dimension_only_entry(doc)
	prepare_correction_rows(doc)
	return submit_with_temporary_negative_stock(doc)


def get_grouped_balances():
	params = {
		"company": COMPANY,
		"min_qty": MIN_QTY,
		"filter_warehouses": 1 if WAREHOUSES else 0,
		"warehouses": tuple(WAREHOUSES) if WAREHOUSES else ("",),
	}

	non_batch_query = """
		select
			sle.item_code,
			sle.warehouse,
			coalesce(sle.project_aa, '') as project_value,
			coalesce(sle.bin_location, '') as bin_value,
			'' as batch_no,
			sum(sle.actual_qty) as qty
		from `tabStock Ledger Entry` sle
		inner join `tabItem` item on item.name = sle.item_code and item.has_batch_no = 0
		where sle.is_cancelled = 0
			and sle.company = %(company)s
			and (%(filter_warehouses)s = 0 or sle.warehouse in %(warehouses)s)
		group by sle.item_code, sle.warehouse,
			coalesce(sle.project_aa, ''), coalesce(sle.bin_location, '')
		having abs(sum(sle.actual_qty)) > %(min_qty)s
	"""

	batch_query = """
		select
			sle.item_code,
			sle.warehouse,
			coalesce(sle.project_aa, '') as project_value,
			coalesce(sle.bin_location, '') as bin_value,
			batch.batch_no,
			sum(batch.qty) as qty
		from `tabStock Ledger Entry` sle
		inner join `tabItem` item on item.name = sle.item_code and item.has_batch_no = 1
		inner join `tabSerial and Batch Entry` batch
			on batch.parent = sle.serial_and_batch_bundle and batch.batch_no is not null
		where sle.is_cancelled = 0
			and sle.company = %(company)s
			and (%(filter_warehouses)s = 0 or sle.warehouse in %(warehouses)s)
		group by sle.item_code, sle.warehouse,
			coalesce(sle.project_aa, ''), coalesce(sle.bin_location, ''), batch.batch_no
		having abs(sum(batch.qty)) > %(min_qty)s
	"""

	return frappe.db.sql(non_batch_query, params, as_dict=True) + frappe.db.sql(
		batch_query, params, as_dict=True
	)


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
		batch_no = clean_dimension(row.batch_no)
		key = (row.item_code, row.warehouse, batch_no)
		entry = {
			"item_code": row.item_code,
			"warehouse": row.warehouse,
			"project": clean_dimension(row.project_value),
			"bin": clean_dimension(row.bin_value),
			"batch_no": batch_no,
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
						"batch_no": target["batch_no"],
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
		reason = None

		if not item:
			reason = "Item not found"
		elif not item.is_stock_item:
			reason = "Not a stock item"
		elif item.has_serial_no:
			reason = "Serialized item"
		elif item.has_batch_no and not row.get("batch_no"):
			reason = "Batch item without batch allocation"

		if reason:
			skipped.append(dict(row, reason=reason))
		else:
			supported.append(row)

	return supported, skipped


def skip_inventory_dimension_mandatory_validation():
	"""Allow correction rows to consume legacy empty dimension buckets."""


def create_stock_entry(plan, item_details, skipped, marker=PATCH_MARKER, posting_datetime=None):
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = COMPANY
	stock_entry.stock_entry_type = STOCK_ENTRY_TYPE
	stock_entry.purpose = PURPOSE
	posting_datetime = posting_datetime or now_datetime()
	stock_entry.posting_date = posting_datetime.date()
	stock_entry.posting_time = posting_datetime.time()
	stock_entry.set_posting_time = 1
	stock_entry.remarks = (
		marker
		+ "\nCorrection for negative Project/Bin inventory dimension balances."
		+ "\nWarehouse is unchanged on every row."
		+ "\nSerialized/unsupported skipped rows: "
		+ str(len(skipped))
	)

	expected_dimensions = []
	for row in plan:
		expected_dimensions.append(
			{
				"from_project": clean_dimension(row.get("from_project")),
				"from_bin": clean_dimension(row.get("from_bin")),
				"to_project": clean_dimension(row.get("to_project")),
				"to_bin": clean_dimension(row.get("to_bin")),
			}
		)
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
		if row.get("batch_no"):
			child.use_serial_batch_fields = 1
			child.batch_no = row["batch_no"]

	stock_entry.flags.ignore_permissions = True
	# Legacy stock can exist in an empty dimension bucket even when that
	# dimension is mandatory now. The correction must preserve that empty
	# source value so it consumes the legacy bucket instead of creating a new
	# negative balance under the target dimension.
	stock_entry.flags.ignore_mandatory = True
	stock_entry.validate_inventory_dimension_mandatory = skip_inventory_dimension_mandatory_validation
	stock_entry.insert()
	restore_inserted_dimensions(stock_entry, expected_dimensions)
	validate_dimension_only_entry(stock_entry)
	return stock_entry


def get_row_dimensions(row):
	return {
		"from_project": clean_dimension(row.get(PROJECT_FIELD)),
		"from_bin": clean_dimension(row.get(BIN_FIELD)),
		"to_project": clean_dimension(row.get(TO_PROJECT_FIELD)),
		"to_bin": clean_dimension(row.get(TO_BIN_FIELD)),
	}


def restore_inserted_dimensions(stock_entry, expected_dimensions):
	if len(stock_entry.items) != len(expected_dimensions):
		frappe.throw(
			_("Stock Entry {0}: Expected {1} rows after insert, found {2}.").format(
				stock_entry.name,
				len(expected_dimensions),
				len(stock_entry.items),
			)
		)

	for index, child in enumerate(stock_entry.items):
		expected = expected_dimensions[index]
		actual = get_row_dimensions(child)
		if expected != actual:
			frappe.logger("ace").warning(
				"Stock Entry dimension fields changed during insert. Row=%s Item=%s Expected=%s Actual=%s",
				child.idx,
				child.item_code,
				expected,
				actual,
			)

		child.set(PROJECT_FIELD, expected["from_project"] or None)
		child.set(BIN_FIELD, expected["from_bin"] or None)
		child.set(TO_PROJECT_FIELD, expected["to_project"] or None)
		child.set(TO_BIN_FIELD, expected["to_bin"] or None)

		frappe.db.set_value(
			"Stock Entry Detail",
			child.name,
			{
				PROJECT_FIELD: expected["from_project"] or None,
				BIN_FIELD: expected["from_bin"] or None,
				TO_PROJECT_FIELD: expected["to_project"] or None,
				TO_BIN_FIELD: expected["to_bin"] or None,
			},
			update_modified=False,
		)

	stock_entry.reload()
	stock_entry.flags.ignore_permissions = True
	stock_entry.flags.ignore_mandatory = True
	stock_entry.validate_inventory_dimension_mandatory = skip_inventory_dimension_mandatory_validation
	for index, child in enumerate(stock_entry.items):
		expected = expected_dimensions[index]
		actual = get_row_dimensions(child)
		if expected != actual:
			frappe.throw(
				_(
					"Stock Entry {0}, row {1}: Could not restore intended dimensions. Expected={2}, Actual={3}."
				).format(
					stock_entry.name,
					child.idx,
					frappe.as_json(expected),
					frappe.as_json(actual),
				)
			)


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
			frappe.throw(
				_(
					"Row {0}, Item {1}, Warehouse {2}: "
					"Source and target dimensions are identical. "
					"Source Project={3}, Source Bin={4}, "
					"Target Project={5}, Target Bin={6}."
				).format(
					row.idx,
					row.item_code,
					row.s_warehouse,
					clean_dimension(row.get(PROJECT_FIELD)) or "[empty]",
					clean_dimension(row.get(BIN_FIELD)) or "[empty]",
					clean_dimension(row.get(TO_PROJECT_FIELD)) or "[empty]",
					clean_dimension(row.get(TO_BIN_FIELD)) or "[empty]",
				)
			)


def dimensions_changed(row):
	source = (clean_dimension(row.get(PROJECT_FIELD)), clean_dimension(row.get(BIN_FIELD)))
	target = (clean_dimension(row.get(TO_PROJECT_FIELD)), clean_dimension(row.get(TO_BIN_FIELD)))
	return source != target


def remove_noop_document_rows(doc):
	removed = []

	for row in list(doc.items):
		source = (
			clean_dimension(row.get(PROJECT_FIELD)),
			clean_dimension(row.get(BIN_FIELD)),
		)
		target = (
			clean_dimension(row.get(TO_PROJECT_FIELD)),
			clean_dimension(row.get(TO_BIN_FIELD)),
		)

		if source != target:
			continue

		removed.append(
			{
				"idx": row.idx,
				"item_code": row.item_code,
				"warehouse": row.s_warehouse,
				"project": source[0],
				"bin": source[1],
			}
		)
		doc.remove(row)

	return removed


def prepare_correction_rows(doc):
	for row in doc.items:
		if hasattr(row, "allow_zero_valuation_rate"):
			row.allow_zero_valuation_rate = 1


def submit_with_temporary_negative_stock(doc):
	prepare_correction_rows(doc)

	stock_settings = frappe.get_single("Stock Settings")
	original_allow_negative_stock = stock_settings.allow_negative_stock or 0
	original_allow_negative_stock_for_batch = stock_settings.allow_negative_stock_for_batch or 0

	try:
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock_for_batch", 1)
		frappe.clear_cache(doctype="Stock Settings")

		doc.flags.ignore_permissions = True
		doc.flags.ignore_mandatory = True
		doc.submit()

		if doc.docstatus != 1:
			frappe.throw(_("Stock Entry {0} was not submitted.").format(doc.name))

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
		frappe.db.set_single_value(
			"Stock Settings",
			"allow_negative_stock_for_batch",
			original_allow_negative_stock_for_batch,
		)
		frappe.clear_cache(doctype="Stock Settings")
