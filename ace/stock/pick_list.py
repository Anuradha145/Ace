import frappe
from frappe import _
from frappe.utils import flt


MIN_QTY = 0.000001


def set_project_from_work_order(doc, method=None):
	if not doc.get("work_order"):
		return

	project = frappe.db.get_value("Work Order", doc.work_order, "project")
	if not project:
		frappe.throw(_("Please set Project in Work Order {0} before creating Pick List.").format(doc.work_order))

	doc.custom_project = project


def allocate_work_order_stock_by_bin(doc, method=None):
	"""Allocate each outstanding Work Order item from its project-specific bin stock."""
	if not doc.get("work_order") or doc.get("purpose") != "Material Transfer for Manufacture":
		return

	set_project_from_work_order(doc)
	work_order = frappe.get_doc("Work Order", doc.work_order)
	requirements = get_outstanding_requirements(work_order)
	if not requirements:
		doc.set("locations", [])
		return

	templates = get_location_templates(doc)
	doc.set("locations", [])
	shortages = []

	for requirement in requirements:
		remaining = requirement.qty
		allocated = 0.0
		template = templates.get((requirement.item_code, requirement.warehouse)) or get_item_template(
			requirement.item_code
		)
		conversion_factor = flt(template.get("conversion_factor")) or 1.0

		for stock in get_bin_stock(
			requirement.item_code,
			requirement.warehouse,
			doc.custom_project,
		):
			if remaining <= MIN_QTY:
				break

			stock_qty = min(remaining, flt(stock.qty))
			if stock_qty <= MIN_QTY:
				continue

			doc.append(
				"locations",
				{
					**template,
					"warehouse": requirement.warehouse,
					"qty": stock_qty / conversion_factor,
					"stock_qty": stock_qty,
					"custom_source_bin_location": stock.bin_location,
					"custom_project": doc.custom_project,
				},
			)
			allocated += stock_qty
			remaining -= stock_qty

		if remaining > MIN_QTY:
			shortages.append(
				_('{0}: required {1}, allocated {2}, shortage {3}').format(
					requirement.item_code,
					frappe.format_value(requirement.qty, {"fieldtype": "Float"}),
					frappe.format_value(allocated, {"fieldtype": "Float"}),
					frappe.format_value(remaining, {"fieldtype": "Float"}),
				)
			)

	if shortages:
		frappe.msgprint(
			_("Only available stock for Project {0} was allocated:<br>{1}").format(
				doc.custom_project, "<br>".join(shortages)
			),
			title=_("Partial Stock Allocated"),
			indicator="orange",
		)


def get_outstanding_requirements(work_order):
	default_warehouse = work_order.source_warehouse or "Stores - ACE"
	quantities = {}
	order = []

	for row in work_order.required_items:
		if not row.item_code or not row.get("include_item_in_manufacturing"):
			continue

		qty = flt(row.required_qty) - flt(row.transferred_qty)
		if qty <= MIN_QTY:
			continue

		key = (row.item_code, row.source_warehouse or default_warehouse)
		if key not in quantities:
			quantities[key] = 0.0
			order.append(key)
		quantities[key] += qty

	return [
		frappe._dict(item_code=item_code, warehouse=warehouse, qty=quantities[(item_code, warehouse)])
		for item_code, warehouse in order
	]


def get_location_templates(doc):
	templates = {}
	for row in doc.get("locations") or []:
		if not row.item_code or not row.warehouse:
			continue
		templates.setdefault((row.item_code, row.warehouse), location_template_from_row(row))
	return templates


def location_template_from_row(row):
	return {
		"item_code": row.item_code,
		"item_name": row.item_name,
		"description": row.description,
		"item_group": row.item_group,
		"uom": row.uom or row.stock_uom,
		"stock_uom": row.stock_uom or row.uom,
		"conversion_factor": flt(row.conversion_factor) or 1.0,
	}


def get_item_template(item_code):
	item = frappe.db.get_value(
		"Item",
		item_code,
		["item_code", "item_name", "description", "item_group", "stock_uom"],
		as_dict=True,
	)
	if not item:
		frappe.throw(_("Item {0} does not exist.").format(item_code))

	return {
		"item_code": item.item_code,
		"item_name": item.item_name,
		"description": item.description,
		"item_group": item.item_group,
		"uom": item.stock_uom,
		"stock_uom": item.stock_uom,
		"conversion_factor": 1.0,
	}


def get_bin_stock(item_code, warehouse, project):
	return frappe.db.sql(
		"""
		select bin_location, sum(actual_qty) as qty
		from `tabStock Ledger Entry`
		where is_cancelled = 0
			and item_code = %(item_code)s
			and warehouse = %(warehouse)s
			and project_aa = %(project)s
			and coalesce(bin_location, '') != ''
		group by bin_location
		having sum(actual_qty) > %(min_qty)s
		order by sum(actual_qty) desc, bin_location
		""",
		{
			"item_code": item_code,
			"warehouse": warehouse,
			"project": project,
			"min_qty": MIN_QTY,
		},
		as_dict=True,
	)
