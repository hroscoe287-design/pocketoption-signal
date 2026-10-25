from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Literal
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
import json, os, secrets, time

app = FastAPI(title="Mega Man Signal API", version="1.1.0")
FRONTEND = os.getenv("FRONTEND_URL", "https://mega-man-signal-lab.onrender.com").rstrip("/")
GODMODE_BASE = os.getenv("GODMODE_FEED_BASE", "https://godmode-next-signal-lab.onrender.com").rstrip("/")
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", FRONTEND).split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

TIMEFRAMES = {5, 15, 30, 60, 300, 900, 3600}
LIVE_MODES = {"POCKET OPTION LIVE CANDLES", "POCKET OPTION LIVE TICKS"}

class AnalyzeRequest(BaseModel):
    asset: str = Field(min_length=1, max_length=80)
    timeframe_seconds: int = Field(default=300, ge=5, le=3600)

@app.middleware("http")
async def protect_api(request, call_next):
    secret = os.getenv("MEGAMAN_API_KEY", "")
    if request.url.path in {"/analyze", "/api/analyze"} and secret and not secrets.compare_digest(
        request.headers.get("X-API-Key", ""), secret
    ):
        from starlette.responses import JSONResponse
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)

def broker_asset(value: str) -> str:
    raw = value.strip()
    compact = "".join(ch for ch in raw if ch.isalnum() or ch == "_")
    upper = compact.upper()
    # Match the labels shown in Mega Man's dropdown to Pocket Option's symbols.
    aliases = {
        "EURUSDOTC": "EURUSD_otc", "GBPUSDOTC": "GBPUSD_otc",
        "AUDUSDOTC": "AUDUSD_otc", "USDJPYOTC": "USDJPY_otc",
        "USDCHFOTC": "USDCHF_otc", "USDCADOTC": "USDCAD_otc",
        "EURJPYOTC": "EURJPY_otc", "GBPJPYOTC": "GBPJPY_otc",
        "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
        "AUDUSD": "AUDUSD", "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
    }
    return aliases.get(upper, raw.replace("/", "").replace(" ", ""))

def fetch_upstream(path: str, params: dict | None = None, timeout: float = 22.0):
    url = GODMODE_BASE + path
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "MegaManSignalLab/1.1"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            payload = response.read(2_000_000)
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Unexpected feed response")
        return data
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Pocket Option feed service returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Could not reach the Godmode Next live-feed service. Check that its Pocket Option feed is connected.") from exc

def live_market(asset: str, timeframe_seconds: int):
    if timeframe_seconds not in TIMEFRAMES:
        raise HTTPException(status_code=422, detail="Choose a supported timeframe: 5s, 15s, 30s, 1m, 5m, 15m, or 1h.")
    data = fetch_upstream("/api/market", {"tf": timeframe_seconds, "asset": broker_asset(asset)})
    mode = str(data.get("data_mode", ""))
    bars = data.get("bars") if isinstance(data.get("bars"), list) else []
    if mode not in LIVE_MODES or len(bars) < 30:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "No verified live Pocket Option candles are available for this asset/timeframe. No signal was generated.",
                "feed_status": data.get("feed_status", mode or "Live feed unavailable"),
                "bar_count": len(bars),
                "automatic_execution": False,
            },
        )
    # Verify the final OHLC bar is structurally sound and reasonably fresh.
    last = bars[-1]
    try:
        stamp = float(last["time"])
        opening, high, low, close = (float(last[k]) for k in ("open", "high", "low", "close"))
        now = time.time()
        if stamp > 100_000_000_000:
            stamp /= 1000
        if not all(map(lambda v: v == v and abs(v) != float("inf"), (stamp, opening, high, low, close))):
            raise ValueError("non-finite candle")
        age = max(0.0, now - stamp)
        if low <= 0 or high < max(opening, close, low) or low > min(opening, close, high):
            raise ValueError("invalid OHLC")
        if age > max(30, timeframe_seconds * 2):
            raise HTTPException(status_code=503, detail="Live candles are stale for this timeframe. No signal was generated.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The latest live candle failed validation. No signal was generated.") from exc
    return data, bars, age

@app.get("/")
def root():
    return {
        "name": "Mega Man Signal API",
        "status": "online",
        "feed_source": "Godmode Next verified Pocket Option feed",
        "signals": "analysis-only",
        "automatic_execution": False,
    }

@app.get("/health")
def health():
    return {
        "ok": True,
        "feed_source_configured": bool(GODMODE_BASE),
        "feed_source": GODMODE_BASE,
        "automatic_execution": False,
    }

@app.get("/api/assets")
def api_assets():
    data = fetch_upstream("/api/assets", timeout=15)
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []
    return {
        "assets": assets,
        "count": len(assets),
        "live": bool(data.get("live")),
        "status": data.get("status", "Broker catalog status unavailable"),
        "automatic_execution": False,
    }

@app.get("/api/market")
def api_market(asset: str = "EURUSD_otc", timeframe_seconds: int = 300):
    data, bars, age = live_market(asset, timeframe_seconds)
    return {
        "asset": data.get("asset", broker_asset(asset)),
        "timeframe_seconds": timeframe_seconds,
        "data_mode": data.get("data_mode"),
        "feed_status": data.get("feed_status"),
        "price": data.get("price"),
        "bars": bars[-120:],
        "candle_age_seconds": round(age, 2),
        "verified": True,
        "automatic_execution": False,
    }

@app.post("/api/analyze")
@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    data, bars, age = live_market(req.asset, req.timeframe_seconds)
    source_signal = str(data.get("signal", "WAIT")).upper()
    direction = "UP" if source_signal == "BUY" else ("DOWN" if source_signal == "SELL" else "WAIT")
    # Keep Godmode's confluence score as an indicator-strength score, not a win probability.
    confidence = int(data.get("confidence") or 0)
    if data.get("entry_status") != "FRESH WINDOW":
        direction = "WAIT"
    price = data.get("price")
    if price is None:
        price = bars[-1].get("close")
    return {
        "asset": data.get("asset", broker_asset(req.asset)),
        "timeframe_seconds": req.timeframe_seconds,
        "signal": direction,
        "direction": direction,
        "arrow": "green_up" if direction == "UP" else ("red_down" if direction == "DOWN" else None),
        "confidence": confidence if direction != "WAIT" else 0,
        "confidence_type": "indicator_strength_not_win_probability",
        "price": price,
        "expiry_seconds": req.timeframe_seconds,
        "verified": True,
        "data_quality": data.get("data_mode", "Verified live feed"),
        "feed_status": data.get("feed_status"),
        "candle_age_seconds": round(age, 2),
        "entry_status": data.get("entry_status"),
        "analysis": {
            "trend": data.get("trend"),
            "rsi": data.get("rsi"),
            "macd_bias": data.get("macd_bias"),
            "wavetrend_bias": data.get("wavetrend_bias"),
            "plus_di": data.get("plus_di"),
            "minus_di": data.get("minus_di"),
            "strength_label": data.get("strength_label"),
            "reason": (data.get("hybrid_engine") or {}).get("reason", data.get("reason", "")),
        },
        "candle_count": len(bars),
        "automatic_execution": False,
        "disclaimer": "Indicator strength is not a calibrated win probability or guaranteed result. No orders are placed.",
    }
