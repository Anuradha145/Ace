import frappe


def execute():
	for script_name in ("Pick Final Fix", "Pick-Final", "Pick List", "Bin Pick", "Set Qty in Pick list item"):
		if frappe.db.exists("Server Script", script_name):
			frappe.db.set_value("Server Script", script_name, "disabled", 1, update_modified=False)

	frappe.clear_cache(doctype="Pick List")
