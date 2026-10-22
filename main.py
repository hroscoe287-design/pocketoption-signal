from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response, FileResponse
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError
from fastapi.middleware.cors import CORSMiddleware
import math, random, os, json, threading, time, logging, zipfile

from godmode_strategy import GodModeStrategy
from godmode_enhancements import analyze as analyze_enhancements
from hybrid_engine import CombinedEngine

_godmode_engine = GodModeStrategy()
_hybrid_engine = CombinedEngine()


logger = logging.getLogger("godmode.po")
_po_client = None
_po_status = "PO_AUTH_JSON not configured"
_po_lock = threading.Lock()
_po_last_fetch = 0.0
_po_last_key = None
_po_cached_bars = []
_po_connect_started = False
# Separate broker-history polling from feed freshness. Repeatedly requesting
# full history on every browser poll can stall the unofficial broker connection.
_po_last_history_fetch = 0.0
_po_last_tick_signature = {}
_po_last_tick_received_at = {}
_po_subscription_started_at = {}
_po_subscribed_keys = set()
_PO_HISTORY_POLL_SECONDS = 1.0
_PO_TICK_STALE_SECONDS = 15.0

def _auth_wire_value():
    raw = os.getenv("PO_AUTH_JSON", "").strip()
    if not raw:
        return None
    if raw.startswith('42["auth",'):
        return raw
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if isinstance(obj, str) and obj.startswith('42["auth",'):
        return obj
    if isinstance(obj, dict):
        if isinstance(obj.get("ssid"), str) and obj["ssid"].startswith('42["auth",'):
            return obj["ssid"]
        payload = obj.get("auth") if isinstance(obj.get("auth"), dict) else obj
        if isinstance(payload, dict) and payload.get("session") and payload.get("uid") is not None:
            payload = dict(payload)
            payload.setdefault("platform", 2)
            payload.setdefault("isFastHistory", True)
            payload.setdefault("isOptimized", True)
            return '42["auth",' + json.dumps(payload, separators=(",", ":")) + "]"
    return None

def _connect_po():
    global _po_client, _po_status, _po_connect_started
    with _po_lock:
        if _po_connect_started:
            return
        _po_connect_started = True
    try:
        ssid = _auth_wire_value()
        if not ssid:
            _po_status = "PO_AUTH_JSON missing or unrecognized; demo feed active"
            with _po_lock:
                _po_connect_started = False
            return
        from pocketoptionapi import PocketOption
        # Use the installed upstream client's maintained server discovery and defaults.
        client = PocketOption(ssid)
        ok, err = client.connect()
        if not ok:
            _po_status = "Pocket Option connection failed; live feed unavailable"
            # Connection errors contain endpoint/error diagnostics, not the SSID.
            logger.warning("PO connection failed: %s", err or "unknown reason")
            with _po_lock:
                _po_connect_started = False
            return
        # Wait for server clock synchronization; history calls need it.
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if client.check_connect() and client.is_time_synced():
                    break
            except Exception:
                pass
            time.sleep(0.25)
        # A fresh client has no active market subscriptions or valid tick heartbeat.
        global _po_subscribed_keys, _po_subscription_started_at, _po_last_tick_signature, _po_last_tick_received_at
        with _po_lock:
            _po_client = client
            _po_subscribed_keys.clear()
            _po_subscription_started_at.clear()
            _po_last_tick_signature.clear()
            _po_last_tick_received_at.clear()
        try:
            synced = bool(client.is_time_synced())
        except Exception:
            synced = False
        _po_status = "Pocket Option connected; server time synchronized" if synced else "Pocket Option connected; waiting for server time sync"
        logger.info("Pocket Option WebSocket connected; time_synced=%s; signal-only mode", synced)
    except Exception:
        _po_status = "Pocket Option client unavailable or auth rejected; demo feed active"
        with _po_lock:
            _po_connect_started = False
        logger.exception("Pocket Option setup failed; credential details withheld")


_po_last_reconnect_attempt = 0.0

def _disconnect_then_reconnect(old_client):
    """Close the dead socket before creating a replacement client."""
    if old_client is not None:
        try:
            close_fn = getattr(old_client, "disconnect_websocket", None)
            if callable(close_fn):
                try:
                    close_fn(timeout=3.0)
                except TypeError:
                    close_fn()
        except Exception as exc:
            logger.info("Old Pocket Option client cleanup failed (%s); continuing reconnect", type(exc).__name__)
    with _po_lock:
        # Release the connection-start guard only after old-client cleanup, so a
        # chart request cannot create a competing connection during teardown.
        global _po_connect_started
        _po_connect_started = False
    _connect_po()


def _schedule_po_reconnect(reason):
    """Recreate a stale broker client with a cooldown; never place trades."""
    global _po_client, _po_status, _po_connect_started, _po_last_reconnect_attempt
    now = time.time()
    with _po_lock:
        if now - _po_last_reconnect_attempt < 15:
            return
        _po_last_reconnect_attempt = now
        old_client = _po_client
        _po_client = None
        # Reserve the connection guard while the old socket is being closed.
        _po_connect_started = True
        _po_subscribed_keys.clear()
        _po_subscription_started_at.clear()
        _po_last_tick_signature.clear()
        _po_last_tick_received_at.clear()
        _po_status = "Pocket Option connection lost; reconnecting; live feed unavailable"
    logger.warning("Scheduling read-only Pocket Option reconnect: %s", reason)
    threading.Thread(
        target=_disconnect_then_reconnect,
        args=(old_client,),
        daemon=True,
        name="po-market-reconnect",
    ).start()

# Retry the broker connection independently of browser polling. This matters when
# DNS or broker endpoints are temporarily unavailable: the chart should recover
# on its own as soon as a server becomes reachable.
_PO_RECONNECT_RETRY_SECONDS = 20.0

def _po_connection_supervisor():
    """Keep attempting read-only feed recovery while never fabricating candles."""
    while True:
        try:
            client = _po_client
            if client is None:
                if _auth_wire_value():
                    _connect_po()
            else:
                try:
                    if not client.check_connect():
                        _schedule_po_reconnect("supervisor detected disconnected WebSocket")
                except Exception as exc:
                    logger.warning("Pocket Option connection health check failed (%s)", type(exc).__name__)
                    _schedule_po_reconnect("supervisor health check raised an exception")
        except Exception as exc:
            # A supervisor error must not permanently disable later retries.
            logger.warning("Pocket Option reconnect supervisor recovered from %s", type(exc).__name__)
        time.sleep(_PO_RECONNECT_RETRY_SECONDS)


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

# Read-only connection: this service never calls order-placement methods.
threading.Thread(target=_po_connection_supervisor, daemon=True, name="po-market-feed-supervisor").start()

