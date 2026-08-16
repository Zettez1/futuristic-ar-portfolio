"""Тесты единственного индикатора: пересечение MA(7)/MA(25) на 30m -> 3m."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config
from core.models import Position
from main import build_reversal_signal
from strategies.ma_cross import MaCrossStrategy

PASSED = 0
FAILED = []


def check(name, cond, detail=""):
    global PASSED
    if cond:
        PASSED += 1
        print(f"  ok: {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL: {name} {detail}")


def series(tf_minutes, ts0, n, last_close=None, price=100.0):
    """n закрытых свечей; при last_close не None последняя свеча закрывается с ним."""
    step = tf_minutes * 60000
    rows = []
    ts = ts0
    closes = [price] * n
    if last_close is not None:
        closes[-1] = last_close
    for c in closes:
        rows.append([ts, c - 0.01, c + 0.02, c - 0.03, c, 1000.0])
        ts += step
    return rows


def rows_3m(last_open_before, last_close=None, n=30, price=100.0):
    step = 3 * 60000
    ts0 = last_open_before - n * step
    return series(3, ts0, n, last_close=last_close, price=price)


def test_cross_detection():
    step30 = 30 * 60000
    ts0 = int(time.time() * 1000) - 300 * step30

    res = MaCrossStrategy(Config())._cross(series(30, ts0, 40), 30)
    check("нет пересечения на флэте", res["cross"] == 0, str(res))

    res = MaCrossStrategy(Config())._cross(series(30, ts0, 35, last_close=100.001), 30)
    check("пересечение ВВЕРХ на последней закрытой", res["cross"] == 1, str(res))

    res = MaCrossStrategy(Config())._cross(series(30, ts0, 35, last_close=99.999), 30)
    check("пересечение ВНИЗ на последней закрытой", res["cross"] == -1, str(res))

    res = MaCrossStrategy(Config())._cross(series(30, ts0, 35, last_close=100.001), 30)
    check("atr(14) > 0", res["atr"] > 0, f"atr={res['atr']}")

    res = MaCrossStrategy(Config())._cross(series(30, ts0, 35, last_close=100.001), 30)
    check("trend=1 при fast>slow", res.get("trend") == 1, str(res))

    res = MaCrossStrategy(Config())._cross(series(30, ts0, 35, last_close=99.999), 30)
    check("trend=-1 при fast<slow", res.get("trend") == -1, str(res))


def test_strategy_flow():
    cfg = Config()
    strat = MaCrossStrategy(cfg)
    sym = "TEST/USDT:USDT"

    step30 = 30 * 60000
    ts0 = int(time.time() * 1000) - 300 * step30
    rows30 = series(30, ts0, 35, last_close=100.001)
    bar30_open = rows30[-1][0]

    rows3_flat = rows_3m(bar30_open + 4 * 3 * 60000, last_close=None)
    rows3_bear = rows_3m(bar30_open + 4 * 3 * 60000, last_close=99.999)
    rows3_bull = rows_3m(bar30_open + 4 * 3 * 60000, last_close=100.001)

    s = strat.evaluate(sym, rows30, rows3_flat, price=102.0)
    check("30m пересечение -> вооружение, входа нет", s is None)
    diag = strat.diagnose(sym)
    check("вооружён: dir30 = 1", diag.get("dir30") == 1, str(diag))
    check("вооружён: armed = 1", diag.get("armed") == 1, str(diag))

    s = strat.evaluate(sym, rows30, rows3_flat, price=102.5)
    diag = strat.diagnose(sym)
    check("рукав активен на повторном скане (ждём 3m)", s is None and diag.get("armed") == 1, str(diag))

    s = strat.evaluate(sym, rows30, rows3_bear, price=102.0)
    check("3m пересечение в ПРОТИВОПОЛОЖНУЮ сторону -> нет входа + рукав снят", s is None and not strat.diagnose(sym).get("armed"),
          str(s))

    rows30_rearm = series(30, ts0, 35, last_close=100.001)
    s = strat.evaluate(sym, rows30_rearm, rows3_flat, price=105.0)
    check("после снятия противоположным 3m -> новое 30m пересечение -> перевооружение", s is None and strat.diagnose(sym).get("armed") == 1, str(s))
    s = strat.evaluate(sym, rows30_rearm, rows3_bull, price=105.0)
    check("после перевооружения 3m в ту же сторону -> вход", s is not None, str(s))
    if s:
        check("вход long", s.side == "long", s.side)
        check("SL ниже входа", s.stop_loss < s.entry, f"sl={s.stop_loss} entry={s.entry}")
        check("TP отключён", s.take_profit is None)
        check("стратегия ma_cross", s.strategy == "ma_cross")
        check("в фичах периоды MA", s.features.get("ma_cross_fast") == 7 and s.features.get("ma_cross_slow") == 25)

    s2 = strat.evaluate(sym, rows30, rows3_bull, price=106.0)
    check("та же 3m-свеча после входа -> нет второго входа", s2 is None)

    strat.confirm_entry(sym)
    rows3_bull2 = rows_3m(bar30_open + 20 * 3 * 60000, last_close=100.001)
    s3 = strat.evaluate(sym, rows30, rows3_bull2, price=107.0)
    check("после входа тот же 30m рукав -> входа нет", s3 is None)

    rows30_no_cross = series(30, ts0 + step30, 35)
    s4 = strat.evaluate(sym, rows30_no_cross, rows3_bull2, price=107.0)
    check("новая 30m-свеча без пересечения -> рукав снят", s4 is None and not strat.diagnose(sym).get("armed"),
          str(s4))

    rows30_bull2 = series(30, ts0 + step30, 35, last_close=100.001)
    s5 = strat.evaluate(sym, rows30_bull2, rows3_flat, price=108.0)
    check("новое 30m пересечение -> перевооружение", s5 is None and strat.diagnose(sym).get("armed") == 1, str(s5))

    rows3_early = rows_3m(bar30_open - 12 * 3 * 60000, last_close=100.001)
    rows30_c = series(30, ts0, 35, last_close=100.001)
    s6 = strat.evaluate(sym, rows30_c, rows3_early, price=100.0)
    check("3m пересечение ДО 30m -> нет входа", s6 is None)

    rows30_continue = []
    ts_cont = ts0 + step30
    for i in range(36):
        price = 100.1 if i >= 15 else 100.0
        rows30_continue.append([ts_cont, price - 0.01, price + 0.02, price - 0.03, price, 1000.0])
        ts_cont += 30 * 60000
    s7 = strat.evaluate(sym, rows30_continue, rows3_flat, price=102.0)
    check("новая 30m-свеча без пересечения, но тренд продолжается -> рукав остаётся",
          s7 is None and strat.diagnose(sym).get("armed") == 1, str(s7))

    rows30_reverse = []
    ts_rev = ts0 + step30
    for i in range(37):
        price = 100.0 if i >= 15 else 100.1
        rows30_reverse.append([ts_rev, price - 0.01, price + 0.02, price - 0.03, price, 1000.0])
        ts_rev += 30 * 60000
    s8 = strat.evaluate(sym, rows30_reverse, rows3_flat, price=102.0)
    check("новая 30m-свеча без пересечения и разворот тренда -> рукав снят",
          s8 is None and not strat.diagnose(sym).get("armed"), str(s8))

    rows30_old = []
    ts_old = ts0 + 7 * step30
    for i in range(40):
        price = 100.001 if i < 20 else 100.0
        rows30_old.append([ts_old, price - 0.01, price + 0.02, price - 0.03, price, 1000.0])
        ts_old += 30 * 60000
    s9 = strat.evaluate(sym, rows30_old, rows3_flat, price=102.0)
    check("пересечение старше 5 баров -> рукав снят",
          s9 is None and not strat.diagnose(sym).get("armed"), str(s9))


def test_first_3m_cross_passed():
    cfg = Config()
    strat = MaCrossStrategy(cfg)
    sym = "TEST2/USDT:USDT"

    step30 = 30 * 60000
    ts0 = int(time.time() * 1000) - 300 * step30
    rows30 = series(30, ts0, 35, last_close=100.001)
    bar30_open = rows30[-1][0]
    step = 3 * 60000

    def series3(start_ts, closes):
        rows = []
        ts = start_ts
        for c in closes:
            rows.append([ts, c - 0.01, c + 0.02, c - 0.03, c, 1000.0])
            ts += step
        return rows

    s = strat.evaluate(sym, rows30, series3(bar30_open, [100.0] * 20), price=102.0)
    check("30m up -> вооружение", s is None and strat.diagnose(sym).get("armed") == 1, str(s))

    # 3m-перекрёст вверх случился НА БОЛЕЕ РАННЕЙ свече, последние свечи уже выше
    rows3_late = series3(bar30_open, [100.0] * 25 + [101.0] * 15)
    s = strat.evaluate(sym, rows30, rows3_late, price=102.0)
    diag = strat.diagnose(sym)
    check("первый 3m-перекрёст УЖЕ прошёл -> нет входа", s is None, str(s))
    check("монета пропущена (skip_first_3m_passed)", bool(diag.get("skip_first_3m_passed")), str(diag))
    check("рукав снят после пропуска", not diag.get("armed"), str(diag))

    # перекрёст РОВНО на последней закрытой 3m-свече, но 30m-перекрёст тот же (пропущенный) -> входа нет
    rows3_now = series3(bar30_open, [100.0] * 29 + [100.001])
    s = strat.evaluate(sym, rows30, rows3_now, price=102.0)
    diag = strat.diagnose(sym)
    check("тот же 30m-перекрёст после пропуска -> не вооружается заново",
          s is None and not diag.get("armed"), str(diag))

    # НОВЫЙ 30m-перекрёст (другой бар) -> перевооружение
    rows30_new = series(30, ts0 + step30, 35, last_close=100.001)
    s = strat.evaluate(sym, rows30_new, series3(bar30_open, [100.0] * 20), price=102.0)
    diag = strat.diagnose(sym)
    check("перевооружение по новому 30m-перекрёсту", s is None and diag.get("armed") == 1, str(diag))
    s = strat.evaluate(sym, rows30_new, rows3_now, price=102.0)
    check("перекрёст на текущей последней 3m-свече -> вход", s is not None, str(s))


def closed_position(side, entry=100.0, sl=99.5, cross_dir=-1, reason="Стоп-лосс",
                    stop_loss=None):
    """Закрытая позиция с фичами, как их заполняет бот при мониторинге."""
    pos = Position(id="p1", symbol="TEST/USDT:USDT", side=side, entry=entry, qty=10.0,
                   stop_loss=sl if stop_loss is None else stop_loss, take_profit=None,
                   strategy="ma_cross", opened_at=time.time(), status="closed",
                   features={"initial_stop_loss": sl, "initial_risk": abs(entry - sl),
                             "ltf_cross_dir": cross_dir}, realized_pnl=-5.0)
    pos.reason = reason
    return pos


def test_ltf_cross_since():
    strat = MaCrossStrategy(Config())
    step = 3 * 60000
    ts0 = int(time.time() * 1000) - 100 * step

    rows = series(3, ts0, 40, last_close=99.999)  # пересечение ВНИЗ на последней закрытой
    check("ltf_cross_since: пересечение вниз после входа", strat.ltf_cross_since(rows, ts0) == -1,
          str(strat.ltf_cross_since(rows, ts0)))

    rows_up = series(3, ts0, 40, last_close=100.001)  # пересечение ВВЕРХ
    check("ltf_cross_since: пересечение вверх", strat.ltf_cross_since(rows_up, ts0) == 1,
          str(strat.ltf_cross_since(rows_up, ts0)))

    rows_flat = series(3, ts0, 40)
    check("ltf_cross_since: без пересечения -> 0", strat.ltf_cross_since(rows_flat, ts0) == 0,
          str(strat.ltf_cross_since(rows_flat, ts0)))

    last_open = rows[-1][0]
    check("ltf_cross_since: пересечение раньше входа -> 0",
          strat.ltf_cross_since(rows, last_open + step) == 0,
          str(strat.ltf_cross_since(rows, last_open + step)))

    # перекрёст был 5 свечей назад, последние свечи без перекрёста — всё равно находим
    ts_mid = ts0 + 35 * step
    check("ltf_cross_since: старый перекрёст в окне позиции учитывается",
          strat.ltf_cross_since(rows, ts_mid) == -1, str(strat.ltf_cross_since(rows, ts_mid)))


def test_build_reversal_signal():
    sig = build_reversal_signal(closed_position("long"), 99.5)
    check("разворот: лонг выбит SL + 3m вниз -> шорт", sig is not None and sig.side == "short", str(sig))
    if sig:
        check("разворот: SL шорта выше входа", sig.stop_loss > sig.entry,
              f"sl={sig.stop_loss} entry={sig.entry}")
        check("разворот: дистанция та же, что у закрытой позиции",
              abs((sig.stop_loss - sig.entry) - 0.5) < 1e-9, str(sig.stop_loss - sig.entry))
        check("разворот: TP отключён", sig.take_profit is None)
        check("разворот: помечен reversal_of", sig.features.get("reversal_of") == "p1")

    sig = build_reversal_signal(closed_position("short", cross_dir=1), 100.5)
    check("разворот: шорт выбит SL + 3m вверх -> лонг", sig is not None and sig.side == "long", str(sig))
    if sig:
        check("разворот: SL лонга ниже входа", sig.stop_loss < sig.entry,
              f"sl={sig.stop_loss} entry={sig.entry}")

    sig = build_reversal_signal(closed_position("long", cross_dir=1), 99.5)
    check("разворот: 3m в ту же сторону -> нет", sig is None)

    sig = build_reversal_signal(closed_position("long", cross_dir=0), 99.5)
    check("разворот: без 3m-перекрёста -> нет", sig is None)

    sig = build_reversal_signal(closed_position("long"), 0.0)
    check("разворот: без цены -> нет", sig is None)

    sig = build_reversal_signal(closed_position("long", reason="Тейк-профит"), 99.5)
    check("разворот: не по стоп-лоссу -> нет", sig is None)

    sig = build_reversal_signal(closed_position("long", stop_loss=100.001), 99.5)
    check("разворот: стоп уже подтянут трейлингом -> нет", sig is None)

    sig = build_reversal_signal(closed_position("long", reason="Стоп-лосс Binance"), 99.5)
    check("разворот: внешний стоп-лосс Binance тоже разворачивает",
          sig is not None and sig.side == "short", str(sig))

    sig = build_reversal_signal(closed_position("long", sl=0.0), 99.5)
    check("разворот: нет начального стопа -> нет", sig is None)


def test_entry_filters():
    cfg = Config()
    cfg.ma_cross_filters_enabled = True
    strat = MaCrossStrategy(cfg)
    step = 3 * 60000
    ts0 = int(time.time() * 1000) - 200 * step

    rows = series(3, ts0, 60)
    ok, why = strat._entry_filters(rows, 100.0, "long", 0.005)
    check("фильтры: чистый вход проходит", ok, why)

    rows2 = series(3, ts0, 60)
    rows2[-40][4] = 95.0
    ok, why = strat._entry_filters(rows2, 100.0, "long", 0.005)
    check("фильтры: анти-чейз 2h (long) блокирует", not ok and "2h" in why, why)

    rows2b = series(3, ts0, 60)
    rows2b[-40][4] = 106.5
    ok, why = strat._entry_filters(rows2b, 100.0, "short", 0.005)
    check("фильтры: анти-чейз 2h (short) блокирует", not ok and "2h" in why, why)

    ok, why = strat._entry_filters(rows, 100.0, "long", 2.0)
    check("фильтры: ATR(3m) 2% > 1.2% блокирует", not ok and "ATR" in why, why)

    rows3 = series(3, ts0, 60)
    rows3[-1][5] = 30000.0
    ok, why = strat._entry_filters(rows3, 100.0, "long", 0.005)
    check("фильтры: всплеск объёма блокирует", not ok and "объём" in why, why)

    rows4 = []
    ts = ts0
    for i in range(120):
        rows4.append([ts, 99.7, 100.1, 99.4, 100.0, 1000.0])
        ts += step
    ok, why = strat._entry_filters(rows4, 100.0, "long", 0.005)
    check("фильтры: лонг у вершины диапазона блокирует", not ok and "вершины" in why, why)

    rows5 = []
    ts5 = int(time.time() * 1000) - 600 * step
    for i in range(500):
        c = 130.0 if i == 20 else 100.0
        rows5.append([ts5, c - 0.01, c + 0.02, c - 0.03, c, 1000.0])
        ts5 += step
    ok, why = strat._entry_filters(rows5, 100.0, "long", 0.005)
    check("фильтры: падающий нож 24h -23% блокирует long", not ok and "нож" in why, why)

    rows5b = series(3, int(time.time() * 1000) - 560 * step, 500)
    rows5b[-480][4] = 80.0
    ok, why = strat._entry_filters(rows5b, 100.0, "short", 0.005)
    check("фильтры: шорт перегретой монеты (24h +25%) блокирует", not ok and "перегрет" in why, why)


def test_entry_filters_evaluate():
    cfg = Config()
    cfg.ma_cross_filters_enabled = True
    step30 = 30 * 60000
    ts0 = int(time.time() * 1000) - 120 * step30
    rows30 = series(30, ts0, 35, last_close=100.001)
    bar30_open = rows30[-1][0]
    step = 3 * 60000
    ts_start = bar30_open - 60 * step

    def rows3_case(base, last_close):
        rows = []
        ts = bar30_open
        for i in range(60):
            rows.append([ts, base - 0.01, base + 0.02, base - 0.03, base, 1000.0])
            ts += step
        rows[-1][4] = last_close
        return rows

    strat = MaCrossStrategy(cfg)
    strat.evaluate("T1/USDT:USDT", rows30, rows3_case(95.0, 100.2), price=100.2)
    s = strat.evaluate("T1/USDT:USDT", rows30, rows3_case(95.0, 100.2), price=100.2)
    diag = strat.diagnose("T1/USDT:USDT")
    check("evaluate: чейз 2h блокирует вход (filter_reject)",
          s is None and "2h" in str(diag.get("filter_reject")), str(diag))

    strat2 = MaCrossStrategy(cfg)
    strat2.evaluate("T2/USDT:USDT", rows30, rows3_case(100.0, 100.001), price=100.0)
    s2 = strat2.evaluate("T2/USDT:USDT", rows30, rows3_case(100.0, 100.001), price=100.0)
    check("evaluate: чистый вход проходит фильтры", s2 is not None, str(s2))
    if s2:
        check("evaluate: чистый вход long", s2.side == "long", s2.side)


def near_cross_rows(move_per_bar, jump_at=45, n=60, jump_bars=0):
    """Свечи: флэт 100.0, потом с jump_at медленный дрейф; при jump_bars>0 — резкий скачок в конце."""
    step = 3 * 60000
    ts = int(time.time() * 1000) - (n + 10) * step
    closes = [100.0] * n
    for i in range(jump_at, n):
        closes[i] = closes[i - 1] + move_per_bar
    if jump_bars > 0:
        for i in range(n - jump_bars, n):
            closes[i] = closes[i - 1] + move_per_bar * 200
    rows = []
    for c in closes:
        rows.append([ts, c - 0.01, c + 0.02, c - 0.03, c, 1000.0])
        ts += step
    return rows


def test_build_reversal_near_cross():
    strat = MaCrossStrategy(Config())
    rows_up = near_cross_rows(0.004)          # EMAs почти коснулись, slow дрейф вверх
    sig = build_reversal_signal(closed_position("long", cross_dir=0), 99.9,
                                rows_ltf=rows_up, strategy=strat)
    check("разворот: EMAs почти пересеклись + цена ниже fast (long) -> short",
          sig is not None and sig.side == "short", str(sig))
    if sig:
        check("разворот: причина ближнего перекрёста", "почти перекрестились" in str(sig.reason),
              str(sig.reason))

    rows_dn = near_cross_rows(-0.004)         # медленный дрейф вниз, EMAs почти коснулись
    sig = build_reversal_signal(closed_position("short", cross_dir=0), 100.2,
                                rows_ltf=rows_dn, strategy=strat)
    check("разворот: EMAs почти пересеклись + цена выше fast (short) -> long",
          sig is not None and sig.side == "long", str(sig))

    sig = build_reversal_signal(closed_position("long", cross_dir=0), 100.1,
                                rows_ltf=rows_up, strategy=strat)
    check("разворот: EMAs близко, но цена НЕ против -> нет (long)", sig is None, str(sig))

    rows_jump = near_cross_rows(0.004, jump_bars=5)   # скачок вверх -> RMAs далеко
    sig = build_reversal_signal(closed_position("long", cross_dir=0), 99.9,
                                rows_ltf=rows_jump, strategy=strat)
    check("разворот: EMAs далеко друг от друга -> нет", sig is None, str(sig))

    sig = build_reversal_signal(closed_position("long", cross_dir=0), 99.9,
                                rows_ltf=[], strategy=strat)
    check("разворот: нет 3m-свечей -> нет", sig is None, str(sig))

    sig = build_reversal_signal(closed_position("long", cross_dir=0), 99.9,
                                rows_ltf=rows_up, strategy=None)
    check("разворот: без стратегии -> нет", sig is None, str(sig))


def test_all():
    print("== ma_cross (индикатор) ==")
    test_cross_detection()
    print("== ma_cross (стратегия) ==")
    test_strategy_flow()
    print("== ma_cross (первый 3m-перекрёст) ==")
    test_first_3m_cross_passed()
    print("== ma_cross (3m-перекрёст во время позиции) ==")
    test_ltf_cross_since()
    print("== ma_cross (разворот после стоп-лосса) ==")
    test_build_reversal_signal()
    print("== ma_cross (разворот: почти пересекшиеся EMAs) ==")
    test_build_reversal_near_cross()
    print("== ma_cross (фильтры входа) ==")
    test_entry_filters()
    test_entry_filters_evaluate()
    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:", FAILED)
        sys.exit(1)


if __name__ == "__main__":
    test_all()