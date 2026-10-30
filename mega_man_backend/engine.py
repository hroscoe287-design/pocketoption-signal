"""Transparent technical confluence. Score is NOT a win probability."""
from __future__ import annotations
from math import isfinite
from di_forecast import forecast_di


def ema(values, period):
    if not values or period < 1:return []
    alpha=2.0/(period+1.0); out=[float(values[0])]
    for v in values[1:]:out.append(alpha*float(v)+(1-alpha)*out[-1])
    return out


def sma(values,period):
    return [sum(values[max(0,i-period+1):i+1])/len(values[max(0,i-period+1):i+1]) for i in range(len(values))]


def rsi(values,period=14):
    if len(values)<=period:return None
    changes=[float(b)-float(a) for a,b in zip(values[:-1],values[1:])]
    gains=[max(v,0) for v in changes];losses=[max(-v,0) for v in changes]
    ag=sum(gains[-period:])/period;al=sum(losses[-period:])/period
    if al==0:return 100.0 if ag else 50.0
    return 100-100/(1+ag/al)


def analyze(candles,min_score=55,timeframe_seconds=5):
    if len(candles)<60:
        return {"direction":"WAIT","score":0,"ready":False,"eligible":False,"reason":"Need 60 verified candles for stable indicator context","components":{}}
    try:
        close=[float(c["close"]) for c in candles];high=[float(c["high"]) for c in candles];low=[float(c["low"]) for c in candles]
        if not all(isfinite(v) for v in close+high+low):raise ValueError()
        if any(lo<=0 or hi<lo or hi<max(o,c) or lo>min(o,c) for lo,hi,o,c in zip(low,high,[float(x["open"]) for x in candles],close)):raise ValueError()
    except (ValueError,TypeError,KeyError,OverflowError):
        return {"direction":"WAIT","score":0,"ready":False,"eligible":False,"reason":"Malformed candles","components":{}}
    e9,e21=ema(close,9),ema(close,21)
    rv=rsi(close,14)
    # MACD 12/26/9
    macd=[a-b for a,b in zip(ema(close,12),ema(close,26))]
    ms=ema(macd,9); hist=macd[-1]-ms[-1]
    # Directional movement, DI length 7 and Wilder smoothing 14.
    plus=[];minus=[];tr=[]
    for i in range(1,len(close)):
        up=high[i]-high[i-1];dn=low[i-1]-low[i]
        plus.append(up if up>dn and up>0 else 0.0);minus.append(dn if dn>up and dn>0 else 0.0)
        tr.append(max(high[i]-low[i],abs(high[i]-close[i-1]),abs(low[i]-close[i-1])))
    def wilder(xs,n):
        if len(xs)<n:return []
        val=sum(xs[:n])/n;out=[val]
        for v in xs[n:]:val=(val*(n-1)+v)/n;out.append(val)
        return out
    p7,m7,t7=wilder(plus,7),wilder(minus,7),wilder(tr,7)
    p14,m14,t14=wilder(plus,14),wilder(minus,14),wilder(tr,14)
    if not p7 or not m7 or not t7 or not t7[-1] or not p14 or not m14 or not t14 or not t14[-1]:
        return {"direction":"WAIT","score":0,"ready":False,"eligible":False,"reason":"Insufficient directional-movement data","components":{}}
    dip_series=[100*x/y for x,y in zip(p7,t7) if y]
    dim_series=[100*x/y for x,y in zip(m7,t7) if y]
    dip,dim=100*p7[-1]/t7[-1],100*m7[-1]/t7[-1]
    di_spread=dip-dim
    prev_spread=(100*p7[-2]/t7[-2]-100*m7[-2]/t7[-2]) if len(p7)>1 and t7[-2] else di_spread
    di_velocity=di_spread-prev_spread
    # On very short charts, react to direction changes, not just line position.
    # Use a short multi-bar slope so a single tiny fluctuation is not over-read.
    di_plus_delta=(dip_series[-1]-dip_series[-2]) if len(dip_series)>1 else 0.0
    di_minus_delta=(dim_series[-1]-dim_series[-2]) if len(dim_series)>1 else 0.0
    di_plus_falling_two_bars=(len(dip_series)>=3 and dip_series[-1] < dip_series[-2] < dip_series[-3])
    di_minus_rising_two_bars=(len(dim_series)>=3 and dim_series[-1] > dim_series[-2] > dim_series[-3])
    macd_hist_series=[a-b for a,b in zip(macd,ms)]
    macd_fading=(len(macd_hist_series)>=3 and macd_hist_series[-1] < macd_hist_series[-2] < macd_hist_series[-3])
    di_plus_turning_down=di_plus_delta < -0.03 or di_plus_falling_two_bars
    di_accel=di_velocity-((prev_spread-(100*p7[-3]/t7[-3]-100*m7[-3]/t7[-3])) if len(p7)>2 and t7[-3] else di_velocity)
    dip14,dim14=100*p14[-1]/t14[-1],100*m14[-1]/t14[-1]
    dx=[100*abs(a-b)/(a+b) if a+b else 0 for a,b in zip([100*x/y if y else 0 for x,y in zip(p14,t14)],[100*x/y if y else 0 for x,y in zip(m14,t14)])]
    adx_series=wilder(dx,14);adx=adx_series[-1] if adx_series else 0
    # Volatility context: ATR(14) normalized by price, plus latest candle body/range.
    true_ranges=[max(high[i]-low[i],abs(high[i]-close[i-1]),abs(low[i]-close[i-1])) for i in range(1,len(close))]
    atr14=sum(true_ranges[-14:])/min(14,len(true_ranges)) if true_ranges else 0.0
    volatility_pct=(atr14/close[-1])*100 if close[-1] else 0.0
    candle_range=max(0.0,high[-1]-low[-1]); candle_body=abs(close[-1]-float(candles[-1]["open"]))
    body_ratio=candle_body/candle_range if candle_range else 0.0
    # Williams Alligator-inspired smoothed median lines (unshifted for causal confirmation).
    median=[(h+l)/2 for h,l in zip(high,low)]
    jaw=sma(median,13)[-1];teeth=sma(median,8)[-1];lips=sma(median,5)[-1]
    # WaveTrend-style oscillator.
    typical=[(h+l+c)/3 for h,l,c in zip(high,low,close)]
    esa=ema(typical,10);dev=ema([abs(v-e) for v,e in zip(typical,esa)],10)
    ci=[(typical[i]-esa[i])/(.015*dev[i]) if dev[i]>1e-12 else 0 for i in range(len(typical))]
    wt1=ema(ci,21);wt2=sma(wt1,4)
    # Period-3 fractal: three bars on either side; only the most recent confirmed fractal is used.
    fh=any(high[-4] > max(high[-7:-4]+high[-3:]) for _ in [0]) if len(high)>=7 else False
    fl=any(low[-4] < min(low[-7:-4]+low[-3:]) for _ in [0]) if len(low)>=7 else False
    votes={
      "ema_9_21": 1 if e9[-1]>e21[-1] else -1 if e9[-1]<e21[-1] else 0,
      "di_spread":1 if di_spread>0 else -1 if di_spread<0 else 0,
      "macd":1 if hist>0 else -1 if hist<0 else 0,
      "rsi":1 if rv is not None and rv>=52 else -1 if rv is not None and rv<=48 else 0,
      "wavetrend":1 if wt1[-1]>wt2[-1] else -1 if wt1[-1]<wt2[-1] else 0,
      "alligator":1 if lips>teeth>jaw else -1 if lips<teeth<jaw else 0,
    }
    weights={"ema_9_21":20,"di_spread":20,"macd":15,"rsi":10,"wavetrend":10,"alligator":10,"adx":10,"fractal":5}
    bull=sum(weights[k] for k,v in votes.items() if v==1)
    bear=sum(weights[k] for k,v in votes.items() if v==-1)
    direction=1 if bull>bear else -1 if bear>bull else 0
    score=(bull if direction==1 else bear if direction==-1 else 0)
    # ADX confirms trend quality; it never chooses direction by itself.
    if adx<15:score=max(0,score-10)
    elif adx>=20:score=min(100,score+5)
    fractal_direction=1 if fl and close[-1]>high[-4] else -1 if fh and close[-1]<low[-4] else 0
    if fractal_direction==direction and direction:score=min(100,score+5)
    score=min(100,max(0,score))
    bullish_di_rollover_block=bool(timeframe_seconds<=5 and direction==1 and (
        di_plus_turning_down or (di_minus_rising_two_bars and macd_fading)
    ))
    # On 5s, block UP when DI+ rolls over; also block if DI- is climbing
    # for two bars while MACD momentum fades. Wait for fresh confirmation.
    if bullish_di_rollover_block:
        direction=0
        score=0
    early_cross=(prev_spread<=0<di_spread) if direction==1 else ((prev_spread>=0>di_spread) if direction==-1 else False)
    # Avoid late entries and DI whipsaws: require spread direction to reinforce the signal.
    fakeout=bool(direction and (abs(di_spread)<0.6 or (di_velocity*direction<0 and abs(di_spread)<1.8 and not early_cross)))
    eligible=bool(direction and score>=min_score and adx>=8 and not fakeout)
    reason=("5s DI+ turning downward — bullish signal blocked" if bullish_di_rollover_block else "Four-stage confluence threshold met" if eligible else "DI fakeout risk — WAIT" if fakeout else "Trend strength too weak (ADX < 8)" if adx<8 else f"Confluence below {min_score}/100" if score<min_score else "No directional agreement")
    market_context=f"Verified candles: {len(candles)}; EMA trend {'bullish' if e9[-1]>e21[-1] else 'bearish' if e9[-1]<e21[-1] else 'flat'}"
    volatility_context=f"ATR14 {atr14:.6g} ({volatility_pct:.4f}% of price); latest candle body/range {body_ratio:.2f}"
    pattern_context=f"DI spread {di_spread:.2f}, velocity {di_velocity:.2f}; MACD {'positive' if hist>0 else 'negative' if hist<0 else 'flat'}; fractal confirmation {'yes' if fractal_direction==direction and direction else 'no'}"
    final_context=f"{'BUY/CALL' if direction==1 else 'SELL/PUT' if direction==-1 else 'WAIT'}; score {score}/100; {'eligible for signal' if eligible else reason}"
    di_projection=forecast_di(dip_series,dim_series,timeframe_seconds)
    return {"direction":"BUY/CALL" if direction==1 else "SELL/PUT" if direction==-1 else "WAIT",
      "score":score,"ready":True,"eligible":eligible,"reason":reason,
      "stages":[
        {"name":"Market Context","status":"complete","summary":market_context},
        {"name":"Volatility Analysis","status":"complete","summary":volatility_context},
        {"name":"Pattern Matching","status":"complete","summary":pattern_context},
        {"name":"Finalizing Signal","status":"complete" if eligible else "blocked","summary":final_context}
      ],
      "components":{"atr14":round(atr14,8),"volatility_pct":round(volatility_pct,5),"latest_candle_body_ratio":round(body_ratio,3),"ema9":round(e9[-1],6),"ema21":round(e21[-1],6),"rsi14":round(rv,2) if rv is not None else None,
      "macd_histogram":round(hist,7),"wave_trend_1":round(wt1[-1],3),"wave_trend_2":round(wt2[-1],3),
      "alligator_jaw":round(jaw,6),"alligator_teeth":round(teeth,6),"alligator_lips":round(lips,6),
      "plus_di_7":round(dip,3),"minus_di_7":round(dim,3),"plus_di_14":round(dip14,3),"minus_di_14":round(dim14,3),
      "adx_14":round(adx,3),"di_spread":round(di_spread,3),"spread_velocity":round(di_velocity,3),
      "spread_acceleration":round(di_accel,3),"di_plus_delta":round(di_plus_delta,3),
      "di_minus_delta":round(di_minus_delta,3),"di_plus_turning_down":di_plus_turning_down,
      "di_plus_falling_two_bars":di_plus_falling_two_bars,"di_minus_rising_two_bars":di_minus_rising_two_bars,
      "macd_fading":macd_fading,"di_forecast":di_projection,
      "bullish_di_rollover_block":bullish_di_rollover_block,"timeframe_seconds":timeframe_seconds,
      "early_cross":early_cross,"fractal_high_confirmed":fh,
      "fractal_low_confirmed":fl,"fakeout_risk":fakeout,"votes":votes}}
