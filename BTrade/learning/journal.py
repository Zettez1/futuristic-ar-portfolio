import json
import os
import sqlite3
import time
from pathlib import Path

from features.pipeline import FEATURE_NAMES

ROOT = Path(__file__).resolve().parent.parent
_db_raw = os.environ.get("DB_PATH", "db/trades.db")
DB_PATH = Path(_db_raw) if os.path.isabs(_db_raw) else ROOT / _db_raw


class TradeJournal:
    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY, symbol TEXT, side TEXT, entry REAL, exit REAL,
                qty REAL, stop_loss REAL, take_profit REAL, strategy TEXT,
                opened_at REAL, closed_at REAL, pnl REAL, r_multiple REAL,
                reason TEXT, features TEXT, fees REAL DEFAULT 0.0)"""
        )
        self._ensure_column("trades", "fees", "REAL DEFAULT 0.0")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS signals_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, side TEXT,
                strategy TEXT, entry REAL, stop_loss REAL, features TEXT, outcome REAL)"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS micro_exits (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, entry REAL,
                exit_price REAL, exit_ts REAL, reason TEXT, p30 REAL, p60 REAL, p180 REAL,
                p300 REAL, max_against REAL, max_for REAL, false_exit REAL, done INTEGER DEFAULT 0)"""
        )
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str):
        columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self.conn.commit()

    def open_trade(self, pos):
        self.conn.execute(
            "INSERT OR REPLACE INTO trades (id, symbol, side, entry, qty, stop_loss, take_profit, strategy, opened_at, features, fees) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pos.id, pos.symbol, pos.side, pos.entry, pos.qty, pos.stop_loss, pos.take_profit,
             pos.strategy, pos.opened_at, json.dumps(pos.features), getattr(pos, "fees", 0.0)),
        )
        self.conn.commit()

    def close_trade(self, pos, exit_price: float, reason: str):
        initial_stop = (pos.features or {}).get("initial_stop_loss", pos.stop_loss)
        initial_risk = abs(pos.entry - initial_stop) * pos.qty
        self.conn.execute(
            "UPDATE trades SET exit=?, closed_at=?, pnl=?, r_multiple=?, reason=?, fees=? WHERE id=?",
            (exit_price, time.time(), pos.realized_pnl,
             pos.realized_pnl / max(initial_risk, 1e-12), reason, getattr(pos, "fees", 0.0), pos.id),
        )
        self.conn.commit()

    def close_external(self, pos, exit_price: float = None, pnl: float = None,
                       fees: float = None, reason: str = "закрыта вне бота"):
        """Фиксирует закрытие на бирже с фактической или оценочной ценой и комиссиями."""
        exit_price = float(exit_price if exit_price is not None else pos.entry)
        pnl = float(pnl if pnl is not None else getattr(pos, "realized_pnl", 0.0))
        fees = float(fees if fees is not None else getattr(pos, "fees", 0.0))
        initial_stop = (pos.features or {}).get("initial_stop_loss", pos.stop_loss)
        initial_risk = abs(pos.entry - initial_stop) * pos.qty
        self.conn.execute(
            "UPDATE trades SET exit=?, closed_at=?, pnl=?, r_multiple=?, reason=?, fees=? WHERE id=?",
            (exit_price, time.time(), pnl, pnl / max(initial_risk, 1e-12), reason, fees, pos.id),
        )
        self.conn.commit()

    def open_trades(self) -> list:
        rows = self.conn.execute(
            "SELECT id, symbol, side, entry, qty, stop_loss, take_profit, strategy, opened_at, fees FROM trades "
            "WHERE closed_at IS NULL"
        ).fetchall()
        return [dict(zip(("id", "symbol", "side", "entry", "qty", "stop_loss", "take_profit", "strategy", "opened_at", "fees"), r))
                for r in rows]

    def purge_stale_open(self, symbols: list) -> int:
        placeholders = ",".join("?" * len(symbols))
        cur = self.conn.execute(
            f"DELETE FROM trades WHERE closed_at IS NULL AND symbol NOT IN ({placeholders})", symbols)
        self.conn.commit()
        return cur.rowcount

    def close_open_by_strategy(self, strategy: str, reason: str = "стратегия удалена") -> int:
        rows = self.conn.execute(
            "SELECT id FROM trades WHERE closed_at IS NULL AND strategy = ?", (strategy,)).fetchall()
        for (tid,) in rows:
            self.conn.execute(
                "UPDATE trades SET exit=entry, closed_at=?, pnl=0.0, r_multiple=0.0, reason=? WHERE id=?",
                (time.time(), reason, tid),
            )
        self.conn.commit()
        return len(rows)

    def total_pnl(self) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE closed_at IS NOT NULL").fetchone()
        return row[0] or 0.0

    def pnl_since(self, ts: float) -> float:
        """Суммарный PnL сделок, закрытых после ts (для восстановления дневного лимита)."""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE closed_at IS NOT NULL AND closed_at > ?",
            (ts,),
        ).fetchone()
        return row[0] or 0.0

    def training_samples(self, limit: int = 2000) -> list:
        rows = self.conn.execute(
            "SELECT features, pnl FROM trades WHERE closed_at IS NOT NULL AND features IS NOT NULL "
            "ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for features_json, pnl in rows:
            try:
                d = json.loads(features_json)
                vec = [d.get(n, 0.0) for n in FEATURE_NAMES]
                out.append({"features": vec, "pnl": pnl or 0.0})
            except Exception:
                continue
        return out

    def closed_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM trades WHERE closed_at IS NOT NULL").fetchone()
        return row[0] or 0

    def pnl_by_strategy_since(self, ts: float) -> dict:
        rows = self.conn.execute(
            "SELECT strategy, SUM(pnl) FROM trades WHERE closed_at IS NOT NULL AND closed_at > ? GROUP BY strategy",
            (ts,),
        ).fetchall()
        return {r[0]: round(r[1] or 0, 2) for r in rows}

    def recent_losing_close(self, symbol: str, within_sec: float):
        """Возвращает время и PnL последнего убыточного закрытия по символу (или None)."""
        row = self.conn.execute(
            "SELECT closed_at, pnl FROM trades WHERE symbol = ? AND closed_at IS NOT NULL AND pnl < 0 "
            "ORDER BY closed_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if not row or (row[0] or 0) <= 0:
            return None
        if time.time() - row[0] > within_sec:
            return None
        return {"closed_at": row[0], "pnl": row[1] or 0.0}

    def recent_close(self, symbol: str, within_sec: float):
        """Возвращает время и PnL последнего закрытия по символу (любого исхода) или None."""
        row = self.conn.execute(
            "SELECT closed_at, pnl FROM trades WHERE symbol = ? AND closed_at IS NOT NULL "
            "ORDER BY closed_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if not row or (row[0] or 0) <= 0:
            return None
        if time.time() - row[0] > within_sec:
            return None
        return {"closed_at": row[0], "pnl": row[1] or 0.0}

    def open_micro_exit(self, symbol: str, side: str, entry: float, exit_price: float, reason: str) -> int:
        """Регистрирует микро-выход для наблюдения за ценой после него (30с/1м/3м/5м)."""
        cur = self.conn.execute(
            "INSERT INTO micro_exits (symbol, side, entry, exit_price, exit_ts, reason) VALUES (?,?,?,?,?,?)",
            (symbol, side, entry, exit_price, time.time(), reason),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_micro_exit_check(self, watch_id: int, col: str, price: float):
        self.conn.execute(f"UPDATE micro_exits SET {col}=? WHERE id=?", (price, watch_id))
        self.conn.commit()

    def finish_micro_exit(self, watch_id: int, max_against: float, max_for: float, false_exit: float):
        """Фиксирует итог наблюдения. max_against — худший ход против позиции после выхода (%),
        max_for — лучший ход в сторону позиции (пропущенное движение, %), false_exit — 1/0."""
        self.conn.execute(
            "UPDATE micro_exits SET max_against=?, max_for=?, false_exit=?, done=1 WHERE id=?",
            (max_against, max_for, false_exit, watch_id),
        )
        self.conn.commit()

    def micro_exit_stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(false_exit),0), AVG(max_for), AVG(max_against) "
            "FROM micro_exits WHERE done=1"
        ).fetchone()
        return {"total": rows[0] or 0, "false_exits": rows[1] or 0,
                "avg_max_for": round(rows[2] or 0, 3), "avg_max_against": round(rows[3] or 0, 3)}

    def log_signal(self, symbol: str, side: str, strategy: str, entry: float, stop_loss: float, features: dict):
        self.conn.execute(
            "INSERT INTO signals_log (ts, symbol, side, strategy, entry, stop_loss, features) VALUES (?,?,?,?,?,?,?)",
            (time.time(), symbol, side, strategy, entry, stop_loss, json.dumps(features or {})),
        )
        self.conn.execute("DELETE FROM signals_log WHERE id NOT IN (SELECT id FROM signals_log ORDER BY id DESC LIMIT 8000)")
        self.conn.commit()

    def signal_context(self, symbol: str, side: str, entry: float, tolerance: float = 0.01):
        """Находит недавний сигнал, из которого можно восстановить защитный стоп."""
        rows = self.conn.execute(
            "SELECT ts, strategy, entry, stop_loss, features FROM signals_log "
            "WHERE symbol=? AND side=? ORDER BY id DESC LIMIT 50",
            (symbol, side),
        ).fetchall()
        for ts, strategy, signal_entry, stop_loss, features_json in rows:
            if not signal_entry or abs(float(signal_entry) - entry) / entry > tolerance:
                continue
            try:
                features = json.loads(features_json or "{}")
            except Exception:
                features = {}
            return {"ts": ts, "strategy": strategy, "entry": float(signal_entry),
                    "stop_loss": float(stop_loss), "features": features}
        return None

    def update_signal_outcomes(self, prices: dict, horizon_sec: float = 1200.0, r_target: float = 0.5):
        cutoff = time.time() - horizon_sec
        rows = self.conn.execute(
            "SELECT id, symbol, side, entry, stop_loss FROM signals_log WHERE outcome IS NULL AND ts < ?",
            (cutoff,),
        ).fetchall()
        updated = 0
        for rid, symbol, side, entry, stop_loss in rows:
            price = prices.get(symbol)
            if not price or not entry or not stop_loss:
                continue
            risk_pct = abs(entry - stop_loss) / entry
            if risk_pct <= 0:
                continue
            move = (price - entry) / entry if side == "long" else (entry - price) / entry
            label = 1.0 if move >= r_target * risk_pct else 0.0
            self.conn.execute("UPDATE signals_log SET outcome=? WHERE id=?", (label, rid))
            updated += 1
        if updated:
            self.conn.commit()
        return updated

    def trade_training_rows(self, limit: int = 2000) -> list:
        rows = self.conn.execute(
            "SELECT entry, stop_loss, qty, pnl, closed_at, features FROM trades "
            "WHERE closed_at IS NOT NULL AND features IS NOT NULL ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for entry, stop_loss, qty, pnl, closed_at, features_json in rows:
            try:
                out.append({"entry": entry, "stop_loss": stop_loss, "qty": qty, "pnl": pnl,
                            "ts": closed_at, "features": json.loads(features_json)})
            except Exception:
                continue
        return out

    def signal_training_samples(self, limit: int = 2000) -> list:
        rows = self.conn.execute(
            "SELECT ts, entry, stop_loss, features, outcome FROM signals_log "
            "WHERE outcome IS NOT NULL AND features IS NOT NULL ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for ts, entry, stop_loss, features_json, outcome in rows:
            try:
                d = json.loads(features_json)
                vec = [d.get(n, 0.0) for n in FEATURE_NAMES]
                out.append({"features": vec, "label": float(outcome), "ts": ts})
            except Exception:
                continue
        return out

    def stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT strategy, COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl), AVG(r_multiple) "
            "FROM trades WHERE closed_at IS NOT NULL GROUP BY strategy"
        ).fetchall()
        total = self.conn.execute("SELECT COUNT(*), SUM(pnl) FROM trades WHERE closed_at IS NOT NULL").fetchone()
        return {
            "by_strategy": [{"strategy": r[0], "trades": r[1], "wins": r[2], "pnl": round(r[3] or 0, 4), "avg_r": round(r[4] or 0, 2)} for r in rows],
            "total_trades": total[0] or 0,
            "total_pnl": round(total[1] or 0, 4),
        }

    def close(self):
        self.conn.close()
