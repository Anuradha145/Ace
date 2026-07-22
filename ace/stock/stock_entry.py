import frappe


WIP_WAREHOUSE = "Work In Progress - ACE"
WIP_BIN = "ACE - WIP"
WIP_BIN_CLIENT_SCRIPT = "ACE WIP Bin Default in Stock Entry"


def set_wip_bin_location(doc, method=None):
	ensure_wip_bin_location()

	for row in doc.get("items") or []:
		if row.get("t_warehouse") == WIP_WAREHOUSE and row.get("s_warehouse") != WIP_WAREHOUSE:
			row.set("to_bin_location", WIP_BIN)

		if row.get("s_warehouse") == WIP_WAREHOUSE and row.get("t_warehouse") != WIP_WAREHOUSE:
			row.set("bin_location", WIP_BIN)


def ensure_wip_bin_location():
	if frappe.db.exists("Bin Location", WIP_BIN):
		return

	bin_location = frappe.new_doc("Bin Location")
	bin_location.bin_location = WIP_BIN
	bin_location.warehouse = WIP_WAREHOUSE
	bin_location.common_bin = 1
	bin_location.flags.ignore_permissions = True
	bin_location.insert()


def ensure_wip_bin_client_script():
	script = """
frappe.ui.form.on("Stock Entry", {
	refresh(frm) {
		set_wip_bin_locations(frm);
	},
	validate(frm) {
		set_wip_bin_locations(frm);
	},
});

frappe.ui.form.on("Stock Entry Detail", {
	s_warehouse(frm) {
		set_wip_bin_locations(frm);
	},
	t_warehouse(frm) {
		set_wip_bin_locations(frm);
	},
	items_add(frm) {
		set_wip_bin_locations(frm);
	},
});

function set_wip_bin_locations(frm) {
	const wip_warehouse = "Work In Progress - ACE";
	const wip_bin = "ACE - WIP";

	(frm.doc.items || []).forEach((row) => {
		if (row.t_warehouse === wip_warehouse && row.s_warehouse !== wip_warehouse) {
			frappe.model.set_value(row.doctype, row.name, "to_bin_location", wip_bin);
		}

		if (row.s_warehouse === wip_warehouse && row.t_warehouse !== wip_warehouse) {
			frappe.model.set_value(row.doctype, row.name, "bin_location", wip_bin);
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
