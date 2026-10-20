"""GODMODE-primary signal engine with SONIC as a confirmation checklist.

Signal-only: never places orders. SONIC changes the displayed strength/confirmation,
but does not veto an otherwise valid live GODMODE signal.
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
        godmode = godmode_signal if godmode_signal in ("BUY", "SELL") else "WAIT"
        base = {
            "signal": godmode, "godmode_signal": godmode,
            "sonic_signal": "WAIT", "wide_x": False,
            "fractal_arrow": None, "plus_di": None, "minus_di": None,
            "adx": None, "expiration_seconds": expiration, "locked": False,
            "alert_eligible": False, "agreement": "UNAVAILABLE",
            "confirmation_level": "GODMODE ONLY" if godmode != "WAIT" else "WAIT",
            "confirmation_adjustment": 0,
            "engine": "GODMODE PRIMARY + SONIC CHECKLIST",
            "reason": "SONIC is a checklist; it adjusts confirmation strength but does not veto GODMODE."
        }
        if not live:
            base["signal"] = "WAIT"
            base["godmode_signal"] = "WAIT"
            base["confirmation_level"] = "WAIT"
            base["reason"] = "Live feed unavailable; signals disabled."
            return base
        if len(candles) < 25:
            base["signal"] = "WAIT"
            base["godmode_signal"] = "WAIT"
            base["confirmation_level"] = "WAIT"
            base["reason"] = f"Warming up indicators: {len(candles)}/25 candles."
            return base
        d = _adx(candles, 7, 14)
        fractal = _latest_fractal(candles, 10)
        if not d:
            base["signal"] = godmode
            base["reason"] = "SONIC indicators warming up; GODMODE signal remains primary."
            return base
        gap = abs(d["plus"] - d["minus"])
        wide = gap >= max(5.0, d["plus"] * 0.22, d["minus"] * 0.22)
        sonic = "WAIT"
        if wide and fractal:
            # Existing SONIC arrow-to-DI mapping.
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
            "signal": godmode,
            "godmode_signal": godmode,
        })
        if godmode == "WAIT":
            base["confirmation_level"] = "WAIT"
            base["agreement"] = "NO GODMODE SIGNAL"
            base["reason"] = "No GODMODE signal; SONIC alone cannot create a trade signal."
            return base
        if sonic == godmode:
            base["agreement"] = "AGREE"
            base["confirmation_level"] = "STRONGER CONFIRMATION"
            base["confirmation_adjustment"] = 8
            base["reason"] = f"GODMODE {godmode} + SONIC {sonic} agree; stronger confluence."
        elif sonic == "WAIT":
            base["agreement"] = "PARTIAL"
            base["confirmation_level"] = "GODMODE ONLY"
            base["confirmation_adjustment"] = -8
            if not wide:
                detail = "WIDE X is not strong enough"
            elif not fractal:
                detail = "no confirmed Fractal-3 arrow yet"
            else:
                detail = "SONIC arrow/DI conditions do not fully align"
            base["reason"] = f"GODMODE {godmode} remains active; lower strength because {detail}."
        else:
            base["agreement"] = "CONFLICT"
            base["confirmation_level"] = "CONFLICT — CAUTION"
            base["confirmation_adjustment"] = -18
            base["reason"] = f"GODMODE says {godmode}, but SONIC says {sonic}; keep GODMODE visible with a sharply reduced strength warning."
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
