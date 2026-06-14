"""The stats math behind every emergence score. These are the functions that,
if they silently drift, corrupt the whole report without erroring."""
import unittest

from compute_stats import (NOVELTY_PICKRATE, composite_score, pick_velocity,
                           wilson_lcb)


class CompositeScore(unittest.TestCase):
    def test_components_add(self):
        # WR edge only: (0.55-0.50)*100 = 5
        self.assertAlmostEqual(composite_score(0.55, 0.50, 0.0, 0), 5.0)

    def test_velocity_floored_at_zero(self):
        # negative velocity must not subtract from the score
        a = composite_score(0.50, 0.50, -0.01, 0)
        b = composite_score(0.50, 0.50, 0.0, 0)
        self.assertEqual(a, b)
        self.assertEqual(a, 0.0)

    def test_monotonic_in_each_term(self):
        base = composite_score(0.52, 0.50, 0.01, 2)
        self.assertGreater(composite_score(0.53, 0.50, 0.01, 2), base)  # WR up
        self.assertGreater(composite_score(0.52, 0.50, 0.02, 2), base)  # vel up
        self.assertGreater(composite_score(0.52, 0.50, 0.01, 5), base)  # pro up

    def test_weights_are_injectable(self):
        self.assertAlmostEqual(composite_score(0.55, 0.50, 0.0, 0, wr_w=200),
                               10.0)


class WilsonLCB(unittest.TestCase):
    def test_below_point_estimate(self):
        self.assertLess(wilson_lcb(55, 100), 0.55)

    def test_tighter_with_more_games(self):
        # same win rate, more games -> LCB closer to the point estimate
        self.assertLess(wilson_lcb(55, 100), wilson_lcb(550, 1000))

    def test_bounds_and_zero_games(self):
        self.assertEqual(wilson_lcb(0, 0), 0.0)
        self.assertGreaterEqual(wilson_lcb(1, 1000), 0.0)
        self.assertLessEqual(wilson_lcb(999, 1000), 1.0)


class PickVelocity(unittest.TestCase):
    def test_needs_three_days(self):
        self.assertEqual(pick_velocity([0, 1], [0.0, 0.9]), 0.0)

    def test_positive_slope_for_rising(self):
        self.assertGreater(pick_velocity([0, 1, 2, 3], [0.1, 0.2, 0.3, 0.4]), 0)

    def test_negative_slope_for_falling(self):
        self.assertLess(pick_velocity([0, 1, 2], [0.3, 0.2, 0.1]), 0)

    def test_flat_is_zero(self):
        self.assertAlmostEqual(pick_velocity([0, 1, 2], [0.2, 0.2, 0.2]), 0.0)


class NoveltyThreshold(unittest.TestCase):
    def test_threshold_is_a_small_share(self):
        # guards against an accidental fat-fingered constant (e.g. 0.5)
        self.assertGreater(NOVELTY_PICKRATE, 0)
        self.assertLess(NOVELTY_PICKRATE, 0.05)


if __name__ == "__main__":
    unittest.main()
