"""Read-only Pocket Option feed adapter. No synthetic candles or trade methods."""
from __future__ import annotations
import asyncio, json, logging, math, os, threading, time
from datetime import datetime, timezone
from market_feed import normalize_historical_candles, ticks_to_candles

log=logging.getLogger("godmode.next.feed")
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
        self.offset=0.0
        self.worker_started=False

    @staticmethod
    def _auth():
        raw=os.getenv("PO_AUTH_JSON","").strip()
        if not raw:return None
        if raw.startswith('42["auth",'):return raw
        try:obj=json.loads(raw)
        except Exception:return None
        if isinstance(obj,str) and obj.startswith('42["auth",'):return obj
        if isinstance(obj,dict):
            if isinstance(obj.get("ssid"),str) and obj["ssid"].startswith('42["auth",'):return obj["ssid"]
            payload=obj.get("auth") if isinstance(obj.get("auth"),dict) else obj
            if isinstance(payload,dict) and payload.get("session") and payload.get("uid") is not None:
                payload=dict(payload);payload.setdefault("platform",2);payload.setdefault("isFastHistory",True);payload.setdefault("isOptimized",True)
                return '42["auth",'+json.dumps(payload,separators=(",",":"))+"]"
        return None

    def start(self):
        with self.lock:
            if self.worker_started:return
            self.worker_started=True
        threading.Thread(target=self._supervisor,daemon=True,name="godmode-next-po-supervisor").start()

    def _supervisor(self):
        while True:
            try:
                if self.client is None and self._auth():
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
            from pocketoptionapi import PocketOption
            client=PocketOption(self._auth())
            ok,err=client.connect()
            if not ok:
                self.status="Pocket Option connection failed"
                log.warning("PO connect failed (%s)",type(err).__name__ if err else "unknown")
                return
            api=getattr(client,"api",None)
            if api is not None:
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
            with self.lock:self.client=client;self.subscribed.clear()
            self.status="Connected; waiting for symbol subscription"
        except Exception as e:
            self.status="Pocket Option client unavailable or auth rejected: "+type(e).__name__
            log.warning("PO client setup failed (%s)",type(e).__name__)
        finally:
            with self.lock:self.connecting=False

    def _drop(self,reason):
        old=self.client
        with self.lock:
            self.client=None;self.subscribed.clear()
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
                # Broker library versions return catalogs in different shapes:
                # symbol->metadata mappings, numeric-id->symbol mappings, or lists of records.
                candidates=[]
                def add_candidate(value):
                    if isinstance(value,str):
                        value=value.strip()
                        if value:candidates.append(value)
                    elif isinstance(value,dict):
                        for field in ("symbol","name","asset","ticker","active_symbol","instrument"):
                            item=value.get(field)
                            if isinstance(item,str) and item.strip():candidates.append(item.strip())
                if isinstance(catalog,dict):
                    for key,value in catalog.items():
                        add_candidate(key)
                        add_candidate(value)
                elif isinstance(catalog,(list,tuple,set)):
                    for item in catalog:add_candidate(item)
                match=next((s for s in candidates if s.casefold()==asset.strip().casefold()),None)
                if candidates and not match:
                    # Do not reject an asset based on an unrecognized catalog record shape.
                    # Let the broker subscription/history calls be the source of truth.
                    log.info("Requested symbol not directly matched in catalog; trying broker subscription for %s",asset)
                if match:actual=match
            except Exception:pass
            sub=(actual,tf)
            if sub not in self.subscribed:
                subscribed=client.subscribe(actual,period=tf)
                if not subscribed:return self._response(asset,tf,bars,last,False,"Pocket Option rejected market subscription")
                self.subscribed.add(sub)
            raw=None
            try:raw=client.get_historical_candles(actual,tf,offset=45000,count_request=1)
            except Exception as e:
                self.debug={"stage":"history_error","last_error_type":type(e).__name__}
            normalized=normalize_historical_candles(raw or [],limit=120)
            ticks=[]
            try:ticks=client.get_realtime_ticks(actual,limit=5000) or []
            except Exception as e:
                self.debug={"stage":"tick_error","last_error_type":type(e).__name__}
            corrected=[]
            for tick in ticks:
                try:
                    # PocketOptionApi documents stream tick timestamps as epoch seconds.
                    # Do not subtract its stream timezone offset: doing so can shift live
                    # ticks backward and make an otherwise live candle appear stale.
                    corrected.append((float(tick[0]),float(tick[1])))
                except (ValueError,TypeError,IndexError):continue
            tickbars=ticks_to_candles(corrected,tf,limit=120)
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
                "last_candle_timestamp":normalized[-1]["time"] if normalized else None}
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
