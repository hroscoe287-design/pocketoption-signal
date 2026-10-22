"""SONIC-primary signal engine with lightweight GODMODE confirmation.

Signal-only: SONIC Fractal-3 and +DI/-DI lead direction; GODMODE is secondary.
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
            "signal": "WAIT", "godmode_signal": godmode, "sonic_signal": "WAIT",
            "wide_x": False, "x_status": "UNAVAILABLE", "x_quality": 0,
            "fakeout_risk": "UNKNOWN", "fractal_arrow": None,
            "plus_di": None, "minus_di": None, "adx": None,
            "expiration_seconds": expiration, "locked": False,
            "alert_eligible": False, "agreement": "UNAVAILABLE",
            "confirmation_level": "GODMODE ONLY" if godmode != "WAIT" else "WAIT",
            "confirmation_adjustment": 0, "x_quality_adjustment": 0,
            "plus_di_boxes": 0, "minus_di_boxes": 0, "di_box_ratio": "0:0",
            "engine": "SONIC PRIMARY + LIGHT GODMODE CONFIRMATION",
            "reason": "SONIC Fractal-3 and DI-box majority lead; GODMODE is secondary confirmation."
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
        # Early-X Radar: estimate whether DI lines are converging toward a
        # crossover or have just crossed and are beginning to separate.
        # This is a transparent heuristic, not a trained AI or a probability.
        spread_now = plus - minus
        spread_prev = d["plus_prev"] - d["minus_prev"]
        spread_prev2 = d["plus_prev2"] - d["minus_prev2"]
        spread_velocity = spread_now - spread_prev
        spread_acceleration = spread_velocity - (spread_prev - spread_prev2)
        projected_cross_bars = (abs(spread_now / spread_velocity)
                                if abs(spread_velocity) > 1e-9 and spread_now * spread_velocity < 0
                                else None)
        crossing_soon = projected_cross_bars is not None and projected_cross_bars <= 2.5
        just_crossed = spread_now * spread_prev <= 0 and abs(spread_now) > 0
        early_direction = "BUY" if spread_velocity > 0 else ("SELL" if spread_velocity < 0 else "WAIT")
        early_score = 0
        if abs(spread_velocity) > 0:
            early_score += 30
        if spread_velocity * (spread_prev - spread_prev2) > 0:
            early_score += 20
        if d["plus"] > d["plus_prev"] and d["minus"] < d["minus_prev"]:
            early_score += 20
        elif d["minus"] > d["minus_prev"] and d["plus"] < d["plus_prev"]:
            early_score += 20
        if d.get("adx") is not None and d.get("adx_prev") is not None and d["adx"] >= d["adx_prev"]:
            early_score += 15
        if crossing_soon or just_crossed:
            early_score += 15
        early_score = min(100, early_score)
        early_warning = bool(early_score >= 65 and (crossing_soon or just_crossed or gap > prior_gap))
        if crossing_soon:
            early_status = "CROSS APPROACHING"
        elif just_crossed and gap > prior_gap:
            early_status = "X JUST TURNING / EXPANDING"
        elif early_warning:
            early_status = "EARLY X WATCH"
        else:
            early_status = "SCANNING — NO EARLY X"
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
        # DESIRE agent translates the early-X measurements into a clearly
        # labeled heads-up. "SUPERIOR" requires stronger confluence, not just
        # a close crossover; it remains a heuristic setup grade, not a win rate.
        desire_superior = bool(
            (strong_x and early_score >= 65) or
            (early_warning and early_score >= 80 and adx_rising and abs(spread_velocity) > 0)
        )
        desire_level = "SUPERIOR" if desire_superior else ("EARLY WATCH" if early_warning else "SCANNING")
        desire_direction = early_direction if early_warning else "WAIT"
        desire_message = (
            "SUPERIOR CHANGE WATCH: DI crossover momentum is accelerating; verify price action before entry."
            if desire_superior else
            ("Early DI crossover forming. Wait for stronger separation/confirmation."
             if early_warning else "Monitoring +DI/-DI spread, velocity, acceleration and ADX strength.")
        )
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

        total_di = max(1e-9, plus + minus)
        plus_boxes = max(1, min(4, int(round(5 * plus / total_di))))
        minus_boxes = 5 - plus_boxes
        base.update({
            "sonic_signal": sonic, "wide_x": bool(wide),
            "plus_di_boxes": plus_boxes, "minus_di_boxes": minus_boxes,
            "di_box_ratio": f"{plus_boxes}:{minus_boxes}",
            "x_status": x_status, "x_quality": x_quality, "fakeout_risk": fakeout_risk,
            "x_gap_expanding": bool(widening), "dominant_di_rising": bool(dominant_rising),
            "adx_rising": bool(adx_rising),
            "fractal_arrow": fractal["arrow"] if fractal else None,
            "fractal_time": fractal["time"] if fractal else None,
            "plus_di": round(plus, 2), "minus_di": round(minus, 2),
            "adx": round(adx, 2) if adx is not None else None,
            "di_gap": round(gap, 2), "di_gap_previous": round(prior_gap, 2),
            "early_x_warning": bool(early_warning),
            "early_x_status": early_status,
            "desire_agent": "DESIRE",
            "desire_level": desire_level,
            "desire_superior": desire_superior,
            "desire_direction": desire_direction,
            "desire_score": int(early_score),
            "desire_message": desire_message,
            "early_x_direction": early_direction if early_warning else "WAIT",
            "early_x_score": int(early_score),
            "projected_cross_bars": round(projected_cross_bars, 2) if projected_cross_bars is not None else None,
            "spread_velocity": round(spread_velocity, 3),
            "spread_acceleration": round(spread_acceleration, 3),
            "signal": godmode, "godmode_signal": godmode,
        })
        # SONIC leads. GODMODE is only a lightweight confirmation.
        if sonic == "WAIT":
            base.update(signal="WAIT", confirmation_level="WAIT",
                        agreement="SONIC NOT CONFIRMED",
                        reason=f"SONIC needs confirmed Fractal-3 direction and DI dominance. {x_status}; GODMODE is secondary only.")
            return base

        base["signal"] = sonic
        if godmode == sonic:
            base["agreement"] = "SONIC + GODMODE AGREE"
            base["confirmation_level"] = "SONIC LEAD • GODMODE CONFIRMS"
            base["confirmation_adjustment"] = 4 if strong_x else 2
            base["x_quality_adjustment"] = 4 if strong_x else (-2 if fakeout_risk in ("HIGH", "ELEVATED") else 0)
            base["reason"] = f"SONIC leads with {sonic}; GODMODE agrees as lightweight confirmation. DI boxes {plus_boxes}:{minus_boxes}; {x_status}."
        elif godmode == "WAIT":
            base["agreement"] = "SONIC ONLY"
            base["confirmation_level"] = "SONIC LEAD • NO GODMODE CONFIRM"
            base["confirmation_adjustment"] = -2
            base["x_quality_adjustment"] = -3 if fakeout_risk in ("HIGH", "ELEVATED") else 0
            base["reason"] = f"SONIC leads {sonic}; GODMODE has no confirmation. Confidence reduced slightly. DI boxes {plus_boxes}:{minus_boxes}; {x_status}."
        else:
            base["agreement"] = "SONIC LEADS • GODMODE DISAGREES"
            base["confirmation_level"] = "SONIC LEAD • CONFLICT CAUTION"
            base["confirmation_adjustment"] = -5
            base["x_quality_adjustment"] = -3
            base["reason"] = f"SONIC direction {sonic} remains primary; GODMODE disagrees ({godmode}), so confidence is reduced without overriding direction. DI boxes {plus_boxes}:{minus_boxes}; {x_status}."

        # DESIRE high-confluence gate. This is a setup-grade score, NOT a
        # calibrated win probability. Early warnings remain visible, but CALL/PUT
        # alerts require strong trend expansion, matching GODMODE direction, and
        # low fakeout risk. No orders are ever placed by this engine.
        desire_setup_strength = 0
        if strong_x:
            desire_setup_strength += 35
        if early_score >= 80:
            desire_setup_strength += 20
        if fractal is not None and (
            (sonic == "BUY" and fractal["arrow"] == "DOWN") or
            (sonic == "SELL" and fractal["arrow"] == "UP")
        ):
            desire_setup_strength += 15
        if godmode == sonic:
            desire_setup_strength += 15
        if adx is not None and adx >= 20 and adx_rising:
            desire_setup_strength += 10
        if fakeout_risk == "LOWER":
            desire_setup_strength += 5
        desire_setup_strength = min(100, desire_setup_strength)
        base["desire_setup_strength"] = desire_setup_strength
        base["desire_signal_threshold"] = 90
        base["desire_signal_status"] = (
            "STRONG SETUP — SIGNAL ELIGIBLE"
            if desire_setup_strength >= 90 and strong_x and godmode == sonic and fakeout_risk == "LOWER"
            else "WAIT — NEED 90+ CONFLUENCE"
        )
        # High-threshold signal filter: don't publish an actionable signal unless
        # every required confirmation agrees. Keep chart/radar/early warning live.
        if not (desire_setup_strength >= 90 and strong_x and godmode == sonic and fakeout_risk == "LOWER"):
            base["signal"] = "WAIT"
            base["alert_eligible"] = False
            base["confirmation_level"] = "DESIRE HIGH-CONFLUENCE FILTER"
            base["reason"] = (
                f"DESIRE blocks the trade alert: setup grade {desire_setup_strength}/100 "
                f"(90 required), strong expanding X, matching GODMODE direction and low fakeout risk required. "
                f"Radar remains active: {early_status}."
            )
            return base

        # Lockout applies to duplicate alerts only, never to the live signal or chart.
        candle_id = str(candles[-1].get("time"))
        same_candle = self.last_signal_candle.get(key) == candle_id
        locked = now < self.lock_until.get(key, 0)
        base["locked"] = bool(locked or same_candle)
        base["alert_eligible"] = not base["locked"]
        if base["alert_eligible"]:
            self.last_signal[key] = sonic
            self.last_signal_candle[key] = candle_id
            self.lock_until[key] = now + expiration
        elif locked:
            base["reason"] += f" Duplicate alert suppressed for about {int(self.lock_until[key] - now)}s; signal/chart continue updating."
        return base
