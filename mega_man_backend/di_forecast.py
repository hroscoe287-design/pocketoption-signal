"""Cautious short-horizon DI line projection. Forecasts are estimates, not future observations."""
from __future__ import annotations
from math import isfinite

def _slope(values):
    n=len(values)
    if n<2:return 0.0
    xs=list(range(n)); xm=(n-1)/2; ym=sum(values)/n
    den=sum((x-xm)**2 for x in xs)
    return sum((x-xm)*(y-ym) for x,y in zip(xs,values))/den if den else 0.0

def forecast_di(plus_values,minus_values,timeframe_seconds=5):
    """Project recent DI movement 1-3 bars; label broad expansion vs early/tight setup."""
    p=[float(x) for x in plus_values[-6:] if isfinite(float(x))]
    m=[float(x) for x in minus_values[-6:] if isfinite(float(x))]
    if len(p)<4 or len(m)<4:
        return {"available":False,"label":"INSUFFICIENT HISTORY","note":"Need at least four DI observations"}
    ps=_slope(p[-5:]); ms=_slope(m[-5:])
    p_acc=(p[-1]-p[-2])-(p[-2]-p[-3])
    m_acc=(m[-1]-m[-2])-(m[-2]-m[-3])
    spread=p[-1]-m[-1]; spread_slope=ps-ms
    projections=[]
    for step in (1,2,3):
        pp=max(0.0,min(100.0,p[-1]+ps*step+0.5*p_acc*step*step))
        mm=max(0.0,min(100.0,m[-1]+ms*step+0.5*m_acc*step*step))
        projections.append({"bars_ahead":step,"plus":round(pp,2),"minus":round(mm,2),"spread":round(pp-mm,2)})
    future_spread=projections[-1]["spread"]
    expanding=abs(future_spread)>abs(spread)+1.0 and abs(future_spread)>=7.0
    narrowing=abs(future_spread)<abs(spread)-0.5
    cross_in=next((x["bars_ahead"] for x in projections if (spread==0 or x["spread"]==0 or (spread>0)!=(x["spread"]>0))),None)
    if expanding: label,strength="WIDE SETUP DEVELOPING","WIDE"
    elif abs(future_spread)<7.0 and (abs(spread_slope)>=0.35 or cross_in is not None): label,strength="SMALL / EARLY SETUP","SMALL"
    elif narrowing: label,strength="DI SPREAD CONTRACTING","FADING"
    else: label,strength="NO CLEAR SETUP","NEUTRAL"
    direction="UP" if future_spread>0 else "DOWN" if future_spread<0 else "UNDECIDED"
    return {"available":True,"label":label,"size":strength,"projected_direction":direction,
      "current_spread":round(spread,2),"projected_spread_3_bars":round(future_spread,2),
      "plus_slope_per_bar":round(ps,3),"minus_slope_per_bar":round(ms,3),
      "spread_velocity":round(spread_slope,3),"plus_acceleration":round(p_acc,3),
      "minus_acceleration":round(m_acc,3),"cross_in_bars":cross_in,
      "projections":projections,"timeframe_seconds":int(timeframe_seconds),
      "note":"Short-horizon mathematical projection from recent DI values; not a guaranteed future line or trade signal."}
