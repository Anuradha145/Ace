import frappe
from frappe import _
from frappe.utils import flt

DEFAULT_WIP_BIN_FIELD = "custom_is_default_wip_bin"
DEFAULT_FG_BIN_FIELD = "custom_is_default_fg_bin"
DEFAULT_BIN_FIELDS = (DEFAULT_WIP_BIN_FIELD, DEFAULT_FG_BIN_FIELD)
DEFAULT_BIN_CLIENT_SCRIPT = "ACE Default Bin in Stock Entry"
OLD_WIP_BIN_CLIENT_SCRIPT = "ACE WIP Bin Default in Stock Entry"
PROJECT_FIELD = "project_aa"
TO_PROJECT_FIELD = "to_project_aa"


def set_default_bin_location(doc, method=None):
	if doc.flags.get("ace_preserve_inventory_dimensions"):
		return

	set_project_dimensions_from_parent(doc)
	default_bins = get_default_bin_locations()

	for row in doc.get("items") or []:
		if row.get("s_warehouse") == row.get("t_warehouse"):
			continue

		target_bin = default_bins.get(row.get("t_warehouse"))
		if target_bin:
			row.set("to_bin_location", target_bin)

		set_source_bin_from_available_stock(row)

	validate_transfer_bins(doc)


def validate_transfer_bins(doc):
	for row in doc.get("items") or []:
		if not row.get("s_warehouse") or not row.get("t_warehouse"):
			continue

		if not row.get("to_bin_location"):
			frappe.throw(
				_("Row {0}: Target Bin Location is required for a stock transfer into {1}.").format(
					row.idx, row.t_warehouse
				)
			)


def set_source_bin_from_available_stock(row):
	"""Use a bin only when the source balance really belongs to that bin.

	Legacy stock can validly live in the blank-bin bucket. Keeping that source blank
	prevents a return transfer from creating a negative balance in an invented bin.
	"""
	if row.get("bin_location") or not row.get("s_warehouse") or not row.get("item_code"):
		return

	balances = get_source_bin_balances(
		row.item_code,
		row.s_warehouse,
		row.get(PROJECT_FIELD),
		row.get("batch_no"),
	)
	required_qty = abs(flt(row.get("transfer_qty") or row.get("qty")))
	blank_qty = next((flt(d.qty) for d in balances if not d.bin_location), 0)
	if blank_qty + 1e-6 >= required_qty:
		return

	candidates = [d for d in balances if d.bin_location and flt(d.qty) + 1e-6 >= required_qty]
	if len(candidates) == 1:
		row.set("bin_location", candidates[0].bin_location)
	elif len(candidates) > 1:
		frappe.throw(
			_(
				"Row {0}: Stock is available in multiple source bins for {1}. "
				"Select the Source Bin Location explicitly."
			).format(row.idx, row.item_code)
		)
	elif any(d.bin_location for d in balances):
		frappe.throw(
			_(
				"Row {0}: No single source bin has enough stock for {1}. "
				"Split the quantity across rows and select the correct Source Bin Location on each row."
			).format(row.idx, row.item_code)
		)


@frappe.whitelist()
def get_source_bin_balances(
	item_code: str,
	warehouse: str,
	project: str | None = None,
	batch_no: str | None = None,
):
	return frappe.db.sql(
		"""
		select coalesce(bin_location, '') as bin_location, sum(actual_qty) as qty
		from `tabStock Ledger Entry`
		where is_cancelled = 0
			and item_code = %(item_code)s
			and warehouse = %(warehouse)s
			and coalesce(project_aa, '') = coalesce(%(project)s, '')
			and (%(batch_no)s is null or %(batch_no)s = '' or batch_no = %(batch_no)s)
		group by coalesce(bin_location, '')
		having sum(actual_qty) > 0.000001
		""",
		{
			"item_code": item_code,
			"warehouse": warehouse,
			"project": project,
			"batch_no": batch_no,
		},
		as_dict=True,
	)


def set_project_dimensions_from_parent(doc):
	parent_project = get_parent_project(doc)
	if not parent_project and not any(row.get(PROJECT_FIELD) for row in doc.get("items") or []):
		return

	if parent_project and not doc.get("project"):
		doc.project = parent_project

	for row in doc.get("items") or []:
		source_project = row.get(PROJECT_FIELD) or parent_project
		target_project = parent_project or source_project

		if parent_project:
			row.set("project", parent_project)

		if row.get("s_warehouse") and source_project:
			row.set(PROJECT_FIELD, source_project)

		if row.get("t_warehouse") and target_project:
			row.set(TO_PROJECT_FIELD, target_project)


def get_parent_project(doc):
	if doc.get("project"):
		return doc.project

	if doc.get("pick_list"):
		project = frappe.db.get_value("Pick List", doc.pick_list, "custom_project")
		if project:
			return project

	if doc.get("work_order"):
		return frappe.db.get_value("Work Order", doc.work_order, "project")

	return None


def validate_default_bin_location(doc, method=None):
	if doc.get(DEFAULT_WIP_BIN_FIELD) and doc.get(DEFAULT_FG_BIN_FIELD):
		frappe.throw(_("A Bin Location cannot be both Default WIP Bin and Default FG Bin."))

	for fieldname in DEFAULT_BIN_FIELDS:
		if not doc.get(fieldname):
			continue

		if not doc.get("warehouse"):
			frappe.throw(_("Warehouse is required for a default stock bin."))

		existing = frappe.get_all(
			"Bin Location",
			filters={
				fieldname: 1,
				"name": ["!=", doc.name],
			},
			pluck="name",
			limit=1,
		)
		if existing:
			label = frappe.get_meta("Bin Location").get_label(fieldname)
			frappe.throw(_("{0} is already set on Bin Location {1}.").format(label, existing[0]))


