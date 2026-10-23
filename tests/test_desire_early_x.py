import unittest

from hybrid_engine import CombinedEngine


def make_candles(count=80):
    candles = []
    price = 1.1000
    for i in range(count):
        # Alternating candle bodies with a gentle drift keep the input deterministic.
        drift = 0.00012 if (i // 8) % 2 == 0 else -0.00008
        open_price = price
        close = price + drift
        high = max(open_price, close) + 0.00015
        low = min(open_price, close) - 0.00015
        candles.append({
            "time": 1_800_000_000 + i * 60,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
        })
        price = close
    return candles


class DesireEarlyXRadarTests(unittest.TestCase):
    def test_radar_exposes_gap_velocity_and_convergence_state(self):
        result = CombinedEngine().evaluate(
            make_candles(), "TEST", 60, True, "WAIT", 0
        )
        self.assertIn("di_gap_velocity", result)
        self.assertIn("di_gap_acceleration", result)
        self.assertIn("di_lines_converging", result)
        self.assertIn("di_lines_diverging", result)
        self.assertEqual(
            result["desire_analysis_version"], "early-x-v2-convergence-radar"
        )

    def test_radar_does_not_claim_a_calibrated_win_probability(self):
        result = CombinedEngine().evaluate(
            make_candles(), "TEST", 60, True, "WAIT", 0
        )
        self.assertNotIn("win_probability", result)
        self.assertNotIn("probability_of_profit", result)


if __name__ == "__main__":
    unittest.main()