def _fetch_po_bars_blocking(asset, timeframe):
    global _po_status, _po_last_fetch, _po_last_key, _po_cached_bars
    client = _po_client
    if client is None:
        # A failed initial connect must not leave the feed permanently stuck.
        # Retry with a cooldown, but never attempt to trade or fabricate live data.
        if _auth_wire_value():
            _schedule_po_reconnect("no active Pocket Option client")
        return None
    global _po_last_history_fetch
    key = (asset, timeframe)
    now = time.time()
    try:
        if not client.check_connect():
            _po_status = "Pocket Option disconnected; live feed unavailable"
            _schedule_po_reconnect("client.check_connect() returned false")
            return None
        # Resolve the user's selection against the broker's actual symbol catalog.
        # This supports OTC crypto, metals, stocks and indices when the broker offers them,
        # even if the UI's symbol capitalization differs from the feed's canonical name.
        actual_asset = asset
        try:
            catalog = client.get_assets() or {}
            catalog_symbols = list(catalog.keys()) if isinstance(catalog, dict) else list(catalog) if isinstance(catalog, (list, tuple, set)) else []
            match = next((str(symbol) for symbol in catalog_symbols if str(symbol).strip().casefold() == asset.strip().casefold()), None)
            if match:
                actual_asset = match
            elif catalog_symbols:
                _po_status = f"Asset unavailable in connected Pocket Option catalog: {asset}"
                return None
        except Exception:
            # Catalog lookup is best-effort; let the broker subscription validate the symbol.
            pass
        try:
            if not client.is_time_synced():
                _po_status = "Pocket Option connected; waiting for server time sync"
                return None
        except Exception:
            _po_status = "Pocket Option server time sync unavailable"
            return None
        # Subscribe once per asset/timeframe per client. Calling subscribe on every
        # browser poll can flood the unofficial API and destabilize its WebSocket.
        subscription_key = (actual_asset, timeframe)
        if subscription_key not in _po_subscribed_keys:
            if not client.subscribe(actual_asset, period=timeframe):
                _po_status = f"Pocket Option subscription failed for {actual_asset}"
                logger.warning("Pocket Option subscribe failed for symbol=%s period=%s", actual_asset, timeframe)
                _schedule_po_reconnect("subscription failed: no active or usable WebSocket")
                return None
            _po_subscribed_keys.add(subscription_key)
            _po_subscription_started_at[key] = now

        # Treat incoming ticks as the feed heartbeat. A WebSocket can claim to be
        # connected while no market messages arrive, so check message freshness too.
        ticks = []
        try:
            ticks = client.get_realtime_ticks(actual_asset, limit=5000) or []
        except Exception:
            ticks = []
        if ticks:
            try:
                last_ts, last_price = ticks[-1]
                signature = (str(last_ts), float(last_price))
                if _po_last_tick_signature.get(key) != signature:
                    _po_last_tick_signature[key] = signature
                    _po_last_tick_received_at[key] = now
            except (TypeError, ValueError, IndexError):
                pass
        last_tick_at = _po_last_tick_received_at.get(key)
        # Also detect the case where the subscription never delivered even its
        # first tick; previously that case could remain stuck indefinitely.
        heartbeat_started = last_tick_at or _po_subscription_started_at.get(key)
        if heartbeat_started is not None and now - heartbeat_started > _PO_TICK_STALE_SECONDS:
            silent_for = int(now - heartbeat_started)
            _po_status = f"Pocket Option feed silent for {silent_for}s; reconnecting"
            _schedule_po_reconnect("market subscription delivered no fresh ticks")
            return None

        # Don't hammer the history endpoint at browser-poll speed. Refresh the
        # history snapshot every 3 seconds and use genuine streaming ticks between
        # history requests to keep the currently forming candle moving.
        if key == _po_last_key and _po_cached_bars and now - _po_last_history_fetch < _PO_HISTORY_POLL_SECONDS:
            updated = [dict(bar) for bar in _po_cached_bars]
            try:
                buckets = {}
                for ts, price in ticks:
                    t, p = int(float(ts)), float(price)
                    bucket = (t // timeframe) * timeframe
                    bar = buckets.get(bucket)
                    if bar is None:
                        buckets[bucket] = {"time": bucket, "open": p, "high": p, "low": p, "close": p}
                    else:
                        bar["high"] = max(bar["high"], p)
                        bar["low"] = min(bar["low"], p)
                        bar["close"] = p
                for tick_bar in sorted(buckets.values(), key=lambda b: b["time"]):
                    if tick_bar["time"] < updated[-1]["time"]:
                        continue
                    if tick_bar["time"] == updated[-1]["time"]:
                        updated[-1]["high"] = max(updated[-1]["high"], tick_bar["high"])
                        updated[-1]["low"] = min(updated[-1]["low"], tick_bar["low"])
                        updated[-1]["close"] = tick_bar["close"]
                    else:
                        updated.append(tick_bar)
                updated = updated[-120:]
                if updated:
                    _po_cached_bars = updated
                    _po_last_fetch = now
                    _po_status = f"POCKET OPTION LIVE TICKS — {actual_asset}"
                    return updated
            except (TypeError, ValueError, IndexError, KeyError):
                pass
            # If there are no recent ticks, return only a still-fresh verified cache.
            if now - _po_last_fetch <= max(10, timeframe * 2):
                return _po_cached_bars

        # Historical snapshots are for bootstrapping and gap correction, not every UI poll.
        _po_last_history_fetch = now
        candles = client.get_historical_candles(actual_asset, timeframe, offset=45000, count_request=1)
        if not candles:
            _po_status = f"Connected, but Pocket Option returned no history for {actual_asset} ({timeframe}s)"
            logger.warning("No historical candles returned for symbol=%s period=%s", actual_asset, timeframe)
            # Use only genuine ticks as a fallback; never manufacture live data.
            try:
                ticks = client.get_realtime_ticks(actual_asset, limit=5000)
                buckets = {}
                for ts, price in (ticks or []):
                    t, p = int(float(ts)), float(price)
                    bucket = (t // timeframe) * timeframe
                    bar = buckets.get(bucket)
                    if bar is None:
                        buckets[bucket] = {"time": bucket, "open": p, "high": p, "low": p, "close": p}
                    else:
                        bar["high"] = max(bar["high"], p)
                        bar["low"] = min(bar["low"], p)
                        bar["close"] = p
                tick_bars = [buckets[k] for k in sorted(buckets)]
                if len(tick_bars) >= 2:
                    normalized = tick_bars[-120:]
                    _po_last_key, _po_last_fetch, _po_cached_bars = key, now, normalized
                    _po_status = f"POCKET OPTION LIVE TICKS — {asset}"
                    return normalized
            except Exception:
                logger.warning("Realtime tick fallback failed for symbol=%s", actual_asset)
            return None
        normalized = []
        for b in candles:
            try:
                t = int(float(b.get("time", b.get("timestamp"))))
                normalized.append({"time": t, "open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"])})
            except (TypeError, ValueError, KeyError):
                continue
        normalized.sort(key=lambda b: b["time"])
        unique = {b["time"]: b for b in normalized}
        normalized = list(unique.values())[-120:]
        if len(normalized) < 2:
            _po_status = f"Pocket Option returned only {len(normalized)} valid candles for {actual_asset} ({timeframe}s)"
            logger.warning("Insufficient valid bars: symbol=%s period=%s count=%s", actual_asset, timeframe, len(normalized))
            return None
        _po_last_key, _po_last_fetch, _po_cached_bars = key, now, normalized
        _po_status = "POCKET OPTION LIVE CANDLES"
        return normalized
    except Exception as e:
        _po_status = "Pocket Option data request failed; live feed unavailable"
        logger.warning("PO candle request failed (%s); credential details withheld", type(e).__name__)
        # A dropped WebSocket can leave a stale client object that never reconnects.
        if "connect" in str(e).lower() or "websocket" in str(e).lower() or "closed" in str(e).lower():
            _schedule_po_reconnect(type(e).__name__)
        return None


# Keep browser/API requests responsive even when the broker WebSocket stalls.
# A watchdog invalidates a worker that hangs inside the broker library and rebuilds
# the client. Each worker has a generation token so a timed-out thread cannot clear
# the in-progress flag belonging to a newer worker.
_po_refresh_guard = threading.Lock()
_po_refresh_in_progress = False
_po_refresh_key = None
_po_refresh_requested_at = 0.0
_po_refresh_started_at = 0.0
_po_refresh_generation = 0
_PO_REFRESH_WATCHDOG_SECONDS = 8.0

def _refresh_po_bars_worker(asset, timeframe, generation):
    global _po_refresh_in_progress
    try:
        _fetch_po_bars_blocking(asset, timeframe)
    except Exception as exc:
        logger.warning("Background market refresh failed (%s); dashboard remains responsive", type(exc).__name__)
    finally:
        with _po_refresh_guard:
            if generation == _po_refresh_generation:
                _po_refresh_in_progress = False

def _get_po_bars(asset, timeframe):
    global _po_refresh_in_progress, _po_refresh_key, _po_refresh_requested_at
    global _po_refresh_started_at, _po_refresh_generation
    now = time.time()
    key = (asset, timeframe)
    reconnect_for_stall = False
    with _po_refresh_guard:
        # Do not let a stuck library call permanently block every future chart refresh.
        if _po_refresh_in_progress and _po_refresh_started_at and now - _po_refresh_started_at > _PO_REFRESH_WATCHDOG_SECONDS:
            logger.warning("Pocket Option market refresh exceeded %.1fs; invalidating stalled worker", _PO_REFRESH_WATCHDOG_SECONDS)
            _po_refresh_generation += 1
            _po_refresh_in_progress = False
            _po_refresh_started_at = 0.0
            reconnect_for_stall = True
        if not reconnect_for_stall and not _po_refresh_in_progress and (
            _po_refresh_key != key or now - _po_refresh_requested_at >= 0.3
        ):
            _po_refresh_generation += 1
            generation = _po_refresh_generation
            _po_refresh_in_progress = True
            _po_refresh_key = key
            _po_refresh_requested_at = now
            _po_refresh_started_at = now
            threading.Thread(
                target=_refresh_po_bars_worker,
                args=(asset, timeframe, generation),
                daemon=True,
                name="po-candle-refresh",
            ).start()
    if reconnect_for_stall:
        _schedule_po_reconnect("market refresh watchdog timeout")
    # Return only recently verified broker bars; never label stale cache as live.
    if _po_last_key == key and _po_cached_bars:
        age = now - _po_last_fetch
        if age <= max(10, timeframe * 2):
            return _po_cached_bars
    return None

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

