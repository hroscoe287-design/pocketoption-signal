"""DESIRE's transparent DI/fractal analysis engine. Signal-only; never places orders."""
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
    if q < 3:
        return None
    p = [ps[j] / atr[j] * 100 if atr[j] else 0 for j in range(q)]
    m = [ms[j] / atr[j] * 100 if atr[j] else 0 for j in range(q)]
    dx = [abs(p[j]-m[j]) / max(1e-9, p[j]+m[j]) * 100 for j in range(q)]
    av = _wilder(dx, adx_smoothing)
    return {"plus":p[-1], "minus":m[-1], "plus_prev":p[-2], "minus_prev":m[-2],
            "plus_prev2":p[-3], "minus_prev2":m[-3],
            "adx":av[-1] if av else None, "adx_prev":av[-2] if len(av)>1 else None}


def _latest_fractal(candles, lookback=10):
    # Fractal-3: a five-candle swing requires two closed candles to its right.
    if len(candles) < 5:
        return None
    start = max(2, len(candles) - lookback - 2)
    for i in range(len(candles)-3, start-1, -1):
        h, low = float(candles[i]["high"]), float(candles[i]["low"])
        if all(h > float(candles[j]["high"]) for j in (i-1,i-2,i+1,i+2)):
            return {"arrow":"DOWN", "time":candles[i]["time"], "price":h}
        if all(low < float(candles[j]["low"]) for j in (i-1,i-2,i+1,i+2)):
            return {"arrow":"UP", "time":candles[i]["time"], "price":low}
    return None