@frappe.whitelist()
def get_default_bin_locations():
	if not has_default_bin_fields():
		return {}

	bins = frappe.get_all(
		"Bin Location",
		filters=[
			["Bin Location", "warehouse", "is", "set"],
			[
				"Bin Location",
				DEFAULT_WIP_BIN_FIELD,
				"=",
				1,
			],
		],
		fields=["warehouse", "bin_location"],
	)
	bins.extend(
		frappe.get_all(
			"Bin Location",
			filters=[
				["Bin Location", "warehouse", "is", "set"],
				[
					"Bin Location",
					DEFAULT_FG_BIN_FIELD,
					"=",
					1,
				],
			],
			fields=["warehouse", "bin_location"],
		)
	)

	return {row.warehouse: row.bin_location for row in bins if row.warehouse and row.bin_location}


def has_default_bin_fields():
	meta = frappe.get_meta("Bin Location")
	return all(meta.has_field(fieldname) for fieldname in DEFAULT_BIN_FIELDS)


def get_default_bin_for_field(fieldname):
	if not has_default_bin_fields():
		return None

	rows = frappe.get_all(
		"Bin Location",
		filters={fieldname: 1},
		fields=["bin_location", "warehouse"],
		limit=1,
	)
	return rows[0] if rows else None


def ensure_seed_default_bin(bin_name, warehouse, fieldname):
	ensure_default_bin_custom_fields()

	if get_default_bin_for_field(fieldname):
		return

	if frappe.db.exists("Bin Location", bin_name):
		doc = frappe.get_doc("Bin Location", bin_name)
	else:
		doc = frappe.new_doc("Bin Location")
		doc.bin_location = bin_name

	doc.warehouse = warehouse
	doc.set(fieldname, 1)
	doc.flags.ignore_permissions = True
	doc.save()


def ensure_default_bin_custom_fields():
	fields = [
		{
			"fieldname": DEFAULT_WIP_BIN_FIELD,
			"label": "Default WIP Bin",
			"insert_after": "warehouse",
		},
		{
			"fieldname": DEFAULT_FG_BIN_FIELD,
			"label": "Default FG Bin",
			"insert_after": DEFAULT_WIP_BIN_FIELD,
		},
	]

	for field in fields:
		if frappe.db.exists("Custom Field", {"dt": "Bin Location", "fieldname": field["fieldname"]}):
			continue

		doc = frappe.new_doc("Custom Field")
		doc.dt = "Bin Location"
		doc.fieldname = field["fieldname"]
		doc.label = field["label"]
		doc.fieldtype = "Check"
		doc.insert_after = field["insert_after"]
		doc.default = "0"
		doc.flags.ignore_permissions = True
		doc.insert()

	frappe.clear_cache(doctype="Bin Location")


def ensure_default_bin_client_script():
	script = """
frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		set_project_dimensions(frm);
		set_default_bin_locations(frm);
	},
	project(frm) {
		set_project_dimensions(frm);
	},
	validate(frm) {
		set_project_dimensions(frm);
		set_default_bin_locations(frm);
	},
});

frappe.ui.form.on("Stock Entry Detail", {
	s_warehouse(frm) {
		set_project_dimensions(frm);
		set_default_bin_locations(frm);
	},
	t_warehouse(frm) {
		set_project_dimensions(frm);
		set_default_bin_locations(frm);
	},
	items_add(frm) {
		set_project_dimensions(frm);
		set_default_bin_locations(frm);
	},
});

function set_project_dimensions(frm) {
	(frm.doc.items || []).forEach((row) => {
		const source_project = row.project_aa || frm.doc.project;
		const target_project = frm.doc.project || source_project;

		if (frm.doc.project) {
			frappe.model.set_value(row.doctype, row.name, "project", frm.doc.project);
		}

		if (row.s_warehouse && source_project) {
			frappe.model.set_value(row.doctype, row.name, "project_aa", source_project);
		}

		if (row.t_warehouse && target_project) {
			frappe.model.set_value(row.doctype, row.name, "to_project_aa", target_project);
		}
	});
}

function set_default_bin_locations(frm) {
	frappe.call({
		method: "ace.stock.stock_entry.get_default_bin_locations",
		callback(r) {
			const default_bins = r.message || {};

			(frm.doc.items || []).forEach((row) => {
				if (row.s_warehouse === row.t_warehouse) return;

				if (default_bins[row.t_warehouse]) {
					frappe.model.set_value(
						row.doctype,
						row.name,
						"to_bin_location",
						default_bins[row.t_warehouse]
					);
				}

			});
		},
	});
}
""".strip()

	if frappe.db.exists("Client Script", DEFAULT_BIN_CLIENT_SCRIPT):
		doc = frappe.get_doc("Client Script", DEFAULT_BIN_CLIENT_SCRIPT)
	else:
		doc = frappe.new_doc("Client Script")
		doc.name = DEFAULT_BIN_CLIENT_SCRIPT
		doc.dt = "Stock Entry"
		doc.view = "Form"

	doc.enabled = 1
	doc.script = script
	doc.flags.ignore_permissions = True
	doc.save()

	if frappe.db.exists("Client Script", OLD_WIP_BIN_CLIENT_SCRIPT):
		frappe.db.set_value("Client Script", OLD_WIP_BIN_CLIENT_SCRIPT, "enabled", 0)
