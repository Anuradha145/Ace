import frappe


def after_migrate():
	logger = frappe.logger("ace")
	patch = "ace.patches.v16_0.reconcile_stock_dimensions_v3"

	logger.warning(
		"ACE AFTER MIGRATE: app=%s patch_logged=%s app_path=%s",
		"ace",
		frappe.db.exists("Patch Log", patch),
		frappe.get_app_path("ace"),
	)
