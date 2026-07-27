import frappe


def execute():
	field = frappe.db.exists("Custom Field", {"dt": "Pick List", "fieldname": "custom_project"})
	if field:
		frappe.db.set_value(
			"Custom Field",
			field,
			{
				"reqd": 0,
				"mandatory_depends_on": "eval:doc.work_order",
				"fetch_from": "work_order.project",
			},
			update_modified=False,
		)

	frappe.db.sql(
		"""
		update `tabPick List` pl
		inner join `tabWork Order` wo on wo.name = pl.work_order
		set pl.custom_project = wo.project
		where ifnull(pl.work_order, '') != ''
		  and ifnull(wo.project, '') != ''
		  and ifnull(pl.custom_project, '') = ''
		"""
	)

	frappe.clear_cache(doctype="Pick List")
