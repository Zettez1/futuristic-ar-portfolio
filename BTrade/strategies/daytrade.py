from core.models import MarketSnapshot
from features import indicators as ind
from features.pipeline import FeatureBundle
from strategies.base import Strategy


class DayTradeStrategy(Strategy):
    name = "daytrade"
    horizon = "15m"

    def signals(self, snap: MarketSnapshot, bundle: FeatureBundle, engine):
        k = snap.klines.get("15m", [])
        if len(k) < 60:
            return []
        c = [x[4] for x in k]
        h = [x[2] for x in k]
        l = [x[3] for x in k]
        v = [x[5] for x in k]
        price = c[-1]
        ema_fast, ema_slow = ind.last(ind.ema(c, 9)), ind.last(ind.ema(c, 21))
        macd_line, _, macd_hist = ind.macd(c)
        hist_now = ind.last(macd_hist)
        hist_prev = ind.last(macd_hist.shift(1))
        atr_v = ind.last(ind.atr(h, l, c))
        vol_ratio = v[-1] / max(ind.last(ind.sma(v, 20)), 1e-9)
        sigs = []
        if ema_fast > ema_slow and hist_now > 0 and hist_prev <= 0 and vol_ratio > 1.2:
            sl = price - 1.5 * atr_v
            tp = price + 3.0 * atr_v
            sigs.append(self._sig(snap, bundle, "long", price, sl, tp, 0.6, self.horizon,
                                  f"свежее пересечение MACD вверх + тренд по EMA + объём {vol_ratio:.1f}x"))
        if ema_fast < ema_slow and hist_now < 0 and hist_prev >= 0 and vol_ratio > 1.2:
            sl = price + 1.5 * atr_v
            tp = price - 3.0 * atr_v
            sigs.append(self._sig(snap, bundle, "short", price, sl, tp, 0.6, self.horizon,
                                  f"свежее пересечение MACD вниз + тренд по EMA + объём {vol_ratio:.1f}x"))
        return [s for s in sigs if s]
