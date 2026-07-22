import frappe


WIP_WAREHOUSE = "Work In Progress - ACE"
WIP_BIN = "ACE - WIP"
FG_WAREHOUSE = "Finished Goods - ACE"
FG_BIN = "ACE - FG"
WIP_BIN_CLIENT_SCRIPT = "ACE WIP Bin Default in Stock Entry"
DEFAULT_BIN_BY_WAREHOUSE = {
	WIP_WAREHOUSE: WIP_BIN,
	FG_WAREHOUSE: FG_BIN,
}


def set_wip_bin_location(doc, method=None):
	ensure_default_bin_locations()

	for row in doc.get("items") or []:
		target_bin = DEFAULT_BIN_BY_WAREHOUSE.get(row.get("t_warehouse"))
		if target_bin and row.get("s_warehouse") != row.get("t_warehouse"):
			row.set("to_bin_location", target_bin)

		source_bin = DEFAULT_BIN_BY_WAREHOUSE.get(row.get("s_warehouse"))
		if source_bin and row.get("s_warehouse") != row.get("t_warehouse"):
			row.set("bin_location", source_bin)


def ensure_default_bin_locations():
	for warehouse, bin_name in DEFAULT_BIN_BY_WAREHOUSE.items():
		ensure_bin_location(bin_name, warehouse)


def ensure_wip_bin_location():
	ensure_bin_location(WIP_BIN, WIP_WAREHOUSE)


def ensure_fg_bin_location():
	ensure_bin_location(FG_BIN, FG_WAREHOUSE)


def ensure_bin_location(bin_name, warehouse):
	if frappe.db.exists("Bin Location", bin_name):
		return

	bin_location = frappe.new_doc("Bin Location")
	bin_location.bin_location = bin_name
	bin_location.warehouse = warehouse
	bin_location.common_bin = 1
	bin_location.flags.ignore_permissions = True
	bin_location.insert()


def ensure_wip_bin_client_script():
	ensure_default_bin_client_script()


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
	const default_bins = {
		"Work In Progress - ACE": "ACE - WIP",
		"Finished Goods - ACE": "ACE - FG",
	};

	(frm.doc.items || []).forEach((row) => {
		if (default_bins[row.t_warehouse] && row.s_warehouse !== row.t_warehouse) {
			frappe.model.set_value(row.doctype, row.name, "to_bin_location", default_bins[row.t_warehouse]);
		}

		if (default_bins[row.s_warehouse] && row.s_warehouse !== row.t_warehouse) {
			frappe.model.set_value(row.doctype, row.name, "bin_location", default_bins[row.s_warehouse]);
		}
	});
}
""".strip()

	if frappe.db.exists("Client Script", WIP_BIN_CLIENT_SCRIPT):
		doc = frappe.get_doc("Client Script", WIP_BIN_CLIENT_SCRIPT)
	else:
		doc = frappe.new_doc("Client Script")
		doc.name = WIP_BIN_CLIENT_SCRIPT
		doc.dt = "Stock Entry"
		doc.view = "Form"

	doc.enabled = 1
	doc.script = script
	doc.flags.ignore_permissions = True
	doc.save()
