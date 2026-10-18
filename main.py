from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import math, random, os

app = FastAPI(title="Godmode V4 Pocket Option Signal Lab")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ASSET = os.getenv("ASSET", "EURUSD_otc")
TIMEFRAME = int(os.getenv("TIMEFRAME_SECONDS", "15"))
_rng = random.Random(42)
_price = 1.08420
_bars = []

def seed_bars():
    global _price, _bars
    now = int(datetime.now(timezone.utc).timestamp())
    step = max(5, TIMEFRAME)
    start = now - 120 * step
    p = 1.08420
    bars = []
    for i in range(120):
        drift = math.sin(i / 8) * 0.000035 + _rng.uniform(-0.00009, 0.00009)
        o = p
        c = max(0.5, o + drift)
        h = max(o, c) + _rng.uniform(0.00001, 0.00007)
        l = min(o, c) - _rng.uniform(0.00001, 0.00007)
        bars.append({"time": start + i * step, "open": round(o, 6), "high": round(h, 6), "low": round(l, 6), "close": round(c, 6)})
        p = c
    _bars = bars
    _price = p

seed_bars()

def ema(values, period):
    if not values: return []
    alpha = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]: out.append(alpha * v + (1 - alpha) * out[-1])
    return out

def rsi(values, period=14):
    if len(values) < period + 1: return 50.0
    changes = [values[i] - values[i-1] for i in range(1, len(values))]
    gains = sum(max(x, 0) for x in changes[-period:]) / period
    losses = sum(max(-x, 0) for x in changes[-period:]) / period
    if losses == 0: return 100.0 if gains else 50.0
    return 100 - (100 / (1 + gains / losses))

def snapshot():
    global _price
    step = max(5, TIMEFRAME)
    now = int(datetime.now(timezone.utc).timestamp())
    last = _bars[-1]
    # Advance the current demo candle gradually. This is synthetic demo data, not a broker feed.
    if now >= last["time"] + step:
        o = last["close"]
        move = math.sin(now / 17) * 0.000025 + _rng.uniform(-0.00007, 0.00007)
        c = max(0.5, o + move)
        _bars.append({"time": last["time"] + step, "open": o, "high": max(o,c), "low": min(o,c), "close": c})
        if len(_bars) > 180: _bars.pop(0)
    else:
        move = math.sin(now / 7) * 0.000006 + _rng.uniform(-0.000004, 0.000004)
        last["close"] = round(max(0.5, last["close"] + move), 6)
        last["high"] = round(max(last["high"], last["close"]), 6)
        last["low"] = round(min(last["low"], last["close"]), 6)
    closes = [b["close"] for b in _bars]
    fast, slow = ema(closes, 9), ema(closes, 21)
    macd_line = [a-b for a,b in zip(ema(closes,12), ema(closes,26))]
    macd_signal = ema(macd_line, 9)
    rv = rsi(closes)
    trend = 1 if fast[-1] > slow[-1] else -1
    mom = 1 if macd_line[-1] > macd_signal[-1] else -1
    score = (1 if trend > 0 else -1) + (1 if mom > 0 else -1) + (1 if rv >= 52 else (-1 if rv <= 48 else 0))
    signal = "BUY" if score >= 2 else ("SELL" if score <= -2 else "WAIT")
    return {
        "asset": ASSET, "timeframe_seconds": step, "price": round(closes[-1], 6),
        "signal": signal, "score": score, "confidence": min(95, 50 + abs(score) * 12),
        "rsi": round(rv, 2), "ema9": round(fast[-1], 6), "ema21": round(slow[-1], 6),
        "macd_bias": "BULLISH" if mom > 0 else "BEARISH",
        "trend": "BULLISH" if trend > 0 else "BEARISH",
        "bars": _bars[-120:], "updated_at": datetime.now(timezone.utc).isoformat(),
        "data_mode": "SYNTHETIC DEMO — NOT LIVE MARKET DATA"
    }

@app.get("/api/market")
def market():
    return snapshot()

