from ace.patches.v16_0.configure_default_stock_bins import execute as configure_default_stock_bins
from ace.patches.v16_0.configure_pick_list_bin_allocation import execute as configure_pick_list
from ace.utils.dimension_stock_correction import execute_dimension_stock_correction
from ace.utils.warehouse_dimension_backfill import execute_warehouse_dimension_backfill


def execute():
	"""Idempotently reconcile sites whose restored Patch Log skipped the original setup."""
	configure_default_stock_bins()
	configure_pick_list()
	execute_dimension_stock_correction()
	execute_warehouse_dimension_backfill()
