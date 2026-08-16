import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
MODELS_DIR.mkdir(exist_ok=True)
MODEL_VERSION = 2


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -500, 500)))


class MLP:
    def __init__(self, input_dim: int, hidden: int = 64, lr: float = 0.01):
        self.input_dim = input_dim
        self.hidden = hidden
        self.lr = lr
        rng = np.random.default_rng(42)
        scale1 = np.sqrt(2.0 / input_dim)
        scale2 = np.sqrt(2.0 / hidden)
        self.w1 = rng.normal(0, scale1, (input_dim, hidden))
        self.b1 = np.zeros(hidden)
        self.w2 = rng.normal(0, scale2, (hidden, 1))
        self.b2 = np.zeros(1)
        self.mean = np.zeros(input_dim)
        self.std = np.ones(input_dim)

    def fit_normalizer(self, X: np.ndarray):
        if len(X) == 0:
            return
        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0)
        self.std[self.std < 1e-9] = 1.0

    def _norm(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean) / self.std

    def forward(self, X: np.ndarray) -> np.ndarray:
        self.z1 = X @ self.w1 + self.b1
        self.a1 = np.maximum(0, self.z1)
        self.z2 = self.a1 @ self.w2 + self.b2
        return sigmoid(self.z2)

    def train_step(self, X: np.ndarray, y: np.ndarray, w: np.ndarray = None):
        p = self.forward(X)
        dz2 = p - y.reshape(-1, 1)
        if w is not None:
            dz2 = dz2 * w.reshape(-1, 1)
        self.w2 -= self.lr * (self.a1.T @ dz2) / len(X)
        self.b2 -= self.lr * dz2.mean(axis=0)
        da1 = dz2 @ self.w2.T
        dz1 = da1 * (self.a1 > 0)
        self.w1 -= self.lr * (X.T @ dz1) / len(X)
        self.b1 -= self.lr * dz1.mean(axis=0)

    def train(self, X: np.ndarray, y: np.ndarray, epochs: int = 200, batch: int = 32,
              X_val: np.ndarray = None, y_val: np.ndarray = None, patience: int = 50,
              verbose: bool = False):
        n = len(X)
        if n == 0:
            return
        X = self._norm(X)
        if X_val is not None:
            X_val = self._norm(X_val)
        pos_frac = float(y.mean()) if y.size else 0.5
        sample_w = np.where(y == 1, 1.0 / max(pos_frac, 1e-6), 1.0 / max(1 - pos_frac, 1e-6))
        best_acc = -1.0
        best_weights = None
        since = 0
        for _ in range(epochs):
            idx = np.random.permutation(n)
            for start in range(0, n, batch):
                b = idx[start:start + batch]
                self.train_step(X[b], y[b], sample_w[b])
            if X_val is not None and y_val is not None:
                acc = self.accuracy(X_val, y_val)
                if acc > best_acc:
                    best_acc = acc
                    best_weights = (self.w1.copy(), self.b1.copy(), self.w2.copy(), self.b2.copy())
                    since = 0
                else:
                    since += 1
                    if since >= patience:
                        if best_weights is not None:
                            self.w1, self.b1, self.w2, self.b2 = best_weights
                        break
        else:
            if best_weights is not None:
                self.w1, self.b1, self.w2, self.b2 = best_weights

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.forward(self._norm(X)).ravel()

    def accuracy(self, X: np.ndarray, y: np.ndarray) -> float:
        if len(X) == 0:
            return 0.0
        preds = (self.predict(X) > 0.5).astype(int)
        return float((preds == y.astype(int)).mean())

    def save(self, path: Path = None):
        path = path or MODELS_DIR / "entry_net.npz"
        np.savez(path, w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2,
                 mean=self.mean, std=self.std, model_version=MODEL_VERSION)

    def load(self, path: Path = None) -> bool:
        path = path or MODELS_DIR / "entry_net.npz"
        if not path.exists():
            return False
        data = np.load(path)
        if "model_version" not in data or int(data["model_version"]) != MODEL_VERSION:
            return False
        if data["w1"].shape != self.w1.shape or data["w2"].shape != self.w2.shape:
            return False
        self.w1, self.b1 = data["w1"], data["b1"]
        self.w2, self.b2 = data["w2"], data["b2"]
        if "mean" in data and "std" in data:
            self.mean, self.std = data["mean"], data["std"]
        return True
