"""GODMODE-primary signal engine with SONIC X-quality confirmation.

Signal-only: never places orders. SONIC grades the DI crossover, warns about weak/
fake-out-prone X patterns, and adjusts a confluence-strength score without vetoing
an otherwise valid live GODMODE signal.
"""
from __future__ import annotations
import time


def _wilder(values, period):
    if len(values) < period:
        return []
    out = [sum(values[:period]) / period]
    for value in values[period:]:
        out.append((out[-1] * (period - 1) + value) / period)
    return out


def _adx(candles, di_length=7, adx_smoothing=14):
    if len(candles) < di_length + 1:
        return None
    h = [float(c["high"]) for c in candles]
    l = [float(c["low"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    tr, plus, minus = [], [], []
    for j in range(1, len(candles)):
        up, down = h[j] - h[j-1], l[j-1] - l[j]
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
        tr.append(max(h[j]-l[j], abs(h[j]-cl[j-1]), abs(l[j]-cl[j-1])))
    atr, ps, ms = _wilder(tr, di_length), _wilder(plus, di_length), _wilder(minus, di_length)
    q = min(len(atr), len(ps), len(ms))
    if q < 2:
        return None
    p = [ps[j] / atr[j] * 100 if atr[j] else 0 for j in range(q)]
    m = [ms[j] / atr[j] * 100 if atr[j] else 0 for j in range(q)]
    dx = [abs(p[j]-m[j]) / max(1e-9, p[j]+m[j]) * 100 for j in range(q)]
    adx_values = _wilder(dx, adx_smoothing)
    return {
        "plus": p[-1], "minus": m[-1],
        "plus_prev": p[-2], "minus_prev": m[-2],
        "plus_prev2": p[-3] if len(p) >= 3 else p[-2],
        "minus_prev2": m[-3] if len(m) >= 3 else m[-2],
        "adx": adx_values[-1] if adx_values else None,
        "adx_prev": adx_values[-2] if len(adx_values) >= 2 else None,
    }


def _latest_fractal(candles, lookback=10):
    # A 5-bar Fractal-3 needs two candles to the right to confirm.
    if len(candles) < 5:
        return None
    start = max(2, len(candles) - lookback - 2)
    for i in range(len(candles)-3, start-1, -1):
        h, low = float(candles[i]["high"]), float(candles[i]["low"])
        if all(h > float(candles[j]["high"]) for j in (i-1, i-2, i+1, i+2)):
            return {"arrow": "DOWN", "time": candles[i]["time"], "price": h}
        if all(low < float(candles[j]["low"]) for j in (i-1, i-2, i+1, i+2)):
            return {"arrow": "UP", "time": candles[i]["time"], "price": low}
    return None


class CombinedEngine:
    def __init__(self):
        self.last_signal = {}
        self.lock_until = {}
        self.last_signal_candle = {}

    def evaluate(self, candles, asset, timeframe_seconds, live, godmode_signal, godmode_score=0):
        key = f"{asset}|{int(timeframe_seconds)}"
        now = time.time()
        expiration = max(5, int(timeframe_seconds) * 2)
        godmode = godmode_signal if godmode_signal in ("BUY", "SELL") else "WAIT"
        base = {
            "signal": godmode, "godmode_signal": godmode, "sonic_signal": "WAIT",
            "wide_x": False, "x_status": "UNAVAILABLE", "x_quality": 0,
            "fakeout_risk": "UNKNOWN", "fractal_arrow": None,
            "plus_di": None, "minus_di": None, "adx": None,
            "expiration_seconds": expiration, "locked": False,
            "alert_eligible": False, "agreement": "UNAVAILABLE",
            "confirmation_level": "GODMODE ONLY" if godmode != "WAIT" else "WAIT",
            "confirmation_adjustment": 0, "x_quality_adjustment": 0,
            "engine": "GODMODE PRIMARY + SONIC X-QUALITY CHECKLIST",
            "reason": "SONIC grades confirmation strength; it does not veto GODMODE."
        }
        if not live:
            base.update(signal="WAIT", godmode_signal="WAIT", confirmation_level="WAIT",
                        reason="Live feed unavailable; signals disabled.")
            return base
        if len(candles) < 25:
            base.update(signal="WAIT", godmode_signal="WAIT", confirmation_level="WAIT",
                        reason=f"Warming up indicators: {len(candles)}/25 candles.")
            return base
        d = _adx(candles, 7, 14)
        fractal = _latest_fractal(candles, 10)
        if not d:
            base["reason"] = "SONIC indicators warming up; GODMODE remains primary."
            return base

        plus, minus = d["plus"], d["minus"]
        gap = abs(plus - minus)
        prior_gap = abs(d["plus_prev"] - d["minus_prev"])
        gap_before = abs(d["plus_prev2"] - d["minus_prev2"])
        threshold = max(5.0, plus * 0.22, minus * 0.22)
        wide = gap >= threshold
        widening = gap > prior_gap > gap_before
        plus_rising = plus > d["plus_prev"] > d["plus_prev2"]
        minus_rising = minus > d["minus_prev"] > d["minus_prev2"]
        adx = d["adx"]
        adx_rising = adx is not None and d["adx_prev"] is not None and adx > d["adx_prev"]
        dominant_rising = (minus_rising if minus > plus else plus_rising)
        strong_x = bool(wide and widening and dominant_rising and adx is not None and adx >= 18 and adx_rising)
        forming_x = bool(not wide and gap > prior_gap and (plus_rising or minus_rising))
        if strong_x:
            x_status, x_quality, fakeout_risk = "STRONG / EXPANDING X", 3, "LOWER"
        elif wide and adx is not None and adx < 15:
            x_status, x_quality, fakeout_risk = "WIDE BUT WEAK TREND", 0, "HIGH"
        elif wide and not widening:
            x_status, x_quality, fakeout_risk = "X LOSING MOMENTUM", 0, "ELEVATED"
        elif wide:
            x_status, x_quality, fakeout_risk = "WIDE X — NOT FULLY CONFIRMED", 1, "MEDIUM"
        elif forming_x:
            x_status, x_quality, fakeout_risk = "X FORMING — WAIT FOR SEPARATION", 1, "MEDIUM"
        else:
            x_status, x_quality, fakeout_risk = "NO CLEAR X", 0, "ELEVATED"

        sonic = "WAIT"
        if wide and fractal:
            # Existing SONIC arrow-to-DI mapping; require the matching DI line to dominate.
            if fractal["arrow"] == "UP" and minus > plus:
                sonic = "SELL"
            elif fractal["arrow"] == "DOWN" and plus > minus:
                sonic = "BUY"

        base.update({
            "sonic_signal": sonic, "wide_x": bool(wide),
            "x_status": x_status, "x_quality": x_quality, "fakeout_risk": fakeout_risk,
            "x_gap_expanding": bool(widening), "dominant_di_rising": bool(dominant_rising),
            "adx_rising": bool(adx_rising),
            "fractal_arrow": fractal["arrow"] if fractal else None,
            "fractal_time": fractal["time"] if fractal else None,
            "plus_di": round(plus, 2), "minus_di": round(minus, 2),
            "adx": round(adx, 2) if adx is not None else None,
            "di_gap": round(gap, 2), "di_gap_previous": round(prior_gap, 2),
            "signal": godmode, "godmode_signal": godmode,
        })
        if godmode == "WAIT":
            base.update(confirmation_level="WAIT", agreement="NO GODMODE SIGNAL",
                        reason="No GODMODE signal; SONIC alone cannot create a trade signal.")
            return base
        if sonic == godmode and strong_x:
            base["agreement"] = "STRONG AGREE"
            base["confirmation_level"] = "STRONG CONFLUENCE"
            base["confirmation_adjustment"] = 8
            base["x_quality_adjustment"] = 8
            base["reason"] = f"GODMODE {godmode} and SONIC {sonic} agree; DI gap is widening, dominant DI is rising, and ADX confirms trend strength."
        elif sonic == godmode:
            base["agreement"] = "AGREE — X QUALITY CAUTION"
            base["confirmation_level"] = "AGREE, BUT X NOT STRONG"
            base["confirmation_adjustment"] = 5
            base["x_quality_adjustment"] = -4
            base["reason"] = f"GODMODE and SONIC both say {godmode}, but the X is not fully strengthening ({x_status}); keep the signal, reduce strength."
        elif sonic == "WAIT":
            base["agreement"] = "PARTIAL"
            base["confirmation_level"] = "GODMODE ONLY"
            base["confirmation_adjustment"] = -8
            base["x_quality_adjustment"] = -4 if fakeout_risk in ("HIGH", "ELEVATED") else 0
            base["reason"] = f"GODMODE {godmode} remains active; SONIC is not fully confirmed. X status: {x_status}; fake-out risk: {fakeout_risk}."
        else:
            base["agreement"] = "CONFLICT"
            base["confirmation_level"] = "CONFLICT — CAUTION"
            base["confirmation_adjustment"] = -18
            base["x_quality_adjustment"] = -4
            base["reason"] = f"GODMODE says {godmode}, but SONIC says {sonic}; retain GODMODE with a sharp strength reduction. X status: {x_status}; fake-out risk: {fakeout_risk}."

        # Lockout applies to duplicate alerts only, never to the live signal or chart.
        candle_id = str(candles[-1].get("time"))
        same_candle = self.last_signal_candle.get(key) == candle_id
        locked = now < self.lock_until.get(key, 0)
        base["locked"] = bool(locked or same_candle)
        base["alert_eligible"] = not base["locked"]
        if base["alert_eligible"]:
            self.last_signal[key] = godmode
            self.last_signal_candle[key] = candle_id
            self.lock_until[key] = now + expiration
        elif locked:
            base["reason"] += f" Duplicate alert suppressed for about {int(self.lock_until[key] - now)}s; signal/chart continue updating."
        return base
