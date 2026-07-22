import frappe

from ace.stock.stock_entry import (
	DEFAULT_BIN_FIELDS,
	ensure_default_bin_custom_fields,
)


def execute():
	ensure_default_bin_custom_fields()

	conditions = " or ".join(fieldname + " = 1" for fieldname in DEFAULT_BIN_FIELDS)
	frappe.db.sql(
		"""
		update `tabBin Location`
		set common_bin = 0
		where {conditions}
		""".format(conditions=conditions)
	)
