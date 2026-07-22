import frappe
from frappe import _


def set_project_from_work_order(doc, method=None):
	if not doc.get("work_order"):
		return

	project = frappe.db.get_value("Work Order", doc.work_order, "project")
	if not project:
		frappe.throw(_("Please set Project in Work Order {0} before creating Pick List.").format(doc.work_order))

	doc.custom_project = project
