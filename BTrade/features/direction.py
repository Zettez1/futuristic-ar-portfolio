"""Комитет фактов: решение только по цифрам стакана и индикатора indi.txt.

ВОХОД — только когда ВСЕ непустые показатели индикатора (1m) одновременно
указывают в одну сторону И стакан с лентой не противоречат. Чем больше
показателей и чем они сильнее, тем выше strength.

ВЫХОД — по развороту индикатора, ослаблению индикатора или потере
поддержки стакана (дисбаланс перевернулся + агрессия против + стена входа
съедена).
"""

import math
import time

from features import indicators as ind
from features.depth_oracle import liquidity_ahead


def _sign(value, eps=1e-12):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0
    if math.isnan(value) or math.isinf(value):
        return 0
    return 1 if value >= eps else -1 if value <= -eps else 0


def _f(cfg, name, default):
    """Значение из cfg; явный 0 не заменяется дефолтом."""
    value = getattr(cfg, name, None)
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_float(value, default=0.0):
    try:
        value = float(value or 0.0)
    except (TypeError, ValueError):
        return float(default)
    return value


def _ob_mid(orderbook):
    """Средняя цена по best bid/ask стакана (или None)."""
    try:
        bids = (orderbook or {}).get("bids") or []
        asks = (orderbook or {}).get("asks") or []
        if bids and asks:
            return (float(bids[0][0]) + float(asks[0][0])) / 2.0
    except (TypeError, ValueError, IndexError):
        pass
    return None


def indicator_components(prob: dict, smc: dict, klines_1m: list):
    """Показатели инди.txt (порт) на 1m. side: +1 бык, -1 медведь, 0 нейтрально."""
    comps = []
    if not prob or not prob.get("ready"):
        return comps
    htf = int(prob.get("htf_trend") or 0)
    if htf:
        comps.append({"name": "HTF-контекст", "side": htf, "weight": 30})
    # Примечание: HTF против стороны входа блокирует вход хард-фильтром
    # в direction_verdict до проверки противоречий компонентов.
    struct = int(prob.get("trend") if prob.get("trend") is not None else (smc or {}).get("trend") or 0)
    if struct:
        comps.append({"name": "Структура BOS/CHoCH", "side": struct, "weight": 25})
    eq = prob.get("equilibrium")
    if eq is not None and klines_1m:
        price = float(klines_1m[-1][4])
        comps.append({"name": "Premium/Discount", "side": 1 if price < float(eq) else -1, "weight": 25})
    bull_ob = (smc or {}).get("bull_ob") or {}
    bear_ob = (smc or {}).get("bear_ob") or {}
    if klines_1m:
        price = float(klines_1m[-1][4])
        inside_bull = bool(bull_ob) and float(bull_ob["bottom"]) <= price <= float(bull_ob["top"])
        inside_bear = bool(bear_ob) and float(bear_ob["bottom"]) <= price <= float(bear_ob["top"])
        if inside_bull:
            comps.append({"name": "Реакция: Bull OB", "side": 1, "weight": 20})
        elif inside_bear:
            comps.append({"name": "Реакция: Bear OB", "side": -1, "weight": 20})
        else:
            closes = [float(row[4]) for row in klines_1m]
            rsi_val = ind.last(ind.rsi(closes, 14), default=None)
            if rsi_val is not None and rsi_val != 50:
                comps.append({"name": "Импульс RSI", "side": 1 if rsi_val > 50 else -1, "weight": 20})
    last_hi = (smc or {}).get("last_hi")
    last_lo = (smc or {}).get("last_lo")
    if klines_1m:
        high = float(klines_1m[-1][2])
        low = float(klines_1m[-1][3])
        close = float(klines_1m[-1][4])
        if last_hi is not None and high > float(last_hi) and close <= float(last_hi):
            comps.append({"name": "Liquidity sweep (медв)", "side": -1, "weight": 25})
        elif last_lo is not None and low < float(last_lo) and close >= float(last_lo):
            comps.append({"name": "Liquidity sweep (быч)", "side": 1, "weight": 25})
    vols = [float(row[5]) for row in klines_1m] if klines_1m else []
    if len(vols) >= 20 and struct:
        avg = float(sum(vols[-20:]) / 20.0)
        if avg > 0 and vols[-1] > avg * 1.4:
            comps.append({"name": "Объём (Rvol>1.4)", "side": struct, "weight": 15})
    return comps


def book_components(depth: dict, tape: dict, dom: dict):
    """Факты стакана и ленты. Идут в комитет, но нейтральность допустима."""
    comps = []
    if not depth or not depth.get("ready"):
        return comps
    p = float(dom.get("dom_pressure") or 0.0) if dom else 0.0
    if abs(p) >= 0.15:
        comps.append({"name": "DOM давление (VAP)", "side": _sign(p), "weight": 20})
    imb = float(depth.get("imbalance") or 0.0)
    if abs(imb) >= 0.1:
        comps.append({"name": "Дисбаланс стакана", "side": _sign(imb), "weight": 25})
    ws = float(depth.get("wall_side") or 0.0)
    if ws:
        comps.append({"name": "Стены стакана", "side": _sign(ws), "weight": 20})
    if tape and tape.get("ready"):
        ratio = float(tape.get("buy_ratio") or 0.5)
        if abs(ratio - 0.5) >= 0.08:
            comps.append({"name": "Лента сделок", "side": 1 if ratio > 0.5 else -1, "weight": 20})
    iside = float(depth.get("iceberg_side") or 0.0)
    if iside:
        comps.append({"name": "Айсберги", "side": _sign(iside), "weight": 20})
    return comps


