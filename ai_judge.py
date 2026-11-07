"""MEGA MAN outcome judge.

Records signal snapshots from verified live market data and grades them only after
the configured observation window. This module never places or manages trades.
Set AI_JUDGE_DB_PATH to a persistent Render disk path to retain learning across
restarts/deploys; the default local path may be ephemeral on hosted platforms.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

DB_PATH = os.getenv("AI_JUDGE_DB_PATH", "data/ai_judge.sqlite3")


def _connect():
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS judge_signals (
            event_id TEXT PRIMARY KEY,
            asset TEXT NOT NULL,
            timeframe_seconds INTEGER NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_time REAL NOT NULL,
            expiry_seconds INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            exit_price REAL,
            settled_time REAL,
            outcome TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_judge_pending ON judge_signals(status, entry_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_judge_pattern ON judge_signals(asset, timeframe_seconds, outcome)")
    return conn


def _features(snapshot):
    """Capture the signal's actual DI/fractal context, including nested engine fields."""
    hybrid = snapshot.get("hybrid_engine") or {}
    keys = (
        "plus_di", "minus_di", "di_spread", "di_spread_velocity",
        "di_spread_acceleration", "adx", "fractal_arrow", "wide_x",
        "x_status", "fakeout_risk", "weighted_score", "weighted_direction",
        "fractal_direction", "di_direction", "di_cross_status",
        "early_x_warning", "early_x_direction", "desire_score",
        "fractal_setup_strength", "x_quality_adjustment",
    )
    features = {}
    for key in keys:
        value = snapshot.get(key)
        if value is None:
            value = hybrid.get(key)
        features[key] = value
    if features.get("di_spread") is None:
        try:
            features["di_spread"] = float(features["plus_di"]) - float(features["minus_di"])
        except (TypeError, ValueError):
            features["di_spread"] = None
    # Preserve the complete compact engine context for later pattern comparisons.
    features["engine_context"] = {
        key: hybrid.get(key) for key in (
            "plus_di", "minus_di", "di_spread", "di_spread_velocity",
            "di_spread_acceleration", "adx", "fractal_arrow", "wide_x",
            "x_status", "fakeout_risk", "weighted_score", "weighted_direction",
            "di_cross_status", "early_x_warning", "early_x_direction",
            "fractal_setup_strength",
        ) if key in hybrid
    }
    return features

def _settle_due(conn, asset, timeframe_seconds, live_price, now):
    rows = conn.execute(
        "SELECT * FROM judge_signals WHERE status='PENDING' AND asset=? AND timeframe_seconds=? AND entry_time + expiry_seconds <= ?",
        (asset, timeframe_seconds, now),
    ).fetchall()
    settled = []
    for row in rows:
        delta = float(live_price) - float(row["entry_price"])
        tolerance = max(abs(float(row["entry_price"])) * 1e-8, 1e-10)
        if abs(delta) <= tolerance:
            outcome = "DRAW"
        elif (delta > 0 and row["direction"] == "BUY") or (delta < 0 and row["direction"] == "SELL"):
            outcome = "WIN"
        else:
            outcome = "LOSS"
        conn.execute(
            "UPDATE judge_signals SET status='SETTLED', exit_price=?, settled_time=?, outcome=? WHERE event_id=? AND status='PENDING'",
            (float(live_price), now, outcome, row["event_id"]),
        )
        settled.append({"event_id": row["event_id"], "outcome": outcome, "entry_price": row["entry_price"], "exit_price": float(live_price)})
    return settled


def observe(snapshot):
    """Settle due events and record a new actionable signal from a live snapshot."""
    if not snapshot or not snapshot.get("live") or snapshot.get("data_mode", "").startswith("LIVE FEED UNAVAILABLE"):
        return {"enabled": True, "status": "WAITING_FOR_VERIFIED_LIVE_FEED", "settled": [], "recorded": False}
    asset = str(snapshot.get("asset") or "")
    timeframe = int(snapshot.get("timeframe_seconds") or 0)
    price = snapshot.get("price")
    direction = snapshot.get("signal")
    if not asset or timeframe <= 0 or price is None:
        return {"enabled": True, "status": "INSUFFICIENT_LIVE_DATA", "settled": [], "recorded": False}
    now = time.time()
    conn = _connect()
    try:
        settled = _settle_due(conn, asset, timeframe, float(price), now)
        recorded = False
        if direction in ("BUY", "SELL"):
            candle_id = str(snapshot.get("bars", [{}])[-1].get("time", "")) if snapshot.get("bars") else ""
            event_id = f"{asset}|{timeframe}|{direction}|{candle_id}"
            expiration = max(5, int(snapshot.get("hybrid_engine", {}).get("expiration_seconds") or timeframe * 2))
            cursor = conn.execute(
                "INSERT OR IGNORE INTO judge_signals(event_id,asset,timeframe_seconds,direction,entry_price,entry_time,expiry_seconds,features_json) VALUES(?,?,?,?,?,?,?,?)",
                (event_id, asset, timeframe, direction, float(price), now, expiration, json.dumps(_features(snapshot), sort_keys=True)),
            )
            recorded = cursor.rowcount == 1
        conn.commit()
        stats = conn.execute(
            "SELECT COUNT(*) AS total, SUM(outcome='WIN') AS wins, SUM(outcome='LOSS') AS losses, SUM(outcome='DRAW') AS draws FROM judge_signals WHERE status='SETTLED' AND asset=? AND timeframe_seconds=?",
            (asset, timeframe),
        ).fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) FROM judge_signals WHERE status='PENDING' AND asset=? AND timeframe_seconds=?",
            (asset, timeframe),
        ).fetchone()[0]
        total = int(stats["total"] or 0)
        wins = int(stats["wins"] or 0)
        losses = int(stats["losses"] or 0)
        draws = int(stats["draws"] or 0)
        decided = wins + losses
        return {
            "enabled": True,
            "status": "LEARNING_FROM_LIVE_OUTCOMES",
            "settled": settled,
            "recorded": recorded,
            "pending": int(pending),
            "settled_count": total,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "observed_win_rate": round(100 * wins / decided, 2) if decided else None,
            "win_rate_sample_size": decided,
            "win_rate_note": "Historical observed rate only; not a prediction or guarantee.",
            "storage_path": DB_PATH,
        }
    finally:
        conn.close()
