from ace.stock.stock_entry import (
	DEFAULT_FG_BIN_FIELD,
	DEFAULT_WIP_BIN_FIELD,
	ensure_default_bin_client_script,
	ensure_seed_default_bin,
)


def execute():
	ensure_seed_default_bin("ACE - WIP", "Work In Progress - ACE", DEFAULT_WIP_BIN_FIELD)
	ensure_seed_default_bin("ACE - FG", "Finished Goods - ACE", DEFAULT_FG_BIN_FIELD)
	ensure_default_bin_client_script()