def _liq_push(liq: dict, side_str: str) -> tuple:
    """Объём ликвидаций (USDT) в сторону давления и последний event.

    side_str: "short" — ликвидации шортов (принудительные BUY, давят вверх),
              "long" — ликвидации лонгов (принудительные SELL, давят вниз).
    Возвращает (fast_usd, mid_usd, last_age_s, accel).
    accel: ускорение каскада — объём за 5с против среднего темпа за 60с (>= 2 = лавина).
    """
    windows = (liq or {}).get("windows") or {}
    if not windows:
        return 0.0, 0.0, None, 0.0
    last_age = (liq or {}).get(f"{side_str}_last_age_s")
    if last_age is None:
        last_ts = (liq or {}).get("last_ts")
        last_age = (time.time() - last_ts) if last_ts else None
    w5 = windows.get("5") or {}
    w60 = windows.get("60") or {}
    fast5 = float(w5.get(side_str + "_usd") or 0.0)
    mid60 = float(w60.get(side_str + "_usd") or 0.0)
    if fast5 > 0:
        fast, mid, accel = fast5, mid60, (fast5 / (mid60 / 12.0)) if mid60 > 0 else 0.0
        return fast, mid, last_age, accel
    for fast_w, mid_w in (("15", "60"), ("60", "300")):
        w = windows.get(fast_w) or {}
        if w.get(side_str + "_usd", 0.0) > 0:
            fast = float(w.get(side_str + "_usd") or 0.0)
            mid = float((windows.get(mid_w) or {}).get(side_str + "_usd") or 0.0)
            return fast, mid, last_age, 0.0
    return 0.0, 0.0, last_age, 0.0


def flow_components(flow: dict, side: int, cfg, price_move: float = 0.0) -> list:
    """Микроструктура: ликвидации, OI, funding, premium.

    Возвращает компоненты с side относительно ВХОДА (+1 усиливает, -1 ослабляет).
    Никогда не блокирует вход — только корректирует strength.
    flow = raw["flow"] = {"liq": {...}, "oi": {...}, "premium": {...}, "fast": {...}}.
    price_move: изменение цены за ~2 минуты (положительное = вверх), согласуется с OI.
    """
    comps = []
    flow = flow or {}
    liq = flow.get("liq") or {}
    oi = flow.get("oi") or {}
    prem = flow.get("premium") or {}

    min_fast_usd = _f(cfg, "flow_liq_min_fast_usd", 20000.0)
    min_usd = _f(cfg, "flow_liq_min_usd", 50000.0)
    decay = _f(cfg, "flow_liq_decay_sec", 120.0)
    if min_fast_usd > 0:
        my_side = "short" if side == 1 else "long"   # каскад, давящий В СТОРОНУ входа
        opp_side = "long" if side == 1 else "short"  # контр-каскад против входа
        my_fast, my_mid, my_age, my_accel = _liq_push(liq, my_side)
        opp_fast, opp_mid, opp_age, opp_accel = _liq_push(liq, opp_side)
        if my_age is not None and decay > 0:
            fresh = max(0.0, 1.0 - my_age / decay)
            boost = 1.5 if my_accel >= 2.0 else 1.0  # лавина разгоняется — вес выше
            if my_fast >= min_fast_usd and fresh > 0.3:
                comps.append({"name": "Каскад ликвидаций " + ("шортов" if side == 1 else "лонгов"),
                              "side": 1, "weight": round(15 * fresh * boost, 1)})
            elif my_mid >= min_usd and fresh > 0.5:
                comps.append({"name": "Каскад ликвидаций " + ("шортов" if side == 1 else "лонгов"),
                              "side": 1, "weight": round(10 * fresh * boost, 1)})
        if opp_age is not None and decay > 0:
            fresh = max(0.0, 1.0 - opp_age / decay)
            boost = 1.5 if opp_accel >= 2.0 else 1.0
            if opp_fast >= min_fast_usd and fresh > 0.3:
                comps.append({"name": "Контр-каскад ликвидаций " + ("лонгов" if side == 1 else "шортов"),
                              "side": -1, "weight": round(10 * fresh * boost, 1)})
            elif opp_mid >= min_usd and fresh > 0.5:
                comps.append({"name": "Контр-каскад ликвидаций " + ("лонгов" if side == 1 else "шортов"),
                              "side": -1, "weight": round(7 * fresh * boost, 1)})

    min_change = _f(cfg, "flow_oi_min_change", 0.5)
    oi_max_age = _f(cfg, "flow_oi_max_age", 1800.0)
    if min_change > 0 and oi.get("change_pct") is not None:
        change = float(oi["change_pct"])
        age = float(oi.get("age_s") or 0.0)
        if abs(change) >= min_change and (oi_max_age <= 0 or age <= oi_max_age):
            # свежесть: чем старше замер, тем меньше вес (1.0 -> 0.2)
            freshness = max(0.2, 1.0 - age / oi_max_age) if oi_max_age > 0 else 1.0
            # согласие с ценой: рост OI усиливает, только если цена движется в ту же сторону
            aligned = (change > 0 and price_move >= 0) or (change < 0 and price_move <= 0)
            if change > 0 and aligned:
                comps.append({"name": "Рост OI — новые позиции по тренду", "side": 1,
                              "weight": round(10 * freshness, 1)})
            elif change > 0 and not aligned:
                comps.append({"name": "OI растёт против цены — ловушка", "side": -1,
                              "weight": round(9 * freshness, 1)})
            else:
                comps.append({"name": "OI падает — движение на закрытии", "side": -1,
                              "weight": round(8 * freshness, 1)})

    max_fund = _f(cfg, "flow_funding_max_abs", 0.0004)
    funding = _to_float(prem.get("funding"))
    if max_fund > 0 and funding != 0.0:
        if side == 1:
            if funding > max_fund:
                comps.append({"name": "Funding перегрет (все в лонгах)", "side": -1, "weight": 8})
            elif funding < -max_fund:
                comps.append({"name": "Funding отрицательный (шорты перегружены)", "side": 1, "weight": 5})
        else:
            if funding < -max_fund:
                comps.append({"name": "Funding перегрет (все в шортах)", "side": -1, "weight": 8})
            elif funding > max_fund:
                comps.append({"name": "Funding положительный (лонги перегружены)", "side": 1, "weight": 5})

    max_prem = _f(cfg, "flow_premium_max_pct", 0.3)
    prem_z = _f(cfg, "flow_premium_z", 2.0)
    premium = _to_float(prem.get("premium_pct"))
    mean = _to_float(prem.get("mean"))
    std = _to_float(prem.get("std"))
    n = int(prem.get("n") or 0)
    if premium != 0.0:
        # адаптивно: z-score по истории символа; пока истории мало — фикс-порог
        if n >= 10 and std > 1e-9 and prem_z > 0:
            z = (premium - mean) / std
            over = abs(z) >= prem_z
            penalty_w = 6 if abs(z) >= prem_z * 1.5 else 4
            boost_w = 3 if abs(z) >= prem_z * 1.5 else 2
        else:
            over = max_prem > 0 and abs(premium) >= max_prem
            penalty_w, boost_w = 6, 4
        # перегрев фьючерса в сторону входа -> штраф; в противоположную -> бонус
        if side == 1:
            if over and premium > 0:
                comps.append({"name": "Фьючерс дороже индекса (перегрев)", "side": -1, "weight": penalty_w})
            elif over and premium < 0:
                comps.append({"name": "Фьючерс дешевле индекса (в пользу лонга)", "side": 1, "weight": boost_w})
        else:
            if over and premium < 0:
                comps.append({"name": "Фьючерс дешевле индекса (перегрев)", "side": -1, "weight": penalty_w})
            elif over and premium > 0:
                comps.append({"name": "Фьючерс дороже индекса (в пользу шорта)", "side": 1, "weight": boost_w})
    return comps