def snapshot(tf=None, asset=None):
    global _price, _bars, TIMEFRAME, ASSET, _rng
    requested_tf = min(3600, max(5, int(tf or TIMEFRAME)))
    requested_asset = str(asset or ASSET)[:64]
    if requested_tf != TIMEFRAME or requested_asset != ASSET:
        TIMEFRAME = requested_tf
        ASSET = requested_asset
        _rng = random.Random(42 + sum(ord(ch) for ch in ASSET) + TIMEFRAME)
        seed_bars()
    step = max(5, TIMEFRAME)
    live_bars = _get_po_bars(ASSET, step)
    is_live = bool(live_bars)
    if is_live:
        _bars = live_bars
        _price = _bars[-1]["close"]
    now = int(datetime.now(timezone.utc).timestamp())
    last = _bars[-1]
    # Generated candles are never displayed or used for actionable signals.
    closes = [b["close"] for b in _bars]
    fast, slow = ema(closes, 9), ema(closes, 21)
    macd_line = [a-b for a,b in zip(ema(closes,12), ema(closes,26))]
    macd_signal = ema(macd_line, 9)
    rv = rsi(closes)
    # Original WaveTrend-style momentum calculation; not the commercial Godmode formula.
    typical = [(b["high"] + b["low"] + b["close"]) / 3 for b in _bars]
    esa = ema(typical, 10)
    deviation = ema([abs(v - e) for v, e in zip(typical, esa)], 10)
    ci = [(typical[i] - esa[i]) / (0.015 * deviation[i]) if deviation[i] > 1e-12 else 0.0 for i in range(len(typical))]
    wt1 = ema(ci, 21)
    wt2 = [sum(wt1[max(0, i - 3):i + 1]) / len(wt1[max(0, i - 3):i + 1]) for i in range(len(wt1))]
    wt_cross = 1 if wt1[-1] > wt2[-1] else -1
    prior_cross = 1 if wt1[-2] > wt2[-2] else -1
    wt_event = "BULL CROSS" if wt_cross > 0 and prior_cross < 0 else ("BEAR CROSS" if wt_cross < 0 and prior_cross > 0 else "NO CROSS")
    trend = 1 if fast[-1] > slow[-1] else -1
    mom = 1 if macd_line[-1] > macd_signal[-1] else -1
    rsi_bias = 1 if rv >= 52 else (-1 if rv <= 48 else 0)
    base_score = trend + mom + rsi_bias + wt_cross
    extra = analyze_enhancements(_bars)
    extra_vote = int(extra.get("vote", 0)) if extra.get("ready") else 0
    # Extra indicators confirm or veto the base Godmode-family signal; they are not win probabilities.
    score = base_score + (1 if extra_vote >= 2 else (-1 if extra_vote <= -2 else 0))
    aligned = (trend > 0 and extra_vote >= 1) if score > 0 else (trend < 0 and extra_vote <= -1)
    signal = ("BUY" if score >= 3 and aligned else ("SELL" if score <= -3 and aligned else "WAIT")) if is_live else "WAIT"
    # SONIC owns direction; Godmode-family indicators are lightweight confirmation only.
    hybrid = _hybrid_engine.evaluate(_bars, ASSET, step, is_live, signal, score)
    signal = hybrid["signal"]
    # Transparent confluence index (not a statistically verified win probability).
    # Sonic DI dominance/X quality drives strength; Godmode contributes only a small adjustment.
    di_gap = float(hybrid.get("di_gap", 0) or 0)
    di_strength = min(12, int(di_gap / 4))
    sonic_ready = signal != "WAIT"
    strength = min(99, 78 + di_strength + (5 if hybrid.get("x_quality", 0) >= 3 else 0)) if is_live and sonic_ready else 0
    strength = max(1, min(99, strength + int(hybrid.get("confirmation_adjustment", 0)) + int(hybrid.get("x_quality_adjustment", 0)))) if sonic_ready and is_live else 0
    # Minimum actionable confluence is 80/100. This is an indicator score, not a win probability.
    if is_live and signal != "WAIT" and strength < 80:
        signal = "WAIT"
    strength_label = ("PERFECT" if strength >= 99 else ("AMAZING" if strength >= 90 else ("TERRIFIC" if strength >= 80 else "BELOW 80 — NO SIGNAL")))
    candle_age = max(0, now - int(_bars[-1]["time"])) if is_live else 0
    age_fraction = min(1.0, candle_age / step) if is_live and step else 1.0
    entry_status = ("FEED UNAVAILABLE" if not is_live else
                    ("LATE — SKIP ENTRY" if age_fraction >= 0.65 else
                     ("WAIT FOR FRESH CANDLE" if age_fraction >= 0.35 else "FRESH WINDOW")))
    return {
        "asset": ASSET, "timeframe_seconds": step, "price": round(closes[-1], 6) if is_live else None,
        "signal": signal, "score": score if is_live else 0,
        "confidence": strength, "strength_label": strength_label if is_live else "UNAVAILABLE",
        "entry_status": entry_status, "candle_age_seconds": candle_age if is_live else None,
        "rsi": round(rv, 2) if is_live else None,
        "ema9": round(fast[-1], 6) if is_live else None, "ema21": round(slow[-1], 6) if is_live else None,
        "macd_bias": ("BULLISH" if mom > 0 else "BEARISH") if is_live else "UNAVAILABLE",
        "trend": ("BULLISH" if trend > 0 else "BEARISH") if is_live else "UNAVAILABLE",
        "wavetrend": round(wt1[-1], 2) if is_live else None,
        "wavetrend_signal": round(wt2[-1], 2) if is_live else None,
        "wavetrend_bias": ("BULLISH" if wt_cross > 0 else "BEARISH") if is_live else "UNAVAILABLE",
        "wavetrend_event": wt_event if is_live else "NO LIVE DATA",
        "sonic_signal": hybrid.get("sonic_signal", "WAIT"),
        "godmode_signal": hybrid.get("godmode_signal", "WAIT"),
        "engine": hybrid.get("engine", "SONIC PRIMARY"),
        "agreement": hybrid.get("agreement", "UNAVAILABLE"),
        "confirmation_level": hybrid.get("confirmation_level", "WAIT"),
        "fractal_arrow": hybrid.get("fractal_arrow"),
        "plus_di": hybrid.get("plus_di"), "minus_di": hybrid.get("minus_di"),
        "plus_di_boxes": hybrid.get("plus_di_boxes", 0),
        "minus_di_boxes": hybrid.get("minus_di_boxes", 0),
        "di_box_ratio": hybrid.get("di_box_ratio", "0:0"),
        "wide_x": hybrid.get("wide_x", False),
        "x_status": hybrid.get("x_status", "UNAVAILABLE"),
        "reason": hybrid.get("reason", ""),

        "base_score": base_score if is_live else 0,
        "enhancement_vote": extra_vote if is_live else 0,
        "hybrid_engine": hybrid,
        "enhancement_indicators": extra.get("components", {}) if is_live else {},
        "squeeze_context": bool(extra.get("components", {}).get("squeeze", False)) if is_live else False,
        "bars": _bars[-120:] if is_live else [],  # Never draw seeded/demo candles as if they were the live market.
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data_mode": ("POCKET OPTION LIVE CANDLES" if is_live and _po_status == "POCKET OPTION LIVE CANDLES" else ("POCKET OPTION LIVE TICKS" if is_live and _po_status.startswith("POCKET OPTION LIVE TICKS") else "LIVE FEED UNAVAILABLE — NO SYNTHETIC DATA SHOWN")),
        "feed_status": _po_status,
        "godmode": {**_godmode_engine.update(_bars, ASSET, step, is_live), "signal": signal, "hybrid_engine": hybrid}
    }

@app.get("/api/market")
def market(tf: int = 15, asset: str = ASSET):
    return snapshot(tf=tf, asset=asset)

@app.get("/api/status")
def api_status():
    try:
        connected = bool(_po_client and _po_client.check_connect())
    except Exception:
        connected = False
    try:
        time_synced = bool(_po_client and _po_client.is_time_synced())
    except Exception:
        time_synced = False
    assets = {}
    try:
        if _po_client:
            assets = _po_client.get_assets() or {}
    except Exception:
        pass
    return {
        "status": _po_status,
        "live": _po_status == "POCKET OPTION LIVE CANDLES" or _po_status.startswith("POCKET OPTION LIVE TICKS"),
        "signal_only": True,
        "po_auth_configured": bool(os.getenv("PO_AUTH_JSON", "").strip()),
        "po_auth_format_recognized": bool(_auth_wire_value()),
        "client_loaded": _po_client is not None,
        "connected": connected,
        "time_synced": time_synced,
        "broker_asset_count": len(assets),
        "nzdusd_symbols_from_broker": [k for k in assets if "NZDUSD" in str(k).upper()],
        "selected_asset_note": "Regular forex symbols may be closed outside weekday market hours; OTC symbols are separate instruments."
    }

@app.get("/api/assets")
def api_assets():
    """Expose the symbols advertised by the connected broker; never invent a live asset list."""
    client = _po_client
    try:
        if not client:
            return {"live": False, "status": _po_status, "assets": [], "count": 0}
        raw = client.get_assets() or {}
        if isinstance(raw, dict):
            symbols = sorted({str(k) for k in raw.keys() if str(k).strip()}, key=str.casefold)
        elif isinstance(raw, (list, tuple, set)):
            symbols = sorted({str(x) for x in raw if str(x).strip()}, key=str.casefold)
        else:
            symbols = []
        connected = bool(client.check_connect())
        return {
            "live": connected and bool(symbols),
            "status": _po_status,
            "count": len(symbols),
            "assets": symbols,
            "note": "Broker catalog only; a symbol is live only after its own real candles or ticks are verified."
        }
    except Exception as e:
        logger.warning("Broker asset catalog unavailable (%s)", type(e).__name__)
        return {"live": False, "status": _po_status, "assets": [], "count": 0}

@app.get("/api/diagnostics")
def api_diagnostics(asset: str = "NZDUSD", tf: int = 60):
    if tf not in {5, 10, 15, 30, 60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 86400, 604800, 2592000}:
        return {"ok": False, "error": "Unsupported timeframe"}
    assets = {}
    try:
        if _po_client:
            assets = _po_client.get_assets() or {}
    except Exception:
        pass
    broker_match = [k for k in assets if str(k).lower() == asset.lower()]
    try:
        bars = _get_po_bars(asset, tf) or []
        return {
            "ok": bool(bars),
            "requested_asset": asset,
            "timeframe_seconds": tf,
            "status": _po_status,
            "bar_count": len(bars),
            "latest_bar": bars[-1] if bars else None,
            "broker_exact_symbol_match": broker_match,
            "broker_similar_symbols": [k for k in assets if asset.replace("_otc", "").lower() in str(k).lower()][:20],
            "connection": {
                "configured": bool(os.getenv("PO_AUTH_JSON", "").strip()),
                "recognized": bool(_auth_wire_value()),
                "connected": bool(_po_client and _po_client.check_connect()),
                "time_synced": bool(_po_client and _po_client.is_time_synced())
            }
        }
    except Exception as e:
        logger.exception("PO diagnostics failed for %s tf=%s", asset, tf)
        return {"ok": False, "requested_asset": asset, "timeframe_seconds": tf, "error_type": type(e).__name__, "status": _po_status}


