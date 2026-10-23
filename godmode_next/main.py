from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pathlib import Path
import os, time
from engine import analyze

app=FastAPI(title="GODMODE NEXT",version="0.1.0")
STARTED=time.time()
ROOT=Path(__file__).parent

@app.get("/",response_class=HTMLResponse)
def home():
    return (ROOT/"index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health():
    return {"ok":True,"app":"GODMODE NEXT","mode":"signal-only","automatic_execution":False,
      "live_feed_verified":False,"uptime_seconds":round(time.time()-STARTED,1),
      "timestamp":datetime.now(timezone.utc).isoformat()}

@app.get("/api/diagnostics")
def diagnostics(asset:str="EURUSD_otc",tf:int=5):
    # No feed is claimed until a separately validated provider adapter is installed.
    return {"ok":False,"status":"LIVE_FEED_NOT_CONFIGURED","requested_asset":asset,
      "timeframe_seconds":tf,"bar_count":0,"latest_bar":None,
      "feed":{"provider":"Pocket Option","configured":bool(os.getenv("PO_AUTH_JSON")),
      "connected":False,"verified":False,"reason":"Isolated rebuild scaffold has no live adapter enabled"},
      "signals_enabled":False,"automatic_execution":False}

@app.post("/api/analyze")
def analyze_request(payload:dict):
    bars=payload.get("candles",[])
    verified=payload.get("live_verified") is True and payload.get("fresh") is True
    result=analyze(bars)
    if not verified:
        return {**result,"direction":"WAIT","eligible":False,"signals_enabled":False,
          "reason":"Live feed not independently verified; signal blocked"}
    return {**result,"signals_enabled":bool(result.get("eligible"))}
