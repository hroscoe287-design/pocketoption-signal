"""Pure, testable signal analysis for GODMODE NEXT. Never executes trades."""
from __future__ import annotations
from math import isfinite


def ema(values, period):
    if not values or period < 1:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1-alpha) * out[-1])
    return out


def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = [], []
    for a, b in zip(values[-period-1:-1], values[-period:]):
        delta = float(b)-float(a)
        gains.append(max(delta, 0.0)); losses.append(max(-delta, 0.0))
    avg_gain=sum(gains)/period; avg_loss=sum(losses)/period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100.0 - 100.0/(1.0+avg_gain/avg_loss)


def analyze(candles, min_score=90):
    """Score confluence transparently; requires enough real closed candles.

    Output is WAIT unless caller explicitly marks data verified and fresh.
    Score is confluence, not probability of winning.
    """
    if len(candles) < 35:
        return {"direction":"WAIT","score":0,"ready":False,"reason":"Need at least 35 closed candles","components":{}}
    try:
        close=[float(x["close"]) for x in candles]
        high=[float(x["high"]) for x in candles]
        low=[float(x["low"]) for x in candles]
        if not all(isfinite(v) for v in close+high+low):
            raise ValueError("non-finite candle values")
    except (KeyError, TypeError, ValueError):
        return {"direction":"WAIT","score":0,"ready":False,"reason":"Malformed candles","components":{}}
    e9, e21=ema(close,9), ema(close,21)
    r=rsi(close,14)
    trend=1 if e9[-1]>e21[-1] else (-1 if e9[-1]<e21[-1] else 0)
    momentum=1 if close[-1]>close[-2] else (-1 if close[-1]<close[-2] else 0)
    # DI estimates from directional movement, Wilder-style smoothing.
    plus=[]; minus=[]; tr=[]
    for i in range(1,len(candles)):
        up=high[i]-high[i-1]; down=low[i-1]-low[i]
        plus.append(up if up>down and up>0 else 0.0)
        minus.append(down if down>up and down>0 else 0.0)
        tr.append(max(high[i]-low[i],abs(high[i]-close[i-1]),abs(low[i]-close[i-1])))
    def smooth(xs,n):
        if len(xs)<n:return []
        val=sum(xs[:n])/n; out=[val]
        for x in xs[n:]:
            val=(val*(n-1)+x)/n; out.append(val)
        return out
    p,m,t=smooth(plus,7),smooth(minus,7),smooth(tr,7)
    if not p or not m or not t or t[-1] == 0:
        return {"direction":"WAIT","score":0,"ready":False,"reason":"Insufficient directional movement history","components":{}}
    dip,dim=100*p[-1]/t[-1],100*m[-1]/t[-1]
    spread=dip-dim
    prev_spread=(100*p[-2]/t[-2]-100*m[-2]/t[-2]) if len(p)>1 and len(t)>1 and t[-2] else spread
    accel=spread-prev_spread
    votes=[trend, momentum, 1 if spread>0 else (-1 if spread<0 else 0),
           1 if r is not None and 50<r<75 else (-1 if r is not None and 25<r<50 else 0)]
    direction=1 if sum(votes)>0 else (-1 if sum(votes)<0 else 0)
    aligned=sum(1 for v in votes if v==direction) if direction else 0
    # Deliberately conservative, transparent score; no win-probability language.
    score=round(100*aligned/len(votes)) if direction else 0
    early_cross=(prev_spread<=0<spread) if direction==1 else ((prev_spread>=0>spread) if direction==-1 else False)
    return {"direction":"BUY/CALL" if direction==1 else ("SELL/PUT" if direction==-1 else "WAIT"),
      "score":score,"ready":True,"eligible":score>=min_score,
      "reason":"Confluence threshold met" if score>=min_score else "Confluence below threshold",
      "components":{"ema9":round(e9[-1],6),"ema21":round(e21[-1],6),"rsi14":round(r,2) if r is not None else None,
      "plus_di_7":round(dip,3),"minus_di_7":round(dim,3),"di_spread":round(spread,3),
      "spread_velocity":round(spread-prev_spread,3),"spread_acceleration":round(accel,3),"early_cross":early_cross}}
