"""Extra Godmode-family confluence indicators. Pure Python; no external dependencies."""
from math import sqrt

def _ema(xs, period):
    if not xs: return []
    a=2.0/(period+1.0); out=[float(xs[0])]
    for x in xs[1:]: out.append(a*float(x)+(1-a)*out[-1])
    return out

def _rsi(xs, period=14):
    if len(xs)<period+1: return 50.0
    ds=[float(xs[i])-float(xs[i-1]) for i in range(1,len(xs))]
    g=sum(max(x,0) for x in ds[-period:])/period
    l=sum(max(-x,0) for x in ds[-period:])/period
    return 100.0 if l==0 and g>0 else (50.0 if l==g==0 else (0.0 if g==0 else 100-100/(1+g/l)))

def analyze(bars):
    """Return conservative directional votes plus transparent component readings."""
    if len(bars)<35:
        return {"vote":0,"components":{},"ready":False}
    c=[float(b["close"]) for b in bars]; h=[float(b["high"]) for b in bars]; lo=[float(b["low"]) for b in bars]
    # True TSI-style double-smoothed momentum.
    delta=[0.0]+[c[i]-c[i-1] for i in range(1,len(c))]
    absdelta=[abs(x) for x in delta]
    tsi_num=_ema(_ema(delta,13),7)[-1]
    tsi_den=_ema(_ema(absdelta,13),7)[-1]
    tsi=100*tsi_num/tsi_den if tsi_den>1e-12 else 0.0
    tsi_prev_num=_ema(_ema(delta[:-1],13),7)[-1]
    tsi_prev_den=_ema(_ema(absdelta[:-1],13),7)[-1]
    tsi_prev=100*tsi_prev_num/tsi_prev_den if tsi_prev_den>1e-12 else 0.0
    tsi_vote=1 if tsi>0 and tsi>=tsi_prev else (-1 if tsi<0 and tsi<=tsi_prev else 0)
    # MFI-inspired money flow using typical price and candle range/volume proxy.
    tp=[(h[i]+lo[i]+c[i])/3 for i in range(len(c))]
    pos=neg=0.0
    for i in range(max(1,len(c)-14),len(c)):
        flow=tp[i]*max(h[i]-lo[i],abs(c[i]-c[i-1]))
        if tp[i]>tp[i-1]: pos+=flow
        elif tp[i]<tp[i-1]: neg+=flow
    mfi=100*pos/(pos+neg) if pos+neg else 50.0
    mfi_vote=1 if mfi>=55 else (-1 if mfi<=45 else 0)
    # Williams %R (Willy) over 14 bars.
    hh=max(h[-14:]); ll=min(lo[-14:])
    willy=-50.0 if hh==ll else -100*(hh-c[-1])/(hh-ll)
    willy_vote=1 if willy>=-50 and willy>-80 else (-1 if willy<=-50 and willy<-20 else 0)
    # Laguerre-like RSI approximation using a smoothed RSI series.
    rsis=[_rsi(c[:i+1],7) for i in range(max(8,len(c)-8),len(c)+1)]
    lag=_ema(rsis,4)[-1] if rsis else 50.0
    lag_vote=1 if lag>=55 else (-1 if lag<=45 else 0)
    # Bollinger width and range expansion flag; squeeze is context, not direction.
    window=c[-20:]; mean=sum(window)/20
    sd=sqrt(sum((x-mean)**2 for x in window)/20)
    width=(4*sd/abs(mean)) if mean else 0.0
    prior=c[-40:-20]
    if len(prior)==20:
        pm=sum(prior)/20; ps=sqrt(sum((x-pm)**2 for x in prior)/20)
        squeeze=width < (4*ps/abs(pm) if pm else 0.0)
    else: squeeze=False
    # Candle body confirmation avoids treating a tiny/indecisive candle as a strong vote.
    rng=max(1e-12,h[-1]-lo[-1]); body=(c[-1]-float(bars[-1]["open"]))/rng
    candle_vote=1 if body>=0.35 else (-1 if body<=-0.35 else 0)
    # Multi-horizon trend agreement using available candle history, not fake higher-timeframe data.
    e8=_ema(c,8)[-1]; e21=_ema(c,21)[-1]; e34=_ema(c,34)[-1]
    mtf_vote=1 if e8>e21>e34 else (-1 if e8<e21<e34 else 0)
    votes=[tsi_vote,mfi_vote,willy_vote,lag_vote,candle_vote,mtf_vote]
    return {"vote":sum(votes),"ready":True,"components":{
        "tsi":round(tsi,2),"tsi_vote":tsi_vote,"mfi_proxy":round(mfi,2),"mfi_vote":mfi_vote,
        "williams_r":round(willy,2),"williams_vote":willy_vote,"laguerre_rsi_proxy":round(lag,2),
        "laguerre_vote":lag_vote,"bollinger_width":round(width,6),"squeeze":bool(squeeze),
        "candle_body_ratio":round(body,3),"candle_vote":candle_vote,"multi_horizon_trend_vote":mtf_vote
    }}
