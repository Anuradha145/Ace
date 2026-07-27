import frappe

JOB_CARD_DIMENSION_FIELDS = (
	"project_aa",
	"bin_location",
)


def execute():
	for fieldname in JOB_CARD_DIMENSION_FIELDS:
		field = frappe.db.exists("Custom Field", {"dt": "Job Card", "fieldname": fieldname})
		if not field:
			continue

		frappe.db.set_value(
			"Custom Field",
			field,
			{
				"reqd": 0,
				"mandatory_depends_on": "",
			},
			update_modified=False,
		)

	frappe.clear_cache(doctype="Job Card")