def _impulse(depth: dict, tape: dict, cfg) -> tuple:
    """Старт импульса: скачок дисбаланса + скорость + путь свободен."""
    if not depth or not depth.get("ready") or not depth.get("prev"):
        return False, 0
    if not tape or not tape.get("ready"):
        return False, 0
    imb = float(depth.get("imbalance") or 0.0)
    vel = float(depth.get("imbalance_velocity") or 0.0)
    min_vel = _f(cfg, "ob_tape_min_velocity", 2.0)
    velocity = float(tape.get("velocity") or 0.0)
    side = 0
    if abs(imb) >= 0.3:
        side = _sign(imb)
    elif vel >= 0.015:
        side = 1
    elif vel <= -0.015:
        side = -1
    if side == 0 or velocity < min_vel:
        return False, 0
    bid_mass = float(depth.get("bid_wall_mass") or 0.0)
    ask_mass = float(depth.get("ask_wall_mass") or 0.0)
    clear = True
    if side == 1 and ask_mass > bid_mass * 1.5:
        clear = False
    if side == -1 and bid_mass > ask_mass * 1.5:
        clear = False
    return (clear and (abs(imb) >= 0.3 or abs(vel) >= 0.015)), side


def _micro_flow(fast: dict, max_age_s: float = 5.0):
    """Свежие микро-метрики FastFeed или None (нет данных/устарели)."""
    if not fast or not fast.get("subscribed"):
        return None
    age = fast.get("last_msg_age")
    if age is not None and age > max_age_s:
        return None
    return fast


