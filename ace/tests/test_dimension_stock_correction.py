from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ace.utils.dimension_stock_correction import (
	BIN_FIELD,
	PROJECT_FIELD,
	TO_BIN_FIELD,
	TO_PROJECT_FIELD,
	create_stock_entry,
	remove_noop_document_rows,
	submit_plan,
	validate_transfer_plan,
)


class FakeRow(dict):
	def __getattr__(self, key):
		return self.get(key)

	def set(self, key, value):
		self[key] = value


class FakeStockEntry:
	def __init__(self, mutate_dimensions_on_insert=False):
		self.items = []
		self.flags = SimpleNamespace()
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
