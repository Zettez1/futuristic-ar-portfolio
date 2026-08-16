from core.models import MarketSnapshot
from features import indicators as ind
from features.pipeline import FeatureBundle
from strategies.base import Strategy


class TrendFollowingStrategy(Strategy):
    name = "trend_follow"
    horizon = "1h"

    def signals(self, snap: MarketSnapshot, bundle: FeatureBundle, engine):
        k = snap.klines.get("1h", [])
        if len(k) < 80:
            return []
        c = [x[4] for x in k]
        h = [x[2] for x in k]
        l = [x[3] for x in k]
        price = c[-1]
        ema50, ema100, ema200 = ind.last(ind.ema(c, 50)), ind.last(ind.ema(c, 100)), ind.last(ind.ema(c, 200))
        adx_v = ind.last(ind.adx(h, l, c))
        atr_v = ind.last(ind.atr(h, l, c))
        sigs = []
        if ema50 > ema100 > ema200 and adx_v > 25:
            pullback = c[-2] < ema50 < price
            if pullback:
                sl = ema50 * 0.995
                tp = price + 3 * atr_v
                sigs.append(self._sig(snap, bundle, "long", price, sl, tp, 0.62, self.horizon,
                                      f"восходящий тренд (EMA50>100>200, ADX {adx_v:.0f}) + откат к EMA50"))
        if ema50 < ema100 < ema200 and adx_v > 25:
            pullback = c[-2] > ema50 > price
            if pullback:
                sl = ema50 * 1.005
                tp = price - 3 * atr_v
                sigs.append(self._sig(snap, bundle, "short", price, sl, tp, 0.62, self.horizon,
                                      f"нисходящий тренд (EMA50<100<200, ADX {adx_v:.0f}) + откат к EMA50"))
        return [s for s in sigs if s]
