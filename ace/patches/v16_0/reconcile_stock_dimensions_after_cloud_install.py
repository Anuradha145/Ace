import frappe

from ace.patches.v16_0.configure_default_stock_bins import execute as configure_default_stock_bins
from ace.patches.v16_0.configure_pick_list_bin_allocation import execute as configure_pick_list
from ace.utils.dimension_stock_correction import execute_dimension_stock_correction
from ace.utils.warehouse_dimension_backfill import execute_warehouse_dimension_backfill


def execute():
	"""Idempotently reconcile sites whose restored Patch Log skipped the original setup."""
	logger = frappe.logger("ace")
	logger.info("Starting negative stock balance correction patch")

	try:
		results = {
			"default_bins": configure_default_stock_bins(),
			"pick_list": configure_pick_list(),
			"negative_dimensions": execute_dimension_stock_correction(),
			"wip_fg_dimensions": execute_warehouse_dimension_backfill(),
		}
		logger.info("Negative stock balance correction patch completed successfully: %s", results)
		return results
	except Exception:
		logger.exception("Negative stock balance correction patch failed")
		raise
