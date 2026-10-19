"""Combined GODMODE + SONIC confirmation engine.

Signal-only: never places orders. Requires live candles, GODMODE confluence,
SONIC ADX +DI/-DI separation ("wide X"), and a confirmed Fractal-3 arrow
aligned with the SONIC direction. Re-alerts are locked out for 2x timeframe.
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
    return {"plus": p[-1], "minus": m[-1], "adx": adx_values[-1] if adx_values else None}

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
        base = {
            "signal": "WAIT", "sonic_signal": "WAIT", "wide_x": False,
            "fractal_arrow": None, "plus_di": None, "minus_di": None,
            "adx": None, "expiration_seconds": expiration, "locked": False,
            "engine": "GODMODE + SONIC HYBRID",
            "reason": "Waiting for live candles and aligned confirmations."
        }
        if not live:
            base["reason"] = "Live feed unavailable; signals disabled."
            return base
        if len(candles) < 25:
            base["reason"] = f"Warming up hybrid indicators: {len(candles)}/25 candles."
            return base
        d = _adx(candles, 7, 14)
        fractal = _latest_fractal(candles, 10)
        if not d:
            base["reason"] = "Waiting for ADX +DI/-DI values."
            return base
        gap = abs(d["plus"] - d["minus"])
        wide = gap >= max(5.0, d["plus"] * 0.22, d["minus"] * 0.22)
        sonic = "WAIT"
        if wide and fractal:
            # Match the existing SONIC arrow-to-DI mapping.
            if fractal["arrow"] == "UP" and d["minus"] >= d["plus"]:
                sonic = "SELL"
            elif fractal["arrow"] == "DOWN" and d["plus"] >= d["minus"]:
                sonic = "BUY"
        base.update({
            "sonic_signal": sonic, "wide_x": bool(wide),
            "fractal_arrow": fractal["arrow"] if fractal else None,
            "fractal_time": fractal["time"] if fractal else None,
            "plus_di": round(d["plus"], 2), "minus_di": round(d["minus"], 2),
            "adx": round(d["adx"], 2) if d["adx"] is not None else None,
            "di_gap": round(gap, 2),
        })
        if not wide:
            base["reason"] = "Waiting for a WIDE X: +DI/-DI separation is not strong enough."
            return base
        if not fractal:
            base["reason"] = "Wide X detected; waiting for a confirmed Fractal-3 arrow."
            return base
        if sonic == "WAIT":
            base["reason"] = "Wide X detected, but Fractal arrow and DI direction do not line up."
            return base
        if godmode_signal != sonic:
            base["reason"] = f"SONIC confirms {sonic}, but GODMODE confluence is {godmode_signal}; waiting for agreement."
            return base
        if now < self.lock_until.get(key, 0):
            base["locked"] = True
            base["reason"] = f"Hybrid signal lockout active for {int(self.lock_until[key] - now)}s; chart continues updating."
            return base
        candle_id = str(candles[-1].get("time"))
        if self.last_signal_candle.get(key) == candle_id:
            base["reason"] = "This candle already produced a hybrid signal; waiting for a new candle."
            return base
        self.last_signal[key] = sonic
        self.last_signal_candle[key] = candle_id
        self.lock_until[key] = now + expiration
        base.update({
            "signal": sonic, "locked": False,
            "reason": f"GODMODE + SONIC aligned: WIDE X + Fractal {fractal['arrow']} + DI confirmation."
        })
        return base
