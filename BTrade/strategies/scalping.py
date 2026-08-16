from core.models import MarketSnapshot
from features import indicators as ind
from features import volume_profile as vp
from features.pipeline import FeatureBundle
from strategies.base import Strategy


class ScalpingStrategy(Strategy):
    name = "scalping"
    horizon = "1m"

    def signals(self, snap: MarketSnapshot, bundle: FeatureBundle, engine):
        k = snap.klines.get("1m", [])
        if len(k) < 30:
            return []
        closes = [c[4] for c in k]
        price = float(k[-1][4])
        imb = vp.imbalance(snap.orderbook, depth=10)
        depth = vp.depth_ratio(snap.orderbook, depth=10)
        trades = snap.trades or []
        cvd = vp.cvd(trades[-100:])
        cvd_slope = (cvd[-1] - cvd[0]) / max(abs(cvd[0]), 1e-9) if len(cvd) > 1 else 0.0
        rsi = ind.last(ind.rsi(closes))
        atr_v = ind.last(ind.atr([k[i][2] for i in range(len(k))], [k[i][3] for i in range(len(k))], closes))
        sigs = []
        if imb > 0.15 and depth > 1.2 and cvd_slope > 0.02 and rsi < 70:
            sl = price - 0.6 * atr_v
            tp = price + 1.2 * atr_v
            sigs.append(self._sig(snap, bundle, "long", price, sl, tp, 0.65 + min(imb, 0.3), self.horizon,
                                  f"поток покупок: имбаланс стакана {imb:+.2f}, лимитки 1.2x, CVD {cvd_slope:+.3f}"))
        if imb < -0.15 and depth < 0.8 and cvd_slope < -0.02 and rsi > 30:
            sl = price + 0.6 * atr_v
            tp = price - 1.2 * atr_v
            sigs.append(self._sig(snap, bundle, "short", price, sl, tp, 0.65 + min(-imb, 0.3), self.horizon,
                                  f"поток продаж: имбаланс стакана {imb:+.2f}, лимитки 0.8x, CVD {cvd_slope:+.3f}"))
        return [s for s in sigs if s]
