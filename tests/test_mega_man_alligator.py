"""Regression checks for MEGA MAN's Alligator market-state calculations."""
import unittest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mega_man_backend"))
from engine import analyze, detect_fractal


def candles(mode, count=100):
    rows=[]
    price=1.1000
    for i in range(count):
        if mode=="flat":
            opening=close=price
            high=price+0.00005
            low=price-0.00005
        else:
            step=0.00001 + (i*i)*0.00000002
            opening=price
            close=price+step
            high=max(opening,close)+0.00003
            low=min(opening,close)-0.00003
            price=close
        rows.append({"time":1800000000+i*5,"open":opening,"high":high,"low":low,"close":close})
    return rows


class AlligatorTests(unittest.TestCase):
    def test_period_three_fractal_uses_three_closed_bars_each_side(self):
        upper, lower = detect_fractal([1, 2, 3, 9, 4, 3, 2], [9, 8, 7, 1, 6, 7, 8], period=3)
        self.assertTrue(upper)
        self.assertTrue(lower)

    def test_user_arrow_direction_mapping_is_explicit(self):
        # Upper/UP arrow maps to SELL (-1); lower/DOWN arrow maps to BUY (+1).
        upper, lower = detect_fractal([1, 2, 3, 9, 4, 3, 2], [9, 8, 7, 1, 6, 7, 8], period=3)
        self.assertEqual(-1 if upper else 1 if lower else 0, -1)

    def test_flat_market_is_identified_as_tight(self):
        result=analyze(candles("flat"), timeframe_seconds=5)
        self.assertTrue(result["ready"])
        comp=result["components"]
        self.assertTrue(comp["alligator_tight"])
        self.assertGreaterEqual(comp["alligator_gap_atr"],0)
        self.assertIn("alligator_expansion_rate",comp)

    def test_expansion_metrics_are_reported_and_finite(self):
        result=analyze(candles("rising"), timeframe_seconds=5)
        self.assertTrue(result["ready"])
        comp=result["components"]
        self.assertGreaterEqual(comp["alligator_gap_atr"],0)
        self.assertTrue(abs(comp["alligator_expansion_rate"])<1e6)
        self.assertIn(comp["alligator_direction"],("BUY","SELL","MIXED"))
        self.assertIn("alligator_score_adjustment",comp)
        self.assertEqual(comp["fractal_period"],3)

    def test_alligator_state_is_in_pattern_explanation(self):
        result=analyze(candles("rising"), timeframe_seconds=15)
        pattern=next(stage for stage in result["stages"] if stage["name"]=="Pattern Matching")
        self.assertIn("Alligator",pattern["summary"])

if __name__=="__main__":
    unittest.main()
