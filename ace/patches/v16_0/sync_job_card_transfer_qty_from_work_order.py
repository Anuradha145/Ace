import frappe

from ace.stock.work_order import update_material_transferred_for_work_order


def execute():
	work_orders = frappe.get_all(
		"Work Order",
		filters={
			"docstatus": 1,
			"skip_transfer": 0,
			"transfer_material_against": "Work Order",
		},
		pluck="name",
	)

	for work_order in work_orders:
		update_material_transferred_for_work_order(work_order)
