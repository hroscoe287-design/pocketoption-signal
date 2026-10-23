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
@app.get("/api/assets")
def assets():
    """Return the connected broker's actual symbol catalog for the UI dropdown."""
    client=feed.client
    if client is None:
        return {"live":False,"status":feed.status,"count":0,"assets":[]}
    try:
        raw=client.get_assets() or {}
        symbols=set()
        def add(value):
            if isinstance(value,str):
                value=value.strip()
                if value and not value.isdigit(): symbols.add(value)
            elif isinstance(value,dict):
                for field in ("symbol","name","asset","ticker","active_symbol","instrument"):
                    item=value.get(field)
                    if isinstance(item,str) and item.strip(): symbols.add(item.strip())
        if isinstance(raw,dict):
            for key,value in raw.items():
                add(key)
                add(value)
        elif isinstance(raw,(list,tuple,set)):
            for item in raw: add(item)
        try: connected=bool(client.check_connect())
        except Exception: connected=False
        ordered=sorted(symbols,key=str.casefold)
        return {"live":connected and bool(ordered),"status":feed.status,"count":len(ordered),"assets":ordered}
    except Exception as exc:
        return {"live":False,"status":"Broker catalog unavailable: "+type(exc).__name__,"count":0,"assets":[]}

@app.post("/api/analyze")
def analyze_request(payload:dict):
    # Client-supplied candles are never trusted for live signals.
    return signal(str(payload.get("asset",DEFAULT_ASSET)),int(payload.get("tf",DEFAULT_TIMEFRAME_SECONDS)))

# Uploaded-chart visual analysis. This endpoint is advisory only and never places trades.
from pydantic import BaseModel, Field
import base64, json, re, urllib.request, urllib.error

class ChartImageRequest(BaseModel):
    image_data: str = Field(min_length=32, max_length=12_000_000)
    question: str = Field(default="Analyze this trading chart", max_length=600)

@app.post("/api/chart-analysis")
def chart_image_analysis(payload: ChartImageRequest):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Screenshot AI is not configured: add OPENAI_API_KEY to this Render service's environment variables.")
    match = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$", payload.image_data)
    if not match:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG, or WEBP chart screenshot.")
    mime, encoded = match.group(1), match.group(2)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Image data is invalid.")
    if len(raw) > 8 * 1024 * 1024:
        from fastapi import HTTPException
        raise HTTPException(status_code=413, detail="Image must be 8 MB or smaller.")
    prompt = (
      "You are a cautious technical chart analyst. Inspect only what is visible in this uploaded screenshot. "
      "Do not invent prices, indicators, timeframe, asset, or data that cannot be read. This is screenshot-only analysis, "
      "not a live feed. Return valid JSON with keys: direction (BUY, SELL, or WAIT), confidence (0-100 as a qualitative "
      "setup-strength score, never a win probability), chart_readability, asset, timeframe, trend, momentum, visible_indicators, "
      "support_resistance, bullish_evidence (array), bearish_evidence (array), risks (array), invalidation, summary. "
      "If the image is unclear, conflicting, missing context, or insufficient for a responsible directional view, direction must be WAIT. "
      "Never promise profits. Do not provide fabricated entry/stop/target prices. Automatic trading is disabled. "
      "User's question: " + payload.question
    )
    body = {"model": os.getenv("CHART_VISION_MODEL", "gpt-4o-mini"),
      "messages":[{"role":"user","content":[{"type":"text","text":prompt},
      {"type":"image_url","image_url":{"url":"data:"+mime+";base64,"+encoded,"detail":"high"}}]}],
      "temperature":0.1,"max_tokens":900,"response_format":{"type":"json_object"}}
    request = urllib.request.Request("https://api.openai.com/v1/chat/completions",
      data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json"},method="POST")
    try:
      with urllib.request.urlopen(request, timeout=45) as response:
        result=json.loads(response.read().decode())
      content=result["choices"][0]["message"]["content"]
      analysis=json.loads(content)
    except urllib.error.HTTPError as exc:
      from fastapi import HTTPException
      detail="Chart analysis provider rejected the request (HTTP "+str(exc.code)+"). Check the OpenAI key and model configuration."
      raise HTTPException(status_code=502, detail=detail)
    except Exception:
      from fastapi import HTTPException
      raise HTTPException(status_code=502, detail="Chart analysis could not be completed. Try a clearer screenshot.")
    direction=str(analysis.get("direction","WAIT")).upper()
    if direction not in ("BUY","SELL","WAIT"): direction="WAIT"
    try: confidence=max(0,min(100,int(analysis.get("confidence",0))))
    except (TypeError,ValueError): confidence=0
    analysis["direction"]=direction
    analysis["confidence"]=confidence
    analysis["mode"]="uploaded-screenshot-only"
    analysis["automatic_execution"]=False
    analysis["disclaimer"]="Visual interpretation of a screenshot, not a verified live signal or a win probability."
    return analysis
