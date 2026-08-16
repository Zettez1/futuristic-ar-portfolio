"""Python port of ``Indi.txt`` (Smart Money Concepts & Path Trajectory Predictor).

Считает по OHLCV-свечам: свинговые экстремумы, BOS/CHoCH (слом структуры),
Order Blocks, FVG-имбалансы, зоны Premium/Discount, Fibo-цели ликвидности
и составные траектории (откат -> реакция -> цель). Всё отдаётся AI, чтобы
он спокойнее вёл позицию, когда цена идёт по предсказанному маршруту.
"""

import math


def _safe(value, default=0.0):
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return default
    return float(value)


def _detect_pivots(klines, length: int = 7):
    """Свинг-хаи и свинг-лои: строгий экстремум в окне ±length (как pivothigh/low)."""
    highs = [float(row[2]) for row in klines]
    lows = [float(row[3]) for row in klines]
    n = len(highs)
    pivots_hi = []
    pivots_lo = []
    for i in range(length, n - length):
        left_hi = max(highs[i - length: i])
        right_hi = max(highs[i + 1: i + length + 1])
        left_lo = min(lows[i - length: i])
        right_lo = min(lows[i + 1: i + length + 1])
        if highs[i] > left_hi and highs[i] > right_hi:
            pivots_hi.append((i, highs[i]))
        if lows[i] < left_lo and lows[i] < right_lo:
            pivots_lo.append((i, lows[i]))
    return pivots_hi, pivots_lo


def _find_last_ob(klines, pivots_hi, pivots_lo, scan: int = 15):
    """Последний бычий OB (красная свеча перед свинг-хаем) и медвежий OB (зелёная перед свинг-лоу)."""
    bull_ob = None
    bear_ob = None
    n = len(klines)
    if pivots_hi:
        idx = pivots_hi[-1][0]
        for i in range(max(0, idx - 1), max(0, idx - scan - 1), -1):
            if float(klines[i][4]) < float(klines[i][1]):  # close < open — красная свеча
                bull_ob = {"top": float(klines[i][2]), "bottom": float(klines[i][3])}
                break
    if pivots_lo:
        idx = pivots_lo[-1][0]
        for i in range(max(0, idx - 1), max(0, idx - scan - 1), -1):
            if float(klines[i][4]) > float(klines[i][1]):  # close > open — зелёная свеча
                bear_ob = {"top": float(klines[i][2]), "bottom": float(klines[i][3])}
                break
    return bull_ob, bear_ob


def _fvg_last(klines):
    """FVG на последних трёх свечах: low[0] > high[2] (бычий), high[0] < low[2] (медвежий)."""
    if len(klines) < 3:
        return False, False
    low0 = float(klines[-1][3])
    high2 = float(klines[-3][2])
    high0 = float(klines[-1][2])
    low2 = float(klines[-3][3])
    return bool(low0 > high2), bool(high0 < low2)


def _structure_events(klines, length: int = 7):
    """BOS/CHoCH: слом последнего свинга с определением тренда и типа события."""
    highs = [float(row[2]) for row in klines]
    lows = [float(row[3]) for row in klines]
    closes = [float(row[4]) for row in klines]
    n = len(highs)
    last_hi = None
    last_lo = None
    last_hi_bar = None
    last_lo_bar = None
    trend = 0
    events = []
    for i in range(n):
        if i >= length and i < n - length:
            if highs[i] > max(highs[i - length: i]) and highs[i] > max(highs[i + 1: i + length + 1]):
                last_hi = highs[i]
                last_hi_bar = i
            if lows[i] < min(lows[i - length: i]) and lows[i] < min(lows[i + 1: i + length + 1]):
                last_lo = lows[i]
                last_lo_bar = i
        if last_hi is not None and closes[i] > last_hi:
            label = "CHoCH" if trend == -1 else "BOS"
            events.append({"type": label, "side": "bull", "price": last_hi, "bar": i})
            trend = 1
        if last_lo is not None and closes[i] < last_lo:
            label = "CHoCH" if trend == 1 else "BOS"
            events.append({"type": label, "side": "bear", "price": last_lo, "bar": i})
            trend = -1
    last_event = events[-1] if events else None
    return {
        "trend": trend,
        "last_hi": last_hi,
        "last_lo": last_lo,
        "last_hi_bar": last_hi_bar,
        "last_lo_bar": last_lo_bar,
        "events": events[-8:],
        "last_event": last_event,
        "bos_count": sum(1 for e in events if e["type"] == "BOS"),
        "choch_count": sum(1 for e in events if e["type"] == "CHoCH"),
    }


