from core.models import MarketSnapshot
from features import indicators as ind
from features.pipeline import FeatureBundle
from strategies.base import Strategy


class BreakoutStrategy(Strategy):
    name = "breakout"
    horizon = "1h"
    min_volume_ratio = 2.0
    max_extension_atr = 1.5
    last_diagnostic = "нет сигнала"

    def _reject(self, reason: str):
        self.last_diagnostic = reason
        return []

    def signals(self, snap: MarketSnapshot, bundle: FeatureBundle, engine):
        self.last_diagnostic = "нет сигнала"
        k = snap.klines.get("1h", [])
        if len(k) < 50:
            return self._reject("мало 1h-свечей")
        o = [x[1] for x in k]
        c = [x[4] for x in k]
        h = [x[2] for x in k]
        l = [x[3] for x in k]
        v = [x[5] for x in k]
        price = float(snap.last_price or snap.ticker.get("last") or c[-1])
        recent_h = max(h[-30:-1])
        recent_l = min(l[-30:-1])
        range_width = (recent_h - recent_l) / recent_l if recent_l else 0.0
        atr_series = ind.atr(h, l, c)
        atr_v = float(atr_series.iloc[-2]) if len(atr_series) > 1 else 0.0
        atr_avg = float(atr_series.iloc[-32:-2].mean()) if len(atr_series) >= 34 else 0.0
        vol_ratio = v[-1] / max(ind.last(ind.sma(v, 20)), 1e-9)
        true_range = max(h[-1] - l[-1], abs(h[-1] - c[-2]), abs(l[-1] - c[-2]))
        body_ratio = abs(price - o[-1]) / max(true_range, 1e-12)
        expansion_ratio = true_range / max(atr_v, 1e-12)
        structure = bundle.raw.get("structure", "")
        trend_score = bundle.scores.get("trend", 0.0)
        cvd_slope = bundle.raw.get("cvd_slope", 0.0)
        imb = bundle.raw.get("ob_imbalance", 0.0)
        if range_width > 0.12:
            return self._reject("1h-диапазон слишком широкий")
        if atr_avg <= 0 or atr_v > 0.95 * atr_avg:
            return self._reject("нет сжатия ATR на 1h")
        if vol_ratio < self.min_volume_ratio or body_ratio < 0.55 or expansion_ratio < 1.25:
            failed = []
            if vol_ratio < self.min_volume_ratio:
                failed.append(f"объём {vol_ratio:.2f}x < {self.min_volume_ratio:.2f}x")
            if body_ratio < 0.55:
                failed.append(f"тело свечи {body_ratio:.2f} < 0.55")
            if expansion_ratio < 1.25:
                failed.append(f"расширение {expansion_ratio:.2f} < 1.25")
            return self._reject("; ".join(failed))
        sigs = []
        if (price > recent_h and c[-2] <= recent_h and price > o[-1]
                and (price - recent_h) / max(atr_v, 1e-12) <= self.max_extension_atr
                and structure != "bearish" and trend_score >= -10
                and cvd_slope >= 0.04 and imb >= 0.08):
            sl = recent_h - 0.25 * atr_v
            tp = price + max(3.0 * (price - sl), 2.5 * atr_v)
            sigs.append(self._sig(snap, bundle, "long", price, sl, tp, 0.78, self.horizon,
                                  f"ранний 1h-пробой вверх из сжатия: диапазон {range_width:.1%}, "
                                  f"объём {vol_ratio:.1f}x, расширение {expansion_ratio:.2f} ATR"))
        if (price < recent_l and c[-2] >= recent_l and price < o[-1]
                and (recent_l - price) / max(atr_v, 1e-12) <= self.max_extension_atr
                and structure != "bullish" and trend_score <= 10
                and cvd_slope <= -0.04 and imb <= -0.08):
            sl = recent_l + 0.25 * atr_v
            tp = price - max(3.0 * (sl - price), 2.5 * atr_v)
            sigs.append(self._sig(snap, bundle, "short", price, sl, tp, 0.78, self.horizon,
                                  f"ранний 1h-пробой вниз из сжатия: диапазон {range_width:.1%}, "
                                  f"объём {vol_ratio:.1f}x, расширение {expansion_ratio:.2f} ATR"))
        if not sigs:
            return self._reject("пробой есть, но не подтверждены поток/тренд/стакан")
        self.last_diagnostic = "сигнал"
        return [s for s in sigs if s]
