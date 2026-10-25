import unittest

import detector


class DriftScoreTests(unittest.TestCase):
    def test_calm_session_is_intentional(self):
        s = {"duration_s": 120, "shorts_per_min": 0, "scroll_px_per_min": 0, "surfaces": ["search"]}
        result = detector.drift_score(s)
        self.assertLess(result["score"], 25)
        self.assertEqual(result["label"], "intentional")

    def test_endless_feed_has_explainable_score(self):
        s = {"duration_s": 1800, "shorts_per_min": 0, "scroll_px_per_min": 5000, "surfaces": ["feed"]}
        result = detector.drift_score(s)
        self.assertGreaterEqual(result["score"], 50)
        self.assertIn("continuous feed scrolling", result["reasons"])

    def test_goal_mismatch_is_surface_only(self):
        s = {"surfaces": ["shorts"]}
        result = detector.intent_mismatch({"mainGoal": "finish my exam project"}, s)
        self.assertTrue(result["mismatch"])
        self.assertIn("finish my exam project", result["reason"])

    def test_no_goal_does_not_assume_mismatch(self):
        result = detector.intent_mismatch({}, {"surfaces": ["feed"]})
        self.assertFalse(result["mismatch"])

    def test_adaptive_learning_prefers_short_rescue_after_success(self):
        result = detector.adaptive_intervention(["rescue_completed", "rescue_completed", "snoozed"])
        self.assertEqual(result["action"], "rescue")
        self.assertEqual(result["minutes"], 5)

    def test_adaptive_learning_starts_with_default(self):
        result = detector.adaptive_intervention(["dismissed", "stayed"])
        self.assertEqual(result["minutes"], 10)


if __name__ == "__main__":
    unittest.main()
