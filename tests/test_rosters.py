"""SoloqueueIds wikitext parsing — the messiest input in the pipeline. Region
markers, default-tag recovery for legacy names, comma lists, and junk all have
to be handled, since bad output here means wasted Account-V1 calls or, worse,
wrong accounts attributed to a pro."""
import unittest

from fetch_pro_rosters import parse_soloqueue_ids


class ParseSoloqueueIds(unittest.TestCase):
    def test_region_markers_route_platforms(self):
        text = ("'''KR:''' Hide on bush#KR1<br>'''EUW:''' name#1234<br>"
                "'''NA:''' other#5678")
        out = parse_soloqueue_ids(text, "kr")
        self.assertIn(("kr", "Hide on bush#KR1"), out)
        self.assertIn(("euw1", "name#1234"), out)
        self.assertIn(("na1", "other#5678"), out)

    def test_untagged_default_tag_recovery(self):
        # legacy KR name with no tagline -> gets the default #KR1
        out = parse_soloqueue_ids("'''KR:''' Goldtec", "kr")
        self.assertEqual(out, [("kr", "Goldtec#KR1")])

    def test_unsupported_regions_dropped(self):
        # CN/BR/VN have no public API and must not leak through
        text = "'''CN:''' dakjshdkj<br>'''BR:''' 19960507<br>'''VN:''' mid24"
        self.assertEqual(parse_soloqueue_ids(text, "kr"), [])

    def test_comma_list_splits(self):
        out = parse_soloqueue_ids("'''KR:''' Aiming, Irene", "kr")
        self.assertIn(("kr", "Aiming#KR1"), out)
        self.assertIn(("kr", "Irene#KR1"), out)

    def test_junk_with_parens_rejected(self):
        # "I have script (NA)" style notes are not valid riot ids
        self.assertEqual(parse_soloqueue_ids("I have script (NA)", "kr"), [])

    def test_spaces_around_hash_normalized(self):
        out = parse_soloqueue_ids("'''KR:''' Peyz #KR11", "kr")
        self.assertEqual(out, [("kr", "Peyz#KR11")])

    def test_no_duplicates(self):
        out = parse_soloqueue_ids("'''KR:''' dup#KR1, dup#KR1", "kr")
        self.assertEqual(out.count(("kr", "dup#KR1")), 1)


if __name__ == "__main__":
    unittest.main()
