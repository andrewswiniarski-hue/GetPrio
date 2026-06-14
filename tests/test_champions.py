"""champ_key must collapse Riot internal ids and esports display names to the
same canonical key — this join is load-bearing for the whole ground-truth
pipeline, so a silent drift here would corrupt the backtest and dashboard."""
import unittest

from champions import champ_key


class ChampKey(unittest.TestCase):
    def test_display_vs_riot_id_collapse(self):
        pairs = [
            ("Wukong", "MonkeyKing"),
            ("Kai'Sa", "Kaisa"),
            ("Rek'Sai", "RekSai"),
            ("Nunu & Willump", "Nunu"),
            ("Renata Glasc", "Renata"),
            ("Lee Sin", "LeeSin"),
            ("Dr. Mundo", "DrMundo"),
            ("Cho'Gath", "Chogath"),
        ]
        for display, riot in pairs:
            self.assertEqual(champ_key(display), champ_key(riot),
                             f"{display} should match {riot}")

    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(champ_key("Twisted Fate"), champ_key("TwistedFate"))
        self.assertEqual(champ_key("twistedfate"), champ_key("TWISTEDFATE"))

    def test_distinct_champs_stay_distinct(self):
        self.assertNotEqual(champ_key("Renata Glasc"), champ_key("Renekton"))
        self.assertNotEqual(champ_key("Sylas"), champ_key("Syndra"))

    def test_empty_and_none(self):
        self.assertEqual(champ_key(""), "")
        self.assertEqual(champ_key(None), "")


if __name__ == "__main__":
    unittest.main()
