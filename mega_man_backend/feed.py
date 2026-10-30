"""Read-only Pocket Option feed adapter. No synthetic candles or trade methods."""
from __future__ import annotations
import asyncio, json, logging, math, os, threading, time
from datetime import datetime, timezone
from market_feed import normalize_historical_candles, ticks_to_candles

log=logging.getLogger("godmode.next.feed")

def _configure_direct_websocket_transport():
    """Disable websockets' implicit environment-proxy discovery for Pocket Option.

    Pocket Option's region probe uses direct asyncio TCP connections. Match that
    behavior for the actual WebSocket handshake so a host-level HTTPS_PROXY or
    ALL_PROXY cannot silently send the broker socket down a different route.
    Only report whether proxy settings exist; never expose their values.
    """
    proxy_names = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                   "ALL_PROXY", "all_proxy")
    proxy_configured = any(os.getenv(name) for name in proxy_names)
    try:
        import inspect
        import websockets
        connect = websockets.connect
        if getattr(connect, "_mega_man_direct_egress", False):
            return {"mode": "direct", "proxy_environment_configured": proxy_configured}
        if "proxy" not in inspect.signature(connect).parameters:
            return {"mode": "library_default", "proxy_environment_configured": proxy_configured}

        def direct_connect(*args, **kwargs):
            kwargs.setdefault("proxy", None)
            return connect(*args, **kwargs)

        direct_connect._mega_man_direct_egress = True
        websockets.connect = direct_connect
        log.info("Mega Man Pocket Option WebSocket egress=direct; proxy environment configured=%s",
                 proxy_configured)
        return {"mode": "direct", "proxy_environment_configured": proxy_configured}
    except Exception as exc:
        log.warning("Could not configure direct WebSocket egress (%s)", type(exc).__name__)
        return {"mode": "library_default", "proxy_environment_configured": proxy_configured}


def _install_safe_protocol_trace():
    """Trace Engine.IO / Socket.IO phases without logging packet contents."""
    from pocketoptionapi.ws.client import WebsocketClient
    current = WebsocketClient._handle_text_message
    if getattr(current, "_mega_man_protocol_trace", False):
        return

    async def traced(self, message):
        phase = "other_text"
        if isinstance(message, str):
            if message.startswith('0{"sid":'):
                phase = "engineio_open_received"
            elif message == "2":
                phase = "engineio_ping_received"
            elif message.startswith('40{"sid":'):
                phase = "socketio_namespace_connected_auth_sent"
            elif message == "41":
                phase = "socketio_namespace_disconnected"
            elif message.startswith("451-["):
                try:
                    packet = json.loads(message.split("-", 1)[1])
                    event_name = packet[0] if isinstance(packet, list) and packet else "unknown"
                    phase = "binary_event_header:" + str(event_name)[:40]
                except Exception:
                    phase = "binary_event_header_unparsed"
            elif message.startswith("42") and "NotAuthorized" in message:
                phase = "auth_rejected"
            elif message.startswith("42"):
                phase = "socketio_event_received"
        self._mega_man_last_phase = phase
        if phase != "engineio_ping_received":
            log.info("Pocket Option protocol phase=%s", phase)
        return await current(self, message)

    traced._mega_man_protocol_trace = True
    WebsocketClient._handle_text_message = traced


def _normalize_demo_flag(value):
    """Accept only Pocket Option's documented 0=live / 1=demo flag."""
    if isinstance(value, bool):
        return int(value)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 1
    return parsed if parsed in (0, 1) else 1


def _safe_connection_detail(value):
    """Keep useful transport errors while stripping credentials and URL queries."""
    import re
    if value is None:
        return "no detail returned"
    detail=str(value)[:500]
    detail=re.sub(r'(?i)(session|ssid|token|password|authorization|cookie)(["\\s:=]+)[^,;\\s}]+',r'\\1\\2[redacted]',detail)
    detail=re.sub(r'(wss?://[^\\s?]+)\\?[^\\s]+',r'\\1?[redacted]',detail)
    return detail
