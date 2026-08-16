"""Стратегия на фактах: вход только когда ВСЕ показатели инди.txt (1m)
и стакан с лентой согласованы. SL динамический по ATR: дистанция = множитель × ATR,
чтобы позиция дышала внутри рыночного шума, а не выбивалась случайным тиком.
"""

import os

from core.counters import entry_counters
from features import indicators as ind
from strategies.base import Strategy


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


class FactsConfluenceStrategy(Strategy):
    name = "facts_confluence"
    horizon = "1m"

    @staticmethod
    def _atr_1m(raw) -> float:
        klines = raw.get("indicator_klines") or []
        if len(klines) >= 15:
            high = [float(r[2]) for r in klines]
            low = [float(r[3]) for r in klines]
            close = [float(r[4]) for r in klines]
            return float(ind.last(ind.atr(high, low, close, 14), default=0.0))
        step = float((raw.get("order_book") or {}).get("step") or 0.0)
        return max(step, 0.0)

    @staticmethod
    def _atr_5m(raw) -> float:
        """ATR по 5m свечам: более широкий шум, чтобы стоп не душил позицию."""
        klines = raw.get("indicator_klines_5m") or []
        if len(klines) >= 15:
            high = [float(r[2]) for r in klines]
            low = [float(r[3]) for r in klines]
            close = [float(r[4]) for r in klines]
            return float(ind.last(ind.atr(high, low, close, 14), default=0.0))
        return 0.0

    @staticmethod
    def _price_fmt(value) -> str:
        value = float(value or 0.0)
        return f"{value:.2f}"

    @classmethod
    def _stop_loss(cls, side: str, price: float, smc: dict, depth: dict, atr: float,
                   atr_mult: float = 1.5, min_dist_pct: float = 0.2,
                   atr_mult_hi: float = 0.0, atr_mult_lo: float = 0.0,
                   atr_rel_hi: float = 0.0, atr_rel_lo: float = 0.0,
                   spread_buffer: float = 0.0, bar_extremes: dict = None) -> float:
        """SL по уровню идеи, ATR как буфер (не множитель, если есть уровень).

        Приоритет уровней (для лонга, ниже цены):
          1. Низ ПОСЛЕДНЕЙ (пробойной) свечи bar_extremes["lo"] + буфер ATR×0.25 —
             стоп ЗА свечой, а не впритык;
          2. last_lo / OB-бык / стена — стоп за структурой;
          если уровень дальше ATR-дистанции — стоп точно на уровне;
          если уровень ближе ATR-дистанции — ATR-дистанция (позиция дышит в шуме).
        - Адаптивный множитель: для высоковолатильных (ATR/цена >= atr_rel_hi) берётся
          atr_mult_hi, для низколиквидных (ATR/цена <= atr_rel_lo) — atr_mult_lo.
        - Буфер на спред/слиппедж: stop ∓ spread_buffer.
        """
        if atr_rel_hi > 0 and price > 0 and atr > 0:
            atr_rel = atr / price
            if atr_mult_hi > 0 and atr_rel >= atr_rel_hi:
                atr_mult = atr_mult_hi
            elif atr_mult_lo > 0 and atr_rel_lo > 0 and atr_rel <= atr_rel_lo:
                atr_mult = atr_mult_lo
        min_dist = max(atr * atr_mult, abs(price) * min_dist_pct / 100.0)
        walls = (depth or {}).get("bid_walls") or []
        if side == "long":
            candidates = []
            bar_lo = None
            if bar_extremes and bar_extremes.get("lo"):
                bar_lo = float(bar_extremes["lo"])
                candidates.append(bar_lo)
            last_lo = smc.get("last_lo")
            if last_lo is not None:
                candidates.append(float(last_lo))
            bull_ob = smc.get("bull_ob") or {}
            if bull_ob:
                candidates.append(float(bull_ob["bottom"]))
            if len(walls) >= 2:
                candidates.append(float(walls[1]["price"]))
            elif walls:
                candidates.append(float(walls[0]["price"]))
            if not candidates:
                stop = price - min_dist
            else:
                stop = min(candidates)
                if price - stop < min_dist:
                    stop = price - min_dist
                elif bar_lo is not None and stop == bar_lo and atr > 0:
                    # пробойная свеча: буфер ATR×0.25 ЗА низом свечи
                    stop -= atr * atr_mult * 0.25
            if spread_buffer > 0:
                stop -= spread_buffer
            return stop
        last_hi = smc.get("last_hi")
        candidates = []
        bar_hi = None
        if bar_extremes and bar_extremes.get("hi"):
            bar_hi = float(bar_extremes["hi"])
            candidates.append(bar_hi)
        if last_hi is not None:
            candidates.append(float(last_hi))
        bear_ob = smc.get("bear_ob") or {}
        if bear_ob:
            candidates.append(float(bear_ob["top"]))
        walls = (depth or {}).get("ask_walls") or []
        if len(walls) >= 2:
            candidates.append(float(walls[1]["price"]))
        elif walls:
            candidates.append(float(walls[0]["price"]))
        if not candidates:
            stop = price + min_dist
        else:
            stop = max(candidates)
            if stop - price < min_dist:
                stop = price + min_dist
            elif bar_hi is not None and stop == bar_hi and atr > 0:
                # пробойная свеча: буфер ATR×0.25 ЗА верхом свечи
                stop += atr * atr_mult * 0.25
        if spread_buffer > 0:
            stop += spread_buffer
        return stop

    def signals(self, snap, bundle, engine):
        raw = bundle.raw if bundle else {}
        verdict = raw.get("verdict") or {}
        if verdict.get("side") not in ("long", "short"):
            return []
        side = verdict["side"]
        price = float(snap.last_price or raw.get("price") or 0.0)
        if price <= 0:
            return []
        atr = self._atr_1m(raw)
        atr5 = self._atr_5m(raw)
        if atr5 > atr:
            atr = atr5
        smc = raw.get("smc") or {}
        depth = raw.get("depth") or {}
        atr_mult = _env_float("ENTRY_SL_ATR_MULT", 1.5)
        min_dist_pct = _env_float("ENTRY_SL_MIN_PCT", 0.2)
        atr_mult_hi = _env_float("ENTRY_SL_ATR_MULT_HI", 0.0)
        atr_mult_lo = _env_float("ENTRY_SL_ATR_MULT_LO", 0.0)
        atr_rel_hi = _env_float("ENTRY_SL_ATR_REL_HI", 0.0)
        atr_rel_lo = _env_float("ENTRY_SL_ATR_REL_LO", 0.0)
        spread_buffer = float((depth or {}).get("spread") or 0.0) * _env_float("ENTRY_SL_SPREAD_MULT", 0.0)
        # экстремумы последней (пробойной) 1m свечи — стоп ЗА ней
        bar_extremes = None
        klines = raw.get("indicator_klines") or []
        if klines:
            last_bar = klines[-1]
            if len(last_bar) > 3:
                try:
                    bar_extremes = {"hi": float(last_bar[2]), "lo": float(last_bar[3])}
                except (TypeError, ValueError):
                    bar_extremes = None
        stop_loss = self._stop_loss(side, price, smc, depth, atr, atr_mult=atr_mult,
                                    min_dist_pct=min_dist_pct, atr_mult_hi=atr_mult_hi,
                                    atr_mult_lo=atr_mult_lo, atr_rel_hi=atr_rel_hi,
                                    atr_rel_lo=atr_rel_lo, spread_buffer=spread_buffer,
                                    bar_extremes=bar_extremes)
        if stop_loss <= 0:
            entry_counters.inc("blocked_by_spread")
            return []
        if (side == "long" and stop_loss >= price) or (side == "short" and stop_loss <= price):
            entry_counters.inc("blocked_by_spread")
            return []

        strength = float(verdict.get("strength") or 0.0)
        confidence = min(0.95, 0.50 + strength / 250.0)
        if confidence < 0.50:
            return []

        def _tag(c):
            return "%s:%s" % (c["name"], "B" if c["side"] == 1 else "S")

        comps = [_tag(c) for c in verdict.get("indicator_components", []) if c.get("side") in (1, -1)]
        comps += [_tag(c) for c in verdict.get("book_components", []) if c.get("side") in (1, -1)]
        retest = verdict.get("retest") or {}
        retest_txt = ""
        if retest.get("enabled") and retest.get("pullback") is not None:
            retest_txt = (f" | РЕТЕСТ: ход {retest['impulse_run_atr']:.1f} ATR, "
                          f"откат {retest['pullback'] * 100:.0f}% хода"
                          + (" [sweep+reclaim]" if retest.get("sweep_ok") else ""))
        reason = (
            f"{side.upper()} — все показатели инди.txt синхронно "
            f"{'вверх' if side == 'long' else 'вниз'}: "
            f"{', '.join(comps) if comps else 'нет'} | сила {strength:.0f} | "
            f"вероятность {verdict.get('probability')}%"
            + (" | ИМПУЛЬС: вход в начале движения" if verdict.get("impulse_start") else "")
            + retest_txt
        )

        depth_f = raw.get("depth") or {}
        tape = raw.get("tape") or {}
        route = depth_f.get("route") or []
        route_txt = ";".join(self._price_fmt(r["price"]) for r in route[:5])
        extra = {
            "verdict_strength": float(verdict.get("strength") or 0.0),
            "entry_strength": float(verdict.get("strength") or 0.0),
            "entry_probability": float(verdict.get("probability") or 0.0),
            "entry_imbalance": float(depth_f.get("imbalance") or 0.0),
            "entry_wall_mass": float(
                (depth_f.get("bid_wall_mass") if side == "long" else depth_f.get("ask_wall_mass")) or 0.0),
            "entry_tape_ratio": float((tape or {}).get("buy_ratio") or 0.5),
            "impulse_start": bool(verdict.get("impulse_start")),
            "depth_route": route_txt,
            "agree_count": int(verdict.get("agree_count") or 0),
        }
        return [self._sig(snap, bundle, side, price, stop_loss, None, confidence,
                          self.horizon, reason, extra)]