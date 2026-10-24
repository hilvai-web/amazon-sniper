"""Base de datos SQLite local (alertas.db): historial de precios y antiduplicados."""
from __future__ import annotations

import sqlite3
import statistics
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    asin        TEXT NOT NULL,
    market      TEXT NOT NULL,
    title       TEXT,
    last_price  REAL,
    list_price  REAL,
    last_seen   REAL,
    PRIMARY KEY (asin, market)
);
CREATE TABLE IF NOT EXISTS price_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    asin    TEXT NOT NULL,
    market  TEXT NOT NULL,
    price   REAL NOT NULL,
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hist ON price_history (asin, market, ts);
CREATE TABLE IF NOT EXISTS alerts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    asin      TEXT NOT NULL,
    market    TEXT NOT NULL,
    price     REAL NOT NULL,
    reference REAL,
    discount  REAL,
    sent      INTEGER NOT NULL DEFAULT 0,
    ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alerts ON alerts (asin, market, ts);
CREATE TABLE IF NOT EXISTS excluded (
    asin    TEXT NOT NULL,
    market  TEXT NOT NULL,
    reason  TEXT,
    ts      REAL NOT NULL,
    PRIMARY KEY (asin, market)
);
"""


class Database:
    """Envoltorio thread-safe: el motor async escribe y la GUI lee."""

    def __init__(self, path: Path | str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- lectura
    def recently_scanned(self, asin: str, market: str, hours: float) -> bool:
        if hours <= 0:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seen FROM products WHERE asin=? AND market=?",
                (asin, market),
            ).fetchone()
        return bool(row and row[0] and time.time() - row[0] < hours * 3600)

    def recently_excluded(self, asin: str, market: str, days: float = 7) -> bool:
        """Productos descartados (baratos/consumibles) no se vuelven a pedir en N días."""
        with self._lock:
            row = self._conn.execute(
                "SELECT ts FROM excluded WHERE asin=? AND market=?", (asin, market)).fetchone()
        return bool(row and time.time() - row[0] < days * 86400)

    def mark_excluded(self, asin: str, market: str, reason: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO excluded (asin, market, reason, ts) VALUES (?,?,?,?)",
                (asin, market, reason, time.time()))
            self._conn.commit()

    def reference_price(self, asin: str, market: str, days: int = 90) -> float | None:
        """Mediana del histórico propio (necesita >= 3 muestras para ser fiable)."""
        since = time.time() - days * 86400
        with self._lock:
            rows = self._conn.execute(
                "SELECT price FROM price_history WHERE asin=? AND market=? AND ts>=?",
                (asin, market, since),
            ).fetchall()
        prices = [r[0] for r in rows if r[0] and r[0] > 0]
        if len(prices) < 3:
            return None
        return statistics.median(prices)

    def should_alert(self, asin: str, market: str, price: float, cooldown_h: float) -> bool:
        """Evita duplicados: solo avisa si no hubo alerta reciente o el precio ha
        bajado al menos un 1 % respecto a la última alerta."""
        since = time.time() - cooldown_h * 3600
        with self._lock:
            row = self._conn.execute(
                "SELECT price FROM alerts WHERE asin=? AND market=? AND ts>=? "
                "ORDER BY ts DESC LIMIT 1",
                (asin, market, since),
            ).fetchone()
        return row is None or price < row[0] * 0.99

    def stats(self) -> dict[str, int]:
        with self._lock:
            products = self._conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            alerts = self._conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        return {"products": products, "alerts": alerts}

    # -------------------------------------------------------------- escritura
    def record_scan(self, asin: str, market: str, title: str | None,
                    price: float | None, list_price: float | None) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO products (asin, market, title, last_price, list_price, last_seen) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(asin, market) DO UPDATE SET "
                "title=COALESCE(excluded.title, title), last_price=excluded.last_price, "
                "list_price=excluded.list_price, last_seen=excluded.last_seen",
                (asin, market, title, price, list_price, now),
            )
            if price:
                self._conn.execute(
                    "INSERT INTO price_history (asin, market, price, ts) VALUES (?,?,?,?)",
                    (asin, market, price, now),
                )
            self._conn.commit()

    def record_alert(self, asin: str, market: str, price: float,
                     reference: float | None, discount: float, sent: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO alerts (asin, market, price, reference, discount, sent, ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (asin, market, price, reference, discount, int(sent), time.time()),
            )
            self._conn.commit()