def micro_trigger(fast: dict, side: int, cfg) -> dict:
    """Триггер начала импульса по быстрой ленте (свежесть критична).

    Для лонга (side=1): buy burst / buy_ratio_5s >= порога / ускорение CVD вверх.
    Возвращает {side, score(0..100), age_s, reasons, fresh}.
    Если данных нет — score=0, fresh=False (вход по триггеру невозможен).
    """
    fast = _micro_flow(fast)
    if not fast:
        return {"side": 0, "score": 0, "age_s": None, "reasons": [], "fresh": False}
    score = 0
    reasons = []
    side_str = "buy" if side == 1 else "sell"
    opp_str = "sell" if side == 1 else "buy"
    # burst в сторону входа — сильнейший признак: движение началось прямо сейчас
    burst_usd = float(fast.get("burst_usd_1s") or 0.0)
    if fast.get("burst_side") == side_str and burst_usd >= _f(cfg, "micro_trigger_burst_usd", 5000.0):
        score += 45
        reasons.append(f"burst {side_str} {burst_usd:.0f} USDT/с")
    elif fast.get("burst_side") == opp_str:
        score -= 30
        reasons.append(f"burst {opp_str} против")
    # доля покупок на коротком окне
    ratio = float(fast.get(f"buy_ratio_{_f(cfg, 'micro_trigger_ratio_win', 5.0):.0f}") or 0.5)
    ratio_th = _f(cfg, "micro_trigger_ratio", 0.58)
    if side == 1:
        if ratio >= ratio_th:
            score += int(40 * (ratio - 0.5) / 0.5)
            reasons.append(f"buy_ratio {ratio:.2f}")
        elif ratio <= 0.42:
            score -= 35
            reasons.append(f"buy_ratio {ratio:.2f} против")
    else:
        if ratio <= 1.0 - ratio_th:
            score += int(40 * (0.5 - ratio) / 0.5)
            reasons.append(f"buy_ratio {ratio:.2f}")
        elif ratio >= 0.58:
            score -= 35
            reasons.append(f"buy_ratio {ratio:.2f} против")
    # ускорение CVD в сторону входа
    accel = float(fast.get("cvd_accel") or 0.0)
    accel_th = _f(cfg, "micro_trigger_cvd_accel", 3000.0)
    if side == 1 and accel >= accel_th:
        score += 25
        reasons.append(f"CVD accel +{accel:.0f}")
    elif side == -1 and accel <= -accel_th:
        score += 25
        reasons.append(f"CVD accel {accel:.0f}")
    elif (side == 1 and accel <= -accel_th * 2) or (side == -1 and accel >= accel_th * 2):
        score -= 20
        reasons.append("CVD против")
    age_s = fast.get("last_trade_age")
    fresh = age_s is not None and age_s <= _f(cfg, "micro_trigger_max_age", 5.0)
    score = max(-100, min(100, score))
    return {"side": (1 if score > 0 else -1 if score < 0 else 0) if side in (1, -1) else 0,
            "score": score, "age_s": age_s, "reasons": reasons, "fresh": fresh}


def micro_gate(fast: dict, side: int, cfg) -> tuple:
    """Жёсткий side gate: быстрый поток против стороны вердикта -> блок входа.

    Лонг блокируется при: sell-burst >= порога, buy_ratio_3s < 0.40,
    cvd_3s <= -порога (агрессия продавцов прямо сейчас).
    Возвращает (blocked, reason). Без данных — не блокирует.
    """
    fast = _micro_flow(fast)
    if not fast:
        return False, ""
    ratio3 = float(fast.get("buy_ratio_3") or 0.5)
    cvd3 = float(fast.get("cvd_3") or 0.0)
    burst_usd = float(fast.get("burst_usd_1s") or 0.0)
    # тонкая лента: даже правильный по стороне поток — это шум на 2-3 сделках.
    # Вход по такой монете = спред и неисполнение; блокируем раньше side-проверок.
    vol5 = float(fast.get("vol_5") or 0.0)
    vol_th = _f(cfg, "micro_gate_min_vol_usd", 1000.0)
    if fast.get("trades_5") is not None and vol5 < vol_th:
        return True, f"лента слишком тонкая для входа (vol 5с {vol5:.0f} USDT < {vol_th:.0f})"
    if side == 1:
        if fast.get("burst_side") == "sell" and burst_usd >= _f(cfg, "micro_gate_burst_usd", 20000.0):
            return True, f"sell-burst {burst_usd:.0f} USDT/с против лонга"
        if ratio3 < _f(cfg, "micro_gate_ratio", 0.40):
            return True, f"лента против входа: buy_ratio 3с {ratio3:.2f} < {_f(cfg, 'micro_gate_ratio', 0.40):.2f}"
        if cvd3 <= -_f(cfg, "micro_gate_cvd_usd", 10000.0):
            return True, f"CVD 3с {cvd3:.0f} USDT против лонга"
    else:
        if fast.get("burst_side") == "buy" and burst_usd >= _f(cfg, "micro_gate_burst_usd", 20000.0):
            return True, f"buy-burst {burst_usd:.0f} USDT/с против шорта"
        if ratio3 > 1.0 - _f(cfg, "micro_gate_ratio", 0.40):
            return True, f"лента против входа: buy_ratio 3с {ratio3:.2f} > {1.0 - _f(cfg, 'micro_gate_ratio', 0.40):.2f}"
        if cvd3 >= _f(cfg, "micro_gate_cvd_usd", 10000.0):
            return True, f"CVD 3с +{cvd3:.0f} USDT против шорта"
    return False, ""


def btc_gate(btc: dict, side: int, cfg) -> tuple:
    """Глобальный фильтр: направление BTC против стороны вердикта -> блок.

    btc = flow["btc"] = fast.micro("BTC/USDT:USDT").
    Движение BTC за ~10с (mid_slope_pct_10s) >= btc_min_move_pct против
    стороны входа при живой ленте -> рынок разворачивается в целом.
    Без данных — не блокирует.
    """
    if not btc or not btc.get("subscribed"):
        return False, ""
    slope = btc.get("mid_slope_pct_10s")
    if slope is None:
        return False, ""
    age = btc.get("last_book_age")
    max_age = _f(cfg, "btc_max_age_s", 30.0)
    if age is not None and age > max_age:
        return False, ""
    min_move = _f(cfg, "btc_min_move_pct", 0.15)
    if side == 1 and slope <= -min_move:
        return True, f"BTC идёт вниз {slope:+.2f}% за 10с против лонга"
    if side == -1 and slope >= min_move:
        return True, f"BTC идёт вверх {slope:+.2f}% за 10с против шорта"
    return False, ""


