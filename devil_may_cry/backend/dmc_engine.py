"""DEVIL MAY CRY scoring wrapper: Mega Man Alligator + Aroon/OSMA confirmation.
Scores measure setup confluence, not win probability. This module does not place trades.
"""
from __future__ import annotations
from engine import analyze as analyze_alligator
from engine import ema, sma


def _clamp(value, low, high):
    return max(low, min(high, value))


def _aroon(high, low, period=30):
    if len(high) < period or len(low) < period:
        return 0.0, 0.0
    hs, ls = high[-period:], low[-period:]
    since_high = period - 1 - max(range(period), key=lambda i: hs[i])
    since_low = period - 1 - min(range(period), key=lambda i: ls[i])
    return ((period - since_high) / period) * 100.0, ((period - since_low) / period) * 100.0


def _osma(close, fast=30, slow=60, signal=30):
    if len(close) < slow + signal:
        return 0.0, 0.0, 0.0
    line = [a - b for a, b in zip(ema(close, fast), ema(close, slow))]
    sig = sma(line, signal)
    hist = [a - b for a, b in zip(line, sig)]
    return line[-1], sig[-1], hist[-1] - hist[-2]


def analyze(candles, min_score=70, timeframe_seconds=10):
    base = analyze_alligator(candles, min_score=55, timeframe_seconds=timeframe_seconds)
    components = dict(base.get("components") or {})
    if not base.get("ready") or len(candles) < 90:
        base["score"] = 0
        base["eligible"] = False
        base["direction"] = "WAIT"
        base["reason"] = "Collecting at least 90 verified candles for Aroon 30 and OSMA 30/60/30."
        base["components"] = components
        return base

    close = [float(c["close"]) for c in candles]
    high = [float(c["high"]) for c in candles]
    low = [float(c["low"]) for c in candles]
    spread = float(components.get("di_spread") or 0.0)
    direction = 1 if spread > 0 else -1 if spread < 0 else 0
    direction_name = "BUY" if direction == 1 else "SELL" if direction == -1 else "WAIT"
    minimum_gap = float(components.get("minimum_di_gap") or 1.0)
    gap_ratio = abs(spread) / max(minimum_gap, 1e-9)
    velocity = float(components.get("spread_velocity") or 0.0)
    acceleration = float(components.get("spread_acceleration") or 0.0)
    adx = float(components.get("adx_14") or 0.0)

    # 1) DI / ADX — 35 points. DI remains the direction authority.
    di_score = 0
    if direction:
        di_score += 20 if gap_ratio >= 2.0 else 17 if gap_ratio >= 1.5 else 12 if gap_ratio >= 1.0 else 3
        if velocity * direction > 0: di_score += 6
        if acceleration * direction > 0: di_score += 3
        di_score += 6 if adx >= 25 else 5 if adx >= 20 else 3 if adx >= 15 else 1 if adx >= 8 else 0
    di_score = _clamp(di_score, 0, 35)

    # 2) Alligator — 20 points. It confirms/weakens DI; never creates direction.
    ag_dir = components.get("alligator_direction", "MIXED")
    ag_aligned = ag_dir == direction_name and direction != 0
    ag_gap = float(components.get("alligator_gap_atr") or 0.0)
    ag_expanding = bool(components.get("alligator_expanding"))
    ag_contracting = bool(components.get("alligator_contracting"))
    ag_tight = bool(components.get("alligator_tight"))
    alligator_score = (10 if ag_aligned else 0)
    if ag_aligned and ag_expanding: alligator_score += 5
    if ag_aligned and ag_gap >= 0.8: alligator_score += 3
    elif ag_aligned and ag_gap >= 0.35: alligator_score += 2
    if ag_contracting: alligator_score -= 3
    if ag_tight: alligator_score = min(alligator_score, 5)
    alligator_score = _clamp(alligator_score, 0, 20)

    # 3) Aroon(30) — 15 points.
    aroon_up, aroon_down = _aroon(high, low, 30)
    aroon_dir = 1 if aroon_up > aroon_down else -1 if aroon_down > aroon_up else 0
    aroon_gap = abs(aroon_up - aroon_down)
    aroon_score = 0
    if direction and aroon_dir == direction:
        aroon_score = 10 + (5 if aroon_gap >= 50 else 3 if aroon_gap >= 25 else 1)
    aroon_score = _clamp(aroon_score, 0, 15)

    # 4) OSMA(30,60,30) — 15 points; positive/rising supports BUY, negative/falling SELL.
    osma_line, osma_signal, osma_delta = _osma(close, 30, 60, 30)
    osma_hist = osma_line - osma_signal
    osma_dir = 1 if osma_hist > 0 else -1 if osma_hist < 0 else 0
    osma_score = 0
    if direction and osma_dir == direction:
        osma_score = 9 + (4 if osma_delta * direction > 0 else 0)
        if abs(osma_hist) > 0: osma_score += 2
    osma_score = _clamp(osma_score, 0, 15)

    # 5) Fractal-3 — 15 points. Preserve the existing Alligator engine's
    # confirmed pivot mapping: lower-pivot/down arrow is bullish; upper-pivot/up arrow bearish.
    fractal_dir = components.get("fractal_direction", "WAIT")
    fractal_score = 15 if direction and fractal_dir == direction_name else 8 if fractal_dir == "WAIT" else 0

    score = int(_clamp(di_score + alligator_score + aroon_score + osma_score + fractal_score, 0, 100))
    fakeout = bool(components.get("fakeout_risk"))
    gap_valid = bool(components.get("di_gap_valid"))
    rollover_block = bool(components.get("bullish_di_rollover_block"))
    eligible = bool(direction and gap_valid and adx >= 8 and not fakeout and not rollover_block and score >= min_score)
    final_direction = direction_name if eligible else "WAIT"
    reason = (
        "Qualified DI direction with Alligator, Aroon and OSMA confluence"
        if eligible and fractal_dir == direction_name else
        "Wide DI setup qualified; Fractal-3 has not confirmed yet"
        if eligible and fractal_dir == "WAIT" else
        "DI+ rollover / MACD fade protection" if rollover_block else
        "Small DI X / whipsaw / collapsing spread — fakeout protection" if fakeout else
        "DI spread is not wide enough" if not gap_valid else
        "ADX trend strength below minimum" if adx < 8 else
        f"Confluence below {min_score}/100"
    )
    components.update({
        "scoring_model": "DEVIL MAY CRY 35/20/15/15/15",
        "di_score": di_score, "di_max": 35,
        "alligator_score": alligator_score, "alligator_max": 20,
        "aroon_period": 30, "aroon_up": round(aroon_up, 2), "aroon_down": round(aroon_down, 2),
        "aroon_direction": "BUY" if aroon_dir == 1 else "SELL" if aroon_dir == -1 else "NEUTRAL",
        "aroon_score": aroon_score, "aroon_max": 15,
        "osma_fast_ema": 30, "osma_slow_ema": 60, "osma_signal_sma": 30,
        "osma_line": round(osma_line, 10), "osma_signal": round(osma_signal, 10),
        "osma_histogram": round(osma_hist, 10), "osma_histogram_delta": round(osma_delta, 10),
        "osma_direction": "BUY" if osma_dir == 1 else "SELL" if osma_dir == -1 else "NEUTRAL",
        "osma_score": osma_score, "osma_max": 15,
        "fractal_score": fractal_score, "fractal_max": 15,
        "score_breakdown": {"di": di_score, "alligator": alligator_score, "aroon": aroon_score,
                            "osma": osma_score, "fractal_3": fractal_score},
        "fakeout_risk": fakeout,
    })
    base.update({
        "direction": final_direction, "score": score, "eligible": eligible, "reason": reason,
        "stages": (base.get("stages") or [])[:3] + [{
            "name": "Finalizing Signal", "status": "complete" if eligible else "blocked",
            "summary": f"{final_direction}; score {score}/100; {reason}"
        }],
        "components": components,
    })
    return base
