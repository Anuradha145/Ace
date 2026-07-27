import frappe

from ace.stock.stock_entry import (
	ensure_default_bin_client_script,
	set_default_bin_location,
)


def execute():
	ensure_default_bin_client_script()

	names = frappe.get_all(
		"Stock Entry",
		filters={
			"docstatus": 0,
			"project": ["is", "set"],
		},
		pluck="name",
	)

	for name in names:
		doc = frappe.get_doc("Stock Entry", name)
		set_default_bin_location(doc)
		doc.flags.ignore_permissions = True
		doc.save()