def _recent_expansion(klines_1m, side, min_atr) -> tuple:
    """Разгон движения: свеча за последние 2 минуты (текущая или предыдущая) должна быть
    >= min_atr × ATR в сторону сигнала. Иначе рынок стоит — вход отложить.

    Возвращает (ok, range_atr). ok=True при отсутствии данных (не блокируем без свечей).
    """
    if not klines_1m or len(klines_1m) < 3 or min_atr <= 0:
        return True, None
    try:
        highs = [float(r[2]) for r in klines_1m]
        lows = [float(r[3]) for r in klines_1m]
        closes = [float(r[4]) for r in klines_1m]
    except (TypeError, ValueError, IndexError):
        return True, None
    atr = float(ind.last(ind.atr(highs, lows, closes, 14), default=0.0))
    if atr <= 1e-12:
        return True, None
    best = 0.0
    for r in klines_1m[-2:]:
        try:
            o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
        except (TypeError, ValueError, IndexError):
            continue
        if (c > o) if side == 1 else (o > c):
            best = max(best, (h - l) / atr)
    return best >= min_atr, round(best, 2)


def _run_progress(klines_1m, side) -> tuple:
    """Сколько ATR цена прошла от экстремума за последние 3 минуты."""
    if not klines_1m or len(klines_1m) < 15:
        return None, 0.0
    try:
        highs = [float(r[2]) for r in klines_1m]
        lows = [float(r[3]) for r in klines_1m]
        closes = [float(r[4]) for r in klines_1m]
    except (TypeError, ValueError, IndexError):
        return None, 0.0
    atr = float(ind.last(ind.atr(highs, lows, closes, 14), default=0.0))
    if atr <= 1e-12:
        return None, 0.0
    recent = klines_1m[-3:]
    last_close = float(closes[-1])
    if side == 1:
        run = last_close - min(float(r[3]) for r in recent)
    else:
        run = max(float(r[2]) for r in recent) - last_close
    return run / atr, atr


def _impulse_progress(klines_1m, side, atr) -> tuple:
    """Ход импульса (в ATR) и откат от экстремума за последние 10 свечей.

    Возвращает (ход_ATR, доля_отката). Доля отката = насколько цена ушла от
    экстремума хода в долях от всего хода: 0 = у экстремума (вход в пробой),
    0.5 = середина движения (ретест), 1+ = полный разворот хода.
    """
    if not klines_1m or len(klines_1m) < 12 or atr is None or atr <= 1e-12:
        return None, None
    try:
        window = klines_1m[-10:]
        highs = [float(r[2]) for r in window]
        lows = [float(r[3]) for r in window]
        close = float(window[-1][4])
    except (TypeError, ValueError, IndexError):
        return None, None
    span = max(highs) - min(lows)
    if span <= 1e-12:
        return 0.0, 0.0
    run = span / atr
    if side == 1:
        pull = (max(highs) - close) / span
    else:
        pull = (close - min(lows)) / span
    return run, pull


