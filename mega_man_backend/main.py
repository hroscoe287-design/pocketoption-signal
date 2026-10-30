from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import os, time, socket
from urllib.parse import urlparse
from feed import feed
from engine import analyze

FRONTEND = os.getenv("FRONTEND_URL", "https://mega-man-signal-lab.onrender.com").rstrip("/")
DEFAULT_ASSET = os.getenv("PO_DEFAULT_ASSET", "EURUSD_otc")
DEFAULT_TF = int(os.getenv("PO_DEFAULT_TIMEFRAME_SECONDS", "5"))
TIMEFRAMES = {5, 15, 30, 60, 300, 900, 3600}
STARTED = time.time()

@asynccontextmanager
async def lifespan(app):
    feed.start()
    yield

app = FastAPI(title="Mega Man Signal API", version="2.0.0", lifespan=lifespan)
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", FRONTEND).split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_credentials=False,
    allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-API-Key"]
)

class AnalyzeRequest(BaseModel):
    asset: str = Field(default=DEFAULT_ASSET, min_length=1, max_length=80)
    timeframe_seconds: int = Field(default=DEFAULT_TF, ge=5, le=3600)

def broker_asset(value: str) -> str:
    raw = value.strip()
    compact = "".join(ch for ch in raw if ch.isalnum() or ch == "_")
    upper = compact.upper()
    aliases = {
        "EURUSDOTC": "EURUSD_otc", "GBPUSDOTC": "GBPUSD_otc",
        "AUDUSDOTC": "AUDUSD_otc", "USDJPYOTC": "USDJPY_otc",
        "USDCHFOTC": "USDCHF_otc", "USDCADOTC": "USDCAD_otc",
        "EURJPYOTC": "EURJPY_otc", "GBPJPYOTC": "GBPJPY_otc",
        "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
        "AUDUSD": "AUDUSD", "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
    }
    return aliases.get(upper, raw.replace("/", "").replace(" ", ""))

def market_snapshot(asset: str, tf: int):
    if tf not in TIMEFRAMES:
        raise HTTPException(status_code=422, detail="Choose 5s, 15s, 30s, 1m, 5m, 15m, or 1h.")
    return feed.snapshot(broker_asset(asset), tf)

@app.get("/")
def root():
    return {
        "name": "Mega Man Signal API", "status": "online",
        "feed_source": "Direct Pocket Option connection",
        "auth_configured": bool(feed._auth()), "auth_status": feed.auth_status(), "feed_status": feed.status, "signals": "analysis-only",
        "automatic_execution": False
    }

@app.get("/health")
def health():
    return {
        "ok": True, "service": "Mega Man independent feed",
        "feed_source": "Pocket Option direct", "auth_configured": bool(feed._auth()),
        "auth_status": feed.auth_status(),
        "feed_status": feed.status, "uptime_seconds": round(time.time() - STARTED, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "automatic_execution": False
    }

@app.get("/api/diagnostics")
def diagnostics(asset: str = DEFAULT_ASSET, tf: int = DEFAULT_TF):
    result = market_snapshot(asset, tf)
    return {
        "ok": bool(result.get("verified")), "status": result.get("status"),
        "requested_asset": broker_asset(asset), "timeframe_seconds": tf,
        "bar_count": result.get("bar_count", 0), "latest_bar": result.get("latest_bar"),
        "feed": {"provider": "Pocket Option", "configured": bool(feed._auth()),
                 "connected": feed.client is not None, "verified": bool(result.get("verified")),
                 "reason": result.get("status")},
        "debug": result.get("debug", {}), "signals_enabled": bool(result.get("verified")),
        "automatic_execution": False
    }

@app.get("/api/network-diagnostics")
def network_diagnostics():
    """Credential-free DNS check for the LIVE WebSocket hosts used by the installed client."""
    try:
        from pocketoptionapi.constants import REGION
        urls = list(REGION().get_real_servers(randomize=False))
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "automatic_execution": False}
    results = []
    for url in urls:
        host = urlparse(url).hostname
        item = {"host": host, "port": 443, "dns_ok": False, "address_count": 0}
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            unique = sorted({entry[4][0] for entry in addresses})
            item.update({"dns_ok": bool(unique), "address_count": len(unique)})
        except socket.gaierror as exc:
            item["error"] = "DNS_" + str(exc.errno)
        except Exception as exc:
            item["error"] = type(exc).__name__
        results.append(item)
    return {
        "ok": any(item["dns_ok"] for item in results),
        "mode": "LIVE endpoint DNS only",
        "servers": results,
        "credentials_used": False,
        "automatic_execution": False
    }