@app.post("/api/desire/voice")
def desire_voice(payload: dict):
    """Generate real hosted speech for DESIRE; API key stays on the server."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return Response(
            content=json.dumps({"error": "DESIRE voice is not connected yet. Add ELEVENLABS_API_KEY in Render Environment."}),
            status_code=503,
            media_type="application/json",
        )
    text = str(payload.get("text", ""))[:240]
    if not text.strip():
        return Response(content=json.dumps({"error": "Speech text is required."}), status_code=400, media_type="application/json")
    voice_id = os.getenv("DESIRE_VOICE_ID", "XB0fDUnXU5powFXDhCwa").strip()
    request_body = json.dumps({
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {"stability": 0.55, "similarity_boost": 0.78, "style": 0.35, "use_speaker_boost": True},
    }).encode("utf-8")
    request = URLRequest(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
        data=request_body,
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=8) as result:
            audio = result.read()
        return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
    except HTTPError as exc:
        logger.warning("DESIRE hosted voice request failed with HTTP %s", exc.code)
        return Response(content=json.dumps({"error": "DESIRE voice provider rejected the request. Check the API key, voice ID, and account credits."}), status_code=502, media_type="application/json")
    except (URLError, TimeoutError, OSError) as exc:
        logger.warning("DESIRE hosted voice unavailable (%s)", type(exc).__name__)
        return Response(content=json.dumps({"error": "DESIRE voice provider is temporarily unavailable."}), status_code=502, media_type="application/json")


@app.get("/audio/desire/{clip_name}")
def desire_audio_clip(clip_name: str):
    """Serve DESIRE's locally bundled MP3 clips without exposing arbitrary files."""
    audio_files = {
        "intro": "01_desire_intro.mp3",
        "activation": "01_desire_intro.mp3",
        # These aliases must point to files that are actually inside the committed
        # 13-clip bundle. Older aliases referenced separate files that were never
        # bundled, causing 404s and silently bypassing DESIRE's recorded voice.
        "sup": "02_superior_signal.mp3",
        "super-focused": "02_superior_signal.mp3",
        "super-excited": "02_superior_signal.mp3",
        "super-excited-alt": "02_superior_signal.mp3",
        "super-excited-copy": "02_superior_signal.mp3",
        "scanning": "10_scanning.mp3",
        "early-warning": "11_fakeout_warning.mp3",
        "bullish-setup": "08_call_signal.mp3",
        "bearish-setup": "09_put_signal.mp3",
        "superior-signal": "02_superior_signal.mp3",
        "strong-setup": "03_perfect_signal.mp3",
        "fakeout-warning": "11_fakeout_warning.mp3",
        "signal-rejected": "06_below_threshold.mp3",
        "entry-countdown": "04_five_second_countdown.mp3",
        "long-timeframe": "05_twenty_second_entry_window.mp3",
        "momentum-fading": "06_below_threshold.mp3",
        "standby": "13_standby.mp3",
        "signal-cancelled": "07_signal_cancelled.mp3",
        "signal-locked": "12_signal_locked.mp3",
    }
    filename = audio_files.get(clip_name)
    if not filename:
        return Response(status_code=404)
    audio_dir = os.path.join(os.path.dirname(__file__), "static", "desire_audio")
    # A single uploaded ZIP is enough: extract only flat MP3 members into this
    # folder on first playback. This avoids requiring an ElevenLabs API key.
    bundle = os.path.join(audio_dir, "desire_13_voice_clips_repo_upload.zip")
    try:
        os.makedirs(audio_dir, exist_ok=True)
        if os.path.isfile(bundle):
            with zipfile.ZipFile(bundle, "r") as archive:
                for member in archive.infolist():
                    # Only extract flat MP3 files; ignore directories and path traversal.
                    name = os.path.basename(member.filename)
                    if name == member.filename and name.lower().endswith(".mp3") and not member.is_dir():
                        target = os.path.join(audio_dir, name)
                        if not os.path.isfile(target):
                            with archive.open(member, "r") as src, open(target, "wb") as dst:
                                dst.write(src.read())
    except (OSError, zipfile.BadZipFile) as exc:
        logger.warning("DESIRE local voice bundle could not be extracted (%s)", type(exc).__name__)
    path = os.path.join(audio_dir, filename)
    if not os.path.isfile(path):
        return Response(status_code=404)
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/desire/audio-status")
def desire_audio_status():
    """Report whether the committed DESIRE recordings are present and playable."""
    audio_dir = os.path.join(os.path.dirname(__file__), "static", "desire_audio")
    bundle = os.path.join(audio_dir, "desire_13_voice_clips_repo_upload.zip")
    expected = [
        "01_desire_intro.mp3", "02_superior_signal.mp3", "03_perfect_signal.mp3",
        "04_five_second_countdown.mp3", "05_twenty_second_entry_window.mp3",
        "06_below_threshold.mp3", "07_signal_cancelled.mp3", "08_call_signal.mp3",
        "09_put_signal.mp3", "10_scanning.mp3", "11_fakeout_warning.mp3",
        "12_signal_locked.mp3", "13_standby.mp3",
    ]
    # Trigger the same safe extraction path used by playback.
    try:
        os.makedirs(audio_dir, exist_ok=True)
        if os.path.isfile(bundle):
            with zipfile.ZipFile(bundle, "r") as archive:
                for member in archive.infolist():
                    name = os.path.basename(member.filename)
                    if name == member.filename and name.lower().endswith(".mp3") and not member.is_dir():
                        target = os.path.join(audio_dir, name)
                        if not os.path.isfile(target):
                            with archive.open(member, "r") as src, open(target, "wb") as dst:
                                dst.write(src.read())
    except (OSError, zipfile.BadZipFile) as exc:
        logger.warning("DESIRE audio status extraction failed (%s)", type(exc).__name__)
    present = [name for name in expected if os.path.isfile(os.path.join(audio_dir, name))]
    return {
        "ok": len(present) == len(expected),
        "bundle_present": os.path.isfile(bundle),
        "clips_present": len(present),
        "clips_expected": len(expected),
        "missing": [name for name in expected if name not in present],
        "elevenlabs_configured": bool(os.getenv("ELEVENLABS_API_KEY", "").strip()),
        "note": "Bundled MP3 playback does not require an ElevenLabs API key."
    }