def smc_snapshot(klines: list, price: float, swing_length: int = 7, ob_scan: int = 15,
                 fibo_low: float = 0.272, fibo_high: float = 0.618) -> dict:
    """Полная картина SMC: структура, OB, FVG, PD-зоны, цели и траектории."""
    rows = list(klines or [])
    if len(rows) < swing_length * 2 + 3:
        return {"ready": False, "reason": "not enough bars"}
    structure = _structure_events(rows, swing_length)
    pivots_hi, pivots_lo = _detect_pivots(rows, swing_length)
    bull_ob, bear_ob = _find_last_ob(rows, pivots_hi, pivots_lo, ob_scan)
    fvg_bull, fvg_bear = _fvg_last(rows)

    last_hi = structure["last_hi"]
    last_lo = structure["last_lo"]
    equilibrium = (last_hi + last_lo) / 2 if last_hi is not None and last_lo is not None else None
    price = float(price or rows[-1][4])

    targets = {}
    paths = {}
    if last_hi is not None and last_lo is not None:
        span = abs(last_hi - last_lo)
        bull_top = last_hi + span * fibo_high
        bull_bot = last_hi + span * fibo_low
        bear_top = last_lo - span * fibo_low
        bear_bot = last_lo - span * fibo_high
        targets = {
            "bull_top": bull_top, "bull_bottom": bull_bot, "bull_center": (bull_top + bull_bot) / 2,
            "bear_top": bear_top, "bear_bottom": bear_bot, "bear_center": (bear_top + bear_bot) / 2,
        }
        demand_level = (bull_ob or {}).get("top", last_lo)
        supply_level = (bear_ob or {}).get("bottom", last_hi)
        paths = {
            # Составной маршрут: цена -> откат к demand/supply -> цель
            "bull": [round(price, 8), round(demand_level, 8), round(targets["bull_center"], 8)],
            "bear": [round(price, 8), round(supply_level, 8), round(targets["bear_center"], 8)],
        }

    eq_pos = None
    if equilibrium is not None:
        eq_pos = 1.0 if price > equilibrium else -1.0 if price < equilibrium else 0.0

    return {
        "ready": True,
        "trend": structure["trend"],
        "last_event": structure["last_event"],
        "bos_count": structure["bos_count"],
        "choch_count": structure["choch_count"],
        "last_hi": last_hi,
        "last_lo": last_lo,
        "equilibrium": equilibrium,
        "price_vs_eq": eq_pos,
        "bull_ob": bull_ob,
        "bear_ob": bear_ob,
        "fvg_bull": fvg_bull,
        "fvg_bear": fvg_bear,
        "targets": targets,
        "paths": paths,
    }


def find_fvgs(open_, high, low, close):
    """Все FVG-имбалансы: (низ[i] > верх[i-2]) — бычий, (верх[i] < низ[i-2]) — медвежий."""
    fvgs = []
    for i in range(2, len(close)):
        if low[i] > high[i - 2]:
            fvgs.append({"type": "bull", "bottom": high[i - 2], "top": low[i], "bar": i})
        if high[i] < low[i - 2]:
            fvgs.append({"type": "bear", "bottom": high[i], "top": low[i - 2], "bar": i})
    return fvgs


def _htf_trend(htf_klines, ema_fast: int = 50, ema_slow: int = 200):
    """Тренд старшего ТФ, как в индикаторе: close/ema50 против ema200."""
    closes = [float(row[4]) for row in (htf_klines or ())]
    if len(closes) < ema_slow + 5:
        return None
    import features.indicators as ind
    ema_fast_v = ind.last(ind.ema(closes, ema_fast))
    ema_slow_v = ind.last(ind.ema(closes, ema_slow))
    last_close = closes[-1]
    if last_close > ema_slow_v and ema_fast_v > ema_slow_v:
        return 1
    if last_close < ema_slow_v and ema_fast_v < ema_slow_v:
        return -1
    return 0


