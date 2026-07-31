from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ace.stock.stock_entry import (
	get_parent_project,
	set_default_bin_location,
	set_project_dimensions_from_parent,
)
from ace.utils.dimension_stock_correction import (
	BIN_FIELD,
	PROJECT_FIELD,
	TO_BIN_FIELD,
	TO_PROJECT_FIELD,
	create_stock_entry,
	get_plan_patch_marker,
	remove_noop_document_rows,
	submit_plan,
	validate_transfer_plan,
)


class FakeRow(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value

	def set(self, key, value):
		self[key] = value


class FakeFlags(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


class FakeStockEntry:
	def __init__(self, mutate_dimensions_on_insert=False):
		self.items = []
		self.flags = FakeFlags()
		self.name = "MAT-STE-TEST"
		self.mutate_dimensions_on_insert = mutate_dimensions_on_insert

	def append(self, _fieldname, values):
		row = FakeRow(values)
		row.idx = len(self.items) + 1
		row.name = f"MAT-STE-TEST-{row.idx}"
		self.items.append(row)
		return row

	def insert(self):
		if self.mutate_dimensions_on_insert:
			for row in self.items:
				row.set(TO_PROJECT_FIELD, row.get(PROJECT_FIELD))
				row.set(TO_BIN_FIELD, row.get(BIN_FIELD))
		return self

	def reload(self):
		return self

	def remove(self, row):
		self.items.remove(row)


class TestDimensionStockCorrection(TestCase):
	def test_parent_project_has_priority_over_pick_list_and_work_order(self):
		doc = FakeRow(
			project="PROJ-PARENT",
			pick_list="STO-PICK-1",
			work_order="MFG-WO-1",
		)

		with patch("ace.stock.stock_entry.frappe.db.get_value") as get_value:
			project = get_parent_project(doc)

		self.assertEqual(project, "PROJ-PARENT")
		get_value.assert_not_called()

	@patch("ace.stock.stock_entry.frappe.db.get_value")
	def test_pick_list_project_is_fallback_before_work_order(self, get_value):
		get_value.return_value = "PROJ-PICK"
		doc = FakeRow(pick_list="STO-PICK-1", work_order="MFG-WO-1")

		project = get_parent_project(doc)

		self.assertEqual(project, "PROJ-PICK")
		get_value.assert_called_once_with("Pick List", "STO-PICK-1", "custom_project")

	@patch("ace.stock.stock_entry.get_parent_project", return_value="PROJ-PICK")
	def test_pick_list_fallback_populates_parent_and_row_dimensions(self, _get_parent_project):
		row = FakeRow(s_warehouse="Stores - ACE", t_warehouse="Work In Progress - ACE")
		doc = FakeRow(project=None, items=[row])

		set_project_dimensions_from_parent(doc)

		self.assertEqual(doc.project, "PROJ-PICK")
		self.assertEqual(row.project, "PROJ-PICK")
		self.assertEqual(row.project_aa, "PROJ-PICK")
		self.assertEqual(row.to_project_aa, "PROJ-PICK")

	def test_validate_transfer_plan_keeps_dimension_changes_and_skips_noop(self):
		plan = [
			make_plan("PROJ-1", "BIN-1", "PROJ-2", "BIN-2"),
			make_plan("PROJ-1", "BIN-1", "PROJ-1", "BIN-2"),
			make_plan("", "BIN-1", "PROJ-2", "BIN-1"),
			make_plan("PROJ-1", "BIN-1", "", "BIN-1"),
			make_plan("PROJ-1", "BIN-1", "PROJ-1", "BIN-1"),
		]

		valid, skipped = validate_transfer_plan(plan)

		self.assertEqual(len(valid), 4)
		self.assertTrue(all(row["warehouse"] == "Stores - ACE" for row in valid))
		self.assertEqual(len(skipped), 1)
		self.assertEqual(skipped[0]["plan_row"], 5)

	@patch("ace.utils.dimension_stock_correction.frappe.db.set_value")
	@patch("ace.utils.dimension_stock_correction.frappe.new_doc")
	def test_create_stock_entry_restores_dimensions_changed_during_insert(self, new_doc, set_value):
		stock_entry = FakeStockEntry(mutate_dimensions_on_insert=True)
		new_doc.return_value = stock_entry
		plan = [make_plan("", "BIN-1", "PROJ-2", "BIN-2")]
		item_details = {
			"ITEM-1": SimpleNamespace(stock_uom="Nos"),
		}

		created = create_stock_entry(plan, item_details, [])

		row = created.items[0]
		self.assertIsNone(row.get(PROJECT_FIELD))
		self.assertEqual(row.get(BIN_FIELD), "BIN-1")
		self.assertEqual(row.get(TO_PROJECT_FIELD), "PROJ-2")
		self.assertEqual(row.get(TO_BIN_FIELD), "BIN-2")
		set_value.assert_called_once()

	def test_remove_noop_document_rows(self):
		doc = FakeStockEntry()
		noop = doc.append(
			"items",
			make_document_row("PROJ-1", "BIN-1", "PROJ-1", "BIN-1"),
		)
		actionable = doc.append(
			"items",
			make_document_row("PROJ-1", "BIN-1", "PROJ-2", "BIN-1"),
		)

		removed = remove_noop_document_rows(doc)

		self.assertEqual(len(removed), 1)
		self.assertNotIn(noop, doc.items)
		self.assertIn(actionable, doc.items)

	def test_plan_marker_changes_with_remaining_plan(self):
		first = get_plan_patch_marker("marker", [make_plan("PROJ-1", "BIN-1", "PROJ-2", "BIN-2")])
		second = get_plan_patch_marker("marker", [make_plan("PROJ-1", "BIN-1", "PROJ-3", "BIN-2")])

		self.assertNotEqual(first, second)
		self.assertEqual(
			first, get_plan_patch_marker("marker", [make_plan("PROJ-1", "BIN-1", "PROJ-2", "BIN-2")])
		)

	@patch("ace.stock.stock_entry.set_project_dimensions_from_parent")
	@patch("ace.stock.stock_entry.get_default_bin_locations")
	def test_correction_flag_skips_stock_entry_dimension_defaults(self, get_defaults, set_projects):
		doc = FakeStockEntry()
		doc.flags.ace_preserve_inventory_dimensions = True
		doc.append("items", make_document_row("PROJ-1", "BIN-1", "PROJ-2", "BIN-2"))

		set_default_bin_location(doc)

		set_projects.assert_not_called()
		get_defaults.assert_not_called()

	@patch("ace.utils.dimension_stock_correction.create_stock_entry")
	@patch("ace.utils.dimension_stock_correction.submit_existing_or_report")
	@patch("ace.utils.dimension_stock_correction.get_existing_correction_entry")
	def test_rerun_reuses_existing_correction_entry(self, get_existing, submit_existing, create_entry):
		get_existing.return_value = "MAT-STE-EXISTING"
		submit_existing.return_value = {
			"status": "already_submitted",
			"stock_entry": "MAT-STE-EXISTING",
		}
		plan = [make_plan("PROJ-1", "BIN-1", "PROJ-2", "BIN-2")]

		first = submit_plan(plan, {}, [], "marker")
		second = submit_plan(plan, {}, [], "marker")

		self.assertEqual(first["stock_entry"], "MAT-STE-EXISTING")
		self.assertEqual(second["stock_entry"], "MAT-STE-EXISTING")
		self.assertEqual(submit_existing.call_count, 2)
		create_entry.assert_not_called()


def make_plan(from_project, from_bin, to_project, to_bin):
	return {
		"item_code": "ITEM-1",
		"warehouse": "Stores - ACE",
		"from_project": from_project,
		"from_bin": from_bin,
		"to_project": to_project,
		"to_bin": to_bin,
		"batch_no": "",
		"qty": 1,
	}


def make_document_row(from_project, from_bin, to_project, to_bin):
	return {
		"item_code": "ITEM-1",
		"s_warehouse": "Stores - ACE",
		"t_warehouse": "Stores - ACE",
		"qty": 1,
		"transfer_qty": 1,
		PROJECT_FIELD: from_project,
		BIN_FIELD: from_bin,
		TO_PROJECT_FIELD: to_project,
		TO_BIN_FIELD: to_bin,
	}