@app.get("/health")
def health():
    return {"status": "ok", "mode": "live" if _po_status == "POCKET OPTION LIVE CANDLES" else "demo", "feed_status": _po_status}

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
.controls{display:flex;gap:6px;flex-wrap:wrap;max-width:100%}.controls button,select{background:#0a1020;color:var(--text);border:1px solid #34415a;border-radius:8px;padding:8px 10px}.platformlink{font-size:11px;color:var(--gold);border:1px solid #725c2c;border-radius:8px;padding:9px;text-decoration:none;font-weight:800}
.controls button.active{background:#283650;border-color:var(--gold)}#chart{height:430px;width:100%;position:relative}.chart-signal-badge{position:absolute;z-index:5;top:18px;left:18px;min-width:150px;padding:12px 18px;border:3px solid currentColor;border-radius:12px;text-align:center;font-size:clamp(24px,4vw,38px);font-weight:1000;letter-spacing:2px;text-transform:uppercase;box-shadow:0 5px 0 #0008,0 0 22px currentColor;pointer-events:none;background:#101722eF}.chart-signal-badge .chart-signal-price{display:block;font-size:clamp(15px,2vw,21px);letter-spacing:1px;margin-top:5px}.chart-signal-badge.BUY{color:#23d6a0;background:#0b2b22f2}.chart-signal-badge.SELL{color:#ff5277;background:#32101cf2}.chart-signal-badge.WAIT{color:#ffca67;background:#2b2417f2}.chart-signal-badge.OFF{display:none}
.disclaimer{padding:10px 16px;color:var(--gold);font-size:11px;background:#211c14;border-top:1px solid #4b3a1b}
.side{display:flex;flex-direction:column;gap:14px}.signalbox{padding:20px;text-align:center}.label{font-size:11px;color:var(--muted);letter-spacing:1.4px;font-weight:800}.signal{font-size:34px;font-weight:950;margin:12px 0}.signal.BUY{color:var(--green)}.signal.SELL{color:var(--red)}.signal.WAIT{color:var(--gold)}@keyframes signalFlash{0%,100%{opacity:1;filter:drop-shadow(0 0 12px currentColor);transform:scale(1)}50%{opacity:.28;filter:none;transform:scale(1.04)}}.signal.alerting,.chart-signal-badge.alerting{animation:signalFlash .65s steps(2,end) infinite}.sound-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.sound-btn{background:#102337;color:#eef3ff;border:1px solid #4a7397;border-radius:9px;padding:9px 12px;font-weight:900}.sound-btn.enabled{background:#103126;border-color:#23d6a0;color:#23d6a0}.sound-hint{font-size:10px;color:var(--muted)}
.price{font-size:27px;font-weight:800;letter-spacing:.5px;margin:6px 0}
.strength-banner{font-size:clamp(30px,5vw,48px);font-weight:1000;font-style:italic;letter-spacing:2px;text-transform:uppercase;-webkit-text-stroke:1px #fff;text-shadow:3px 3px 0 #111,5px 5px 0 #000,0 0 18px currentColor;transform:skew(-5deg);margin:12px 0}
.strength-banner.PERFECT{color:#ffca67}.strength-banner.AMAZING{color:#23d6a0}.strength-banner.TERRIFIC{color:#76b8ff}.strength-banner.UNAVAILABLE{color:#8e9bb3;-webkit-text-stroke:0;text-shadow:none}
 .countdown-box{margin:12px auto 0;padding:12px 10px;border:1px solid #34415a;border-radius:10px;background:#080d18;text-align:center}.countdown-label{font-size:10px;color:var(--muted);letter-spacing:1.4px;font-weight:900}.countdown-number{font-size:42px;line-height:1.1;font-weight:1000;color:var(--gold);margin-top:4px}.countdown-box.active{border-color:var(--gold);box-shadow:0 0 18px #ffca6725}.countdown-box.BUY .countdown-number{color:var(--green)}.countdown-box.SELL .countdown-number{color:var(--red)}.entry-status{font-size:12px;font-weight:900;letter-spacing:1px;padding:8px;border-radius:8px;background:#0a1020;border:1px solid #34415a;margin-top:10px}.entry-status.late{color:#ff5277;border-color:#ff5277}.entry-status.fresh{color:#23d6a0;border-color:#23d6a0}.small{color:var(--muted);font-size:11px}.meter{height:7px;background:#263148;border-radius:9px;margin:14px 0;overflow:hidden}.meter div{height:100%;width:0;background:var(--green);transition:width .3s}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:9px;padding:13px}.metric{padding:12px;background:#0a1020;border:1px solid #202c42;border-radius:10px}.metric .v{font-size:17px;font-weight:800;margin-top:7px}.good{color:var(--green)}.bad{color:var(--red)}.gold{color:var(--gold)}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:14px}.action{padding:15px 6px;border-radius:10px;font-weight:900;text-align:center;letter-spacing:1px}.buy{border:1px solid #1c916e;color:var(--green);background:#103126}.sell{border:1px solid #9b3450;color:var(--red);background:#351522}
.foot{max-width:1400px;margin:0 auto;padding:0 18px 24px;color:var(--muted);font-size:11px;line-height:1.6}.history{padding:14px 17px;color:var(--muted);font-size:12px;line-height:1.7}
@media(max-width:850px){main{grid-template-columns:1fr;padding:10px}#chart{height:330px}header{padding:14px}.side{display:grid;grid-template-columns:1fr 1fr}.signalpanel{grid-column:1/-1}.metrics{grid-column:1/-1}.actions{grid-column:1/-1}}@media(max-width:480px){.side{display:flex}.brand{font-size:16px}#chart{height:290px}}
</style></head>
<body><header><div><div class="brand">GODMODE <span>V4</span> / SIGNAL LAB</div><div class="sub">POCKET OPTION • MULTI-TIMEFRAME RESEARCH TERMINAL</div></div><div class="sound-controls"><button id="soundToggle" class="sound-btn" type="button">🔊 ENABLE SOUND</button><span id="soundHint" class="sound-hint">Tap once to enable BING / ROAR alerts</span><div class="pill">● SIGNAL-ONLY MODE</div></div></header>
<main><section class="panel"><div class="panelhead"><h2>PRICE ACTION / CANDLESTICKS</h2><div class="controls" id="timeframes"><button class="active" data-tf="5">5s</button><button data-tf="10">10s</button><button data-tf="15">15s</button><button data-tf="30">30s</button><button data-tf="60">1m</button><button data-tf="120">2m</button><button data-tf="180">3m</button><button data-tf="300">5m</button><button data-tf="600">10m</button><button data-tf="900">15m</button><button data-tf="1800">30m</button><button data-tf="3600">1h</button><button data-tf="7200">2h</button><button data-tf="14400">4h</button><button data-tf="86400">1d</button><button data-tf="604800">1w</button><button data-tf="2592000">1mo</button></div><select id="asset"><optgroup label="Forex"><option>EURUSD</option><option>GBPUSD</option><option>USDJPY</option><option>USDCHF</option><option>USDCAD</option><option>AUDUSD</option><option>NZDUSD</option><option>EURGBP</option><option>EURJPY</option><option>EURCHF</option><option>EURAUD</option><option>EURNZD</option><option>EURCAD</option><option>GBPJPY</option><option>GBPCHF</option><option>GBPAUD</option><option>GBPCAD</option><option>GBPNZD</option><option>AUDJPY</option><option>AUDCHF</option><option>AUDCAD</option><option>AUDNZD</option><option>NZDJPY</option><option>NZDCHF</option><option>NZDCAD</option><option>CADJPY</option><option>CADCHF</option><option>CHFJPY</option></optgroup><optgroup label="OTC Currency"><option>EURUSD_otc</option><option>AUDCAD_otc</option><option>AUDCHF_otc</option><option>AUDJPY_otc</option><option>AUDNZD_otc</option><option>AUDUSD_otc</option><option>CADCHF_otc</option><option>CADJPY_otc</option><option>CHFJPY_otc</option><option>EURCHF_otc</option><option>EURGBP_otc</option><option>EURJPY_otc</option><option>EURNZD_otc</option><option>GBPAUD_otc</option><option>GBPJPY_otc</option><option>GBPUSD_otc</option><option>NZDJPY_otc</option><option>NZDUSD_otc</option><option>USDCAD_otc</option><option>USDCHF_otc</option><option>USDJPY_otc</option><option>USDMXN_otc</option><option>USDBRL_otc</option><option>USDINR_otc</option><option>USDSGD_otc</option></optgroup><optgroup label="OTC Commodities"><option>Gold_otc</option><option>Silver_otc</option><option>Brent_Oil_otc</option><option>WTI_Crude_Oil_otc</option><option>Natural_Gas_otc</option><option>Platinum_spot_otc</option><option>Palladium_spot_otc</option></optgroup><optgroup label="OTC Stocks"><option>Apple_otc</option><option>Microsoft_otc</option><option>Amazon_otc</option><option>Tesla_otc</option><option>Netflix_otc</option><option>AMD_otc</option><option>Intel_otc</option><option>Cisco_otc</option><option>Alibaba_otc</option><option>Coinbase_otc</option><option>VISA_otc</option><option>ExxonMobil_otc</option><option>Pfizer_otc</option><option>Palantir_Technologies_otc</option><option>GameStop_otc</option></optgroup><optgroup label="OTC Crypto"><option>Bitcoin_otc</option><option>Ethereum_otc</option><option>BNB_otc</option><option>Solana_otc</option><option>Cardano_otc</option><option>Dogecoin_otc</option><option>Litecoin_otc</option><option>Chainlink_otc</option><option>Polygon_otc</option><option>Polkadot_otc</option><option>TRON_otc</option><option>Avalanche_otc</option><option>Toncoin_otc</option></optgroup><optgroup label="OTC Indices"><option>AUS_200_otc</option><option>E35EUR_otc</option><option>100GBP_otc</option><option>F40EUR_otc</option><option>JPN225_otc</option><option>D30EUR_otc</option><option>E50EUR_otc</option><option>SP500_otc</option><option>DJI30_otc</option><option>US100_otc</option></optgroup><optgroup label="Crypto"><option>Bitcoin</option><option>Ethereum</option><option>Dogecoin</option><option>Dash</option><option>BCH_EUR</option><option>BCH_GBP</option><option>BCH_JPY</option><option>BTC_GBP</option><option>BTC_JPY</option><option>Chainlink</option></optgroup></select><a class="platformlink" href="https://pocketoption.com/en/" target="_blank" rel="noopener noreferrer">OPEN POCKET OPTION ↗</a></div><div id="chart"><div id="chartSignalBadge" class="chart-signal-badge WAIT"><span id="chartSignalText">WAIT</span><span id="chartSignalPrice" class="chart-signal-price">LIVE PRICE UNAVAILABLE</span></div></div><div class="disclaimer" id="feedbanner">Connecting to configured Pocket Option session; demo candles remain labeled until live data is verified.</div></section>
<aside class="side"><section class="panel signalpanel"><div class="signalbox"><div class="label">CURRENT CONFLUENCE SIGNAL</div><div id="signal" class="signal WAIT">WAIT</div><div id="price" class="price">—</div><div id="assetname" class="small">EURUSD_otc</div><div class="meter"><div id="meter"></div></div><div id="strengthLabel" class="strength-banner UNAVAILABLE">UNAVAILABLE</div><div class="small">Confluence strength index (not win probability): <b id="confidence">—</b></div><div id="entryStatus" class="entry-status">Waiting for verified live feed</div></div><div class="actions"><div class="action buy">BUY / CALL</div><div class="action sell">SELL / PUT</div></div></section>
<section class="panel"><div class="panelhead"><h2>INDICATOR CONFIRMATION</h2><span class="small">LIVE UPDATE</span></div><div class="metrics"><div class="metric"><div class="label">EMA 9 / 21</div><div id="trend" class="v">—</div></div><div class="metric"><div class="label">RSI (14)</div><div id="rsi" class="v">—</div></div><div class="metric"><div class="label">MACD BIAS</div><div id="macd" class="v">—</div></div><div class="metric"><div class="label">WAVETREND</div><div id="wt" class="v">—</div><div id="wtevent" class="small">—</div></div><div class="metric"><div class="label">SCORE</div><div id="score" class="v">—</div></div></div></section>
<section class="panel"><div class="panelhead"><h2>DESIRE AI AGENT</h2><span class="small">EARLY X • BIG CHANGE DETECTOR</span></div><div class="history"><div id="desireLevel" style="font-size:19px;font-weight:950;letter-spacing:1px;color:var(--gold)">SCANNING</div><div id="desireStatus" style="font-size:14px;font-weight:850;margin-top:5px">Watching for a developing DI crossover</div><div id="earlyXDirection" style="margin-top:8px;font-weight:800">Direction: WAIT</div><div class="meter"><div id="earlyXMeter" style="background:var(--gold);width:0%"></div></div><div class="small">DESIRE setup score: <b id="earlyXScore">0/100</b> · setup grade, not win probability</div><div class="small" style="margin-top:7px">Monitoring +DI/-DI spread, velocity, acceleration and ADX strength.</div><div id="tradeCountdown" class="countdown-box" aria-live="assertive"><div class="countdown-label">DESIRE • ENTRY COUNTDOWN</div><div id="countdownNumber" class="countdown-number">—</div><div id="countdownMessage" class="small">Waiting for a verified live setup at 90+ strength</div><div class="small" style="margin-top:6px">TARGET ALERT TIME: <b id="countdownTime">—</b></div></div><div id="earlyXDetails" class="small" style="margin-top:7px">Waiting for verified live market data.</div></div></section>
<section class="panel"><div class="panelhead"><h2>SESSION STATUS</h2></div><div class="history"><div>Feed status: <b id="feedstatus" class="gold">CONNECTING / DEMO FALLBACK</b></div><div>Official live chart: <a style="color:var(--gold)" href="https://pocketoption.com/en/" target="_blank" rel="noopener noreferrer">Open Pocket Option ↗</a></div><div>Automatic execution: <b>DISABLED</b></div><div>Last refresh: <span id="updated">—</span></div><div>Strategy: EMA + RSI + MACD</div></div></section></aside></main>

<section class="panel" style="margin:0 16px 18px;padding:18px;border-radius:14px">
  <div class="panelhead"><h2>OTC GOD MODE CONFIRMATION</h2><span class="small">BOLLINGER 20/2 • STOCH 5/3/3 • RSI 7</span></div>
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:12px 0">
    <span id="godmodeLamp" class="lamp gold">●</span><b id="godmodeSignal" class="signal WAIT" style="font-size:26px;margin:0">WAIT</b>
    <span id="godmodeExpiry" class="small">2-candle expiry: —</span>
  </div>
  <div id="godmodeDetails" class="small">Waiting for verified live closed candles. Demo candles never trigger OTC God Mode signals.</div>
  <div id="godmodeLog" style="max-height:230px;overflow:auto;margin-top:12px"></div>
</section>
<div class="foot">This is an original experimental signal prototype, not the proprietary Godmode V4 algorithm. “PERFECT,” “AMAZING,” and “TERRIFIC” describe indicator confluence only and are not probabilities of winning. Entry timing is a candle-age warning, not a guarantee that a trade is safe. Binary-options trading carries a high risk of loss. Signals remain disabled unless Pocket Option live candles are actually received.</div>
<script>
const chartEl=document.getElementById('chart');let candleCanvas=null,selectedTF=15,lastChartBars=[],refreshInFlight=false;
const godmodeSeen=new Set();let soundEnabled=false,audioCtx=null,lastAudibleSignal="",lastMainSignal="";let signalLock=null;let previousLiveClose=null;let pendingCountdown=null;let latestRawSignal="WAIT",latestSignalIsLive=false,latestSignalStrength=0,lastDesireAlertKey="",lastEarlyXAlertKey="";
let desireSpeechQueue=Promise.resolve(),desireChosenVoice=null,desireVoiceWarningShown=false;
function chooseDesireVoice(){
 if(desireChosenVoice)return desireChosenVoice;
 const synth=window.speechSynthesis;
 const voices=synth&&synth.getVoices?synth.getVoices():[];
 if(!voices.length)return null;
 const english=voices.filter(v=>/^en([_-]|$)/i.test(v.lang||''));
 const pool=english.length?english:voices;
 const score=v=>{
  const n=String(v.name||'').toLowerCase(),lang=String(v.lang||'').toLowerCase();
  let s=0;
  if(/en-us|en_us/.test(lang))s+=35;
  if(/female|samantha|jenny|aria|zira|ava|serena|victoria|karen|susan|google us english|microsoft jenny|microsoft aria/.test(n))s+=65;
  if(/natural|enhanced|premium|neural/.test(n))s+=25;
  if(/compact|robot|male|daniel|david|alex|fred|junior/.test(n))s-=70;
  if(/google.*(female|us english)|en-us-x-.*female/.test(n))s+=30;
  if(v.localService)s+=2;
  return s;
 };
 // Cache one preferred English female-sounding voice for all Desire announcements.
 // Warning, countdown, and signal alerts must never switch speakers.
 desireChosenVoice=pool.slice().sort((a,b)=>score(b)-score(a))[0]||null;
 return desireChosenVoice;
}
function speakDesire(text){
 if(!soundEnabled)return;
 const phrase=String(text||'').trim();if(!phrase)return;
 // Use one cached device voice for every announcement; never mix recorded clips
 // or a second hosted voice into Desire's countdowns and signal alerts.
 desireSpeechQueue=desireSpeechQueue.then(()=>new Promise(resolve=>{
  const synth=window.speechSynthesis;
  if(!synth||!window.SpeechSynthesisUtterance){resolve();return;}
  const say=()=>{
   try{
    const utterance=new SpeechSynthesisUtterance(phrase);
    const voice=chooseDesireVoice();if(voice)utterance.voice=voice;
    utterance.lang=(voice&&voice.lang)||'en-US';
    utterance.rate=0.94;utterance.pitch=1.08;utterance.volume=1;
    let finished=false,watchdog;
    const finish=()=>{if(!finished){finished=true;clearTimeout(watchdog);resolve();}};
    watchdog=setTimeout(finish,Math.max(5000,phrase.length*180));
    utterance.onend=finish;utterance.onerror=finish;
    synth.speak(utterance);
   }catch(err){console.warn('DESIRE device voice unavailable',err);resolve();}
  };
  if(synth.getVoices&&synth.getVoices().length){say();}
  else{
   let done=false;
   const ready=()=>{if(done)return;done=true;if(synth.removeEventListener)synth.removeEventListener('voiceschanged',ready);chooseDesireVoice();say();};
   if(synth.addEventListener)synth.addEventListener('voiceschanged',ready,{once:true});
   setTimeout(ready,900);
  }
 }));
}
function cancelTradeCountdown(message){
 const state=pendingCountdown;if(state&&state.timer)clearInterval(state.timer);pendingCountdown=null;
 const box=document.getElementById("tradeCountdown");box.className="countdown-box";
 document.getElementById("countdownNumber").textContent="—";
 const target=document.getElementById("countdownTime");if(target)target.textContent="—";
 document.getElementById("countdownMessage").textContent=message||"Setup no longer qualifies — no alert";
}
function desireCountdownSeconds(tf){
 const seconds=Math.max(1,Number(tf)||5);
 if(seconds<=15)return 5;
 if(seconds<=60)return 8;
 if(seconds<=300)return 20;
 if(seconds<=900)return 30;
 if(seconds<=1800)return 45;
 return 60;
}
function startTradeCountdown(direction,asset,tf){
 if(pendingCountdown)return;
 const box=document.getElementById("tradeCountdown"),num=document.getElementById("countdownNumber"),msg=document.getElementById("countdownMessage");
 const duration=desireCountdownSeconds(tf);
 const state={direction,asset,tf,remaining:duration,endAt:Date.now()+duration*1000,timer:null};pendingCountdown=state;
 const target=document.getElementById("countdownTime");
 const showTarget=()=>{if(target)target.textContent=new Date(state.endAt).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});};
 box.className="countdown-box active "+direction;num.textContent=String(duration);msg.textContent="You have "+duration+" seconds to place your trade • "+direction+" • "+latestSignalStrength+"/100";showTarget();if(duration>5){speakDesire("You have "+duration+" seconds to place your trade");}else{speakDesire("Entering trade in "+duration);}let lastSpoken=duration;
 state.timer=setInterval(()=>{
  if(pendingCountdown!==state){clearInterval(state.timer);return;}
  if(!latestSignalIsLive||latestRawSignal!==direction||latestSignalStrength<90){
   clearInterval(state.timer);cancelTradeCountdown("Live setup weakened below 90/100 or direction changed — no alert");return;
  }
  state.remaining=Math.max(0,Math.ceil((state.endAt-Date.now())/1000));num.textContent=String(state.remaining);showTarget();
  if(state.remaining>0&&state.remaining<=5&&state.remaining!==lastSpoken){lastSpoken=state.remaining;msg.textContent="Entering trade in "+state.remaining+" • "+direction+" • "+latestSignalStrength+"/100";speakDesire(String(state.remaining));}
  if(state.remaining>0)return;
  clearInterval(state.timer);pendingCountdown=null;
  if(latestSignalIsLive&&latestRawSignal===direction&&latestSignalStrength>=90){
   num.textContent="NOW!";msg.textContent="NOW! SUPERIOR! — "+direction+" • "+latestSignalStrength+"/100 • "+new Date().toLocaleTimeString();speakDesire("Now! Superior!");playAlert(direction);lastMainSignal=direction+":"+asset+":"+tf;
   const sig=document.getElementById("signal");sig.textContent=direction;sig.className="signal "+direction+" alerting";
   document.getElementById("entryStatus").textContent="COUNTDOWN 0 — "+latestSignalStrength+"/100 — VERIFY LIVE PRICE NOW";
   document.getElementById("chartSignalBadge").className="chart-signal-badge "+direction+" alerting";document.getElementById("chartSignalText").textContent=direction;
  }else{num.textContent="—";msg.textContent="Strength fell below 90 at entry time — alert cancelled";box.className="countdown-box";}
 },250);
}
function gateSignal(rawSignal,asset,tf,bars,now,eligible){
  if(rawSignal!=="BUY"&&rawSignal!=="SELL")return {signal:rawSignal,alert:false};
  const last=bars&&bars.length?bars[bars.length-1]:null;
  const key=asset+":"+tf;
  if(!signalLock||signalLock.key!==key||now>=signalLock.until){
    if(!eligible)return {signal:"WAIT",alert:false};
    signalLock={key,direction:rawSignal,until:now+(Math.max(1,Number(tf)||5)+5)*1000,candleTime:last?last.time:null,continuationAlerted:false,lastClose:last?last.close:null};
    previousLiveClose=last?last.close:null;
    return {signal:rawSignal,alert:true};
  }
  // During the expiry lock, only permit one extra alert if the same live candle
  // is still moving decisively in the original direction and makes a new extreme.
  let continuation=false;
  if(rawSignal===signalLock.direction&&last&&last.time===signalLock.candleTime&&!signalLock.continuationAlerted){
    const body=Number(last.close)-Number(last.open);
    const aligned=rawSignal==="BUY"?body>0:body<0;
    const extendsMove=signalLock.lastClose!=null&&(rawSignal==="BUY"?Number(last.close)>Number(signalLock.lastClose):Number(last.close)<Number(signalLock.lastClose));
    continuation=aligned&&extendsMove;
  }
  if(last)signalLock.lastClose=Number(last.close);
  if(continuation&&eligible){signalLock.continuationAlerted=true;return {signal:rawSignal,alert:true,continuation:true};}
  return {signal:"WAIT",alert:false,locked:true};
}
function playAlert(kind){if(!soundEnabled)return;try{audioCtx=audioCtx||new (window.AudioContext||window.webkitAudioContext)();if(audioCtx.state==="suspended")audioCtx.resume();const now=audioCtx.currentTime;const notes=kind==="BUY"?[880,1174,880]:[220,165,110];notes.forEach((freq,i)=>{const osc=audioCtx.createOscillator(),gain=audioCtx.createGain();osc.type=kind==="BUY"?"sine":"sawtooth";osc.frequency.setValueAtTime(freq,now+i*.16);gain.gain.setValueAtTime(.0001,now+i*.16);gain.gain.exponentialRampToValueAtTime(.18,now+i*.16+.025);gain.gain.exponentialRampToValueAtTime(.0001,now+i*.16+.14);osc.connect(gain);gain.connect(audioCtx.destination);osc.start(now+i*.16);osc.stop(now+i*.16+.15)});}catch(e){console.warn("Audio alert unavailable",e)}}
document.getElementById("soundToggle").addEventListener("click",async()=>{try{audioCtx=audioCtx||new (window.AudioContext||window.webkitAudioContext)();await audioCtx.resume();soundEnabled=!soundEnabled;const b=document.getElementById("soundToggle");b.textContent=soundEnabled?"🔊 SOUND ON":"🔇 SOUND OFF";b.classList.toggle("enabled",soundEnabled);document.getElementById("soundHint").textContent=soundEnabled?"BING = BUY • ROAR = SELL":"Tap to enable sound alerts";if(soundEnabled){const osc=audioCtx.createOscillator(),gain=audioCtx.createGain();osc.frequency.value=660;gain.gain.value=.08;osc.connect(gain);gain.connect(audioCtx.destination);osc.start();osc.stop(audioCtx.currentTime+.12)}}catch(e){document.getElementById("soundHint").textContent="Sound blocked by browser; check phone volume/settings"}});
function renderGodmode(g){
  if(!g)return;
  const sig=g.signal, el=document.getElementById('godmodeSignal'), lamp=document.getElementById('godmodeLamp');
  el.textContent=sig?(sig.direction+' / '+sig.label):'WAIT';
  el.className='signal '+(sig?(sig.direction==='HIGHER'?'BUY':'SELL'):'WAIT');
  lamp.style.color=sig?(sig.direction==='HIGHER'?'#23d6a0':'#ff5277'):'#ffca67';
  lamp.style.background=lamp.style.color;
  document.getElementById('godmodeExpiry').textContent='2-candle expiry: '+(g.expiration_label||'—');
  document.getElementById('godmodeDetails').textContent=g.message||'No confirmed setup on latest closed candle.';
  if(sig && !godmodeSeen.has(sig.id)){
    godmodeSeen.add(sig.id);
    const row=document.createElement('div');row.style.cssText='padding:10px 2px;border-top:1px solid #26344b';
    row.innerHTML='<b style="color:'+(sig.direction==='HIGHER'?'#23d6a0':'#ff5277')+'">'+sig.direction+' / '+sig.label+'</b> — '+sig.asset+
      '<div class="small">'+new Date(sig.timestamp).toLocaleString()+' · '+sig.expiration_label+' expiry</div>';
    document.getElementById('godmodeLog').prepend(row);
    while(document.getElementById('godmodeLog').children.length>50)document.getElementById('godmodeLog').lastElementChild.remove();
  }
}

function initChart(){const badge=document.getElementById('chartSignalBadge');chartEl.replaceChildren();chartEl.style.position='relative';candleCanvas=document.createElement('canvas');candleCanvas.id='candleCanvas';candleCanvas.setAttribute('aria-label','Candlestick price chart');Object.assign(candleCanvas.style,{display:'block',width:'100%',height:'100%',minHeight:'250px'});chartEl.appendChild(candleCanvas);if(badge)chartEl.appendChild(badge);if(window.ResizeObserver)new ResizeObserver(()=>requestAnimationFrame(()=>drawCandles(lastChartBars))).observe(chartEl);window.addEventListener('resize',()=>requestAnimationFrame(()=>drawCandles(lastChartBars)));requestAnimationFrame(()=>drawCandles([]))}
function drawCandles(bars){
 if(!candleCanvas)return;
 lastChartBars=Array.isArray(bars)?bars:[];
 const rect=chartEl.getBoundingClientRect(),w=Math.max(300,Math.floor(rect.width)),h=Math.max(250,Math.floor(rect.height)),dpr=Math.max(1,window.devicePixelRatio||1);
 if(!rect.width||!rect.height){requestAnimationFrame(()=>drawCandles(lastChartBars));return}
 candleCanvas.style.width=w+'px';candleCanvas.style.height=h+'px';
 if(candleCanvas.width!==Math.floor(w*dpr)||candleCanvas.height!==Math.floor(h*dpr)){candleCanvas.width=Math.floor(w*dpr);candleCanvas.height=Math.floor(h*dpr)}
 const ctx=candleCanvas.getContext('2d');if(!ctx){document.getElementById('feedbanner').textContent='CHART ERROR: browser canvas unavailable';return}
 ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#030609';ctx.fillRect(0,0,w,h);
 const pad={l:12,r:78,t:18,b:28},pw=Math.max(1,w-pad.l-pad.r),ph=Math.max(1,h-pad.t-pad.b);
 const valid=lastChartBars.filter(b=>[b.open,b.high,b.low,b.close].every(v=>Number.isFinite(Number(v)))&&Number(b.high)>=Number(b.low))
 .map(b=>({time:Number(b.time),open:Number(b.open),high:Number(b.high),low:Number(b.low),close:Number(b.close)}));
 ctx.font='11px system-ui';ctx.strokeStyle='#19232d';ctx.lineWidth=1;
 for(let j=0;j<=5;j++){const y=pad.t+ph*j/5;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke()}
 if(!valid.length){ctx.fillStyle='#ffca67';ctx.textAlign='center';ctx.font='bold 14px system-ui';ctx.fillText('WAITING FOR LIVE CANDLES',w/2,h/2-8);ctx.font='11px system-ui';ctx.fillStyle='#aab7cc';ctx.fillText('The chart draws when the feed supplies OHLC data',w/2,h/2+14);return}
 let lo=Math.min(...valid.map(b=>b.low)),hi=Math.max(...valid.map(b=>b.high));
 if(!Number.isFinite(lo)||!Number.isFinite(hi))return;
 // ATR-based Supertrend-style cloud, computed locally from the displayed OHLC candles.
 const n=valid.length,atr=Array(n).fill(null),upper=Array(n).fill(null),lower=Array(n).fill(null),trend=Array(n).fill(1),line=Array(n).fill(null);
 const tr=valid.map((b,i)=>i===0?b.high-b.low:Math.max(b.high-b.low,Math.abs(b.high-valid[i-1].close),Math.abs(b.low-valid[i-1].close)));
 const period=10,mult=2.4;
 for(let i=0;i<n;i++){
   if(i<period-1){atr[i]=tr.slice(0,i+1).reduce((a,v)=>a+v,0)/(i+1)}
   else if(i===period-1){atr[i]=tr.slice(i-period+1,i+1).reduce((a,v)=>a+v,0)/period}
   else atr[i]=(atr[i-1]*(period-1)+tr[i])/period;
   const mid=(valid[i].high+valid[i].low)/2,bu=mid+mult*atr[i],bl=mid-mult*atr[i];
   upper[i]=(i&&bu>upper[i-1]&&valid[i-1].close<=upper[i-1])?upper[i-1]:bu;
   lower[i]=(i&&bl<lower[i-1]&&valid[i-1].close>=lower[i-1])?lower[i-1]:bl;
   if(i){if(trend[i-1]===1)trend[i]=valid[i].close<lower[i]? -1:1;else trend[i]=valid[i].close>upper[i]?1:-1}
   line[i]=trend[i]===1?lower[i]:upper[i];
 }
 // Add breathing room so the cloud and candles do not touch the chart frame.
 const rawRange=hi-lo||Math.max(Math.abs(hi)*.0002,.00001);lo-=rawRange*.08;hi+=rawRange*.08;
 const yOf=v=>pad.t+(hi-v)/(hi-lo)*ph;
 const slot=pw/valid.length,bw=Math.max(2,Math.min(9,slot*.68)),xOf=i=>pad.l+i*slot+slot/2;
 // Soft red/teal trend cloud between price and the ATR trailing line.
 for(let i=1;i<n;i++){
   const up=trend[i]===1, color=up?'35,220,190':'255,75,42';
   const x0=xOf(i-1),x1=xOf(i),p0=yOf(valid[i-1].close),p1=yOf(valid[i].close),l0=yOf(line[i-1]),l1=yOf(line[i]);
   ctx.beginPath();ctx.moveTo(x0,p0);ctx.lineTo(x1,p1);ctx.lineTo(x1,l1);ctx.lineTo(x0,l0);ctx.closePath();
   const grad=ctx.createLinearGradient(0,Math.min(p0,p1,l0,l1),0,Math.max(p0,p1,l0,l1));
   grad.addColorStop(0,'rgba('+color+',0.50)');grad.addColorStop(1,'rgba('+color+',0.13)');
   ctx.fillStyle=grad;ctx.fill();
 }
 // Draw the stepped trailing trend line on top of the cloud.
 for(let i=1;i<n;i++){
   ctx.strokeStyle=trend[i]===1?'#3be4c5':'#ff5b38';ctx.lineWidth=Math.max(2,Math.min(4,slot*.35));
   ctx.beginPath();ctx.moveTo(xOf(i-1),yOf(line[i-1]));ctx.lineTo(xOf(i),yOf(line[i]));ctx.stroke();
 }
 // Recent swing zones, styled like the reference image; these are visual areas, not trade orders.
 const zoneCount=Math.min(n,Math.max(12,Math.floor(n*.28))),recent=valid.slice(-zoneCount);
 const swingHigh=Math.max(...recent.map(b=>b.high)),swingLow=Math.min(...recent.map(b=>b.low));
 const zonePad=(hi-lo)*.035;
 function zone(top,bottom,fill,stroke,label){
   const y1=yOf(top),y2=yOf(bottom);ctx.fillStyle=fill;ctx.fillRect(pad.l,Math.min(y1,y2),pw,Math.max(2,Math.abs(y2-y1)));
   ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.strokeRect(pad.l,Math.min(y1,y2),pw,Math.max(2,Math.abs(y2-y1)));
   ctx.fillStyle=stroke;ctx.font='bold 10px system-ui';ctx.textAlign='left';ctx.fillText(label,w-pad.r+5,Math.max(12,Math.min(h-30,(y1+y2)/2)));
 }
 zone(swingHigh,swingHigh-zonePad,'rgba(255,54,65,.10)','#a93642','RESISTANCE');
 zone(swingLow+zonePad,swingLow,'rgba(111,255,91,.08)','#5c9e54','SUPPORT');
 // Candlesticks remain the foreground layer.
 valid.forEach((b,i)=>{const x=xOf(i),yo=yOf(b.open),yc=yOf(b.close),yh=yOf(b.high),yl=yOf(b.low),col=b.close>=b.open?'#9bf8e4':'#ffbd39';
   ctx.strokeStyle=col;ctx.fillStyle=col;ctx.lineWidth=Math.max(1,Math.min(2,slot*.18));
   ctx.beginPath();ctx.moveTo(x,yh);ctx.lineTo(x,yl);ctx.stroke();
   const top=Math.min(yo,yc),bh=Math.max(2,Math.abs(yc-yo));ctx.fillRect(x-bw/2,top,bw,bh);
 });
 // Price scale and timestamps.
 ctx.fillStyle='#d4deee';ctx.textAlign='left';ctx.font='11px system-ui';
 for(let j=0;j<=5;j++){const v=hi-(hi-lo)*j/5;ctx.fillText(v.toFixed(6),w-pad.r+6,pad.t+ph*j/5+4)}
 const first=new Date(valid[0].time*1000),last=new Date(valid[valid.length-1].time*1000);
 ctx.textAlign='left';ctx.fillText(Number.isNaN(first.getTime())?'':first.toLocaleTimeString(),pad.l,h-8);
 ctx.textAlign='right';ctx.fillText(Number.isNaN(last.getTime())?'':last.toLocaleTimeString(),w-pad.r,h-8);
}
function liveSignalLabel(mode){return mode==='POCKET OPTION LIVE CANDLES'||mode==='POCKET OPTION LIVE TICKS'}
async function refresh(){if(refreshInFlight)return;refreshInFlight=true;const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),5000);try{const r=await fetch('/api/market?tf='+selectedTF+'&asset='+encodeURIComponent(document.getElementById('asset').value),{signal:controller.signal,cache:'no-store'});if(!r.ok)throw new Error('Market API HTTP '+r.status);const d=await r.json();renderGodmode(d.godmode);const xr=d.hybrid_engine||{};const liveRadar=liveSignalLabel(d.data_mode);const desireLevel=document.getElementById('desireLevel');const desireIsSuperior=liveRadar&&Boolean(xr.desire_superior);desireLevel.textContent=liveRadar?(xr.desire_level||'SCANNING'):'PAUSED';desireLevel.style.color=desireIsSuperior?'var(--green)':(xr.early_x_warning?'var(--gold)':'var(--muted)');document.getElementById('desireStatus').textContent=liveRadar?(xr.desire_message||'Monitoring DI crossover formation.'):'Requires verified live Pocket Option data; no demo alerts.';document.getElementById('earlyXDirection').textContent='DESIRE direction: '+(liveRadar?(xr.desire_direction||xr.early_x_direction||'WAIT'):'WAIT');document.getElementById('earlyXScore').textContent=(liveRadar?(xr.desire_score??xr.early_x_score??0):0)+'/100';document.getElementById('earlyXMeter').style.width=(liveRadar?(xr.desire_score??xr.early_x_score??0):0)+'%';document.getElementById('earlyXMeter').style.background=desireIsSuperior?'var(--green)':(xr.early_x_warning?'var(--gold)':'var(--muted)');document.getElementById('earlyXDetails').textContent=liveRadar?(xr.projected_cross_bars!=null?'Projected crossover: about '+xr.projected_cross_bars+' candle(s), if current DI movement persists. ':'')+'DESIRE grades crossover momentum and ADX context; it cannot guarantee a market move.':'Radar requires verified live candles/ticks; demo data is not used.';if(liveRadar&&soundEnabled&&xr.early_x_warning){const candle=(Array.isArray(d.bars)&&d.bars.length)?String(d.bars[d.bars.length-1].time):'unknown';const earlyKey=[d.asset,d.timeframe_seconds,candle,xr.early_x_direction||'WAIT'].join('|');if(earlyKey!==lastEarlyXAlertKey){speakDesire('Attention. A potential wide X crossover is developing. Prepare for a possible change in market direction.');lastEarlyXAlertKey=earlyKey;}}if(desireIsSuperior&&soundEnabled&&xr.desire_direction!=='WAIT'){const candle=(Array.isArray(d.bars)&&d.bars.length)?String(d.bars[d.bars.length-1].time):'unknown';const alertKey=[d.asset,d.timeframe_seconds,candle,xr.desire_direction].join('|');if(alertKey!==lastDesireAlertKey){playAlert(xr.desire_direction);speakDesire('Superior signal detected. Momentum and directional pressure are aligned. Review the chart and confirm your entry.');lastDesireAlertKey=alertKey;document.getElementById('soundHint').textContent='DESIRE SUPERIOR alert: '+xr.desire_direction+' • verify before entry';}}let chartBarCount=0;try{const raw=Array.isArray(d.bars)?d.bars:[];const clean=raw.map(b=>{let t=Number(b.time);while(t>1e11)t=Math.floor(t/1000);const o=Number(b.open),h=Number(b.high),l=Number(b.low),c=Number(b.close);return {time:Math.floor(t),open:o,high:h,low:l,close:c}}).filter(b=>Number.isSafeInteger(b.time)&&b.time>0&&[b.open,b.high,b.low,b.close].every(Number.isFinite)&&b.high>=Math.max(b.open,b.close,b.low)&&b.low<=Math.min(b.open,b.close,b.high)).sort((a,b)=>a.time-b.time);const unique=[...new Map(clean.map(b=>[b.time,b])).values()];chartBarCount=unique.length;drawCandles(unique);if(!unique.length)console.warn('No valid candle bars to draw; API bars:',raw.length);}catch(chartError){console.error('Candle chart rendering failed:',chartError);document.getElementById('feedbanner').textContent='CHART ERROR: '+(chartError.message||String(chartError));}
const s=document.getElementById('signal');const liveSignalForAlert=d.data_mode==='POCKET OPTION LIVE CANDLES'||d.data_mode==='POCKET OPTION LIVE TICKS';latestRawSignal=Number(d.confidence)>=90?d.signal:'WAIT';latestSignalStrength=Number(d.confidence)||0;latestSignalIsLive=liveSignalForAlert;const gated=liveSignalForAlert?gateSignal(latestRawSignal,d.asset,d.timeframe_seconds,lastChartBars,Date.now(),d.entry_status==='FRESH WINDOW'&&latestSignalStrength>=90):{signal:'WAIT',alert:false};const shownSignal=gated.signal;if(gated.alert&&(shownSignal==='BUY'||shownSignal==='SELL'))startTradeCountdown(shownSignal,d.asset,d.timeframe_seconds);const displaySignal=pendingCountdown?pendingCountdown.direction:shownSignal;s.textContent=displaySignal;s.className='signal '+displaySignal;if(pendingCountdown){document.getElementById('entryStatus').textContent='COUNTDOWN ACTIVE — WAIT FOR BEEP / ROAR';}else if(!gated.alert){s.classList.remove('alerting');}if(gated.locked){document.getElementById('entryStatus').textContent='SIGNAL LOCKED — expiry cooldown active';}const exactPrice=d.price==null?'LIVE PRICE UNAVAILABLE':String(Number(d.price));document.getElementById('price').textContent=exactPrice;const badge=document.getElementById('chartSignalBadge');const liveSignal=d.data_mode==='POCKET OPTION LIVE CANDLES'||d.data_mode==='POCKET OPTION LIVE TICKS';const visibleSignal=liveSignal&&((pendingCountdown&&(pendingCountdown.direction==='BUY'||pendingCountdown.direction==='SELL'))||(shownSignal==='BUY'||shownSignal==='SELL'))?(pendingCountdown?pendingCountdown.direction:shownSignal):'WAIT';badge.className='chart-signal-badge '+(liveSignal?visibleSignal:'OFF');document.getElementById('chartSignalText').textContent=liveSignal?visibleSignal:'WAIT';document.getElementById('chartSignalPrice').textContent=liveSignal?exactPrice:'LIVE PRICE UNAVAILABLE';document.getElementById('assetname').textContent=d.asset+' • '+d.timeframe_seconds+'s candles';document.getElementById('confidence').textContent=d.confidence?d.confidence+'/100 strength':'—';const sl=document.getElementById('strengthLabel');sl.textContent=d.strength_label;sl.className='strength-banner '+(Number(d.confidence)<80?'BELOW80':d.strength_label);const es=document.getElementById('entryStatus');es.textContent=d.entry_status;es.className='entry-status '+(d.entry_status==='FRESH WINDOW'?'fresh':d.entry_status.includes('LATE')||d.entry_status==='FEED UNAVAILABLE'?'late':'');document.getElementById('meter').style.width=d.confidence+'%';document.getElementById('meter').style.background=d.signal==='SELL'?'#ff5277':d.signal==='BUY'?'#23d6a0':'#ffca67';document.getElementById('trend').textContent=d.trend;document.getElementById('trend').className='v '+(d.trend==='BULLISH'?'good':d.trend==='BEARISH'?'bad':'');document.getElementById('rsi').textContent=d.rsi==null?'—':d.rsi;document.getElementById('rsi').className='v '+(d.rsi!=null&&(d.rsi>70||d.rsi<30)?'gold':'');document.getElementById('macd').textContent=d.macd_bias;document.getElementById('macd').className='v '+(d.macd_bias==='BULLISH'?'good':d.macd_bias==='BEARISH'?'bad':'');document.getElementById('wt').textContent=d.wavetrend==null?'—':d.wavetrend+' / '+d.wavetrend_signal;document.getElementById('wt').className='v '+(d.wavetrend_bias==='BULLISH'?'good':d.wavetrend_bias==='BEARISH'?'bad':'');document.getElementById('wtevent').textContent=d.wavetrend_event;document.getElementById('score').textContent=(d.score>0?'+':'')+d.score;document.getElementById('updated').textContent=new Date(d.updated_at).toLocaleTimeString();const live=d.data_mode==='POCKET OPTION LIVE CANDLES',liveTicks=d.data_mode==='POCKET OPTION LIVE TICKS',liveVerified=live||liveTicks;document.getElementById('feedbanner').textContent=live?'POCKET OPTION LIVE CANDLES — '+chartBarCount+' candles rendered.':liveTicks?'POCKET OPTION LIVE TICKS — '+chartBarCount+' tick-built candles rendered. Read-only feed; no trades executed.':(chartBarCount?'DEMO / DIAGNOSTIC CANDLES — '+chartBarCount+' shown; NOT live Pocket Option prices. Signals disabled. ':'LIVE FEED UNAVAILABLE — 0 candles rendered. ')+(d.feed_status||'');document.getElementById('feedstatus').textContent=live?'POCKET OPTION LIVE CANDLES':liveTicks?'POCKET OPTION LIVE TICKS':'LIVE FEED UNAVAILABLE — DEMO CHART';document.getElementById('feedstatus').className=liveVerified?'good':'gold' }catch(e){document.getElementById('updated').textContent='API reconnecting';document.getElementById('feedbanner').textContent='API / CHART ERROR: '+(e.message||String(e));console.error('GODMODE refresh failed:',e)}finally{clearTimeout(timeout);refreshInFlight=false}}
document.querySelectorAll('[data-tf]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-tf]').forEach(x=>x.classList.remove('active'));b.classList.add('active');selectedTF=Number(b.dataset.tf);refresh()});async function loadBrokerAssets(){try{const r=await fetch('/api/assets');const d=await r.json();if(!Array.isArray(d.assets)||!d.assets.length)return;const select=document.getElementById('asset');const existing=new Set([...select.options].map(o=>o.value.toLowerCase()));let group=select.querySelector('optgroup[data-broker-assets="true"]');if(!group){group=document.createElement('optgroup');group.label='Pocket Option Broker Catalog';group.dataset.brokerAssets='true';select.appendChild(group)}for(const symbol of d.assets){if(!existing.has(String(symbol).toLowerCase())){const opt=document.createElement('option');opt.value=String(symbol);opt.textContent=String(symbol);group.appendChild(opt);existing.add(String(symbol).toLowerCase())}}}catch(e){console.warn('Broker asset catalog unavailable',e)}}document.getElementById('asset').onchange=refresh;initChart();loadBrokerAssets();refresh();setInterval(refresh,250);setInterval(loadBrokerAssets,60000);
</script></body></html>"""