def direction_verdict(prob: dict, smc: dict, klines_1m: list, dom: dict, depth: dict,
                      tape: dict, cfg, orderbook: dict = None, flow: dict = None) -> dict:
    verdict = {
        "side": None, "strength": 0.0, "probability": 0, "impulse_start": False,
        "indicator_components": [], "book_components": [], "agree_count": 0,
        "blocked": False, "blocked_reason": "", "blocked_code": "",
    }
    ic = indicator_components(prob, smc, klines_1m)
    bc = book_components(depth, tape, dom)
    verdict["indicator_components"] = ic
    verdict["book_components"] = bc
    if not depth or not depth.get("ready"):
        verdict["blocked_reason"] = "стакан недоступен — вход только по фактам стакана"
        verdict["blocked_code"] = "depth_unavailable"
        verdict["blocked"] = True
        return verdict

    htf = int((prob or {}).get("htf_trend") or 0)
    non_htf = [c for c in ic if c["side"] != 0 and c["name"] != "HTF-контекст"]
    if htf and len(non_htf) >= 1 and len({c["side"] for c in non_htf}) == 1:
        htf_side = non_htf[0]["side"]
        if htf != htf_side:
            verdict["blocked_reason"] = (f"HTF-тренд {htf} против стороны входа {htf_side} "
                                         "(обязательный фильтр)")
            verdict["blocked_code"] = "htf"
            verdict["blocked"] = True
            return verdict

    non_neutral = [c for c in ic if c["side"] != 0]
    min_agree = int(_f(cfg, "indicator_min_agree", 3))
    if len(non_neutral) < min_agree:
        verdict["blocked_reason"] = f"показателей индикатора {len(non_neutral)} < {min_agree}"
        verdict["blocked_code"] = "min_agree"
        verdict["blocked"] = True
        return verdict
    sides = {c["side"] for c in non_neutral}
    if len(sides) != 1:
        # сторона по сумме весов; одиночный лёгкий компонент против (Premium/Discount,
        # RSI на откате) — ослабляет, но не блокирует; 2+ или тяжёлый против — блок
        best_side = max((-1, 1), key=lambda s: sum(c["weight"] for c in non_neutral if c["side"] == s))
        against = [c for c in non_neutral if c["side"] != best_side]
        against_w = sum(c["weight"] for c in against)
        if len(against) >= 2 or against_w >= 30:
            verdict["blocked_reason"] = (
                f"показатели индикатора противоречат: "
                + ", ".join(f"{c['name']}:{c['side']:+d}" for c in against))
            verdict["blocked_code"] = "indicator_conflict"
            verdict["blocked"] = True
            return verdict
        side = best_side
        verdict["minor_conflict"] = True
    else:
        side = sides.pop()
    verdict["indicator_side"] = side

    book_non = [c for c in bc if c["side"] != 0]
    book_against = [c for c in book_non if c["side"] != side]
    if len(book_against) >= 2 or sum(c["weight"] for c in book_against) >= 30:
        verdict["blocked_reason"] = (
            f"стакан/лента против показаний индикатора: "
            + ", ".join(f"{c['name']}:{c['side']:+d}" for c in book_against))
        verdict["blocked_code"] = "book_conflict"
        verdict["blocked"] = True
        return verdict
    if book_against:
        verdict["minor_book_conflict"] = True

    run_atr_frac, run_atr_val = _run_progress(klines_1m, side)
    retest_on = _f(cfg, "entry_retest_enabled", 0.0) > 0
    run_hi = _f(cfg, "entry_max_run_atr", 0.0)
    run_lo = _f(cfg, "entry_min_run_atr", 0.0)
    retest_ok = False
    if retest_on:
        imp_run, pull = _impulse_progress(klines_1m, side, run_atr_val)
        min_imp = _f(cfg, "retest_min_impulse_atr", 0.0)
        min_pull = _f(cfg, "retest_min_pullback", 0.0)
        max_pull = _f(cfg, "retest_max_pullback", 1.0)
        verdict["retest"] = {
            "enabled": True,
            "impulse_run_atr": float(imp_run) if imp_run is not None else None,
            "pullback": float(pull) if pull is not None else None,
        }
        require_sweep = _f(cfg, "retest_require_sweep", 0.0) > 0
        require_trigger = _f(cfg, "retest_require_trigger", 0.0) > 0
        sweep_ok = any(c["side"] == side and c["name"].startswith("Liquidity sweep") for c in ic)
        abs_side = _to_float((depth or {}).get("absorption_side"))
        tape_div = _to_float((tape or {}).get("delta_divergence"))
        ice_side = _to_float((depth or {}).get("iceberg_side"))
        verdict["retest"]["sweep_ok"] = bool(sweep_ok)
        verdict["retest"]["trigger_ok"] = bool(
            sweep_ok or abs_side == side or tape_div == side or ice_side == side)
        # ретест — бонусный режим: импульс достаточен и откат в зоне
        ok_imp = imp_run is not None and (min_imp <= 0 or imp_run >= min_imp)
        ok_pull = pull is not None and (min_pull <= 0 or pull >= min_pull) \
            and (max_pull <= 0 or pull <= max_pull)
        if ok_imp and ok_pull and (not require_sweep or sweep_ok) \
                and (not require_trigger or verdict["retest"]["trigger_ok"]):
            retest_ok = True
        else:
            missing = []
            if not ok_imp:
                missing.append("impulse_too_small")
            if not ok_pull:
                missing.append("retest_zone")
            if require_sweep and not sweep_ok:
                missing.append("sweep_reclaim")
            if require_trigger:
                if not sweep_ok:
                    missing.append("sweep_reclaim")
                if abs_side != side:
                    missing.append("absorption")
                if tape_div != side:
                    missing.append("delta_divergence")
                if ice_side != side:
                    missing.append("iceberg")
            verdict["retest"]["missing_triggers"] = missing
    if not retest_ok and run_atr_frac is not None:
        run_hi_eff, run_lo_eff = run_hi, run_lo
        adapt = _f(cfg, "run_atr_adaptive", 0.0)
        if adapt > 0 and run_atr_val:
            mid = _ob_mid(orderbook) or 0.0
            if mid > 0:
                atr_rel = run_atr_val / mid
                base = _f(cfg, "run_atr_rel_base", 0.001)
                if base > 0:
                    scale = 1.0 + (atr_rel / base - 1.0) * adapt
                    run_hi_eff = run_hi * scale
                    run_lo_eff = run_lo * scale
        if run_hi_eff > 0 and run_atr_frac >= run_hi_eff:
            verdict["blocked_reason"] = (
                f"вход в хвост движения: цена прошла {run_atr_frac:.2f} ATR от экстремума"
                f" за 3 минуты (лимит {run_hi_eff:.2f} ATR)")
            verdict["blocked_code"] = "run_progress"
            verdict["blocked"] = True
            return verdict
        if run_lo_eff > 0 and run_atr_frac < run_lo_eff:
            verdict["blocked_reason"] = (
                f"вход раньше начала движения: цена прошла {run_atr_frac:.2f} ATR от экстремума"
                f" (нужно ≥ {run_lo_eff:.2f} ATR)")
            verdict["blocked_code"] = "impulse_too_small"
            verdict["blocked"] = True
            return verdict

    # микро-триггер: начало импульса по быстрой ленте (свежесть критична).
    # flow["micro"] — полные микро-метрики из FastFeed (быстрее чем snapshot);
    # fallback на flow["fast"] для старых снапшотов/тестов.
    flow_fast = ((flow or {}).get("micro") or (flow or {}).get("fast") or {})
    trigger = micro_trigger(flow_fast, side, cfg)
    verdict["micro_trigger"] = trigger

    # движение должно идти прямо сейчас (и для пробоя, и для ретеста): одна из двух
    # последних свечей >= N ATR в сторону входа, иначе рынок стоит — вход отложить.
    # Свежий сильный микро-триггер = ранний вход: требование к свече ослабляется.
    exp_req = _f(cfg, "entry_min_bar_range_atr", 0.0)
    if trigger.get("fresh") and trigger.get("score", 0) > 0:
        exp_req = exp_req * max(0.4, 1.0 - min(trigger["score"], 100) / 100.0 * 0.6)
    exp_ok, exp_range = _recent_expansion(klines_1m, side, exp_req)
    if not exp_ok:
        verdict["blocked_reason"] = (
            f"движение не разгоняется: свечи за 2 мин {exp_range} ATR в сторону входа"
            f" (нужно ≥ {exp_req:g} ATR) — рынок стоит")
        verdict["blocked_code"] = "no_expansion"
        verdict["blocked"] = True
        return verdict

    if tape and tape.get("ready"):
        ratio = float(tape.get("buy_ratio") or 0.5)
        tape_req = _f(cfg, "entry_tape_min_ratio", 0.0)
        if tape_req > 0:
            ok = ratio >= tape_req if side == 1 else ratio <= 1.0 - tape_req
            if not ok:
                verdict["blocked_reason"] = (f"лента не подтверждает вход (buy_ratio {ratio:.2f}, нужно "
                                             f"{'≥' if side == 1 else '≤'} {tape_req:.2f})")
                verdict["blocked_code"] = "tape"
                verdict["blocked"] = True
                return verdict

    prob = prob or {}
    bull_prob = int(prob.get("bull_prob") or 50)
    bear_prob = int(prob.get("bear_prob") or 50)
    my_prob = bull_prob if side == 1 else bear_prob
    threshold = max(_f(cfg, "entry_probability_min", 60), 60.0)  # хардкод: не ниже 60%
    if my_prob < threshold:
        verdict["blocked_reason"] = f"вероятность индикатора {my_prob}% < {threshold}%"
        verdict["blocked_code"] = "probability"
        verdict["blocked"] = True
        return verdict

    # side gate: быстрый поток против стороны вердикта — жёсткий блок
    gate_blocked, gate_reason = micro_gate(flow_fast, side, cfg)
    if gate_blocked:
        verdict["blocked_reason"] = gate_reason
        verdict["blocked_code"] = "micro_against"
        verdict["blocked"] = True
        return verdict

    # глобальный фильтр: направление BTC против стороны вердикта
    btc_blocked, btc_reason = btc_gate((flow or {}).get("btc") or {}, side, cfg)
    if btc_blocked:
        verdict["blocked_reason"] = btc_reason
        verdict["blocked_code"] = "btc_against"
        verdict["blocked"] = True
        return verdict

    ahead_pct = _f(cfg, "entry_wall_ahead_pct", 0.0)
    ahead_mult = _f(cfg, "entry_wall_ahead_mult", 0.0)
    min_rr = _f(cfg, "entry_min_rr_to_wall", 0.0)
    scan_pct = ahead_pct
    r_pct = None
    if min_rr > 0 and run_atr_val:
        mid = _ob_mid(orderbook)
        if mid and mid > 0:
            r_pct = max(run_atr_val * _f(cfg, "entry_sl_atr_mult", 1.5),
                        mid * _f(cfg, "entry_sl_min_pct", 0.2) / 100.0) / mid * 100.0
            scan_pct = max(scan_pct, min_rr * r_pct * 1.2)
    if scan_pct > 0 and orderbook:
        la = liquidity_ahead(orderbook, side, max_pct=scan_pct / 100.0,
                             wall_mult=ahead_mult if ahead_mult > 0 else _f(cfg, "ob_wall_mult", 3.0))
        verdict["liquidity_ahead"] = {
            "levels_ahead": la.get("levels_ahead"),
            "wall_dist_pct": la.get("wall_dist_pct"),
            "air_pocket_levels": la.get("air_pocket_levels"),
            "ahead_density": la.get("ahead_density"),
        }
        if la.get("ready") and la.get("wall_ahead"):
            wall = la["wall_ahead"]
            dist = la["wall_dist_pct"]
            if ahead_pct > 0 and dist <= ahead_pct:
                verdict["blocked_reason"] = (
                    f"впереди стена {wall['size']:.2g} на {dist:.2f}% вверх "
                    f"(в пределах {ahead_pct:.2f}%) — вероятен отскок, не начало движения")
                verdict["blocked_code"] = "wall_ahead"
                verdict["blocked"] = True
                return verdict
            if min_rr > 0 and r_pct and dist < min_rr * r_pct:
                verdict["blocked_reason"] = (
                    f"стена впереди на {dist:.2f}% ближе {min_rr:g}×R ({min_rr * r_pct:.2f}%) — "
                    f"цель недостижима, R:R не проходит")
                verdict["blocked_code"] = "rr_to_wall"
                verdict["blocked"] = True
                return verdict

    block_abs = _f(cfg, "book_block_abs", 0.2)
    imb = _to_float((depth or {}).get("imbalance"))
    dom_p = _to_float((dom or {}).get("dom_pressure"))
    if (side == 1 and (imb <= -block_abs or dom_p <= -block_abs)) or (
            side == -1 and (imb >= block_abs or dom_p >= block_abs)):
        verdict["blocked_reason"] = "стакан сильно против направления"
        verdict["blocked_code"] = "book_abs"
        verdict["blocked"] = True
        return verdict

    impulse, impulse_side = _impulse(depth, tape, cfg)
    impulse_ok = bool(getattr(cfg, "impulse_enabled", True))
    verdict["impulse_start"] = bool(impulse_ok and impulse and impulse_side == side)

    strength = float(sum(c["weight"] for c in non_neutral))
    strength += float(sum(c["weight"] for c in book_non))
    strength += abs(bull_prob - bear_prob) / 100.0 * 20.0
    # изменение цены за ~2 минуты: OI растёт только в ту же сторону
    price_move = 0.0
    closes = [float(k[4]) for k in klines_1m if k and len(k) > 4]
    if len(closes) >= 3:
        price_move = closes[-1] - closes[-3]
    fc = flow_components(flow, side, cfg, price_move=price_move)
    verdict["flow_components"] = fc
    strength += sum(float(c["weight"]) * int(c["side"]) for c in fc)
    if verdict["impulse_start"]:
        strength += 15
    if trigger.get("fresh") and trigger.get("score", 0) > 0:
        strength += min(trigger["score"], 100) / 100.0 * 25
    verdict["strength"] = round(strength, 2)
    verdict["probability"] = my_prob
    verdict["agree_count"] = len(non_neutral)
    verdict["side"] = "long" if side == 1 else "short"
    return verdict


