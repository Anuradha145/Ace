import frappe

from ace.stock.stock_entry import (
	ensure_default_bin_custom_fields,
)


def execute():
	ensure_default_bin_custom_fields()

	frappe.db.sql(
		"""
		update `tabBin Location`
		set common_bin = 0
		where custom_is_default_wip_bin = 1
			or custom_is_default_fg_bin = 1
		"""
	)
