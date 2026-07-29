import frappe


def execute():
	logger = frappe.logger("ace")
	logger.warning("ACE STOCK RECONCILIATION V3 PATCH STARTED")

	from ace.patches.v16_0.reconcile_stock_dimensions_after_cloud_install import (
		execute as run_reconciliation,
	)

	result = run_reconciliation()

	logger.warning(
		"ACE STOCK RECONCILIATION V3 PATCH COMPLETED: %s",
		result,
	)
	return result
