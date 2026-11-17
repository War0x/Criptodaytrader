# trader/db.py
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "trading.db"


def _utc_ts() -> float:
    return time.time()


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj if obj is not None else {}, ensure_ascii=False, separators=(",", ":"))


def _json_loads(raw: Optional[str], default: Any = None) -> Any:
    if not raw:
        return {} if default is None else default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {} if default is None else default


class TradingDB:
    """
    Capa de persistencia SQLite en modo WAL, thread-safe y atómica
    para Criptodaytrader y Daytrader.
    """

    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH, busy_timeout_ms: int = 5000) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._conn_lock = threading.Lock()
        self._connections: List[sqlite3.Connection] = []
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=self._busy_timeout_ms / 1000.0,
                isolation_level=None,  # Control explícito de transacciones
                check_same_thread=True,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_ms)};")
            conn.execute("PRAGMA temp_store=MEMORY;")
            self._local.conn = conn
            with self._conn_lock:
                self._connections.append(conn)
        return conn

    @contextmanager
    def _write_tx(self) -> Iterator[sqlite3.Connection]:
        """Transacción de escritura atómica y serializada para evitar SQLITE_BUSY."""
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE;")
            try:
                yield conn
                try:
                    conn.execute("COMMIT;")
                except sqlite3.OperationalError:
                    pass  # Transaction was committed externally (e.g. executescript)
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except sqlite3.OperationalError:
                    pass
                raise

    @contextmanager
    def _read_tx(self) -> Iterator[sqlite3.Connection]:
        """Lectura consistente no bloqueante bajo WAL."""
        conn = self._get_conn()
        yield conn

    def close_all(self) -> None:
        with self._conn_lock:
            for conn in self._connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._connections.clear()
        if hasattr(self._local, "conn"):
            delattr(self._local, "conn")

    def _init_schema(self) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    symbol       TEXT PRIMARY KEY,
                    side         TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT', 'BUY', 'SELL')),
                    qty          REAL NOT NULL CHECK (qty > 0),
                    entry_price  REAL NOT NULL CHECK (entry_price > 0),
                    stop_price   REAL NOT NULL,
                    take_profit  REAL,
                    strategy     TEXT DEFAULT 'breakout',
                    opened_at    REAL NOT NULL,
                    updated_at   REAL NOT NULL,
                    notes        TEXT DEFAULT '',
                    meta_json    TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id       TEXT PRIMARY KEY,
                    symbol         TEXT NOT NULL,
                    side           TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                    order_type     TEXT NOT NULL,
                    qty            REAL NOT NULL,
                    price          REAL,
                    stop_price     REAL,
                    status         TEXT NOT NULL,
                    filled_qty     REAL NOT NULL DEFAULT 0.0,
                    avg_fill_price REAL NOT NULL DEFAULT 0.0,
                    commission     REAL NOT NULL DEFAULT 0.0,
                    created_at     REAL NOT NULL,
                    updated_at     REAL NOT NULL,
                    meta_json      TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_orders_symbol_status
                    ON orders(symbol, status);

                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol      TEXT PRIMARY KEY,
                    score       REAL NOT NULL DEFAULT 0.0,
                    last_price  REAL NOT NULL DEFAULT 0.0,
                    atr         REAL NOT NULL DEFAULT 0.0,
                    high_24h    REAL NOT NULL DEFAULT 0.0,
                    low_24h     REAL NOT NULL DEFAULT 0.0,
                    volume_24h  REAL NOT NULL DEFAULT 0.0,
                    signal      TEXT DEFAULT 'NONE',
                    updated_at  REAL NOT NULL,
                    meta_json   TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS trade_history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol       TEXT NOT NULL,
                    side         TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT', 'BUY', 'SELL')),
                    qty          REAL NOT NULL,
                    entry_price  REAL NOT NULL,
                    exit_price   REAL NOT NULL,
                    stop_price   REAL NOT NULL,
                    take_profit  REAL,
                    gross_pnl    REAL NOT NULL,
                    commission   REAL NOT NULL DEFAULT 0.0,
                    net_pnl      REAL NOT NULL,
                    pnl_pct      REAL NOT NULL,
                    r_multiple   REAL NOT NULL DEFAULT 0.0,
                    exit_reason  TEXT NOT NULL,
                    strategy     TEXT DEFAULT 'breakout',
                    opened_at    REAL NOT NULL,
                    closed_at    REAL NOT NULL,
                    meta_json    TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_history_closed_at
                    ON trade_history(closed_at DESC);

                CREATE TABLE IF NOT EXISTS telemetry_logs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts           REAL NOT NULL,
                    level        TEXT NOT NULL,
                    source       TEXT NOT NULL,
                    event_type   TEXT NOT NULL,
                    message      TEXT NOT NULL,
                    payload_json TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_ts
                    ON telemetry_logs(ts DESC);

                CREATE TABLE IF NOT EXISTS kv_config (
                    key        TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    # ------------------------------------------------------------------ #
    # Configuración Key-Value
    # ------------------------------------------------------------------ #
    def set_config(self, key: str, value: Any) -> None:
        now = _utc_ts()
        with self._write_tx() as conn:
            conn.execute(
                """
                INSERT INTO kv_config (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at;
                """,
                (key, json.dumps(value, ensure_ascii=False), now),
            )

    def get_config(self, key: str, default: Any = None) -> Any:
        with self._read_tx() as conn:
            row = conn.execute(
                "SELECT value_json FROM kv_config WHERE key = ?;", (key,)
            ).fetchone()
            if not row:
                return default
            return _json_loads(row["value_json"], default=default)

    # ------------------------------------------------------------------ #
    # Posiciones (Métodos Atómicos)
    # ------------------------------------------------------------------ #
    def save_position(
        self,
        symbol: str,
        side: str,
        qty: float,
        entry_price: float,
        stop_price: float,
        take_profit: Optional[float] = None,
        strategy: str = "breakout",
        notes: str = "",
        meta: Optional[Dict[str, Any]] = None,
        opened_at: Optional[float] = None,
    ) -> None:
        now = _utc_ts()
        opened = opened_at if opened_at is not None else now
        side_norm = side.upper()
        if side_norm not in ("LONG", "SHORT"):
            raise ValueError(f"Lado inválido: {side}")
        if qty <= 0 or entry_price <= 0:
            raise ValueError("qty y entry_price deben ser mayores a 0")

        with self._write_tx() as conn:
            conn.execute(
                """
                INSERT INTO positions (
                    symbol, side, qty, entry_price, stop_price, take_profit,
                    strategy, opened_at, updated_at, notes, meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    side        = excluded.side,
                    qty         = excluded.qty,
                    entry_price = excluded.entry_price,
                    stop_price  = excluded.stop_price,
                    take_profit = excluded.take_profit,
                    strategy    = excluded.strategy,
                    updated_at  = excluded.updated_at,
                    notes       = excluded.notes,
                    meta_json   = excluded.meta_json;
                """,
                (
                    symbol.upper(),
                    side_norm,
                    float(qty),
                    float(entry_price),
                    float(stop_price),
                    float(take_profit) if take_profit is not None else None,
                    strategy,
                    float(opened),
                    now,
                    notes,
                    _json_dumps(meta),
                ),
            )

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._read_tx() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE symbol = ?;", (symbol.upper(),)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM positions ORDER BY opened_at ASC;"
                ).fetchall()
        result: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["meta"] = _json_loads(d.pop("meta_json", "{}"))
            result.append(d)
        return result

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str = "MANUAL",
        commission: float = 0.0,
        closed_at: Optional[float] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Cierra la posición atómicamente: elimina de `positions`, calcula PnL y R-múltiple,
        registra en `trade_history` y escribe telemetría en una sola transacción.
        """
        sym = symbol.upper()
        now = closed_at if closed_at is not None else _utc_ts()

        with self._write_tx() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE symbol = ?;", (sym,)
            ).fetchone()
            if not row:
                return None

            pos = dict(row)
            qty = float(pos["qty"])
            entry = float(pos["entry_price"])
            stop = float(pos["stop_price"])
            side = pos["side"]

            direction = 1.0 if side == "LONG" else -1.0
            gross_pnl = (float(exit_price) - entry) * qty * direction
            net_pnl = gross_pnl - float(commission)
            notional = entry * qty
            pnl_pct = (net_pnl / notional) * 100.0 if notional > 0 else 0.0
            risk_per_unit = abs(entry - stop)
            initial_risk = risk_per_unit * qty
            r_multiple = (net_pnl / initial_risk) if initial_risk > 0 else 0.0

            merged_meta = _json_loads(pos.get("meta_json"))
            if meta:
                merged_meta.update(meta)

            conn.execute("DELETE FROM positions WHERE symbol = ?;", (sym,))
            cur = conn.execute(
                """
                INSERT INTO trade_history (
                    symbol, side, qty, entry_price, exit_price, stop_price, take_profit,
                    gross_pnl, commission, net_pnl, pnl_pct, r_multiple,
                    exit_reason, strategy, opened_at, closed_at, meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    sym,
                    side,
                    qty,
                    entry,
                    float(exit_price),
                    stop,
                    pos["take_profit"],
                    gross_pnl,
                    float(commission),
                    net_pnl,
                    pnl_pct,
                    r_multiple,
                    exit_reason,
                    pos["strategy"],
                    float(pos["opened_at"]),
                    float(now),
                    _json_dumps(merged_meta),
                ),
            )
            trade_id = cur.lastrowid

            conn.execute(
                """
                INSERT INTO telemetry_logs (ts, level, source, event_type, message, payload_json)
                VALUES (?, 'INFO', 'db', 'POSITION_CLOSED', ?, ?);
                """,
                (
                    now,
                    f"Closed {side} {sym} @ {exit_price:.6f} ({exit_reason}) Net PnL: {net_pnl:+.4f}",
                    _json_dumps(
                        {
                            "trade_id": trade_id,
                            "symbol": sym,
                            "net_pnl": round(net_pnl, 6),
                            "r_multiple": round(r_multiple, 4),
                            "exit_reason": exit_reason,
                        }
                    ),
                ),
            )

        return {
            "trade_id": trade_id,
            "symbol": sym,
            "side": side,
            "qty": qty,
            "entry_price": entry,
            "exit_price": float(exit_price),
            "gross_pnl": gross_pnl,
            "commission": float(commission),
            "net_pnl": net_pnl,
            "pnl_pct": pnl_pct,
            "r_multiple": r_multiple,
            "exit_reason": exit_reason,
            "closed_at": now,
        }

    # ------------------------------------------------------------------ #
    # Órdenes
    # ------------------------------------------------------------------ #
    def save_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        status: str,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        filled_qty: float = 0.0,
        avg_fill_price: float = 0.0,
        commission: float = 0.0,
        meta: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
    ) -> None:
        now = _utc_ts()
        created = created_at if created_at is not None else now
        with self._write_tx() as conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, symbol, side, order_type, qty, price, stop_price,
                    status, filled_qty, avg_fill_price, commission,
                    created_at, updated_at, meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    status         = excluded.status,
                    filled_qty     = excluded.filled_qty,
                    avg_fill_price = excluded.avg_fill_price,
                    commission     = excluded.commission,
                    updated_at     = excluded.updated_at,
                    meta_json      = excluded.meta_json;
                """,
                (
                    str(order_id),
                    symbol.upper(),
                    side.upper(),
                    order_type.upper(),
                    float(qty),
                    float(price) if price is not None else None,
                    float(stop_price) if stop_price is not None else None,
                    status.upper(),
                    float(filled_qty),
                    float(avg_fill_price),
                    float(commission),
                    float(created),
                    now,
                    _json_dumps(meta),
                ),
            )

    # ------------------------------------------------------------------ #
    # Watchlist
    # ------------------------------------------------------------------ #
    def save_watchlist(
        self,
        items: Union[Dict[str, Any], Iterable[Dict[str, Any]]],
        replace_all: bool = False,
    ) -> int:
        if isinstance(items, dict):
            item_list = [items]
        else:
            item_list = list(items)

        now = _utc_ts()
        with self._write_tx() as conn:
            if replace_all:
                conn.execute("DELETE FROM watchlist;")
            for item in item_list:
                sym = str(item.get("symbol", "")).upper()
                if not sym:
                    continue
                conn.execute(
                    """
                    INSERT INTO watchlist (
                        symbol, score, last_price, atr, high_24h, low_24h,
                        volume_24h, signal, updated_at, meta_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        score      = excluded.score,
                        last_price = excluded.last_price,
                        atr        = excluded.atr,
                        high_24h   = excluded.high_24h,
                        low_24h    = excluded.low_24h,
                        volume_24h = excluded.volume_24h,
                        signal     = excluded.signal,
                        updated_at = excluded.updated_at,
                        meta_json  = excluded.meta_json;
                    """,
                    (
                        sym,
                        float(item.get("score", 0.0)),
                        float(item.get("last_price", item.get("price", 0.0))),
                        float(item.get("atr", 0.0)),
                        float(item.get("high_24h", item.get("highPrice", 0.0))),
                        float(item.get("low_24h", item.get("lowPrice", 0.0))),
                        float(item.get("volume_24h", item.get("volume_usdt", item.get("quoteVolume", 0.0)))),
                        str(item.get("signal", item.get("setup", "NONE"))).upper(),
                        float(item.get("updated_at", now)),
                        _json_dumps(item.get("meta", item)),
                    ),
                )
        return len(item_list)

    def get_watchlist(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM watchlist ORDER BY score DESC, updated_at DESC"
        params: Tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)
        sql += ";"
        with self._read_tx() as conn:
            rows = conn.execute(sql, params).fetchall()
        result: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["meta"] = _json_loads(d.pop("meta_json", "{}"))
            result.append(d)
        return result

    # ------------------------------------------------------------------ #
    # Telemetría y Resumen de Portafolio
    # ------------------------------------------------------------------ #
    def log_event(
        self,
        level: str,
        source: str,
        event_type: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = _utc_ts()
        with self._write_tx() as conn:
            conn.execute(
                """
                INSERT INTO telemetry_logs (ts, level, source, event_type, message, payload_json)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (now, level.upper(), source, event_type.upper(), message, _json_dumps(payload)),
            )

    def get_recent_logs(
        self,
        limit: int = 100,
        level: Optional[str] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if level:
            clauses.append("level = ?")
            params.append(level.upper())
        if source:
            clauses.append("source = ?")
            params.append(source)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))

        with self._read_tx() as conn:
            rows = conn.execute(
                f"SELECT * FROM telemetry_logs {where_sql} ORDER BY ts DESC, id DESC LIMIT ?;",
                tuple(params),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["payload"] = _json_loads(d.pop("payload_json", "{}"))
            out.append(d)
        return out

    def get_portfolio_summary(self, initial_capital: Optional[float] = None) -> Dict[str, Any]:
        if initial_capital is None:
            initial_capital = float(self.get_config("initial_capital", 100.0))

        with self._read_tx() as conn:
            pos_rows = conn.execute("SELECT * FROM positions;").fetchall()
            hist_agg = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_trades,
                    COALESCE(SUM(net_pnl), 0.0) AS realized_pnl,
                    COALESCE(SUM(commission), 0.0) AS total_commissions,
                    COALESCE(SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END), 0) AS wins
                FROM trade_history;
                """
            ).fetchone()

        open_exposure = sum(float(r["qty"]) * float(r["entry_price"]) for r in pos_rows)
        open_risk = sum(abs(float(r["entry_price"]) - float(r["stop_price"])) * float(r["qty"]) for r in pos_rows)
        realized_pnl = float(hist_agg["realized_pnl"])
        total_trades = int(hist_agg["total_trades"])
        wins = int(hist_agg["wins"])
        equity_base = float(initial_capital) + realized_pnl
        available_cash = max(0.0, equity_base - open_exposure)
        win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0

        return {
            "initial_capital": float(initial_capital),
            "realized_pnl": round(realized_pnl, 6),
            "equity_base": round(equity_base, 6),
            "open_positions_count": len(pos_rows),
            "open_exposure": round(open_exposure, 6),
            "open_risk_usd": round(open_risk, 6),
            "available_cash": round(available_cash, 6),
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 2),
            "total_commissions": round(float(hist_agg["total_commissions"]), 6),
        }

    # ------------------------------------------------------------------ #
    # Compatibilidad hacia atrás (Import / Export JSON atómico)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _atomic_write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    def import_from_json(
        self,
        positions_path: Optional[Union[str, Path]] = None,
        watchlist_path: Optional[Union[str, Path]] = None,
        history_path: Optional[Union[str, Path]] = None,
        config_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, int]:
        counts = {"positions": 0, "watchlist": 0, "history": 0, "config": 0}

        if positions_path and Path(positions_path).exists():
            try:
                raw = json.loads(Path(positions_path).read_text(encoding="utf-8"))
                items = raw.values() if isinstance(raw, dict) else raw
                if isinstance(items, list):
                    for p in items:
                        if isinstance(p, dict) and "symbol" in p:
                            self.save_position(
                                symbol=p["symbol"],
                                side=p.get("side", "LONG"),
                                qty=float(p.get("qty", p.get("shares", 1.0))),
                                entry_price=float(p.get("entry_price", p.get("price", 1.0))),
                                stop_price=float(p.get("stop_price", float(p.get("entry_price", 1.0)) * 0.98)),
                                take_profit=p.get("take_profit"),
                                strategy=p.get("strategy", "legacy_import"),
                                notes=p.get("notes", ""),
                                meta=p.get("meta"),
                                opened_at=p.get("opened_at"),
                            )
                            counts["positions"] += 1
            except Exception as e:
                print(f"[db] Error importando positions: {e}")

        if watchlist_path and Path(watchlist_path).exists():
            try:
                raw = json.loads(Path(watchlist_path).read_text(encoding="utf-8"))
                hits = raw.get("hits", raw) if isinstance(raw, dict) else raw
                items = hits if isinstance(hits, list) else list(hits.values())
                norm_items = []
                for w in items:
                    if isinstance(w, str):
                        norm_items.append({"symbol": w})
                    elif isinstance(w, dict):
                        norm_items.append(w)
                counts["watchlist"] = self.save_watchlist(norm_items)
            except Exception as e:
                print(f"[db] Error importando watchlist: {e}")

        if history_path and Path(history_path).exists():
            try:
                raw = json.loads(Path(history_path).read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    with self._write_tx() as conn:
                        for h in raw:
                            if isinstance(h, dict) and "symbol" in h:
                                conn.execute(
                                    """
                                    INSERT INTO trade_history (
                                        symbol, side, qty, entry_price, exit_price, stop_price, take_profit,
                                        gross_pnl, commission, net_pnl, pnl_pct, r_multiple,
                                        exit_reason, strategy, opened_at, closed_at, meta_json
                                    )
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                                    """,
                                    (
                                        str(h["symbol"]).upper(),
                                        str(h.get("side", "LONG")).upper(),
                                        float(h.get("qty", 1.0)),
                                        float(h.get("entry_price", h.get("price", 1.0))),
                                        float(h.get("exit_price", 1.0)),
                                        float(h.get("stop_price", h.get("entry_price", 1.0))),
                                        h.get("take_profit"),
                                        float(h.get("gross_pnl", h.get("net_pnl", 0.0))),
                                        float(h.get("commission", 0.0)),
                                        float(h.get("net_pnl", 0.0)),
                                        float(h.get("pnl_pct", 0.0)),
                                        float(h.get("r_multiple", 0.0)),
                                        str(h.get("exit_reason", "LEGACY_IMPORT")),
                                        str(h.get("strategy", "legacy_import")),
                                        float(h.get("opened_at", _utc_ts())),
                                        float(h.get("closed_at", _utc_ts())),
                                        _json_dumps(h.get("meta")),
                                    ),
                                )
                                counts["history"] += 1
            except Exception as e:
                print(f"[db] Error importando history: {e}")

        if config_path and Path(config_path).exists():
            try:
                cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
                if isinstance(cfg, dict):
                    for k, v in cfg.items():
                        self.set_config(k, v)
                        counts["config"] += 1
            except Exception as e:
                print(f"[db] Error importando config: {e}")

        return counts

    def export_to_json(self, output_dir: Union[str, Path]) -> Dict[str, Path]:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        positions = self.get_positions()
        watchlist = self.get_watchlist()
        with self._read_tx() as conn:
            history_rows = conn.execute(
                "SELECT * FROM trade_history ORDER BY closed_at DESC;"
            ).fetchall()
            cfg_rows = conn.execute("SELECT key, value_json FROM kv_config;").fetchall()

        history = []
        for r in history_rows:
            d = dict(r)
            d["meta"] = _json_loads(d.pop("meta_json", "{}"))
            history.append(d)

        config = {r["key"]: _json_loads(r["value_json"]) for r in cfg_rows}

        files = {
            "positions": out / "portfolio.json",
            "watchlist": out / "watchlist.json",
            "history": out / "trade_history.json",
            "config": out / "config.json",
        }
        self._atomic_write_json(files["positions"], {"positions": positions, "cash": self.get_portfolio_summary()["available_cash"]})
        self._atomic_write_json(files["watchlist"], {"hits": watchlist, "updated_at": _utc_ts()})
        self._atomic_write_json(files["history"], history)
        self._atomic_write_json(files["config"], config)
        return files