def exit_reason(pos, raw: dict, depth: dict, tape: dict, cfg, r_multiple: float = 0.0) -> str:
    """Правила выхода. Возвращает причину или None (держать)."""
    if time.time() - _to_float(pos.opened_at) < _f(cfg, "exit_grace_seconds", 90):
        return None
    prob = (raw or {}).get("indicator1_probability") or {}
    if not prob.get("ready"):
        return None
    is_long = pos.side == "long"
    my = int(prob.get("bull_prob") or 50) if is_long else int(prob.get("bear_prob") or 50)
    reversal = _f(cfg, "exit_reversal_prob", 45)

    feats = pos.features or {}
    entry_strength = _to_float(feats.get("entry_strength"))
    verdict = (raw or {}).get("verdict") or {}
    current_strength = _to_float(verdict.get("strength"))
    hold = _f(cfg, "exit_hold_prob", 55)
    weaken_ratio = _f(cfg, "exit_weaken_ratio", 0.5)
    weakened = entry_strength > 0 and current_strength < entry_strength * weaken_ratio

    flip_abs = _f(cfg, "exit_book_flip_abs", 0.2)
    imb = _to_float((depth or {}).get("imbalance"))
    ratio = _to_float((tape or {}).get("buy_ratio"), 0.5)
    flipped = ((is_long and imb <= -flip_abs and ratio <= 0.42)
               or (not is_long and imb >= flip_abs and ratio >= 0.58))

    entry_wall_mass = _to_float(feats.get("entry_wall_mass"))
    cur_wall_mass = _to_float(
        (depth or {}).get("bid_wall_mass" if is_long else "ask_wall_mass"))
    wall_lost = entry_wall_mass > 0 and cur_wall_mass < entry_wall_mass * 0.5

    protect_r = _f(cfg, "exit_protect_r", 0.0)
    protected = protect_r > 0 and r_multiple >= protect_r
    # стакан-выход только при явном развороте: стакан против И индикатор уже не за позицию
    # И (ослабление ИЛИ стена слазает) — иначе даём сделке дорасти до стопа/тейка
    if flipped and my < hold and (weakened or wall_lost):
        return "стакан потерял поддержку (дисбаланс/лента против, стена входа слазает)"
    if protected:
        # победа защищена: индикаторные выходы отключаем, держим до трейлинга
        return None
    if my < reversal:
        return f"разворот индикатора (вероятность {my}% < {reversal}%)"
    if weakened and my < hold:
        return f"индикатор ослаб (сила {current_strength:.0f} против {entry_strength:.0f} на входе)"
    return None


def analyze_facts(bundle, depth, tape, cfg) -> dict:
    """Вычисляет вердикт и кладёт стакан/ленту/вердикт в bundle.raw."""
    raw = getattr(bundle, "raw", None) or {}
    prob = raw.get("indicator1_probability") or {}
    smc = raw.get("smc") or {}
    dom = raw.get("order_book") or {}
    klines = raw.get("indicator_klines") or []
    orderbook = raw.get("orderbook")
    flow = raw.get("flow")
    verdict = direction_verdict(prob, smc, klines, dom, depth, tape, cfg,
                                orderbook=orderbook, flow=flow)
    raw["depth"] = depth
    raw["tape"] = tape
    raw["verdict"] = verdict
    return verdict