"""Regression guards for GODMODE's signal-only behavior. Run: python -m unittest discover -s tests -v."""
import unittest

from godmode_strategy import GodModeStrategy
from godmode_enhancements import analyze
from hybrid_engine import CombinedEngine
from market_feed import normalize_historical_candles, ticks_to_candles


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

    def test_historical_candles_are_sorted_deduplicated_and_malformed_rows_rejected(self):
        raw = [
            {"time": 15, "open": 1.2, "high": 1.3, "low": 1.1, "close": 1.25},
            {"time": 5, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05},
            {"time": 5, "open": 1.01, "high": 1.12, "low": 0.95, "close": 1.08},
            {"time": 20, "open": 1.2, "high": 1.0, "low": 0.8, "close": 1.1},
            {"time": 25, "open": "bad", "high": 1.3, "low": 1.0, "close": 1.2},
        ]
        result = normalize_historical_candles(raw)
        self.assertEqual([bar["time"] for bar in result], [5, 15])
        self.assertEqual(result[0]["close"], 1.08)

    def test_real_ticks_aggregate_without_filling_missing_buckets(self):
        result = ticks_to_candles([(101, 1.1), (103, 1.3), (111, 1.2)], 5)
        self.assertEqual([bar["time"] for bar in result], [100, 110])
        self.assertEqual(result[0]["open"], 1.1)
        self.assertEqual(result[0]["high"], 1.3)
        self.assertEqual(result[0]["close"], 1.3)

    def test_tick_milliseconds_are_normalized_to_seconds(self):
        result = ticks_to_candles([(1_800_000_000_000, 1.1), (1_800_000_000_100, 1.2)], 5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["time"], 1_800_000_000)


if __name__ == "__main__":
    unittest.main()
