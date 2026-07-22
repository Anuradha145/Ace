import frappe
from frappe.utils import flt


def update_work_order_transfer_from_stock_entry(doc, method=None):
	if doc.get("purpose") != "Material Transfer for Manufacture" or not doc.get("work_order"):
		return

	update_material_transferred_for_work_order(doc.work_order)


def update_material_transferred_for_work_order(work_order):
	if not work_order:
		return

	recalculate_required_item_transferred_qty(work_order)

	wo = frappe.db.get_value(
		"Work Order",
		work_order,
		["name", "docstatus", "qty", "skip_transfer"],
		as_dict=True,
	)
	if not wo or wo.docstatus != 1 or wo.skip_transfer:
		return

	rows = frappe.get_all(
		"Work Order Item",
		filters={
			"parent": work_order,
			"include_item_in_manufacturing": 1,
			"is_additional_item": 0,
		},
		fields=["required_qty", "transferred_qty"],
	)
	if not rows:
		return

	transfer_ratios = []
	for row in rows:
		required_qty = flt(row.required_qty)
		if required_qty <= 0:
			continue

		transfer_ratios.append(flt(row.transferred_qty) / required_qty)

	if not transfer_ratios:
		return

	material_transferred_for_manufacturing = flt(wo.qty) * min(min(transfer_ratios), 1)

	frappe.db.set_value(
		"Work Order",
		work_order,
		"material_transferred_for_manufacturing",
		material_transferred_for_manufacturing,
		update_modified=False,
	)
	sync_job_card_transferred_qty(work_order, material_transferred_for_manufacturing)


def recalculate_required_item_transferred_qty(work_order):
	wo = frappe.get_doc("Work Order", work_order)
	wo.update_transferred_qty_for_required_items()


def sync_job_card_transferred_qty(work_order, transferred_qty):
	if frappe.db.get_value("Work Order", work_order, "transfer_material_against") != "Work Order":
		return

	job_cards = frappe.get_all(
		"Job Card",
		filters={
			"work_order": work_order,
			"docstatus": ("<", 2),
			"skip_material_transfer": 0,
		},
		fields=["name", "for_quantity", "process_loss_qty"],
	)

	for job_card in job_cards:
		job_card_transferred_qty = min(
			flt(job_card.for_quantity) + flt(job_card.process_loss_qty),
			flt(transferred_qty),
		)
		frappe.db.set_value(
			"Job Card",
			job_card.name,
			"transferred_qty",
			job_card_transferred_qty,
			update_modified=False,
		)