def indicator1_probability(ltf_klines: list, htf_klines: list = None, price: float = None,
                           swing_length: int = 7, ob_scan: int = 15) -> dict:
    """Вероятность направления на 1m — точный порт расчёта из ``Indi.txt``.

    Веса как в индикаторе: HTF-контекст 30%, локальная структура 25%,
    Premium/Discount 25%, реакция на OB/FVG или RSI 20%.
    """
    rows = list(ltf_klines or [])
    if len(rows) < swing_length * 2 + 3:
        return {"ready": False, "reason": "not enough bars"}
    structure = _structure_events(rows, swing_length)
    pivots_hi, pivots_lo = _detect_pivots(rows, swing_length)
    bull_ob, bear_ob = _find_last_ob(rows, pivots_hi, pivots_lo, ob_scan)
    closes = [float(row[4]) for row in rows]
    close = float(price if price is not None else closes[-1])

    bull_score = 0.0
    bear_score = 0.0
    reasons = ["", "", "", ""]

    # 1. Контекст HTF (30%)
    htf_trend = _htf_trend(htf_klines)
    if htf_trend == 1:
        bull_score += 30
        reasons[0] = "HTF: бычий тренд"
    elif htf_trend == -1:
        bear_score += 30
        reasons[0] = "HTF: медвежий тренд"
    else:
        reasons[0] = "HTF: флэт/нет"

    # 2. Локальная структура LTF (25%)
    trend = structure["trend"]
    if trend == 1:
        bull_score += 25
        reasons[1] = "Структура: бычья BOS"
    elif trend == -1:
        bear_score += 25
        reasons[1] = "Структура: медвежья BOS"
    else:
        reasons[1] = "Структура: нет BOS"

    # 3. Premium / Discount (25%)
    eq = None
    if structure["last_hi"] is not None and structure["last_lo"] is not None:
        eq = (float(structure["last_hi"]) + float(structure["last_lo"])) / 2
        if close < eq:
            bull_score += 25
            reasons[2] = "Зона: Discount (покупки)"
        else:
            bear_score += 25
            reasons[2] = "Зона: Premium (продажи)"
    else:
        reasons[2] = "Зона: нет свингов"

    # 4. Реакция на OB / RSI (20%)
    inside_bull_ob = bool(bull_ob) and float(bull_ob["bottom"]) <= close <= float(bull_ob["top"])
    inside_bear_ob = bool(bear_ob) and float(bear_ob["bottom"]) <= close <= float(bear_ob["top"])
    if inside_bull_ob:
        bull_score += 20
        reasons[3] = "Реакция: внутри бычьего OB"
    elif inside_bear_ob:
        bear_score += 20
        reasons[3] = "Реакция: внутри медвежьего OB"
    else:
        import features.indicators as ind
        rsi_val = ind.last(ind.rsi(closes, 14), default=None)
        rsi_txt = f"{rsi_val:.1f}" if rsi_val is not None else "n/a"
        if rsi_val is not None and rsi_val > 50:
            bull_score += 20
            reasons[3] = f"Импульс: RSI > 50 ({rsi_txt})"
        else:
            bear_score += 20
            reasons[3] = f"Импульс: RSI < 50 ({rsi_txt})"

    total = max(bull_score + bear_score, 1.0)
    bull_prob = int(round(bull_score / total * 100))
    bear_prob = 100 - bull_prob
    return {
        "ready": True,
        "bull_prob": bull_prob,
        "bear_prob": bear_prob,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "reasons": reasons,
        "trend": trend,
        "htf_trend": htf_trend,
        "equilibrium": eq,
    }


def smc_analysis(open_, high, low, close, volume, price, swing_length: int = 7, ob_scan: int = 15):
    """Полный анализ по ценам-массивам (совместим с тестами и старым вызовом)."""
    rows = list(zip(open_, high, low, close, volume))
    snapshot = smc_snapshot(rows, price, swing_length=swing_length, ob_scan=ob_scan)
    fvgs = find_fvgs(open_, high, low, close)
    nearest_fvg = None
    for fvg in reversed(fvgs):
        if fvg["bar"] <= len(close) - 2:
            nearest_fvg = fvg
            break
    return {
        "structure": {
            "trend": snapshot["trend"],
            "last_event": snapshot["last_event"],
            "bos_count": snapshot["bos_count"],
            "choch_count": snapshot["choch_count"],
        },
        "n_fvgs": len(fvgs),
        "nearest_fvg": nearest_fvg,
    }
