import unittest
from hybrid_engine import CombinedEngine


def candles(count=120, mode="trend"):
    rows=[]
    price=1.08
    for i in range(count):
        if mode=="trend":
            close=price + (0.00008 if i%18<12 else -0.000025)
        else:
            close=price + (0.00003 if i%2 else -0.00003)
        open_price=price
        high=max(open_price,close)+0.00004
        low=min(open_price,close)-0.00004
        rows.append({"time":1700000000+i*15,"open":open_price,"high":high,"low":low,"close":close})
        price=close
    return rows


class DesireAnalysisTests(unittest.TestCase):
    def test_demo_data_never_emits_trade_signal(self):
        result=CombinedEngine().evaluate(candles(), "EURUSD_otc", 15, False, "BUY")
        self.assertEqual(result["signal"], "WAIT")
        self.assertEqual(result["desire_probability"], 0)

    def test_live_analysis_exposes_bounded_setup_grade_and_di_diagnostics(self):
        result=CombinedEngine().evaluate(candles(), "EURUSD_otc", 15, True, "WAIT")
        self.assertIn("desire_probability", result)
        self.assertGreaterEqual(result["desire_probability"], 0)
        self.assertLessEqual(result["desire_probability"], 100)
        self.assertIn("plus_di", result)
        self.assertIn("minus_di", result)
        self.assertIn("gap_velocity", result)
        self.assertIn("gap_acceleration", result)
        self.assertIn("early_x_status", result)
        self.assertIn("desire_probability_note", result)

    def test_probability_is_explicitly_not_calibrated_win_rate(self):
        result=CombinedEngine().evaluate(candles(), "EURUSD_otc", 15, True, "WAIT")
        self.assertIn("not a measured win probability", result["desire_probability_note"])


if __name__ == "__main__":
    unittest.main()
