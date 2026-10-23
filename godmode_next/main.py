from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pathlib import Path
import time
import os
from engine import analyze
from feed import feed

ROOT=Path(__file__).parent
DEFAULT_ASSET=os.getenv("PO_DEFAULT_ASSET", "EURUSD_otc")
DEFAULT_TIMEFRAME_SECONDS=int(os.getenv("PO_DEFAULT_TIMEFRAME_SECONDS", "5"))
@asynccontextmanager
async def lifespan(app):
    feed.start()
    yield
app=FastAPI(title="GODMODE NEXT",version="0.2.0",lifespan=lifespan)
STARTED=time.time()
@app.get("/",response_class=HTMLResponse)
def home():return (ROOT/"index.html").read_text(encoding="utf-8")
@app.get("/api/health")
def health():
    return {"ok":True,"app":"GODMODE NEXT","mode":"signal-only","automatic_execution":False,
      "live_feed_verified":False,"uptime_seconds":round(time.time()-STARTED,1),"timestamp":datetime.now(timezone.utc).isoformat()}
@app.get("/api/diagnostics")
def diagnostics(asset:str=DEFAULT_ASSET,tf:int=DEFAULT_TIMEFRAME_SECONDS):
    result=feed.snapshot(asset,tf)
    return {"ok":result["verified"],"status":result["status"],"requested_asset":asset,
      "timeframe_seconds":tf,"bar_count":result["bar_count"],"latest_bar":result["latest_bar"],
      "feed":{"provider":"Pocket Option","configured":bool(feed._auth()),"connected":feed.client is not None,
        "verified":result["verified"],"reason":result["status"]},"debug":result["debug"],
      "signals_enabled":result["verified"],"automatic_execution":False}
@app.get("/api/market")
def market(asset:str=DEFAULT_ASSET,tf:int=DEFAULT_TIMEFRAME_SECONDS):
    return feed.snapshot(asset,tf)
@app.get("/api/signal")
def signal(asset:str=DEFAULT_ASSET,tf:int=DEFAULT_TIMEFRAME_SECONDS):
    market=feed.snapshot(asset,tf)
    if not market["verified"]:
        return {"direction":"WAIT","score":0,"eligible":False,"ready":False,
          "reason":"Live feed unverified: "+market["status"],"components":{},"signals_enabled":False}
    result=analyze(market["candles"])
    return {**result,"signals_enabled":bool(result.get("eligible")),"asset":asset,"timeframe_seconds":tf,
        "feed_verified":True,"latest_candle_time":market["latest_bar"]["time"]}
@app.post("/api/analyze")
def analyze_request(payload:dict):
    # Client-supplied candles are never trusted for live signals.
    return signal(str(payload.get("asset",DEFAULT_ASSET)),int(payload.get("tf",DEFAULT_TIMEFRAME_SECONDS)))
