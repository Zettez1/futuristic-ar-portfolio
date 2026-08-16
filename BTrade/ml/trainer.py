import numpy as np

from core.logger import get_logger
from features.pipeline import FEATURE_NAMES
from learning.journal import TradeJournal
from ml.neural import MLP

log = get_logger("trainer")

R_TARGET = 0.5
MIN_VAL_FRAC = 0.25


class Trainer:
    def __init__(self, journal: TradeJournal, net: MLP, min_samples: int = 50):
        self.journal = journal
        self.net = net
        self.min_samples = min_samples

    def _label_trade(self, t: dict):
        risk = abs(t["entry"] - t["stop_loss"]) * t["qty"]
        if risk <= 0 or t["pnl"] is None:
            return None
        r = t["pnl"] / risk
        return 1.0 if r >= R_TARGET else 0.0

    def _collect_samples(self) -> list:
        samples = []
        for t in self.journal.trade_training_rows():
            label = self._label_trade(t)
            if label is None:
                continue
            vec = [t["features"].get(n, 0.0) for n in FEATURE_NAMES]
            samples.append((t["ts"], vec, label))
        for s in self.journal.signal_training_samples():
            samples.append((s["ts"], s["features"], s["label"]))
        samples.sort(key=lambda s: s[0])
        return samples

    def retrain(self) -> dict:
        samples = self._collect_samples()
        if len(samples) < self.min_samples:
            log.info(f"Обучение нейросети пропущено: собрано {len(samples)} примеров (нужно минимум {self.min_samples})")
            return {"status": "skipped", "reason": f"only {len(samples)} samples"}
        X = np.array([s[1] for s in samples], dtype=float)
        y = np.array([s[2] for s in samples], dtype=float)
        split = int(len(X) * (1 - MIN_VAL_FRAC))
        self.net.fit_normalizer(X[:split])
        self.net.train(X[:split], y[:split], epochs=300, batch=32,
                       X_val=X[split:], y_val=y[split:], patience=40)
        acc = self.net.accuracy(X[split:], y[split:])
        self.net.save()
        pos_rate = float(y.mean())
        log.info(f"Нейросеть переобучена: {len(X)} примеров ({len(X[:split])} обучение / {len(X[split:])} валидация по времени),"
                 f" точность на отложенных {acc:.0%}, доля успешных {pos_rate:.0%}")
        return {"status": "ok", "samples": len(X), "val_acc": acc}

    def filter_signal(self, features: dict) -> float:
        if not features:
            return 0.5
        vec = np.array([[features.get(n, 0.0) for n in FEATURE_NAMES]], dtype=float)
        if vec.shape[1] != self.net.input_dim:
            return 0.5
        return float(self.net.predict(vec)[0])
