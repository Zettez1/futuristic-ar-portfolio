import time

from core.models import Signal
from features import indicators as ind
from strategies.base import Strategy


class MaCrossStrategy(Strategy):
    """Вход по пересечению скользящих средних 7/25 на двух таймфреймах.

    1. Находим пересечение MA(7)/MA(25) на 30m — символ «вооружается».
    2. Входим на ПЕРВОМ пересечении на 3m в ту же сторону, что и 30m.

    Выход ботом не управляется из стратегии — только трейлинг-стоп и стоп-лосс.
    """

    name = "ma_cross"
    horizon = "3m"

    def __init__(self, cfg):
        self.cfg = cfg
        self.fast = max(1, int(getattr(cfg, "ma_cross_fast", 7) or 7))
        self.slow = max(2, int(getattr(cfg, "ma_cross_slow", 25) or 25))
        self.ma_type = str(getattr(cfg, "ma_cross_ma_type", "ema") or "ema").lower()
        self.tf_htf = str(getattr(cfg, "ma_cross_htf", "30m") or "30m")
        self.tf_ltf = str(getattr(cfg, "ma_cross_ltf", "3m") or "3m")
        self.htf_minutes = _tf_minutes(self.tf_htf)
        self.ltf_minutes = _tf_minutes(self.tf_ltf)
        self.sl_atr_mult = float(getattr(cfg, "entry_sl_atr_mult", 1.5) or 1.5)
        self.sl_min_pct = float(getattr(cfg, "entry_sl_min_pct", 0.3) or 0.3) / 100.0
        self.max_age_bars = int(getattr(cfg, "ma_cross_max_age_bars", 5) or 5)
        self.filters_enabled = bool(getattr(cfg, "ma_cross_filters_enabled", False))
        self.max_2h_move = float(getattr(cfg, "ma_cross_max_2h_move", 5.0) or 5.0)
        self.max_atr3_pct = float(getattr(cfg, "ma_cross_max_atr3_pct", 1.2) or 1.2)
        self.max_vol_ratio = float(getattr(cfg, "ma_cross_max_vol_ratio", 3.0) or 3.0)
        self.max_range_pos_long = float(getattr(cfg, "ma_cross_max_range_pos_long", 75.0) or 75.0)
        self.max_24h_move = float(getattr(cfg, "ma_cross_max_24h_move", 20.0) or 20.0)
        self.reversal_close_gap_pct = float(getattr(cfg, "ma_cross_reversal_close_gap_pct", 0.2) or 0.2)
        self.last_results = {}
        self._arms = {}

    def _ma(self, values, n):
        if self.ma_type == "ema":
            return ind.ema(values, n)
        if self.ma_type == "wma":
            return ind.sma(values, n)
        return ind.sma(values, n)

    def reset(self):
        self._arms = {}

    def _state(self, symbol):
        state = self._arms.get(symbol)
        if state is None:
            state = {"dir": 0, "bar": None, "cross_bar": None, "ts": 0.0, "entered": False,
                     "last_ltf_bar": None, "skipped_bar": None}
            self._arms[symbol] = state
        return state

    def confirm_entry(self, symbol):
        """Вызывается ботом ПОСЛЕ реального открытия позиции по сигналу."""
        state = self._state(symbol)
        state["entered"] = True
        state["last_ltf_bar"] = None

    @staticmethod
    def _reset_arm(state):
        state.update(dir=0, bar=None, cross_bar=None, ts=0.0, entered=False, last_ltf_bar=None,
                     skipped_bar=None)

    def diagnose(self, symbol):
        return self.last_results.get(symbol) or {}

    def evaluate(self, symbol, rows_htf, rows_ltf, price=None):
        """Проверяет одну монету. Возвращает Signal для входа или None."""
        res30 = self._cross(rows_htf, self.htf_minutes)
        res3 = self._cross(rows_ltf, self.ltf_minutes)
        diag = {
            "dir30": res30["cross"],
            "dir3": res3["cross"],
            "atr30": round(float(res30["atr"] or 0.0), 8),
            "atr3": round(float(res3["atr"] or 0.0), 8),
            "fast": self.fast,
            "slow": self.slow,
        }
        self.last_results[symbol] = diag

        state = self._state(symbol)
        if not state["dir"]:
            recent = self._recent_cross(rows_htf)
            if recent["cross"] and recent["bar_open_ts"] != state.get("skipped_bar"):
                state.update(dir=recent["cross"], bar=recent["bar_open_ts"],
                             cross_bar=recent["bar_open_ts"],
                             ts=time.time(), entered=False, last_ltf_bar=None)
                diag["dir30"] = recent["cross"]
                diag["armed"] = recent["cross"]
            elif recent["cross"] and recent["bar_open_ts"] == state.get("skipped_bar"):
                diag["skip_first_3m_passed"] = True
            return None

        if state.get("cross_bar") is None:
            self._reset_arm(state)
            diag["armed"] = 0
            return None

        if state["bar"] != res30["bar_open_ts"]:
            if res30["cross"] == state["dir"]:
                state.update(bar=res30["bar_open_ts"], ts=time.time(), entered=False,
                             last_ltf_bar=None)
            elif res30["cross"]:
                state.update(dir=res30["cross"], bar=res30["bar_open_ts"], cross_bar=res30["bar_open_ts"],
                             ts=time.time(), entered=False, last_ltf_bar=None)
                diag["armed"] = diag["dir30"]
                return None
            else:
                age_bars = (res30["bar_open_ts"] - state["cross_bar"]) / (self.htf_minutes * 60_000)
                if res30.get("trend") == state["dir"] and age_bars <= self.max_age_bars:
                    state.update(bar=res30["bar_open_ts"], ts=time.time(), entered=False,
                                 last_ltf_bar=None)
                    diag["armed"] = state["dir"]
                else:
                    self._reset_arm(state)
                    diag["armed"] = 0
                return None

        age_bars = (res30["bar_open_ts"] - state["cross_bar"]) / (self.htf_minutes * 60_000)
        if age_bars > self.max_age_bars:
            self._reset_arm(state)
            diag["armed"] = 0
            return None

        diag["armed"] = state["dir"]

        if state["entered"]:
            return None
        if res3["cross"] and res3["cross"] != state["dir"]:
            # 3m-перекрёст в ПРОТИВОПОЛОЖНУЮ сторону — установка не подтвердилась
            self._reset_arm(state)
            diag["armed"] = 0
            return None

        # Ищем ПЕРВЫЙ совпадающий (в сторону 30m) 3m-перекрёст после 30m-бара.
        first_ts = self._first_ltf_cross(rows_ltf, state["cross_bar"], state["dir"])
        diag["first_3m_ts"] = first_ts
        if first_ts is None:
            # совпадающего 3m-перекрёста ещё не было — ждём
            return None

        last_ts = res3["bar_open_ts"]
        if first_ts < last_ts:
            # первый совпадающий 3m-перекрёст УЖЕ прошёл на более ранней свече —
            # монета бесполезна, рукав снимаем и запоминаем бар, чтобы не вооружать заново
            skipped = state["cross_bar"]
            self._reset_arm(state)
            state["skipped_bar"] = skipped
            diag["armed"] = 0
            diag["skip_first_3m_passed"] = True
            return None
        if state["last_ltf_bar"] == last_ts:
            return None
        if last_ts < state["bar"]:
            # 3m-пересечение случилось ДО 30m-пересечения — не берём
            return None

        entry = float(price or (rows_ltf[-1][4] if rows_ltf else 0.0))
        if entry <= 0:
            return None
        atr = float(res3["atr"] or 0.0)
        if atr <= 0:
            return None
        distance = max(self.sl_atr_mult * atr, entry * self.sl_min_pct, entry * 1e-4)
        side = "long" if state["dir"] == 1 else "short"
        stop_loss = entry - distance if side == "long" else entry + distance
        state["last_ltf_bar"] = res3["bar_open_ts"]
        diag["armed"] = state["dir"]
        diag["entry"] = entry
        diag["stop_loss"] = stop_loss

        if self.filters_enabled:
            ok, why = self._entry_filters(rows_ltf, entry, side, atr)
            if not ok:
                diag["filter_reject"] = why
                return None

        return Signal(
            symbol=symbol, side=side, entry=entry, stop_loss=stop_loss,
            take_profit=None, confidence=0.9, strategy=self.name, timeframe=self.tf_ltf,
            reason=(f"30m пересечение MA{self.fast}/MA{self.slow} "
                    f"{'ВВЕРХ' if state['dir'] == 1 else 'ВНИЗ'} + "
                    f"первое 3m пересечение в ту же сторону | SL {self.sl_atr_mult:g}×ATR(3m) "
                    f"({distance / entry * 100:.2f}%)"),
            features={
                "ma_cross_dir30": state["dir"],
                "ma_cross_dir3": res3["cross"],
                "ma_cross_fast": self.fast,
                "ma_cross_slow": self.slow,
                "ma_cross_atr30": diag["atr30"],
                "ma_cross_atr3": diag["atr3"],
                "ma_cross_distance": distance,
                "ma_cross_bar30": state["bar"],
            },
        )

    def _cross(self, rows, tf_minutes):
        """Пересечение MA(fast)/MA(slow) на последней ЗАКРЫТОЙ свече.

        Возвращает:
          cross      — 1 (пересечение вверх), -1 (вниз) или 0 (нет)
          bar_open_ts — ts открытия последней закрытой свечи
          atr         — ATR(14) этого таймфрейма
          trend       — 1 если fast > slow, -1 если fast < slow, иначе 0
        """
        closed = _closed(rows, tf_minutes)
        if len(closed) <= self.slow:
            return {"cross": 0, "bar_open_ts": 0, "atr": 0.0, "trend": 0}
        closes = [float(r[4]) for r in closed]
        fast = float(self._ma(closes, self.fast).iloc[-1])
        slow = float(self._ma(closes, self.slow).iloc[-1])
        fast_prev = float(self._ma(closes, self.fast).iloc[-2])
        slow_prev = float(self._ma(closes, self.slow).iloc[-2])
        cross = 0
        if fast_prev <= slow_prev and fast > slow:
            cross = 1
        elif fast_prev >= slow_prev and fast < slow:
            cross = -1
        trend = 0
        if fast > slow:
            trend = 1
        elif fast < slow:
            trend = -1
        atr = 0.0
        try:
            atr_value = ind.atr([float(r[2]) for r in closed],
                                [float(r[3]) for r in closed],
                                closes, 14).iloc[-1]
            atr = float(atr_value) if atr_value == atr_value else 0.0
        except Exception:
            atr = 0.0
        return {"cross": cross, "bar_open_ts": int(closed[-1][0]), "atr": atr, "trend": trend}

    def _recent_cross(self, rows_htf):
        """Ищет перекрёст MA(fast)/MA(slow) в окне последних max_age_bars закрытых 30m-свечей.

        Возвращает {cross, bar_open_ts, atr, trend} самого свежего перекрёста
        (1/=вверх, -1/=вниз, 0 если в окне перекрёста не было).
        """
        closed = _closed(rows_htf, self.htf_minutes)
        if len(closed) <= self.slow:
            return {"cross": 0, "bar_open_ts": 0, "atr": 0.0, "trend": 0}
        closes = [float(r[4]) for r in closed]
        fast = self._ma(closes, self.fast)
        slow = self._ma(closes, self.slow)
        start = max(self.slow, len(closed) - int(self.max_age_bars))
        found = {"cross": 0, "bar_open_ts": 0}
        for i in range(start, len(closed)):
            f = float(fast.iloc[i])
            s = float(slow.iloc[i])
            f_p = float(fast.iloc[i - 1])
            s_p = float(slow.iloc[i - 1])
            if f != f or s != s or f_p != f_p or s_p != s_p:
                continue
            cross = 0
            if f_p <= s_p and f > s:
                cross = 1
            elif f_p >= s_p and f < s:
                cross = -1
            if cross:
                found = {"cross": cross, "bar_open_ts": int(closed[i][0])}
        if not found["cross"]:
            return {"cross": 0, "bar_open_ts": 0, "atr": 0.0, "trend": 0}
        trend = 1 if float(fast.iloc[-1]) > float(slow.iloc[-1]) else -1
        atr = 0.0
        try:
            atr_value = ind.atr([float(r[2]) for r in closed],
                                [float(r[3]) for r in closed],
                                closes, 14).iloc[-1]
            atr = float(atr_value) if atr_value == atr_value else 0.0
        except Exception:
            atr = 0.0
        return {"cross": found["cross"], "bar_open_ts": found["bar_open_ts"], "atr": atr, "trend": trend}

    def ltf_cross_since(self, rows_ltf, since_ts):
        """Направление САМОГО СВЕЖЕГО 3m-перекрёста после since_ts (1/-1/0).

        Используется для разворота: пока позиция открыта, каждый скан запоминает,
        пересёк ли 3m-индикатор в противоположную сторону (только ЗАКРЫТЫЕ свечи).
        """
        closed = _closed(rows_ltf, self.ltf_minutes)
        if len(closed) <= self.slow:
            return 0
        closes = [float(r[4]) for r in closed]
        fast = self._ma(closes, self.fast)
        slow = self._ma(closes, self.slow)
        found = 0
        for i in range(self.slow, len(closed)):
            if int(closed[i][0]) < since_ts:
                continue
            f = float(fast.iloc[i])
            s = float(slow.iloc[i])
            f_p = float(fast.iloc[i - 1])
            s_p = float(slow.iloc[i - 1])
            if f != f or s != s or f_p != f_p or s_p != s_p:
                continue
            if f_p <= s_p and f > s:
                found = 1
            elif f_p >= s_p and f < s:
                found = -1
        return found

    def ema_gap_pct(self, rows_ltf):
        """Зазор между EMA7 и EMA25 по последней ЗАКРЫТОЙ свече, в % (со знаком)."""
        try:
            closed = _closed(rows_ltf, self.ltf_minutes)
            if len(closed) <= self.slow:
                return 0.0
            closes = [float(r[4]) for r in closed]
            fast = float(self._ma(closes, self.fast).iloc[-1])
            slow = float(self._ma(closes, self.slow).iloc[-1])
            if slow <= 0:
                return 0.0
            return (fast / slow - 1.0) * 100.0
        except Exception:
            return 0.0

    def near_cross_against(self, side, price, rows_ltf):
        """EMAs очень близки друг к другу, а цена уже ПРОТИВ позиции (по закрытым 3m-свечам).

        Разворот без закрытого перекрёста: МА почти пересеклись (зазор <= reversal_close_gap_pct%),
        а цена на момент выбития SL ниже fast-EMA (для лонга) / выше (для шорта).
        """
        try:
            closed = _closed(rows_ltf, self.ltf_minutes)
            if len(closed) <= self.slow or price <= 0:
                return False
            closes = [float(r[4]) for r in closed]
            fast = float(self._ma(closes, self.fast).iloc[-1])
            slow = float(self._ma(closes, self.slow).iloc[-1])
            if fast <= 0 or slow <= 0:
                return False
            d = 1 if side == "long" else -1
            gap = (fast / slow - 1.0) * 100.0 * d
            price_against = (price * d) < (fast * d)
            return abs(gap) <= self.reversal_close_gap_pct and price_against
        except Exception:
            return False

    def _entry_filters(self, rows_ltf, entry, side, atr3):
        """Фильтры «входа как у прибыльных сделок» (только ЗАКРЫТЫЕ 3m-свечи).

        Возвращает (True, "") или (False, причина отказа):
          1. анти-чейз: 2h движение в сторону входа <= max_2h_move%
          2. ATR(3m) не экстремальный: <= max_atr3_pct% от цены
          3. нет всплеска объёма: последняя закрытая <= max_vol_ratio x среднего(30)
          4. лонг не у вершины 24h-диапазона: позиция <= max_range_pos_long%
          5. не падающий нож / не перегрет: лонг при 24h >= -max_24h_move%,
             шорт при 24h <= +max_24h_move%
        При нехватке данных фильтр пропускается (fail-open).
        """
        closed = _closed(rows_ltf, self.ltf_minutes)
        n = len(closed)
        if n < 30:
            return True, ""
        c = [float(r[4]) for r in closed]
        h = [float(r[2]) for r in closed]
        lo = [float(r[3]) for r in closed]
        v = [float(r[5]) for r in closed]
        d = 1 if side == "long" else -1

        if n >= 41:
            move_2h = (entry / c[-40] - 1.0) * 100.0
            if move_2h * d > self.max_2h_move:
                return False, f"2h уже {move_2h * d:+.2f}% в сторону входа (лимит {self.max_2h_move:g}%)"

        if atr3 > 0 and atr3 / entry * 100.0 > self.max_atr3_pct:
            return False, (f"ATR(3m) {atr3 / entry * 100.0:.2f}% от цены "
                           f"(лимит {self.max_atr3_pct:g}%)")

        if n >= 32:
            avg = sum(v[-31:-1]) / 30.0
            if avg > 0 and v[-1] / avg > self.max_vol_ratio:
                return False, (f"всплеск объёма {v[-1] / avg:.1f}x среднего "
                               f"(лимит {self.max_vol_ratio:g}x)")

        window = min(480, n - 1)
        if window >= 100 and side == "long":
            hi = max(h[-window:])
            lo2 = min(lo[-window:])
            if hi > lo2:
                pos = (entry - lo2) / (hi - lo2) * 100.0
                if pos > self.max_range_pos_long:
                    return False, (f"лонг у вершины диапазона: позиция {pos:.0f}% "
                                   f"(лимит {self.max_range_pos_long:g}%)")

        if n >= 481:
            ch24 = (entry / c[-480] - 1.0) * 100.0
            if side == "long" and ch24 < -self.max_24h_move:
                return False, f"падающий нож: 24h {ch24:+.1f}% (лимит -{self.max_24h_move:g}%)"
            if side == "short" and ch24 > self.max_24h_move:
                return False, f"шорт перегретой монеты: 24h {ch24:+.1f}% (лимит +{self.max_24h_move:g}%)"

        return True, ""

    def _first_ltf_cross(self, rows_ltf, from_ts, want_dir):
        """bar_open_ts ПЕРВОГО 3m-пересечения в направлении want_dir начиная с from_ts, или None.

        Проходим по всем ЗАКРЫТЫМ 3m-свечам с bar_open_ts >= from_ts и возвращаем
        свечу, где произошло пересечение MA(fast)/MA(slow) в нужную сторону.
        """
        closed = _closed(rows_ltf, self.ltf_minutes)
        if len(closed) <= self.slow:
            return None
        closes = [float(r[4]) for r in closed]
        fast = self._ma(closes, self.fast)
        slow = self._ma(closes, self.slow)
        for i in range(self.slow, len(closed)):
            if int(closed[i][0]) < from_ts:
                continue
            f = float(fast.iloc[i])
            s = float(slow.iloc[i])
            f_p = float(fast.iloc[i - 1])
            s_p = float(slow.iloc[i - 1])
            if f != f or s != s or f_p != f_p or s_p != s_p:
                continue
            if want_dir == 1 and f_p <= s_p and f > s:
                return int(closed[i][0])
            if want_dir == -1 and f_p >= s_p and f < s:
                return int(closed[i][0])
        return None


def _closed(rows, tf_minutes):
    """Только ЗАКРЫТЫЕ свечи (последняя формирующаяся отбрасывается)."""
    if not rows:
        return []
    step_ms = int(tf_minutes) * 60_000
    now = int(time.time() * 1000.0)
    return [row for row in rows if now >= int(row[0]) + step_ms]


def _tf_minutes(timeframe: str) -> int:
    value = str(timeframe or "").lower()
    if value.endswith("m"):
        return max(1, int(value[:-1] or 0))
    if value.endswith("h"):
        return max(1, int(value[:-1] or 0) * 60)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 3