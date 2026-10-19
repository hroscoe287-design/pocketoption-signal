"""OTC God Mode confirmation strategy for the existing Pocket Option Signal Lab.

Only verified live candles are eligible. The module never places trades.
"""
from __future__ import annotations
from datetime import datetime, timezone
from math import isnan
from typing import Any

def _sma(values, period):
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i-period+1:i+1]
        out[i] = sum(window) / period
    return out

def _rolling_std(values, period):
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i-period+1:i+1]
        mean = sum(window) / period
        out[i] = (sum((x-mean)**2 for x in window) / period) ** 0.5
    return out

def _stoch(bars):
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    raw = [None] * len(bars)
    for i in range(4, len(bars)):
        lo, hi = min(lows[i-4:i+1]), max(highs[i-4:i+1])
        raw[i] = 50.0 if hi == lo else 100.0 * (closes[i]-lo)/(hi-lo)
    smooth_k = [None] * len(bars)
    for i in range(6, len(bars)):
        w = raw[i-2:i+1]
        if all(v is not None for v in w): smooth_k[i] = sum(w)/3
    d = [None] * len(bars)
    for i in range(8, len(bars)):
        w = smooth_k[i-2:i+1]
        if all(v is not None for v in w): d[i] = sum(w)/3
    return smooth_k, d

def _rsi(closes, period=7):
    out = [None] * len(closes)
    if len(closes) <= period: return out
    gains = [max(0.0, closes[i]-closes[i-1]) for i in range(1,len(closes))]
    losses = [max(0.0, closes[i-1]-closes[i]) for i in range(1,len(closes))]
    avg_g = sum(gains[:period])/period
    avg_l = sum(losses[:period])/period
    def val(g,l):
        if l == 0: return 100.0 if g > 0 else 50.0
        if g == 0: return 0.0
        return 100.0 - 100.0/(1.0+g/l)
    out[period] = val(avg_g,avg_l)
    for i in range(period+1,len(closes)):
        avg_g = (avg_g*(period-1)+gains[i-1])/period
        avg_l = (avg_l*(period-1)+losses[i-1])/period
        out[i] = val(avg_g,avg_l)
    return out

def _timestamp(value):
    try:
        n = float(value)
        if n > 1e11: n /= 1000
        return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)

class GodModeStrategy:
    def __init__(self):
        self.last_processed = {}
        self.armed = {}
        self.latest_signal = {}
    def update(self, bars, asset, timeframe_seconds, is_live):
        base = {"signal": None, "message": "Waiting for verified live closed candles.", "expiration_label": f"{2*int(timeframe_seconds)}s"}
        if not is_live:
            base["message"] = "Live feed unavailable. Demo candles are excluded from OTC God Mode."
            return base
        valid = [b for b in bars if all(k in b for k in ("time","open","high","low","close"))]
        if len(valid) < 25:
            base["message"] = f"Warming up indicators: {len(valid)}/25 closed candles."
            return base
        closes = [float(b["close"]) for b in valid]
        lows = [float(b["low"]) for b in valid]
        highs = [float(b["high"]) for b in valid]
        middle = _sma(closes,20); std = _rolling_std(closes,20)
        lower = [None if middle[i] is None else middle[i]-2*std[i] for i in range(len(closes))]
        upper = [None if middle[i] is None else middle[i]+2*std[i] for i in range(len(closes))]
        k,d = _stoch(valid); rsi = _rsi(closes,7)
        i = len(valid)-1
        current = valid[i]
        candle_time = str(current["time"])
        key = f"{asset}|{timeframe_seconds}"
        if self.last_processed.get(key) == candle_time:
            base.update({"message":"Latest candle already evaluated; waiting for next candle close.",
                         "indicators":{"lower":lower[i],"upper":upper[i],"k":k[i],"d":d[i],"rsi":rsi[i]}})
            base["signal"] = self.latest_signal.get(key)
            return base
        self.last_processed[key] = candle_time
        prior_armed = self.armed.get(key)
        close = closes[i]
        inside = lower[i] <= close <= upper[i]
        signal = None
        if prior_armed and prior_armed.get("index") == i-1 and inside and i >= 1:
            bullish_cross = k[i-1] is not None and d[i-1] is not None and k[i] is not None and d[i] is not None and k[i-1] <= d[i-1] and k[i] > d[i] and k[i] < 20 and d[i] < 20
            bearish_cross = k[i-1] is not None and d[i-1] is not None and k[i] is not None and d[i] is not None and k[i-1] >= d[i-1] and k[i] < d[i] and k[i] > 80 and d[i] > 80
            rsi_up = rsi[i-1] is not None and rsi[i] is not None and rsi[i-1] < 30 and rsi[i] > rsi[i-1] and rsi[i] < 30
            rsi_down = rsi[i-1] is not None and rsi[i] is not None and rsi[i-1] > 70 and rsi[i] < rsi[i-1] and rsi[i] > 70
            if prior_armed["side"] == "lower" and bullish_cross and rsi_up:
                direction, label = "HIGHER", "CALL"
            elif prior_armed["side"] == "upper" and bearish_cross and rsi_down:
                direction, label = "LOWER", "PUT"
            else:
                direction = label = None
            if direction:
                sec = 2*int(timeframe_seconds)
                mins, rem = divmod(sec,60)
                exp = f"{mins}m" if rem == 0 and mins else (f"{sec}s" if sec < 60 else f"{mins}m {rem}s")
                stamp = _timestamp(current["time"])
                signal = {"id":f"{key}|{candle_time}|{direction}","asset":asset,"direction":direction,"label":label,
                          "timestamp":stamp,"expiration_seconds":sec,"expiration_label":exp,
                          "strength_label":"CONFIRMED SETUP",
                          "note":"Indicator confirmation only; not a probability or guarantee."}
                self.latest_signal[key] = signal
        # Only the immediate next candle can qualify; arm from this candle for the next close.
        self.armed[key] = {"side":"lower" if close < lower[i] else ("upper" if close > upper[i] else None),"index":i}
        base.update({"signal":signal,"message":"Confirmed setup." if signal else "No confirmed setup on latest closed candle.",
                     "expiration_label":signal["expiration_label"] if signal else base["expiration_label"],
                     "indicators":{"lower":round(lower[i],8),"upper":round(upper[i],8),
                                   "k":round(k[i],2) if k[i] is not None else None,
                                   "d":round(d[i],2) if d[i] is not None else None,
                                   "rsi":round(rsi[i],2) if rsi[i] is not None else None}})
        return base
