"""SQLite storage for Tusa Finance — screener candidates and sent-alert log."""
import sqlite3
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS dividend_candidates (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT NOT NULL,
                name          TEXT,
                yield_pct     REAL,
                payout_ratio  REAL,
                market_cap    REAL,
                years_history INTEGER,
                score         REAL,
                scanned_at    REAL NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS momentum_candidates (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol            TEXT NOT NULL,
                asset_type        TEXT NOT NULL,   -- 'stock' | 'crypto'
                price_change_pct  REAL,
                volume_usd        REAL,
                rsi               REAL,
                score             REAL,
                suggest_leverage  INTEGER NOT NULL DEFAULT 0,
                scanned_at        REAL NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS unusual_volume_candidates (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol            TEXT NOT NULL,
                price_change_pct  REAL,
                volume_usd        REAL,
                relative_volume   REAL,
                rsi               REAL,
                score             REAL,
                scanned_at        REAL NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS sent_alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol     TEXT NOT NULL,
                kind       TEXT NOT NULL,   -- 'dividend' | 'momentum'
                sent_at    REAL NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)


def save_dividend_candidates(rows: list[dict]) -> None:
    now = time.time()
    with _conn() as c:
        c.executemany("""
            INSERT INTO dividend_candidates
                (symbol, name, yield_pct, payout_ratio, market_cap, years_history, score, scanned_at)
            VALUES (:symbol, :name, :yield_pct, :payout_ratio, :market_cap, :years_history, :score, :scanned_at)
        """, [{**r, "scanned_at": now} for r in rows])


def save_momentum_candidates(rows: list[dict]) -> None:
    now = time.time()
    with _conn() as c:
        c.executemany("""
            INSERT INTO momentum_candidates
                (symbol, asset_type, price_change_pct, volume_usd, rsi, score, suggest_leverage, scanned_at)
            VALUES (:symbol, :asset_type, :price_change_pct, :volume_usd, :rsi, :score, :suggest_leverage, :scanned_at)
        """, [{**r, "scanned_at": now} for r in rows])


def save_unusual_volume_candidates(rows: list[dict]) -> None:
    now = time.time()
    with _conn() as c:
        c.executemany("""
            INSERT INTO unusual_volume_candidates
                (symbol, price_change_pct, volume_usd, relative_volume, rsi, score, scanned_at)
            VALUES (:symbol, :price_change_pct, :volume_usd, :relative_volume, :rsi, :score, :scanned_at)
        """, [{**r, "scanned_at": now} for r in rows])


def get_latest_dividend_candidates(limit: int = 10) -> list[dict]:
    """Most recent scan batch (not just top-N ever) — for the on-demand
    Telegram button, so it shows what the last scheduled scan actually found."""
    with _conn() as c:
        latest = c.execute("SELECT MAX(scanned_at) AS t FROM dividend_candidates").fetchone()
        if not latest or latest["t"] is None:
            return []
        rows = c.execute(
            "SELECT * FROM dividend_candidates WHERE scanned_at=? ORDER BY score DESC LIMIT ?",
            (latest["t"], limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_momentum_candidates(limit: int = 10) -> list[dict]:
    with _conn() as c:
        latest = c.execute("SELECT MAX(scanned_at) AS t FROM momentum_candidates").fetchone()
        if not latest or latest["t"] is None:
            return []
        rows = c.execute(
            "SELECT * FROM momentum_candidates WHERE scanned_at=? ORDER BY score DESC LIMIT ?",
            (latest["t"], limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_unusual_volume_candidates(limit: int = 10) -> list[dict]:
    with _conn() as c:
        latest = c.execute("SELECT MAX(scanned_at) AS t FROM unusual_volume_candidates").fetchone()
        if not latest or latest["t"] is None:
            return []
        rows = c.execute(
            "SELECT * FROM unusual_volume_candidates WHERE scanned_at=? ORDER BY score DESC LIMIT ?",
            (latest["t"], limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_scan_at() -> float | None:
    with _conn() as c:
        vals = []
        for table in ("dividend_candidates", "momentum_candidates", "unusual_volume_candidates"):
            row = c.execute(f"SELECT MAX(scanned_at) AS t FROM {table}").fetchone()
            if row and row["t"] is not None:
                vals.append(row["t"])
        return max(vals) if vals else None


def was_recently_sent(symbol: str, kind: str, within_days: int = 7) -> bool:
    cutoff = time.time() - within_days * 86400
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM sent_alerts WHERE symbol=? AND kind=? AND sent_at>? LIMIT 1",
            (symbol, kind, cutoff),
        ).fetchone()
        return row is not None


def mark_sent(symbol: str, kind: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO sent_alerts (symbol, kind, sent_at) VALUES (?, ?, ?)",
            (symbol, kind, time.time()),
        )


def get_state(key: str, default: str = None) -> str:
    with _conn() as c:
        row = c.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