class CombinedEngine:
    """Fast rule-based analysis. Scores are setup grades, not calibrated win probabilities."""
    def __init__(self):
        self.last_signal = {}
        self.lock_until = {}
        self.last_signal_candle = {}

    def evaluate(self, candles, asset, timeframe_seconds, live, godmode_signal, godmode_score=0):
        key = f"{asset}|{int(timeframe_seconds)}"
        now = time.time()
        expiration = max(5, int(timeframe_seconds)*2)
        godmode = godmode_signal if godmode_signal in ("BUY","SELL") else "WAIT"
        base = {
            "signal":"WAIT","godmode_signal":godmode,"sonic_signal":"WAIT",
            "wide_x":False,"x_status":"UNAVAILABLE","x_quality":0,"fakeout_risk":"UNKNOWN",
            "fractal_arrow":None,"plus_di":None,"minus_di":None,"adx":None,
            "expiration_seconds":expiration,"locked":False,"alert_eligible":False,
            "agreement":"UNAVAILABLE","confirmation_level":"WAIT","confirmation_adjustment":0,
            "x_quality_adjustment":0,"plus_di_boxes":0,"minus_di_boxes":0,"di_box_ratio":"0:0",
            "engine":"DESIRE EARLY-X + FRACTAL CONFLUENCE","reason":"Waiting for verified live candles.",
            "desire_probability":0,"probability_label":"INSUFFICIENT DATA",
            "desire_probability_note":"Heuristic setup confidence, not a calibrated win probability."
        }
        if not live:
            base["reason"]="Live feed unavailable; signals disabled."
            return base
        if len(candles)<25:
            base["reason"]=f"Warming up indicators: {len(candles)}/25 candles."
            return base
        d=_adx(candles,7,14)
        fractal=_latest_fractal(candles,10)
        if not d:
            base["reason"]="DI indicators warming up."
            return base

        plus, minus = d["plus"], d["minus"]
        spread, prev = plus-minus, d["plus_prev"]-d["minus_prev"]
        prev2 = d["plus_prev2"]-d["minus_prev2"]
        velocity, prior_velocity = spread-prev, prev-prev2
        acceleration = velocity-prior_velocity
        gap, prior_gap, gap_before = abs(spread), abs(prev), abs(prev2)
        gap_delta, gap_accel = gap-prior_gap, (gap-prior_gap)-(prior_gap-gap_before)
        cross_eta = abs(spread/velocity) if abs(velocity)>1e-9 and spread*velocity<0 else None
        crossing_soon = cross_eta is not None and cross_eta <= 2.5
        just_crossed = spread*prev <= 0 and abs(spread)>0
        direction = "BUY" if spread>0 else ("SELL" if spread<0 else "WAIT")
        approach_direction = "BUY" if velocity>0 else ("SELL" if velocity<0 else "WAIT")
        adx, adx_prev = d["adx"], d["adx_prev"]
        adx_rising = adx is not None and adx_prev is not None and adx>adx_prev
        plus_slope, minus_slope = plus-d["plus_prev"], minus-d["minus_prev"]
        dominant_slope = plus_slope if plus>minus else minus_slope
        dominant_accel = ((plus_slope-(d["plus_prev"]-d["plus_prev2"])) if plus>minus
                          else (minus_slope-(d["minus_prev"]-d["minus_prev2"])))
        wide = gap >= max(5.0, plus*.22, minus*.22)
        widening = gap > prior_gap > gap_before
        forming = gap_delta>0 and (plus_slope*minus_slope<0 or abs(velocity)>abs(prior_velocity))
        early_score=0
        if abs(velocity)>0.01: early_score+=15
        if velocity*prior_velocity>0: early_score+=15
        if acceleration*velocity>0: early_score+=10
        if forming or crossing_soon: early_score+=20
        if just_crossed and gap_delta>0: early_score+=15
        if adx_rising: early_score+=10
        if dominant_slope>0: early_score+=10
        if gap_delta>0: early_score+=5
        early_score=min(100,early_score)
        early_warning=early_score>=45 and (crossing_soon or forming or just_crossed or gap_delta>0)
        early_status=("CROSS APPROACHING" if crossing_soon else
                      "X JUST TURNING / EXPANDING" if just_crossed and gap_delta>0 else
                      "SMALL X STARTING TO EXPAND" if forming else
                      "EARLY X WATCH" if early_warning else "SCANNING — NO EARLY X")
        strong_x=bool(wide and widening and dominant_slope>0 and adx is not None and adx>=18 and adx_rising)
        if strong_x: x_status,x_quality,risk="STRONG / EXPANDING X",3,"LOWER"
        elif wide and adx is not None and adx<15: x_status,x_quality,risk="WIDE BUT WEAK TREND",0,"HIGH"
        elif wide and not widening: x_status,x_quality,risk="WIDE X — LATE / LOSING MOMENTUM",0,"ELEVATED"
        elif wide: x_status,x_quality,risk="WIDE X — VERIFY MOMENTUM",1,"MEDIUM"
        elif forming: x_status,x_quality,risk="SMALL X STARTING TO EXPAND",2,"MEDIUM"
        else: x_status,x_quality,risk="NO CLEAR X",0,"ELEVATED"

        sonic="WAIT"
        if wide and fractal:
            if fractal["arrow"]=="UP" and minus>plus: sonic="SELL"
            elif fractal["arrow"]=="DOWN" and plus>minus: sonic="BUY"
        # Setup grade rewards early development, directional pressure, acceleration,
        # ADX context, and fractal agreement; penalizes late/overextended gaps.
        setup=10
        setup += 20 if early_warning else 0
        setup += 15 if velocity*prior_velocity>0 else 0
        setup += 10 if acceleration*velocity>0 else 0
        setup += 15 if adx is not None and adx>=18 and adx_rising else 0
        setup += 10 if dominant_slope>0 else 0
        expected_arrow = "DOWN" if direction=="BUY" else "UP"
        fractal_agrees = bool(fractal and fractal["arrow"]==expected_arrow)
        setup += 10 if fractal_agrees else 0
        setup += 10 if godmode==direction and direction!="WAIT" else 0
        setup -= 15 if wide and not widening else 0
        setup -= 10 if fractal and not fractal_agrees else 0
        setup=max(0,min(100,setup))
        # Until outcomes are backtested and calibrated, this is an evidence grade,
        # not a statistically established probability of winning.
        label="HIGH SETUP CONFIDENCE" if setup>=80 else "MODERATE SETUP CONFIDENCE" if setup>=60 else "LOW SETUP CONFIDENCE"
        desire_direction=direction if early_warning else "WAIT"
        desire_superior=bool(setup>=90 and strong_x and godmode==sonic and sonic!="WAIT" and risk=="LOWER")
        message=("Strong confluence; verify the live candle and entry timing." if desire_superior else
                 f"{early_status}. {direction if direction!='WAIT' else 'Direction unclear'} pressure; setup grade {setup}/100." if early_warning else
                 "Monitoring DI gap, line speed, acceleration, ADX and fractal context.")
        total=max(1e-9,plus+minus)
        pboxes=max(1,min(4,int(round(5*plus/total))))
        mboxes=5-pboxes
        base.update({
            "sonic_signal":sonic,"wide_x":bool(wide),"x_status":x_status,"x_quality":x_quality,
            "fakeout_risk":risk,"x_gap_expanding":bool(widening),"dominant_di_rising":bool(dominant_slope>0),
            "adx_rising":bool(adx_rising),"fractal_arrow":fractal["arrow"] if fractal else None,
            "fractal_time":fractal["time"] if fractal else None,"plus_di":round(plus,2),
            "minus_di":round(minus,2),"adx":round(adx,2) if adx is not None else None,
            "di_gap":round(gap,2),"di_gap_previous":round(prior_gap,2),
            "spread_velocity":round(velocity,3),"spread_acceleration":round(acceleration,3),
            "gap_velocity":round(gap_delta,3),"gap_acceleration":round(gap_accel,3),
            "projected_cross_bars":round(cross_eta,2) if cross_eta is not None else None,
            "early_x_warning":bool(early_warning),"early_x_status":early_status,
            "early_x_direction":approach_direction if early_warning else "WAIT","early_x_score":int(early_score),
            "desire_agent":"DESIRE","desire_level":"SUPERIOR" if desire_superior else ("EARLY WATCH" if early_warning else "SCANNING"),
            "desire_superior":desire_superior,"desire_direction":desire_direction,"desire_score":int(setup),
            "desire_probability":int(setup),"desire_probability_label":label,
            "desire_probability_note":"Uncalibrated setup-confidence grade, not a measured win probability.",
            "desire_message":message,"desire_setup_strength":int(setup),"desire_signal_threshold":90,
            "desire_signal_status":"STRONG SETUP — SIGNAL ELIGIBLE" if desire_superior else "WAIT — NEED VERIFIED CONFLUENCE",
            "plus_di_boxes":pboxes,"minus_di_boxes":mboxes,"di_box_ratio":f"{pboxes}:{mboxes}",
            "signal":"WAIT","godmode_signal":godmode
        })
        if sonic=="WAIT":
            base.update(confirmation_level="WAIT",agreement="SONIC NOT CONFIRMED",
                        reason=f"{x_status}; waiting for a matching fractal and DI setup.")
            return base
        base["signal"]=sonic
        if godmode==sonic:
            base.update(agreement="SONIC + GODMODE AGREE",confirmation_level="DIRECTIONAL AGREEMENT",
                        confirmation_adjustment=4 if strong_x else 2)
        elif godmode=="WAIT":
            base.update(agreement="SONIC ONLY",confirmation_level="NO GODMODE CONFIRMATION",confirmation_adjustment=-2)
        else:
            base.update(agreement="DIRECTIONAL CONFLICT",confirmation_level="CONFLICT CAUTION",confirmation_adjustment=-5)
        if not desire_superior:
            base.update(signal="WAIT",alert_eligible=False,confirmation_level="DESIRE HIGH-CONFLUENCE FILTER",
                        reason=f"Desire is monitoring, not triggering: setup grade {setup}/100; actionable alerts require 90+, a strong expanding X, matching GODMODE direction and lower fakeout risk. {early_status}.")
            return base
        candle_id=str(candles[-1].get("time"))
        locked=now<self.lock_until.get(key,0)
        same=self.last_signal_candle.get(key)==candle_id
        base["locked"]=bool(locked or same)
        base["alert_eligible"]=not base["locked"]
        if base["alert_eligible"]:
            self.last_signal[key]=sonic
            self.last_signal_candle[key]=candle_id
            self.lock_until[key]=now+expiration
        elif locked:
            base["reason"]+=f" Duplicate alert suppressed for about {int(self.lock_until[key]-now)}s."
        return base