@app.get("/api/assets")
def api_assets():
    client = feed.client
    if client is None:
        return {"live": False, "status": feed.status, "count": 0, "assets": [],
                "automatic_execution": False}
    try:
        raw = client.get_assets() or {}
        symbols = set()
        fields = ("symbol", "name", "asset", "ticker", "active_symbol", "instrument",
                  "activeSymbol", "asset_name", "assetName")
        def add(value):
            if isinstance(value, str):
                value = value.strip()
                # Do not discard broker symbols just because they contain digits (e.g. crypto tickers).
                if value and not value.isdigit() and len(value) <= 80:
                    symbols.add(value)
            elif isinstance(value, dict):
                for field in fields:
                    item = value.get(field)
                    if isinstance(item, str) and item.strip():
                        symbols.add(item.strip())
        if isinstance(raw, dict):
            for key, value in raw.items():
                # Catalogs can be symbol->metadata or numeric-id->symbol mappings.
                add(key)
                add(value)
        elif isinstance(raw, (list, tuple, set)):
            for item in raw:
                add(item)
        # Keep only plausible symbols; metadata-only values are not selectable assets.
        symbols = {s for s in symbols if len(s) <= 80 and any(ch.isalpha() for ch in s)}
        try:
            connected = bool(client.check_connect())
        except Exception:
            connected = False
        assets = sorted(symbols, key=str.casefold)
        return {"live": connected and bool(assets), "status": feed.status,
                "count": len(assets), "assets": assets, "automatic_execution": False}
    except Exception as exc:
        return {"live": False, "status": "Broker catalog unavailable: " + type(exc).__name__,
                "count": 0, "assets": [], "automatic_execution": False}

@app.get("/api/market")
def api_market(asset: str = DEFAULT_ASSET, tf: int = DEFAULT_TF):
    data = market_snapshot(asset, tf)
    return {
        "asset": data.get("asset", broker_asset(asset)), "timeframe_seconds": tf,
        "data_mode": "POCKET OPTION LIVE CANDLES" if data.get("verified") else "UNVERIFIED",
        "feed_status": data.get("status", "Live feed unavailable"),
        "price": (data.get("latest_bar") or {}).get("close"),
        "bars": data.get("candles", [])[-120:], "verified": bool(data.get("verified")),
        "bar_count": data.get("bar_count", 0), "debug": data.get("debug", {}),
        "automatic_execution": False
    }

@app.post("/api/analyze")
@app.post("/analyze")
def analyze_request(req: AnalyzeRequest):
    data = market_snapshot(req.asset, req.timeframe_seconds)
    candles = data.get("candles", [])
    if not data.get("verified"):
        raise HTTPException(status_code=503, detail={
            "message": "Mega Man's direct Pocket Option feed is not verified yet. No signal was generated.",
            "auth_status": feed.auth_status(),
            "feed_status": data.get("status", feed.status),
            "auth_configured": bool(feed._auth()),
            "automatic_execution": False
        })
    if len(candles) < 60:
        return {
            "asset": broker_asset(req.asset), "timeframe_seconds": req.timeframe_seconds,
            "signal": "WAIT", "direction": "WAIT", "arrow": None, "confidence": 0,
            "confidence_type": "indicator_strength_not_win_probability",
            "price": (data.get("latest_bar") or {}).get("close"),
            "verified": True, "data_quality": "Verified Pocket Option live candles",
            "feed_status": data.get("status"), "candle_count": len(candles),
            "analysis": {"reason": "Collecting at least 60 verified candles before analysis.", "components": {}},
            "automatic_execution": False
        }
    result = analyze(candles, timeframe_seconds=req.timeframe_seconds)
    raw_direction = str(result.get("direction", "WAIT")).upper()
    direction = "UP" if raw_direction.startswith("BUY") or "CALL" in raw_direction else (
        "DOWN" if raw_direction.startswith("SELL") or "PUT" in raw_direction else "WAIT")
    if not result.get("eligible"):
        direction = "WAIT"
    score = max(0, min(100, int(result.get("score") or 0)))
    if direction == "WAIT":
        score = 0
    latest = candles[-1]
    return {
        "asset": broker_asset(req.asset), "timeframe_seconds": req.timeframe_seconds,
        "signal": direction, "direction": direction,
        "arrow": "green_up" if direction == "UP" else ("red_down" if direction == "DOWN" else None),
        "confidence": score, "confidence_type": "indicator_strength_not_win_probability",
        "price": latest.get("close"), "expiry_seconds": req.timeframe_seconds,
        "verified": True, "data_quality": "Verified Pocket Option live candles",
        "feed_status": data.get("status"), "candle_age_seconds": data.get("debug", {}).get("last_candle_age_seconds"),
        "analysis": {"reason": result.get("reason", ""), "components": result.get("components", {}),
                     "stages": result.get("stages", []), "score": score, "eligible": bool(result.get("eligible"))},
        "candle_count": len(candles), "automatic_execution": False,
        "disclaimer": "Indicator strength is not a calibrated win probability or guaranteed result. No orders are placed."
    }
