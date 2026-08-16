from core.logger import get_logger
from core.models import MarketSnapshot
from features import indicators as ind
from features import volume_profile as vp
from features.macro import detect_session
from features.pipeline import FeatureBundle
from strategies.base import Strategy

log = get_logger("impulse")


class ImpulseStrategy(Strategy):
    name = "impulse"
    horizon = "5m"
    lookback = 12
    min_volume_ratio = 1.8
    max_extension_atr = 1.5
    min_body_ratio = 0.55
    min_expansion_atr = 1.25
    last_diagnostic = "нет сигнала"

    def _reject(self, reason: str):
        self.last_diagnostic = reason
        return []

    def signals(self, snap: MarketSnapshot, bundle: FeatureBundle, engine):
        self.last_diagnostic = "нет сигнала"
        k = snap.klines.get("5m", [])
        if len(k) < 40:
            return self._reject("мало 5m-свечей")
        opens = [float(c[1]) for c in k]
        highs = [c[2] for c in k]
        lows = [c[3] for c in k]
        closes = [c[4] for c in k]
        vols = [c[5] for c in k]
        price = float(snap.last_price or snap.ticker.get("last") or closes[-1])

        atr_series = ind.atr(highs, lows, closes)
        if len(atr_series) < 34:
            return self._reject("недостаточно данных ATR")
        atr_before = float(atr_series.iloc[-2])
        atr_avg = float(atr_series.iloc[-32:-2].mean())
        if atr_avg <= 0:
            return self._reject("ATR не рассчитан")
        compression_ratio = atr_before / atr_avg
        compressed = compression_ratio <= 0.98

        rng_high = max(highs[-self.lookback - 1:-1])
        rng_low = min(lows[-self.lookback - 1:-1])
        rng_h = rng_high - rng_low
        if rng_h <= 0:
            return self._reject("нулевой диапазон")
        if not compressed:
            return self._reject(f"нет сжатия ATR ({compression_ratio:.2f}x > 0.98x)")
        if rng_h / atr_avg > 4.5:
            return self._reject("диапазон перед пробоем слишком широкий")

        vol_sma = sum(vols[-21:-1]) / 20.0
        vol_ratio = vols[-1] / vol_sma if vol_sma else 1.0
        bar_high = max(float(highs[-1]), price)
        bar_low = min(float(lows[-1]), price)
        true_range = max(bar_high - bar_low, abs(bar_high - float(closes[-2])),
                          abs(bar_low - float(closes[-2])))
        expansion_ratio = true_range / max(atr_before, 1e-12)
        body_ratio = abs(price - opens[-1]) / max(true_range, 1e-12)

        broke_up = price > rng_high and closes[-2] <= rng_high and price > opens[-1]
        broke_dn = price < rng_low and closes[-2] >= rng_low and price < opens[-1]
        if not (broke_up or broke_dn):
            return self._reject("нет свежего пробоя 5m-диапазона")

        imb = vp.imbalance(snap.orderbook, depth=10)
        depth = vp.depth_ratio(snap.orderbook, depth=10)
        cvd_slope = bundle.raw.get("cvd_slope", 0.0)
        structure = bundle.raw.get("structure", "")
        trend_score = bundle.scores.get("trend", 0.0)

        common_ok = (
            vol_ratio >= self.min_volume_ratio
            and expansion_ratio >= self.min_expansion_atr
            and body_ratio >= self.min_body_ratio
        )
        if not common_ok:
            failed = []
            if vol_ratio < self.min_volume_ratio:
                failed.append(f"объём {vol_ratio:.2f}x < {self.min_volume_ratio:.2f}x")
            if expansion_ratio < self.min_expansion_atr:
                failed.append(f"расширение {expansion_ratio:.2f} < {self.min_expansion_atr:.2f}")
            if body_ratio < self.min_body_ratio:
                failed.append(f"тело свечи {body_ratio:.2f} < {self.min_body_ratio:.2f}")
            return self._reject("; ".join(failed))

        sigs = []
        long_flow_votes = sum((cvd_slope >= 0.04, imb >= 0.10, depth >= 1.15))
        short_flow_votes = sum((cvd_slope <= -0.04, imb <= -0.10, depth <= 0.87))
        long_htf_ok = structure != "bearish" and trend_score >= 0
        short_htf_ok = structure != "bullish" and trend_score <= 0
        long_flow_ok = long_flow_votes >= 2 and cvd_slope >= 0.04
        short_flow_ok = short_flow_votes >= 2 and cvd_slope <= -0.04
        long_extension = (price - rng_high) / max(atr_before, 1e-12)
        short_extension = (rng_low - price) / max(atr_before, 1e-12)
        stop_buffer = max(0.5 * atr_before, 0.10 * rng_h)

        if (broke_up and long_flow_ok and long_htf_ok
                and 0 < long_extension <= self.max_extension_atr):
            entry = price
            sl = rng_high - stop_buffer
            risk = entry - sl
            tp = entry + max(4.0 * risk, 4.0 * atr_before)
            conf = min(0.98, 0.72 + min(0.12, (vol_ratio - 1.6) * 0.06)
                       + min(0.08, max(0.0, expansion_ratio - 1.15) * 0.04)
                       + (0.04 if long_flow_votes == 3 else 0.0))
            sigs.append(self._sig(snap, bundle, "long", entry, sl, tp, conf, self.horizon,
                                  f"ранний импульс вверх: пробой {rng_high:.8g}, объём {vol_ratio:.2f}x, "
                                  f"расширение {expansion_ratio:.2f} ATR, поток {cvd_slope:+.3f}, "
                                  f"сжатие ATR {compression_ratio:.2f}x",
                                  {"impulse_volume_ratio": vol_ratio, "impulse_expansion_atr": expansion_ratio,
                                   "impulse_extension_atr": long_extension, "impulse_flow_votes": long_flow_votes}))
        if (broke_dn and short_flow_ok and short_htf_ok
                and 0 < short_extension <= self.max_extension_atr):
            entry = price
            sl = rng_low + stop_buffer
            risk = sl - entry
            tp = entry - max(4.0 * risk, 4.0 * atr_before)
            conf = min(0.98, 0.72 + min(0.12, (vol_ratio - 1.6) * 0.06)
                       + min(0.08, max(0.0, expansion_ratio - 1.15) * 0.04)
                       + (0.04 if short_flow_votes == 3 else 0.0))
            sigs.append(self._sig(snap, bundle, "short", entry, sl, tp, conf, self.horizon,
                                  f"ранний импульс вниз: пробой {rng_low:.8g}, объём {vol_ratio:.2f}x, "
                                  f"расширение {expansion_ratio:.2f} ATR, поток {cvd_slope:+.3f}, "
                                  f"сжатие ATR {compression_ratio:.2f}x",
                                  {"impulse_volume_ratio": vol_ratio, "impulse_expansion_atr": expansion_ratio,
                                   "impulse_extension_atr": short_extension, "impulse_flow_votes": short_flow_votes}))
        if not sigs:
            return self._reject("пробой есть, но не подтверждены поток/тренд/стакан")
        self.last_diagnostic = "сигнал"
        return [s for s in sigs if s]


