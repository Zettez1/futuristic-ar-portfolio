from typing import Optional

from core.models import MarketSnapshot, Signal
from features.pipeline import FeatureBundle


class Strategy:
    name = "base"
    horizon = "unknown"

    def signals(self, snap: MarketSnapshot, bundle: FeatureBundle, engine) -> list:
        return []

    def _sig(self, snap, bundle, side, entry, sl, tp, confidence, tf, reason="", extra_features=None) -> Optional[Signal]:
        if entry <= 0 or sl <= 0 or (tp is not None and tp <= 0) or confidence < 0.5:
            return None
        features = {**bundle.to_dict(), **(extra_features or {})}
        return Signal(
            symbol=snap.symbol, side=side, entry=entry, stop_loss=sl, take_profit=tp,
            confidence=confidence, strategy=self.name, timeframe=tf,
            reason=reason, features=features,
        )
