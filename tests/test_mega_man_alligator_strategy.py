"""Regression tests for Mega Man's Alligator test strategy."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mega_man_backend"))
from engine import (
    analyze, detect_fractal, smma, shifted_smma,
    fractal_signal_direction, alligator_score_adjustment,
)


class AlligatorStrategyTests(unittest.TestCase):
    def test_smma_and_forward_shifts(self):
        values = [float(x) for x in range(1, 31)]
        jaw = shifted_smma(values, 13, 8)
        teeth = shifted_smma(values, 8, 5)
        lips = shifted_smma(values, 5, 3)
        self.assertIsNone(jaw[7])
        self.assertAlmostEqual(jaw[8], sum(values[:13]) / 13)
        self.assertAlmostEqual(teeth[5], sum(values[:8]) / 8)
        self.assertAlmostEqual(lips[3], sum(values[:5]) / 5)
        self.assertAlmostEqual(smma(values, 5)[5], (3.0 * 4 + 6.0) / 5)

    def test_fractal_period_three_confirmed_only_after_three_right_bars(self):
        highs = [2, 3, 4, 10, 5, 4, 3]
        lows = [8, 7, 6, 1, 5, 6, 7]
        self.assertEqual(detect_fractal(highs, lows, 3), (True, True))
        self.assertEqual(detect_fractal(highs[:-1], lows[:-1], 3), (False, False))

    def test_arrow_mapping_is_opposite_trade_direction(self):
        self.assertEqual(fractal_signal_direction(True, False), -1)  # UP arrow => SELL
        self.assertEqual(fractal_signal_direction(False, True), 1)   # DOWN arrow => BUY
        self.assertEqual(fractal_signal_direction(False, False), 0)
        self.assertEqual(fractal_signal_direction(True, True), 0)

    def test_alligator_only_changes_score(self):
        self.assertEqual(alligator_score_adjustment(1, 1, False, 0.9, True, False), 10)
        self.assertEqual(alligator_score_adjustment(-1, -1, False, 0.5, False, False), 6)
        self.assertEqual(alligator_score_adjustment(1, -1, False, 0.5, False, False), -4)
        self.assertEqual(alligator_score_adjustment(1, 1, True, 1.2, True, False), -5)
        self.assertEqual(alligator_score_adjustment(0, 1, False, 1.2, True, False), 0)

    def test_analysis_exposes_fractal_three_and_alligator_settings(self):
        candles = []
        price = 1.10
        for i in range(120):
            opening = price
            close = price + (0.00001 if i % 3 else 0.00002)
            candles.append({"time": 1800000000 + i * 5, "open": opening,
                            "high": max(opening, close) + 0.00003,
                            "low": min(opening, close) - 0.00003,
                            "close": close})
            price = close
        result = analyze(candles, timeframe_seconds=15)
        c = result["components"]
        self.assertEqual(c["fractal_period"], 3)
        self.assertEqual((c["alligator_jaw_period"], c["alligator_jaw_shift"]), (13, 8))
        self.assertEqual((c["alligator_teeth_period"], c["alligator_teeth_shift"]), (8, 5))
        self.assertEqual((c["alligator_lips_period"], c["alligator_lips_shift"]), (5, 3))
        self.assertEqual(c["alligator_method"], "SMMA median price (H+L)/2")


if __name__ == "__main__":
    unittest.main()
