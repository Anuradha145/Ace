import frappe
from frappe import _


DEFAULT_WIP_BIN_FIELD = "custom_is_default_wip_bin"
DEFAULT_FG_BIN_FIELD = "custom_is_default_fg_bin"
DEFAULT_BIN_FIELDS = (DEFAULT_WIP_BIN_FIELD, DEFAULT_FG_BIN_FIELD)
DEFAULT_BIN_CLIENT_SCRIPT = "ACE Default Bin in Stock Entry"
OLD_WIP_BIN_CLIENT_SCRIPT = "ACE WIP Bin Default in Stock Entry"


def set_default_bin_location(doc, method=None):
	default_bins = get_default_bin_locations()

	for row in doc.get("items") or []:
		if row.get("s_warehouse") == row.get("t_warehouse"):
			continue

		target_bin = default_bins.get(row.get("t_warehouse"))
		if target_bin:
			row.set("to_bin_location", target_bin)

		source_bin = default_bins.get(row.get("s_warehouse"))
		if source_bin:
			row.set("bin_location", source_bin)


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
		set_default_bin_locations(frm);
	},
	validate(frm) {
		set_default_bin_locations(frm);
	},
});

frappe.ui.form.on("Stock Entry Detail", {
	s_warehouse(frm) {
		set_default_bin_locations(frm);
	},
	t_warehouse(frm) {
		set_default_bin_locations(frm);
	},
	items_add(frm) {
		set_default_bin_locations(frm);
	},
});

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

				if (default_bins[row.s_warehouse]) {
					frappe.model.set_value(
						row.doctype,
						row.name,
						"bin_location",
						default_bins[row.s_warehouse]
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
