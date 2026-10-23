"""Regression guards for GODMODE's signal-only behavior. Run: python -m unittest discover -s tests -v."""
import unittest

from godmode_strategy import GodModeStrategy
from godmode_enhancements import analyze
from hybrid_engine import CombinedEngine


def bars(count=40):
    result = []
    price = 1.1
    for i in range(count):
        opening = price
        close = opening + (0.0002 if i % 3 else -0.0001)
        result.append({
            "time": 1_800_000_000 + i * 5,
            "open": opening,
            "high": max(opening, close) + 0.0001,
            "low": min(opening, close) - 0.0001,
            "close": close,
        })
        price = close
    return result


class FeedSafetyTests(unittest.TestCase):
    def test_godmode_never_signals_without_verified_live_feed(self):
        result = GodModeStrategy().update(bars(), "EURUSD_otc", 5, False)
        self.assertIsNone(result["signal"])
        self.assertIn("Live feed unavailable", result["message"])

    def test_combined_engine_waits_without_live_feed(self):
        result = CombinedEngine().evaluate(bars(), "EURUSD_otc", 5, False, "BUY", 99)
        self.assertEqual(result["signal"], "WAIT")
        self.assertFalse(result["alert_eligible"])
        self.assertIn("Live feed unavailable", result["reason"])

    def test_strategy_warms_up_below_minimum_history(self):
        result = GodModeStrategy().update(bars(10), "EURUSD_otc", 5, True)
        self.assertIsNone(result["signal"])
        self.assertIn("Warming up", result["message"])

    def test_enhancements_do_not_claim_ready_with_short_history(self):
        result = analyze(bars(20))
        self.assertFalse(result["ready"])
        self.assertEqual(result["vote"], 0)


if __name__ == "__main__":
    unittest.main()
