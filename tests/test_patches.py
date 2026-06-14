"""Patch handling. Two separate concerns that both silently corrupt joins if
they drift:
  - warehouse_patch: esports patch numbering (26.11) -> gameVersion (16.11)
  - patch_sort_key: numeric ordering so 16.9 < 16.11 (lexical sort is wrong)
"""
import unittest

from compute_stats import patch_sort_key
from fetch_pro_picks import warehouse_patch


class WarehousePatch(unittest.TestCase):
    def test_modern_majors_shift_down_by_10(self):
        self.assertEqual(warehouse_patch("26.11"), "16.11")
        self.assertEqual(warehouse_patch("25.1"), "15.1")
        self.assertEqual(warehouse_patch("26.1"), "16.1")

    def test_pre_2025_unchanged(self):
        self.assertEqual(warehouse_patch("14.10"), "14.10")
        self.assertEqual(warehouse_patch("13.24"), "13.24")

    def test_boundary_at_25(self):
        self.assertEqual(warehouse_patch("25.0"), "15.0")
        self.assertEqual(warehouse_patch("24.23"), "24.23")

    def test_unparseable_is_none(self):
        for bad in ("", "TBD", "preseason", "16", "v16.11", None):
            self.assertIsNone(warehouse_patch(bad), repr(bad))


class PatchSortKey(unittest.TestCase):
    def test_numeric_not_lexical(self):
        self.assertLess(patch_sort_key("16.9"), patch_sort_key("16.11"))
        self.assertLess(patch_sort_key("16.2"), patch_sort_key("16.10"))

    def test_major_dominates(self):
        self.assertLess(patch_sort_key("15.24"), patch_sort_key("16.1"))

    def test_sortable_sequence(self):
        patches = ["16.11", "16.2", "16.9", "16.10", "15.24"]
        self.assertEqual(sorted(patches, key=patch_sort_key),
                         ["15.24", "16.2", "16.9", "16.10", "16.11"])


if __name__ == "__main__":
    unittest.main()