@app.get("/health")
def health():
    return {"status": "ok", "mode": "demo"}

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(PAGE)

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GODMODE V4 | Signal Lab</title>
<script src="https://unpkg.com/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root{--bg:#080b13;--panel:#111827;--line:#253047;--muted:#8e9bb3;--text:#eef3ff;--green:#23d6a0;--red:#ff5277;--gold:#ffca67}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top,#17213b 0,#080b13 55%);color:var(--text);font:14px Inter,system-ui,Arial,sans-serif}
header{display:flex;align-items:center;justify-content:space-between;padding:18px 22px;border-bottom:1px solid var(--line);gap:12px;flex-wrap:wrap}
.brand{font-weight:900;letter-spacing:2px;font-size:19px}.brand span{color:var(--gold)}.sub{color:var(--muted);font-size:11px;letter-spacing:1.3px;margin-top:4px}
.pill{border:1px solid #725c2c;background:#2b2417;color:var(--gold);border-radius:30px;padding:8px 12px;font-size:11px;font-weight:800}
main{max-width:1400px;margin:auto;padding:18px;display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:16px}
.panel{background:linear-gradient(145deg,#121a2a,#0d1320);border:1px solid var(--line);border-radius:15px;overflow:hidden;box-shadow:0 12px 40px #0003}
.panelhead{padding:15px 17px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.panelhead h2{margin:0;font-size:13px;letter-spacing:1.3px}
.controls{display:flex;gap:6px}.controls button,select{background:#0a1020;color:var(--text);border:1px solid #34415a;border-radius:8px;padding:8px 10px}
.controls button.active{background:#283650;border-color:var(--gold)}#chart{height:430px;width:100%}
.disclaimer{padding:10px 16px;color:var(--gold);font-size:11px;background:#211c14;border-top:1px solid #4b3a1b}
.side{display:flex;flex-direction:column;gap:14px}.signalbox{padding:20px;text-align:center}.label{font-size:11px;color:var(--muted);letter-spacing:1.4px;font-weight:800}.signal{font-size:34px;font-weight:950;margin:12px 0}.signal.BUY{color:var(--green)}.signal.SELL{color:var(--red)}.signal.WAIT{color:var(--gold)}
.price{font-size:27px;font-weight:800;letter-spacing:.5px;margin:6px 0}.small{color:var(--muted);font-size:11px}.meter{height:7px;background:#263148;border-radius:9px;margin:14px 0;overflow:hidden}.meter div{height:100%;width:0;background:var(--green);transition:width .3s}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:9px;padding:13px}.metric{padding:12px;background:#0a1020;border:1px solid #202c42;border-radius:10px}.metric .v{font-size:17px;font-weight:800;margin-top:7px}.good{color:var(--green)}.bad{color:var(--red)}.gold{color:var(--gold)}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:14px}.action{padding:15px 6px;border-radius:10px;font-weight:900;text-align:center;letter-spacing:1px}.buy{border:1px solid #1c916e;color:var(--green);background:#103126}.sell{border:1px solid #9b3450;color:var(--red);background:#351522}
.foot{max-width:1400px;margin:0 auto;padding:0 18px 24px;color:var(--muted);font-size:11px;line-height:1.6}.history{padding:14px 17px;color:var(--muted);font-size:12px;line-height:1.7}
@media(max-width:850px){main{grid-template-columns:1fr;padding:10px}#chart{height:330px}header{padding:14px}.side{display:grid;grid-template-columns:1fr 1fr}.signalpanel{grid-column:1/-1}.metrics{grid-column:1/-1}.actions{grid-column:1/-1}}@media(max-width:480px){.side{display:flex}.brand{font-size:16px}#chart{height:290px}}
</style></head>
<body><header><div><div class="brand">GODMODE <span>V4</span> / SIGNAL LAB</div><div class="sub">POCKET OPTION • MULTI-TIMEFRAME RESEARCH TERMINAL</div></div><div class="pill">● SIGNAL-ONLY MODE</div></header>
<main><section class="panel"><div class="panelhead"><h2>PRICE ACTION / CANDLESTICKS</h2><div class="controls"><button class="active" data-tf="5">5s</button><button data-tf="15">15s</button><button data-tf="60">1m</button><button data-tf="300">5m</button></div><select id="asset"><option>EURUSD_otc</option><option>GBPUSD_otc</option><option>USDJPY_otc</option><option>AUDUSD_otc</option><option>EURUSD</option></select></div><div id="chart"></div><div class="disclaimer">DEMO FEED: synthetic candles only. Do not treat these as live prices or trading recommendations.</div></section>
<aside class="side"><section class="panel signalpanel"><div class="signalbox"><div class="label">CURRENT CONFLUENCE SIGNAL</div><div id="signal" class="signal WAIT">WAIT</div><div id="price" class="price">—</div><div id="assetname" class="small">EURUSD_otc</div><div class="meter"><div id="meter"></div></div><div class="small">Illustrative confluence score: <b id="confidence">—</b></div></div><div class="actions"><div class="action buy">BUY / CALL</div><div class="action sell">SELL / PUT</div></div></section>
<section class="panel"><div class="panelhead"><h2>INDICATOR CONFIRMATION</h2><span class="small">LIVE UPDATE</span></div><div class="metrics"><div class="metric"><div class="label">EMA 9 / 21</div><div id="trend" class="v">—</div></div><div class="metric"><div class="label">RSI (14)</div><div id="rsi" class="v">—</div></div><div class="metric"><div class="label">MACD BIAS</div><div id="macd" class="v">—</div></div><div class="metric"><div class="label">SCORE</div><div id="score" class="v">—</div></div></div></section>
<section class="panel"><div class="panelhead"><h2>SESSION STATUS</h2></div><div class="history"><div>Feed status: <b class="gold">DEMO MODE</b></div><div>Automatic execution: <b>DISABLED</b></div><div>Last refresh: <span id="updated">—</span></div><div>Strategy: EMA + RSI + MACD</div></div></section></aside></main>
<div class="foot">This is an original experimental signal prototype, not the proprietary Godmode V4 algorithm. Demo prices are synthetic; signal strength is not a probability of winning. Binary-options trading carries a high risk of loss. Connect only a permitted, documented market-data source before evaluating real signals.</div>
<script>
const chartEl=document.getElementById('chart');let chart=null,candleSeries=null,selectedTF=15;
function initChart(){if(!window.LightweightCharts){chartEl.innerHTML='<div style="padding:30px;color:#ffca67">Chart library could not load. Check your connection and refresh.</div>';return}
chart=LightweightCharts.createChart(chartEl,{layout:{background:{color:'#0d1320'},textColor:'#aab7cc'},grid:{vertLines:{color:'#1b263a'},horzLines:{color:'#1b263a'}},rightPriceScale:{borderColor:'#29364c'},timeScale:{borderColor:'#29364c',timeVisible:true,secondsVisible:true},crosshair:{mode:0}});
candleSeries=chart.addCandlestickSeries({upColor:'#23d6a0',downColor:'#ff5277',borderUpColor:'#23d6a0',borderDownColor:'#ff5277',wickUpColor:'#23d6a0',wickDownColor:'#ff5277'});new ResizeObserver(()=>chart.applyOptions({width:chartEl.clientWidth})).observe(chartEl)}
async function refresh(){try{const r=await fetch('/api/market?tf='+selectedTF+'&asset='+encodeURIComponent(document.getElementById('asset').value));const d=await r.json();if(candleSeries){candleSeries.setData(d.bars);chart.timeScale().fitContent()}
const s=document.getElementById('signal');s.textContent=d.signal;s.className='signal '+d.signal;document.getElementById('price').textContent=Number(d.price).toFixed(6);document.getElementById('assetname').textContent=d.asset+' • '+d.timeframe_seconds+'s candles';document.getElementById('confidence').textContent=d.confidence+'/100 score';document.getElementById('meter').style.width=d.confidence+'%';document.getElementById('meter').style.background=d.signal==='SELL'?'#ff5277':d.signal==='BUY'?'#23d6a0':'#ffca67';document.getElementById('trend').textContent=d.trend;document.getElementById('trend').className='v '+(d.trend==='BULLISH'?'good':'bad');document.getElementById('rsi').textContent=d.rsi;document.getElementById('rsi').className='v '+(d.rsi>70||d.rsi<30?'gold':'');document.getElementById('macd').textContent=d.macd_bias;document.getElementById('macd').className='v '+(d.macd_bias==='BULLISH'?'good':'bad');document.getElementById('score').textContent=(d.score>0?'+':'')+d.score;document.getElementById('updated').textContent=new Date(d.updated_at).toLocaleTimeString() }catch(e){document.getElementById('updated').textContent='API reconnecting'}}
document.querySelectorAll('[data-tf]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-tf]').forEach(x=>x.classList.remove('active'));b.classList.add('active');selectedTF=Number(b.dataset.tf);refresh()});document.getElementById('asset').onchange=refresh;initChart();refresh();setInterval(refresh,3000);
</script></body></html>"""
