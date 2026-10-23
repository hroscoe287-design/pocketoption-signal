"""Pure, testable normalization for verified Pocket Option market data.

No network access, credentials, order execution, or synthetic candle generation lives here.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Any


def _seconds(value: Any) -> int:
    stamp = float(value)
    if not math.isfinite(stamp) or stamp <= 0:
        raise ValueError("invalid timestamp")
    # Pocket Option data may use seconds or milliseconds.
    if stamp > 100_000_000_000:
        stamp /= 1000.0
    return int(stamp)


def normalize_historical_candles(candles: Iterable[Mapping[str, Any]], limit: int = 120) -> list[dict]:
    """Normalize real provider OHLC candles; discard malformed rows and deduplicate timestamps."""
    unique: dict[int, dict] = {}
    for candle in candles or []:
        try:
            stamp = _seconds(candle.get("time", candle.get("timestamp")))
            opening = float(candle["open"])
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
            values = (opening, high, low, close)
            if not all(math.isfinite(v) for v in values):
                continue
            if low <= 0 or high < low or high < max(opening, close) or low > min(opening, close):
                continue
            unique[stamp] = {
                "time": stamp, "open": opening, "high": high, "low": low, "close": close
            }
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
    return [unique[t] for t in sorted(unique)][-max(1, int(limit)):]


def ticks_to_candles(ticks: Iterable[Any], timeframe_seconds: int, limit: int = 120) -> list[dict]:
    """Aggregate actual provider ticks into OHLC bars. Never fills missing intervals."""
    timeframe = int(timeframe_seconds)
    if timeframe <= 0:
        raise ValueError("timeframe_seconds must be positive")
    buckets: dict[int, dict] = {}
    normalized_ticks = []
    for tick in ticks or []:
        try:
            stamp, price = tick[0], float(tick[1])
            seconds = _seconds(stamp)
            if not math.isfinite(price) or price <= 0:
                continue
            normalized_ticks.append((seconds, price))
        except (TypeError, ValueError, IndexError, KeyError, OverflowError):
            continue
    for stamp, price in sorted(normalized_ticks):
        bucket = (stamp // timeframe) * timeframe
        bar = buckets.get(bucket)
        if bar is None:
            buckets[bucket] = {
                "time": bucket, "open": price, "high": price, "low": price, "close": price
            }
        else:
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price
    return [buckets[t] for t in sorted(buckets)][-max(1, int(limit)):]