class Impulse15mStrategy(ImpulseStrategy):
    """15m импульс — ловит реальные движения. Пробой + объём = вход, остальное скоринг."""
    name = "impulse15m"
    horizon = "15m"
    lookback = 10
    max_extension_atr = 3.0
    last_diagnostic = "нет сигнала"

    def _reject(self, reason: str):
        self.last_diagnostic = reason
        return []

    def signals(self, snap, bundle, engine):
        self.last_diagnostic = "нет сигнала"
        k = snap.klines.get("15m", [])
        if len(k) < 40:
            return self._reject("мало свечей")
        opens = [float(c[1]) for c in k]
        highs = [c[2] for c in k]
        lows = [c[3] for c in k]
        closes = [c[4] for c in k]
        vols = [c[5] for c in k]
        price = float(snap.last_price or snap.ticker.get("last") or closes[-1])

        atr_series = ind.atr(highs, lows, closes)
        if len(atr_series) < 34:
            return self._reject("нет ATR")
        atr_before = float(atr_series.iloc[-2])
        atr_avg = float(atr_series.iloc[-32:-2].mean())
        if atr_avg <= 0:
            return self._reject("ATR=0")

        rng_high = max(highs[-self.lookback - 1:-1])
        rng_low = min(lows[-self.lookback - 1:-1])
        rng_h = rng_high - rng_low
        if rng_h <= 0:
            return self._reject("нулевой диапазон")

        broke_up = price > rng_high and closes[-2] <= rng_high and price > opens[-1]
        broke_dn = price < rng_low and closes[-2] >= rng_low and price < opens[-1]
        if not (broke_up or broke_dn):
            return self._reject("нет пробоя")

        vol_sma = sum(vols[-21:-1]) / 20.0
        vol_ratio = vols[-1] / vol_sma if vol_sma else 1.0
        if vol_ratio < 0.6:
            return self._reject(f"нет объёма ({vol_ratio:.1f}x)")

        bar_high = max(float(highs[-1]), price)
        bar_low = min(float(lows[-1]), price)
        true_range = max(bar_high - bar_low, abs(bar_high - float(closes[-2])),
                          abs(bar_low - float(closes[-2])))
        expansion_ratio = true_range / max(atr_before, 1e-12)
        body_ratio = abs(price - float(opens[-1])) / max(true_range, 1e-12)

        imb = vp.imbalance(snap.orderbook, depth=10)
        depth = vp.depth_ratio(snap.orderbook, depth=10)
        cvd_slope = bundle.raw.get("cvd_slope", 0.0)
        structure = bundle.raw.get("structure", "")
        trend_score = bundle.scores.get("trend", 0.0)
        compression_ratio = atr_before / atr_avg

        direction = 1 if broke_up else -1

        score = 0
        reasons = []
        if vol_ratio >= 1.4:    score += 1; reasons.append(f"V{vol_ratio:.1f}")
        else:                    reasons.append(f"v{vol_ratio:.1f}")
        if body_ratio >= 0.40:  score += 1; reasons.append(f"B{body_ratio:.0%}")
        else:                    reasons.append(f"b{body_ratio:.0%}")
        if expansion_ratio >= 0.80: score += 1; reasons.append(f"E{expansion_ratio:.1f}")
        else:                    reasons.append(f"e{expansion_ratio:.1f}")
        if cvd_slope * direction >= 0.02: score += 1; reasons.append("CVD")
        if compression_ratio <= 1.05: score += 1; reasons.append(f"ATR{compression_ratio:.2f}")
        if structure != ("bearish" if direction > 0 else "bullish"): score += 1; reasons.append("S")
        if trend_score * direction >= -5: score += 1; reasons.append("T")

        extension = (price - rng_high) / max(atr_before, 1e-12) if broke_up else \
                     (rng_low - price) / max(atr_before, 1e-12)
        if extension > self.max_extension_atr:
            return self._reject(f"поздно ({extension:.1f} ATR)")

        min_score = 5 if detect_session() == "asia" else 4
        if score < min_score:
            return self._reject(f"слабо {score}/7 (нужно {min_score}) {' '.join(reasons)}")

        # SL: 0.75 ATR — даёт движение дышать
        stop_buffer = max(0.75 * atr_before, 0.12 * rng_h)
        entry = price
        if broke_up:
            sl = rng_high - stop_buffer; risk = entry - sl
            # TP: 4x ATR или до PDH — что ближе
            tp_atr = entry + 4.0 * atr_before
            tp = tp_atr
        else:
            sl = rng_low + stop_buffer; risk = sl - entry
            tp_atr = entry - 4.0 * atr_before
            tp = tp_atr

        conf = min(0.95, 0.50 + score * 0.07)
        side = "long" if broke_up else "short"
        extra = {"impulse_score": score, "impulse_volume_ratio": vol_ratio,
                 "impulse_expansion_atr": expansion_ratio}

        self.last_diagnostic = "сигнал"
        return [s for s in [self._sig(snap, bundle, side, entry, sl, tp, conf, self.horizon,
                   f"15m {side}: {rng_high if broke_up else rng_low:.6g} {score}/7 {' '.join(reasons)}", extra)] if s]
