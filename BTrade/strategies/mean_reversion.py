from core.models import MarketSnapshot
from features import indicators as ind
from features.pipeline import FeatureBundle
from strategies.base import Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    horizon = "15m"

    def signals(self, snap: MarketSnapshot, bundle: FeatureBundle, engine):
        k = snap.klines.get("15m", [])
        if len(k) < 40:
            return []
        c = [x[4] for x in k]
        h = [x[2] for x in k]
        l = [x[3] for x in k]
        price = c[-1]
        rsi_v = ind.last(ind.rsi(c))
        _, up, dn = ind.bollinger(c)
        up = ind.last(up)
        dn = ind.last(dn)
        atr_v = ind.last(ind.atr(h, l, c))
        funding = bundle.raw.get("funding", 0.0) or 0.0
        sigs = []
        if rsi_v < 25 and price < dn:
            sl = price - 1.2 * atr_v
            tp = price + 2.5 * atr_v
            sigs.append(self._sig(snap, bundle, "long", price, sl, tp, 0.6, self.horizon,
                                  f"перепроданность: RSI {rsi_v:.0f} ниже нижней полосы Боллинджера"))
        if rsi_v > 75 and price > up and funding > 0.0005:
            sl = price + 1.2 * atr_v
            tp = price - 2.5 * atr_v
            sigs.append(self._sig(snap, bundle, "short", price, sl, tp, 0.62, self.horizon,
                                  f"перекупленность: RSI {rsi_v:.0f} выше верхней полосы + funding {funding:.5f}"))
        return [s for s in sigs if s]
