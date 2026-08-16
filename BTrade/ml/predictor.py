"""Predictor: нейросеть, которая по стакану (Indicator 1 VAP/DOM) строит
вероятность направления цены на горизонте HORIZON. Работает как отдельный
процесс (scripts/run_predictor.py): собирает снипсеты, размечает их фактическим
движением цены, обучает MLP и сохраняет models/predictor_net.npz.
"""

import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np

from core.logger import get_logger
from ml.neural import MLP, MODELS_DIR

log = get_logger("predictor")

DOM_FEATURES = [
    "dom_pressure", "buy_pct", "delta_ratio", "cvd_norm",
    "poc_distance_atr", "value_area_position", "wall_bid_norm", "wall_ask_norm",
    "wall_direction", "volume_pressure", "close_pressure", "body_pressure",
]

DOM_FEATURES_V2 = DOM_FEATURES + [
    "momentum_3c", "momentum_10c", "atr_norm", "range_norm", "volume_surge",
    "smc_trend", "smc_price_vs_eq", "smc_fvg_count",
]

DEFAULT_DB = Path(__file__).resolve().parent.parent / "db" / "predictor.db"
MODEL_PATH = MODELS_DIR / "predictor_net.npz"
MODEL_PATH_V2 = MODELS_DIR / "predictor_net_v2.npz"


def _f(value, default=0.0) -> float:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return default
    return float(value)


def dom_feature_vector(orderbook: dict, price: float) -> list:
    """Компактный вектор состояния стакана из Indicator 1 VAP/DOM."""
    poc = _f(orderbook.get("poc") or price)
    step = max(_f(orderbook.get("step")), 1e-12)
    total = max(_f(orderbook.get("profile_total_volume")), 1.0)
    return [
        _f(orderbook.get("dom_pressure"), 0.0),
        _f(orderbook.get("buy_pct"), 50.0) / 100.0,
        _f(orderbook.get("delta_ratio"), 0.0),
        _f(orderbook.get("cvd")) / total,
        (price - poc) / step if poc else 0.0,
        _f(orderbook.get("value_area_position"), 0.0),
        _f(orderbook.get("wall_bid_size")) / total,
        _f(orderbook.get("wall_ask_size")) / total,
        _f(orderbook.get("wall_direction"), 0.0),
        _f(orderbook.get("volume_pressure"), 0.0),
        _f(orderbook.get("close_pressure"), 0.0),
        _f(orderbook.get("body_pressure"), 0.0),
    ]


def _bar_series(klines):
    """Достаёт OHLCV-массивы из flat-клайнов Binance (без доп. аналитики)."""
    if not klines:
        return None
    try:
        o = [float(r[1]) for r in klines]
        h = [float(r[2]) for r in klines]
        l = [float(r[3]) for r in klines]
        c = [float(r[4]) for r in klines]
        v = [float(r[5]) for r in klines]
    except (TypeError, ValueError, IndexError):
        return None
    return o, h, l, c, v


def dom_feature_vector_v2(orderbook: dict, price: float, klines: list = None) -> list:
    """Вектор V2: 12 DOM-фич + момент-фичи по 1m-свечам + SMC-статус."""
    vec = dom_feature_vector(orderbook, price)
    bars = _bar_series(klines)
    if not bars or not bars[4] or price <= 0:
        return vec + [0.0] * (len(DOM_FEATURES_V2) - len(DOM_FEATURES))
    o, h, l, c, v = bars
    target = float(c[-1]) or price
    mom3 = (target - float(c[-3])) / target if len(c) >= 3 else 0.0
    mom10 = (target - float(c[-10])) / target if len(c) >= 10 else 0.0
    atr = sum(float(h[i]) - float(l[i]) for i in range(max(0, len(c) - 14), len(c))) / max(len(c), 1)
    atr_norm = (atr / target) if atr and target else 0.0
    range_norm = (float(h[-1]) - float(l[-1])) / target if target else 0.0
    recent_v = [float(x) for x in v[-20:]]
    avg_v = (sum(recent_v) / len(recent_v)) if recent_v else 0.0
    volume_surge = (float(v[-1]) / avg_v) if avg_v > 0 else 0.0
    try:
        from features.smc import smc_snapshot
        snap = smc_snapshot(klines, target)
    except Exception:
        snap = {}
    trend = float(_f(snap.get("trend"), 0))
    eq = _f(snap.get("equilibrium"), 0.0)
    price_vs_eq = 0.0
    if eq > 0:
        price_vs_eq = 1.0 if target > eq else (-1.0 if target < eq else 0.0)
    fvg_bull = snap.get("fvg_bull")
    fvg_bear = snap.get("fvg_bear")
    fvg_count = 0
    for value in (fvg_bull, fvg_bear):
        if isinstance(value, (list, tuple)):
            fvg_count += len(value)
        elif value:
            fvg_count += 1
    return vec + [mom3, mom10, atr_norm, range_norm, volume_surge, trend, price_vs_eq, float(fvg_count)]


