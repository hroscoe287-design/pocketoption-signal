"""Regression checks for MEGA MAN's Alligator market-state calculations."""
import unittest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mega_man_backend"))
from engine import (analyze, detect_fractal, smma, shifted_smma,
                    fractal_signal_direction, alligator_score_adjustment)


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

    def test_classic_williams_alligator_smma_and_forward_shifts(self):
        values = [float(x) for x in range(1, 31)]
        jaw = shifted_smma(values, 13, 8)
        teeth = shifted_smma(values, 8, 5)
        lips = shifted_smma(values, 5, 3)
        self.assertIsNone(jaw[7])
        self.assertAlmostEqual(jaw[8], sum(values[:13]) / 13)
        self.assertAlmostEqual(teeth[5], sum(values[:8]) / 8)
        self.assertAlmostEqual(lips[3], sum(values[:5]) / 5)
        expected = ((sum(values[:5]) / 5) * 4 + values[5]) / 5
        self.assertAlmostEqual(smma(values, 5)[5], expected)

    def test_user_fractal_arrow_mapping(self):
        self.assertEqual(fractal_signal_direction(upper=True, lower=False), -1)
        self.assertEqual(fractal_signal_direction(upper=False, lower=True), 1)
        self.assertEqual(fractal_signal_direction(upper=False, lower=False), 0)
        self.assertEqual(fractal_signal_direction(upper=True, lower=True), 0)

    def test_alligator_only_modifies_score(self):
        self.assertEqual(alligator_score_adjustment(1, 1, False, 0.9, True, False), 10)
        self.assertEqual(alligator_score_adjustment(-1, -1, False, 0.5, False, False), 6)
        self.assertEqual(alligator_score_adjustment(1, -1, False, 0.5, False, False), -4)
        self.assertEqual(alligator_score_adjustment(1, 1, True, 1.2, True, False), -5)
        self.assertEqual(alligator_score_adjustment(0, 1, False, 1.2, True, False), 0)

    def test_analyze_reports_canonical_alligator_parameters(self):
        result = analyze(candles("rising"), timeframe_seconds=15)
        comp = result["components"]
        self.assertEqual((comp["alligator_jaw_period"], comp["alligator_jaw_shift"]), (13, 8))
        self.assertEqual((comp["alligator_teeth_period"], comp["alligator_teeth_shift"]), (8, 5))
        self.assertEqual((comp["alligator_lips_period"], comp["alligator_lips_shift"]), (5, 3))
        self.assertEqual(comp["alligator_method"], "SMMA median price (H+L)/2")

if __name__=="__main__":
    unittest.main()