class PocketOptionFeed:
    def __init__(self):
        self.lock=threading.RLock()
        self.client=None
        self.connecting=False
        self.status="PO_AUTH_JSON missing"
        self.debug={"stage":"boot","tick_count":0,"history_count":0,"normalized_candle_count":0}
        self.cache={}
        self.updated={}
        self.subscribed=set()
        self.subscription_times={}
        self.history_pending=set()
        # PocketOptionAPI uses one shared history event/buffer; serialize history
        # requests and retain completed results for the next HTTP poll.
        self.history_request_lock=threading.Lock()
        self.history_results={}
        self.offset=0.0
        self.worker_started=False

    @staticmethod
    def _auth():
        """Normalize supported Render auth-secret shapes to the raw Socket.IO auth packet.

        The upstream client parses SSID with an order-sensitive regex, so always
        serialize session before isDemo. Never log or expose the session value.
        """
        raw=os.getenv("PO_AUTH_JSON","").strip()
        if not raw:return None

        def packet_from_payload(payload):
            if not isinstance(payload,dict):return None
            session=payload.get("session")
            uid=payload.get("uid")
            if not isinstance(session,str) or not session or uid is None:return None
            # Keep the parser-required order stable regardless of the JSON the user pasted.
            normalized={"session":session,"isDemo":_normalize_demo_flag(payload.get("isDemo",1)),"uid":uid}
            for key,value in payload.items():
                if key not in normalized:normalized[key]=value
            normalized.setdefault("platform",2)
            normalized.setdefault("isFastHistory",True)
            normalized.setdefault("isOptimized",True)
            return '42'+json.dumps(["auth",normalized],separators=(",",":"))

        # Pocket Option browser exports commonly provide the complete Socket.IO packet.
        if raw.startswith('42["auth",'):
            try:
                packet=json.loads(raw[2:])
                if isinstance(packet,list) and len(packet)>1 and packet[0]=="auth":
                    normalized=packet_from_payload(packet[1])
                    if normalized:return normalized
            except Exception:
                pass
            return raw

        try:obj=json.loads(raw)
        except Exception:return None
        if isinstance(obj,str) and obj.startswith('42["auth",'):
            return PocketOptionFeed._auth_from_packet_string(obj)
        if isinstance(obj,dict):
            # Some exports wrap the packet in an "ssid" field.
            ssid=obj.get("ssid")
            if isinstance(ssid,str) and ssid.startswith('42["auth",'):
                try:
                    packet=json.loads(ssid[2:])
                    if isinstance(packet,list) and len(packet)>1 and packet[0]=="auth":
                        normalized=packet_from_payload(packet[1])
                        if normalized:return normalized
                except Exception:
                    return ssid
                return ssid
            payload=obj.get("auth") if isinstance(obj.get("auth"),dict) else obj
            return packet_from_payload(payload)
        return None

    @staticmethod
    def _auth_from_packet_string(packet_string):
        try:
            packet=json.loads(packet_string[2:])
            payload=packet[1] if isinstance(packet,list) and len(packet)>1 and packet[0]=="auth" else None
            if isinstance(payload,dict):
                session=payload.get("session")
                uid=payload.get("uid")
                if isinstance(session,str) and session and uid is not None:
                    normalized={"session":session,"isDemo":_normalize_demo_flag(payload.get("isDemo",1)),"uid":uid}
                    for key,value in payload.items():
                        if key not in normalized:normalized[key]=value
                    normalized.setdefault("platform",2)
                    normalized.setdefault("isFastHistory",True)
                    normalized.setdefault("isOptimized",True)
                    return '42'+json.dumps(["auth",normalized],separators=(",",":"))
        except Exception:
            pass
        return packet_string

    @staticmethod
    def auth_status():
        """Return a safe explanation of auth configuration without exposing credentials."""
        raw=os.getenv("PO_AUTH_JSON","").strip()
        if not raw:
            return "PO_AUTH_JSON is not set on the API service"
        try:
            obj=json.loads(raw) if not raw.startswith('42["auth",') else None
        except Exception:
            return "PO_AUTH_JSON is not valid JSON or a supported Socket.IO auth packet"
        if raw.startswith('42["auth",'):
            try:
                packet=json.loads(raw[2:])
                payload=packet[1] if isinstance(packet,list) and len(packet)>1 and packet[0]=="auth" else None
            except Exception:
                payload=None
        elif isinstance(obj,str) and obj.startswith('42["auth",'):
            try:
                packet=json.loads(obj[2:])
                payload=packet[1] if isinstance(packet,list) and len(packet)>1 and packet[0]=="auth" else None
            except Exception:
                payload=None
        elif isinstance(obj,dict):
            payload=obj.get("auth") if isinstance(obj.get("auth"),dict) else obj
            ssid=payload.get("ssid") if isinstance(payload,dict) else None
            if isinstance(ssid,str) and ssid.startswith('42["auth",'):
                try:
                    packet=json.loads(ssid[2:])
                    payload=packet[1] if isinstance(packet,list) and len(packet)>1 and packet[0]=="auth" else None
                except Exception:
                    payload=None
        else:
            payload=None
        if not isinstance(payload,dict) or not isinstance(payload.get("session"),str) or not payload.get("session") or payload.get("uid") is None:
            return "PO_AUTH_JSON must contain a valid session and uid (or a complete supported auth packet)"
        demo_flag=_normalize_demo_flag(payload.get("isDemo",1))
        mode_label="demo" if demo_flag else "live"
        mode_source="explicit isDemo flag" if "isDemo" in payload else "safe default (demo)"
        return f"PO_AUTH_JSON is present and has the expected fields; account mode is {mode_label} ({mode_source}); broker acceptance is still unverified"

    def start(self):
        with self.lock:
            if self.worker_started:return
            self.worker_started=True
        threading.Thread(target=self._supervisor,daemon=True,name="godmode-next-po-supervisor").start()

    def _supervisor(self):
        while True:
            try:
                auth=self._auth()
                if self.client is None and not auth:
                    self.status=self.auth_status()
                elif self.client is None and auth:
                    self._connect()
                elif self.client is not None:
                    try:
                        if not self.client.check_connect(): self._drop("broker disconnected")
                    except Exception:self._drop("broker health check failed")
            except Exception as e:
                self.status="Pocket Option connection error: "+type(e).__name__
                log.warning("PO supervisor error (%s)",type(e).__name__)
            time.sleep(15)

    def _connect(self):
        with self.lock:
            if self.connecting:return
            self.connecting=True
        try:
            transport_mode=_configure_direct_websocket_transport()
            from pocketoptionapi import PocketOption
            _install_safe_protocol_trace()
            client=PocketOption(self._auth())
            ok,err=client.connect()
            if not ok:
                detail=_safe_connection_detail(err)
                # Capture transport state from the upstream client without exposing SSID/session data.
                ws_client=getattr(getattr(client,"api",None),"websocket",None)
                try:
                    import pocketoptionapi.global_value as global_value
                    upstream_reason=_safe_connection_detail(getattr(global_value,"websocket_error_reason",None))
                except Exception:
                    upstream_reason="unavailable"
                try:
                    parsed_url=getattr(ws_client,"url",None)
                    from urllib.parse import urlparse
                    server_host=urlparse(parsed_url).hostname if parsed_url else None
                except Exception:
                    server_host=None
                try:
                    thread_alive=bool(getattr(client,"websocket_thread",None) and client.websocket_thread.is_alive())
                except Exception:
                    thread_alive=False
                transport_debug={
                    "stage":"websocket_connect_failed",
                    "error_type":type(err).__name__ if err else "unknown",
                    "error_detail":detail,
                    "server_host":server_host,
                    "reconnect_attempts":getattr(ws_client,"reconnect_attempts",None),
                    "account_mode":"demo" if getattr(ws_client,"is_demo",None) is True else ("live" if getattr(ws_client,"is_demo",None) is False else "unknown"),
                    "auth_uid_present":getattr(ws_client,"uid",None) is not None,
                    "protocol_phase":getattr(ws_client,"_mega_man_last_phase","no_inbound_message"),
                    "auth_session_length":len(getattr(ws_client,"session","") or ""),
                    "upstream_error":upstream_reason,
                    "websocket_thread_alive":thread_alive,
                    "websocket_egress":transport_mode.get("mode","unknown"),
                    "proxy_environment_configured":transport_mode.get("proxy_environment_configured",False),
                    "automatic_execution":False
                }
                self.status="Pocket Option connection failed: "+detail
                self.debug=transport_debug
                log.warning("PO WebSocket connect failed; type=%s detail=%s transport=%s",
                            type(err).__name__ if err else "unknown",detail,
                            {k:v for k,v in transport_debug.items() if k not in ("auth_session_length",)})
                # Avoid leaving a failed client's background event loop running between retries.
                try:client.disconnect_websocket(timeout=2.0)
                except Exception:pass
                return
            api=getattr(client,"api",None)
            if api is not None:
                # Instrument the library's actual updateStream callback and retain
                # ticks under the broker-provided symbol. This distinguishes a dead
                # subscription from ticks arriving under a symbol alias.
                original_tick_callback=getattr(api,"_on_stream_tick",None)
                if callable(original_tick_callback):
                    def capture_stream_tick(stream_asset,timestamp,price):
                        try:
                            with self.lock:
                                self.debug.update({
                                    "last_stream_asset":str(stream_asset)[:80],
                                    "last_stream_timestamp":float(timestamp),
                                    "last_stream_price":float(price),
                                    "stream_tick_seen_at":time.time()
                                })
                        except Exception:
                            pass
                        return original_tick_callback(stream_asset,timestamp,price)
                    api._on_stream_tick=capture_stream_tick
                def on_loop(factory,timeout=5):
                    loop=getattr(client,"_io_loop",None)
                    if loop is None or not loop.is_running():raise RuntimeError("socket event loop unavailable")
                    fut=asyncio.run_coroutine_threadsafe(factory(),loop)
                    try:return fut.result(timeout=timeout)
                    except Exception:fut.cancel();raise
                api.send_websocket_request=lambda name,msg,request_id="",no_force_send=True:on_loop(lambda:api.websocket.send_message("42"+json.dumps(msg)))
                api.send_subfor=lambda asset:on_loop(lambda:api.websocket.send_subfor(asset))
            deadline=time.time()+15
            while time.time()<deadline:
                try:
                    if client.check_connect() and client.is_time_synced():break
                except Exception:pass
                time.sleep(.25)
            try:self.offset=float(getattr(getattr(getattr(client,"api",None),"time_sync",None),"_stream_tz_offset",0) or 0)
            except (ValueError,TypeError):self.offset=0
            with self.lock:self.client=client;self.subscribed.clear();self.subscription_times.clear()
            self.status="Connected; waiting for symbol subscription"
        except Exception as e:
            detail=_safe_connection_detail(e)
            self.status="Pocket Option client setup failed: "+detail
            self.debug={"stage":"client_setup_failed","error_type":type(e).__name__,
                        "error_detail":detail,"automatic_execution":False}
            log.warning("PO client setup failed; type=%s detail=%s",type(e).__name__,detail)
        finally:
            with self.lock:self.connecting=False

    def _drop(self,reason):
        old=self.client
        with self.lock:
            self.client=None;self.subscribed.clear();self.subscription_times.clear()
            self.status="Live feed disconnected: "+reason
        try:
            if old and callable(getattr(old,"disconnect_websocket",None)):old.disconnect_websocket()
        except Exception:pass

    def snapshot(self,asset,tf):
        asset=str(asset)[:64];tf=max(5,min(2592000,int(tf)))
        key=(asset,tf);now=time.time()
        with self.lock:
            client=self.client
            bars=list(self.cache.get(key,[]))
            last=self.updated.get(key,0)
        if client is None:
            self.start()
            return self._response(asset,tf,bars,last,False,self.status)
        try:
            if not client.check_connect():
                self._drop("socket disconnected")
                return self._response(asset,tf,bars,last,False,self.status)
            if not client.is_time_synced():
                return self._response(asset,tf,bars,last,False,"Waiting for Pocket Option server time sync")
            actual=asset
            try:
                catalog=client.get_assets() or {}
                # Catalog keys are broker symbols; metadata names are display labels
                # (for example "Gold"). Resolve display labels to the real symbol.
                records=[]
                def add_record(symbol,value=None):
                    if isinstance(symbol,str) and symbol.strip():
                        meta=value if isinstance(value,dict) else {}
                        records.append((symbol.strip(),meta))
                    elif isinstance(value,str) and value.strip():
                        records.append((value.strip(),{}))
                if isinstance(catalog,dict):
                    for key,value in catalog.items():add_record(key,value)
                elif isinstance(catalog,(list,tuple,set)):
                    for item in catalog:
                        if isinstance(item,dict):
                            symbol=next((item.get(k) for k in ("symbol","ticker","active_symbol","asset","instrument") if isinstance(item.get(k),str)),None)
                            add_record(symbol,item)
                        elif isinstance(item,str):add_record(item,{})
                requested=asset.strip().casefold()
                exact=next((symbol for symbol,meta in records
                            if symbol.casefold()==requested or str(meta.get("name","")).strip().casefold()==requested),None)
                if exact:
                    actual=exact
                elif requested in ("gold","xau","xauusd","xau/usd"):
                    gold=[(symbol,meta) for symbol,meta in records
                          if "gold" in str(meta.get("name","")).casefold()
                          or symbol.replace("_","").casefold().startswith("xau")]
                    if gold:
                        def rank(item):
                            symbol,meta=item
                            available=meta.get("is_available",True) is not False
                            supported=not meta.get("timeframes") or tf in meta.get("timeframes",[])
                            otc=symbol.casefold().endswith("_otc")
                            return (not available,not supported,otc,symbol.casefold())
                        actual=sorted(gold,key=rank)[0][0]
                if actual==asset and records:
                    log.info("Requested label did not resolve to a catalog symbol: %s",asset)
            except Exception as exc:
                log.info("Broker symbol resolution unavailable (%s)",type(exc).__name__)
            sub=(actual,tf)
            # Re-issue the broker subscription periodically. A successful WebSocket
            # connection does not guarantee that the stream subscription stayed live;
            # the old code subscribed once and could then remain stuck on history-only
            # candles forever if the initial subfor packet was missed.
            now_mono=time.monotonic()
            last_sub=self.subscription_times.get(sub,0.0)
            if sub not in self.subscribed or now_mono-last_sub >= 15.0:
                subscribed=client.subscribe(actual,period=tf)
                if not subscribed:
                    self.subscribed.discard(sub)
                    self.subscription_times.pop(sub,None)
                    return self._response(asset,tf,bars,last,False,"Pocket Option rejected market subscription")
                self.subscribed.add(sub)
                self.subscription_times[sub]=now_mono
            # Read realtime ticks first: historical-candle requests can block while
            # waiting for a broker response, which must not prevent live tick polling.
            ticks=[]
            tick_error=None
            try:
                ticks=client.get_realtime_ticks(actual,limit=5000) or []
                # Some broker/library builds report a stream symbol that differs
                # in case or alias from the requested ticker. If so, use the exact
                # requested key first, then a case-insensitive matching stream key.
                if not ticks:
                    api=getattr(client,"api",None)
                    buffers=getattr(api,"_stream_buffers",{}) if api is not None else {}
                    if isinstance(buffers,dict):
                        alias=next((name for name,buf in buffers.items()
                                    if str(name).casefold()==str(actual).casefold() and buf),None)
                        if alias is not None:
                            ticks=list(buffers[alias])[-5000:]
                            if alias!=actual:
                                log.info("Using case-insensitive stream symbol alias %s for %s",alias,actual)
            except Exception as e:tick_error=type(e).__name__

            # Bound the wait for history and avoid spawning duplicate history requests
            # for the same symbol/timeframe while a previous request is still pending.
            raw=None
            history_error=None
            with self.lock:
                history_result=self.history_results.pop(sub,None)
                request_history=history_result is None and sub not in self.history_pending
                if request_history:self.history_pending.add(sub)
            if request_history:
                finished=threading.Event()
                def load_history():
                    result={"raw":None,"error":None}
                    try:
                        # The upstream library waits up to 10s per history request.
                        # A 2s caller wait discarded successful responses and made
                        # the endpoint report an empty feed while the request was still running.
                        with self.history_request_lock:
                            result["raw"]=client.get_historical_candles(actual,tf,offset=9000,count_request=1)
                    except Exception as e:result["error"]=type(e).__name__
                    finally:
                        with self.lock:
                            self.history_pending.discard(sub)
                            self.history_results[sub]=result
                        finished.set()
                threading.Thread(target=load_history,daemon=True,name="mega-man-history-request").start()
                finished.wait(11.5)
            # Consume the result even if the upstream request completed just after
            # this HTTP poll's wait; otherwise the next poll can reuse it safely.
            with self.lock:
                if history_result is None:
                    history_result=self.history_results.pop(sub,None)
            if history_result:
                raw=history_result.get("raw")
                history_error=history_result.get("error")
            normalized=normalize_historical_candles(raw or [],limit=120)
            # Some Pocket Option history responses use a broker-local clock shifted
            # by whole hours. Infer only a stable, near-whole-hour FUTURE skew; never
            # shift timestamps that are already current or stale in the past.
            if normalized:
                clock_now=time.time()
                skew=normalized[-1]["time"]-clock_now
                hour_skew=round(skew/3600.0)*3600
                if 3600 <= hour_skew <= 14*3600 and abs(skew-hour_skew) <= max(90, tf*2):
                    normalized=[{**bar,"time":int(bar["time"]-hour_skew)} for bar in normalized]
                    log.warning("Corrected broker history clock skew of %s seconds",int(hour_skew))
            if tick_error:
                self.debug={"stage":"tick_error","last_error_type":tick_error}
            elif history_error:
                self.debug={"stage":"history_error","last_error_type":history_error}
            corrected=[]
            raw_tick_stamps=[]
            normalized_tick_stamps=[]
            for tick in ticks:
                try:
                    raw_stamp=float(tick[0])
                    raw_tick_stamps.append(raw_stamp)
                    # Normalize millisecond timestamps before applying the broker's
                    # stream timezone offset. This keeps the diagnostic clock comparison
                    # in epoch seconds.
                    if raw_stamp > 100_000_000_000:
                        raw_stamp /= 1000.0
                    normalized_tick_stamps.append(raw_stamp-self.offset)
                except (ValueError,TypeError,IndexError):continue
            # The broker's history and live-stream timestamps can use different clocks.
            # Correct a stable whole-hour FUTURE skew on ticks independently of history.
            # Only apply it when the newest tick is near an exact hour offset from the
            # server clock; never shift current ticks or ticks stale in the past.
            if normalized_tick_stamps:
                tick_skew=normalized_tick_stamps[-1]-time.time()
                tick_hour_skew=round(tick_skew/3600.0)*3600
                if 3600 <= tick_hour_skew <= 14*3600 and abs(tick_skew-tick_hour_skew) <= max(90,tf*2):
                    normalized_tick_stamps=[stamp-tick_hour_skew for stamp in normalized_tick_stamps]
                    log.warning("Corrected broker stream clock skew of %s seconds",int(tick_hour_skew))
            corrected=[(stamp,float(tick[1])) for stamp,tick in zip(normalized_tick_stamps,ticks)]
            tickbars=ticks_to_candles(corrected,tf,limit=120)
            tick_latest=tickbars[-1]["time"] if tickbars else None
            history_latest=normalized[-1]["time"] if normalized else None
            if len(normalized)<2 and len(tickbars)>=2:normalized=tickbars
            elif normalized and tickbars:
                bytime={b["time"]:dict(b) for b in normalized}
                for b in tickbars:
                    if b["time"] in bytime:
                        c=bytime[b["time"]];c["high"]=max(c["high"],b["high"]);c["low"]=min(c["low"],b["low"]);c["close"]=b["close"]
                    elif b["time"]>max(bytime):bytime[b["time"]]=dict(b)
                normalized=[bytime[t] for t in sorted(bytime)][-120:]
            now=int(time.time())
            age=now-int(normalized[-1]["time"]) if normalized else None
            fresh=bool(normalized) and -tf <= age <= max(15,tf*2)
            if fresh:
                with self.lock:self.cache[key]=normalized;self.updated[key]=time.time()
                self.status="Pocket Option live candles — "+actual
                self.debug={"stage":"live_verified","asset":actual,"timeframe_seconds":tf,"tick_count":len(ticks),
                    "history_count":len(raw or []),"normalized_candle_count":len(normalized),"last_candle_timestamp":normalized[-1]["time"]}
                return self._response(asset,tf,normalized,time.time(),True,self.status)
            reason="No valid candles returned" if not normalized else "Last candle is stale"
            self.status=reason
            self.debug={"stage":"feed_unverified","asset":actual,"timeframe_seconds":tf,"tick_count":len(ticks),
                "history_count":len(raw or []),"normalized_candle_count":len(normalized),
                "last_candle_timestamp":normalized[-1]["time"] if normalized else None,
                "last_candle_age_seconds":age,"stream_timezone_offset_seconds":self.offset,
                "latest_tick_candle_timestamp":tick_latest,
                "latest_tick_candle_age_seconds":now-tick_latest if tick_latest is not None else None,
                "latest_history_candle_timestamp":history_latest,
                "latest_history_candle_age_seconds":now-history_latest if history_latest is not None else None,
                "raw_tick_timestamp_sample":raw_tick_stamps[-1] if raw_tick_stamps else None}
            return self._response(asset,tf,normalized,0,False,reason)
        except Exception as e:
            self.status="Feed request failed: "+type(e).__name__
            self.debug={"stage":"request_error","last_error_type":type(e).__name__}
            log.warning("PO snapshot failed (%s)",type(e).__name__)
            return self._response(asset,tf,bars,last,False,self.status)

    def _response(self,asset,tf,bars,updated,verified,reason):
        now=int(time.time())
        age=now-int(bars[-1]["time"]) if bars else None
        fresh=bool(bars) and -tf<=age<=max(15,tf*2)
        verified=bool(verified and fresh and len(bars)>=2)
        return {"ok":verified,"asset":asset,"timeframe_seconds":tf,"status":reason,
            "verified":verified,"bar_count":len(bars) if verified else 0,
            "latest_bar":bars[-1] if verified else None,"candles":bars if verified else [],
            "age_seconds":round(time.time()-updated,2) if updated else None,
            "debug":dict(self.debug),"automatic_execution":False}

feed=PocketOptionFeed()