class PredictorStore:
    """SQLite: snapshots + метки фактического движения цены."""

    VALID_TABLES = ("snapshots", "snapshots_v2")

    def __init__(self, db_path: Path = None, table: str = "snapshots"):
        if table not in self.VALID_TABLES:
            raise ValueError(f"неизвестная таблица: {table}")
        self.table = table
        self.db_path = Path(db_path or DEFAULT_DB)
        self.db_path.parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "symbol TEXT NOT NULL,"
            "ts REAL NOT NULL,"
            "price REAL NOT NULL,"
            "features TEXT NOT NULL,"
            "horizon_ts REAL NOT NULL,"
            "label INTEGER,"
            "future_price REAL,"
            "UNIQUE(symbol, ts)"
            ")"
        )
        self.conn.commit()

    def insert(self, symbol: str, ts: float, price: float, features: list, horizon_ts: float):
        payload = json.dumps(features)
        try:
            self.conn.execute(
                f"INSERT OR IGNORE INTO {self.table} (symbol, ts, price, features, horizon_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (symbol, ts, price, payload, horizon_ts),
            )
            self.conn.commit()
        except Exception as exc:
            log.warning("predictor insert: %s", exc)

    def due_for_label(self, now: float, limit: int = 2000):
        cur = self.conn.execute(
            f"SELECT id, symbol, price FROM {self.table} "
            "WHERE label IS NULL AND horizon_ts <= ? ORDER BY horizon_ts LIMIT ?",
            (now, limit),
        )
        return cur.fetchall()

    def set_label(self, row_id: int, label: int, future_price: float):
        self.conn.execute(
            f"UPDATE {self.table} SET label = ?, future_price = ? WHERE id = ?",
            (label, future_price, row_id),
        )
        self.conn.commit()

    def labeled(self, min_samples: int = 50, cap: int = 20000):
        cur = self.conn.execute(
            f"SELECT symbol, price, features, label FROM {self.table} "
            "WHERE label IS NOT NULL ORDER BY id DESC LIMIT ?",
            (cap,),
        )
        rows = cur.fetchall()
        if len(rows) < min_samples:
            return [], 0
        return rows, len(rows)

    def stats(self) -> dict:
        cur = self.conn.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN label IS NOT NULL THEN 1 ELSE 0 END) FROM {self.table}"
        )
        total, labeled = cur.fetchone()
        return {"total": int(total or 0), "labeled": int(labeled or 0)}


class DomPredictor:
    """MLP поверх вектора стакана; отдельная модель от entry_net."""

    def __init__(self, input_dim: int = None, model_path=None):
        self.input_dim = int(input_dim or len(DOM_FEATURES))
        self.model_path = Path(model_path or MODEL_PATH)
        self.net = MLP(input_dim=self.input_dim, hidden=64, lr=0.01)

    def load(self) -> bool:
        if not self.model_path.exists():
            return False
        data = np.load(self.model_path)
        if data["w1"].shape != self.net.w1.shape or data["w2"].shape != self.net.w2.shape:
            return False
        self.net.w1, self.net.b1 = data["w1"], data["b1"]
        self.net.w2, self.net.b2 = data["w2"], data["b2"]
        if "mean" in data and "std" in data:
            self.net.mean, self.net.std = data["mean"], data["std"]
        return True

    def save(self):
        features = DOM_FEATURES_V2 if self.input_dim == len(DOM_FEATURES_V2) else DOM_FEATURES
        np.savez(self.model_path, w1=self.net.w1, b1=self.net.b1, w2=self.net.w2, b2=self.net.b2,
                 mean=self.net.mean, std=self.net.std, features=features)

    def predict(self, features: list) -> float:
        vec = np.array([[float(v) for v in features]], dtype=float)
        if vec.shape[1] != self.net.input_dim:
            return 0.5
        return float(self.net.predict(vec)[0])

    def train(self, rows, val_frac: float = 0.25, epochs: int = 400, patience: int = 60) -> dict:
        X = np.array([json.loads(r[2]) for r in rows], dtype=float)
        y = np.array([int(r[3]) for r in rows], dtype=float)
        if X.shape[1] != self.net.input_dim or len(X) < 50:
            return {"status": "skipped", "reason": f"need >=50 samples, got {len(X)}"}
        split = int(len(X) * (1 - val_frac))
        X_tr, y_tr, X_val, y_val = X[:split], y[:split], X[split:], y[split:]
        self.net.fit_normalizer(X_tr)
        self.net.train(X_tr, y_tr, epochs=epochs, batch=32, X_val=X_val, y_val=y_val, patience=patience)
        acc = self.net.accuracy(X_val, y_val)
        self.save()
        pos_rate = float(y.mean())
        log.info(f"Predictor: обучен на {len(X)} снипсетах ({len(X_tr)} тр / {len(X_val)} вал),"
                 f" точность на отложенных {acc:.0%}, доля «вверх» {pos_rate:.0%}")
        return {"status": "ok", "samples": len(X), "val_acc": acc, "pos_rate": pos_rate}
