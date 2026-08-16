"""Тесты факт-движка: стакан L2, лента, комитет фактов, выход по стакану."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config
from features.depth_oracle import DepthOracle, liquidity_ahead, orderbook_facts, tape_facts
from features.direction import direction_verdict, exit_reason

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


def book(bid_qty=100.0, ask_qty=100.0, levels=5):
    bids = [[100.0 - i * 0.01, bid_qty] for i in range(levels)]
    asks = [[100.01 + i * 0.01, ask_qty] for i in range(levels)]
    return {"bids": bids, "asks": asks}


def wall_book():
    bids = [[100.00, 500.0], [99.99, 20.0], [99.98, 15.0], [99.97, 10.0], [99.96, 8.0]]
    asks = [[100.01, 20.0], [100.02, 15.0], [100.03, 10.0], [100.04, 8.0], [100.05, 6.0]]
    return {"bids": bids, "asks": asks}


def make_trades(side="buy", n=30, amount=1.0, ago_s=10.0):
    now = time.time() * 1000.0
    return [{"price": 100.0, "amount": amount, "side": side, "ts": now - (n - i) * 500.0}
            for i in range(n)]


def test_orderbook_facts():
    f = orderbook_facts(book())
    check("ob: ready", f["ready"])
    check("ob: баланс дисбаланс ~0", abs(f["imbalance"]) < 0.05, f"imb={f['imbalance']}")
    f2 = orderbook_facts(wall_book())
    check("ob: bid-стена найдена", len(f2["bid_walls"]) == 1 and f2["bid_walls"][0]["price"] == 100.0, str(f2["bid_walls"]))
    check("ob: дисбаланс бычий", f2["imbalance"] > 0, f"imb={f2['imbalance']}")
    check("ob: спред", abs(f2["spread"] - 0.01) < 1e-9, f"spread={f2['spread']}")
    check("ob: маршрут непуст", len(f2["route"]) > 0, str(f2["route"])[:120])
    check("ob: стена в маршруте", any(r["wall"] for r in f2["route"]))
    check("ob: пустой стакан", orderbook_facts({})["ready"] is False)


def test_tape_facts():
    t = tape_facts(make_trades(side="buy"))
    check("tape: ready", t["ready"])
    check("tape: все покупки", t["buy_ratio"] > 0.99, f"ratio={t['buy_ratio']}")
    t2 = tape_facts(make_trades(side="sell"))
    check("tape: все продажи", t2["buy_ratio"] < 0.01, f"ratio={t2['buy_ratio']}")
    check("tape: скорость > 0", t["velocity"] > 0, f"v={t['velocity']}")
    check("tape: пустая лента", tape_facts([])["ready"] is False)


def test_icebergs():
    oracle = DepthOracle(min_seen=3)
    heavy = [[100.0, 500.0], [99.99, 5.0], [99.98, 4.0], [99.97, 3.0], [99.96, 3.0]]
    light = [[100.0, 5.0], [99.99, 5.0], [99.98, 4.0], [99.97, 3.0], [99.96, 3.0]]
    # цена проходит сквозь большой bid-стакан 100.0 (best_ask опускается ниже),
    # но уровень продолжает стоять -> айсберг-кандидат
    for i in range(3):
        f = oracle.update("BTC/USDT", {"bids": heavy, "asks": [[100.05, 5.0], [100.06, 4.0], [100.07, 4.0], [100.08, 3.0], [100.09, 3.0]]})
    f = oracle.update("BTC/USDT", {"bids": heavy, "asks": [[99.5, 5.0], [99.6, 4.0], [99.7, 4.0], [99.8, 3.0], [99.9, 3.0]]})
    check("iceberg: стакан 100.0 пережил проход цены", 100.0 in f["iceberg_bids"], f"ice={f['iceberg_bids']}")
    oracle2 = DepthOracle(min_seen=3)
    oracle2.update("BTC/USDT", {"bids": light, "asks": [[100.05, 5.0]]})
    f2 = oracle2.update("BTC/USDT", {"bids": light, "asks": [[99.5, 5.0]]})
    check("iceberg: мелкие уровни не айсберги", f2["iceberg_bids"] == [], f"ice={f2['iceberg_bids']}")


def _cfg():
    cfg = Config()
    cfg.indicator_min_agree = 3
    cfg.entry_probability_min = 60
    cfg.book_block_abs = 0.2
    cfg.impulse_enabled = True
    cfg.impulse_only = False
    cfg.ob_tape_min_velocity = 0.1
    cfg.exit_reversal_prob = 45
    cfg.exit_protect_r = 0.0
    cfg.exit_hold_prob = 55
    cfg.exit_weaken_ratio = 0.5
    cfg.exit_book_flip_abs = 0.2
    cfg.exit_grace_seconds = 0
    cfg.entry_tape_min_ratio = 0.0
    cfg.entry_max_run_atr = 0.0
    cfg.entry_min_run_atr = 0.0
    cfg.entry_min_bar_range_atr = 0.0
    cfg.entry_wall_ahead_pct = 0.0
    cfg.entry_wall_ahead_mult = 0.0
    cfg.entry_min_rr_to_wall = 0.0
    cfg.entry_retest_enabled = False
    cfg.retest_min_impulse_atr = 0.0
    cfg.retest_min_pullback = 0.0
    cfg.retest_max_pullback = 1.0
    cfg.retest_require_sweep = False
    cfg.retest_require_trigger = False
    cfg.entry_sl_atr_mult = 1.5
    cfg.entry_sl_min_pct = 0.2
    cfg.run_atr_adaptive = 0.0
    cfg.micro_exit_protect_r = 0.5
    return cfg


def _prob(bull=80, htf=1, trend=1, eq=None, price=99.0):
    return {
        "ready": True, "bull_prob": bull, "bear_prob": 100 - bull,
        "htf_trend": htf, "trend": trend, "equilibrium": eq,
        "reasons": ["r1", "r2", "r3", "r4"],
    }


def _smc():
    return {
        "ready": True, "trend": 1, "last_hi": 102.0, "last_lo": 99.0,
        "equilibrium": 100.5, "bull_ob": {"bottom": 98.8, "top": 99.2},
        "bear_ob": None, "last_event": {"type": "BOS", "side": "bull"},
    }


def _klines_bull():
    rows = []
    for i in range(30):
        o = 99.0 + i * 0.05
        rows.append([i * 60_000, o, o + 0.2, o - 0.1, o + 0.05, 1000.0 + i])
    return rows


def test_direction_verdict_agrees():
    cfg = _cfg()
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    v = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                          {"dom_pressure": 0.5}, depth, tape, cfg)
    check("verdict: LONG при полном согласии", v["side"] == "long", str(v))
    check("verdict: сила > 0", v["strength"] > 100, f"strength={v['strength']}")
    check("verdict: agree >= 3", v["agree_count"] >= 3, f"agree={v['agree_count']}")
    check("verdict: не заблокирован", not v["blocked"], v["blocked_reason"])


def test_direction_verdict_conflicts():
    cfg = _cfg()
    # индикатор бычий, а стакан сильно медвежий
    depth = orderbook_facts(book(bid_qty=10.0, ask_qty=500.0))
    tape = tape_facts(make_trades(side="sell"))
    v = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                          {"dom_pressure": -0.8}, depth, tape, cfg)
    check("verdict: стакан против -> блок", v["side"] is None and v["blocked"], str(v))
    # противоречие внутри индикатора
    v2 = direction_verdict(_prob(bull=80, htf=-1, trend=1), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape, cfg)
    check("verdict: HTF против структуры -> блок", v2["side"] is None, str(v2))
    # недостаточно показателей
    v3 = direction_verdict(_prob(bull=80, htf=0, trend=0, eq=None), {}, [],
                           {"dom_pressure": 0.0}, depth, tape, cfg)
    check("verdict: мало показателей -> блок", v3["side"] is None, str(v3))
    # одиночный лёгкий компонент стакана против -> вход проходит с minor_book_conflict
    depth_bull = orderbook_facts(wall_book())
    tape_bear = {"ready": True, "buy_ratio": 0.1, "velocity": 5.0, "cvd_slope": -0.5}
    v4 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth_bull, tape_bear, cfg)
    check("verdict: одиночный лёгкий против стакана -> не блок",
          not v4["blocked"] and v4.get("minor_book_conflict") is True,
          f"{v4.get('blocked_reason')}")


def test_direction_verdict_tape_filter():
    cfg = _cfg()
    cfg.entry_tape_min_ratio = 0.53
    depth = orderbook_facts(wall_book())
    # индикаторы/стакан бычьи, но лента продавцов -> вход заблокирован
    tape_bear = tape_facts(make_trades(side="sell"))
    v = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                          {"dom_pressure": 0.5}, depth, tape_bear, cfg)
    check("tape_filter: медвежья лента -> блок", v["blocked"] and "лента" in v["blocked_reason"], str(v))
    # лента покупателей -> вход разрешён
    tape_bull = tape_facts(make_trades(side="buy"))
    v2 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape_bull, cfg)
    check("tape_filter: бычья лента -> вход", v2["side"] == "long", str(v2))
    # умеренно медвежья лента (0.45): старый код пропускал (порог компонента 0.08),
    # новый фильтр 0.53 блокирует
    mixed = [{"price": 100.0, "amount": 1.0, "side": s, "ts": time.time() * 1000.0 - i * 500.0}
             for i, s in enumerate(["buy"] * 22 + ["sell"] * 28)]
    tape_mixed = tape_facts(mixed)
    check("tape_filter: фикстура ~0.44", 0.42 < tape_mixed["buy_ratio"] < 0.48,
          f"ratio={tape_mixed['buy_ratio']}")
    v3 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape_mixed, cfg)
    check("tape_filter: 0.53 режет 0.44", v3["blocked"] and "лента" in v3["blocked_reason"], str(v3))
    # порог 0 выключен — умеренно медвежья лента проходит
    cfg0 = _cfg()
    cfg0.entry_tape_min_ratio = 0.0
    v5 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape_mixed, cfg0)
    check("tape_filter: порог 0 -> фильтр выключен", v5["side"] == "long", str(v5))


def _klines_run_tail():
    """Возвращает бычьи свечи, где за последние 3 минуты цена уже прошла >=1 ATR вверх."""
    rows = []
    for i in range(16):
        o = 100.0 + i * 0.1
        rows.append([i * 60_000, o, o + 0.25, o - 0.15, o + 0.1, 1000.0])
    # последние 3 свечи — резкий рывок вверх: 0.30 -> 0.60 -> 0.95 (близко к +1 ATR за 3 мин)
    base = rows[-1][4]
    h_step = [0.35, 0.65, 0.95]
    for j, h in enumerate(h_step):
        rows.append([rows[-1][0] + 60_000, rows[-1][4], base + h, base + h - 0.2, base + h - 0.05, 1500.0])
    return rows


def test_direction_verdict_tail_filter():
    cfg = _cfg()
    cfg.entry_tape_min_ratio = 0.0
    cfg.entry_max_run_atr = 0.8
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    v = direction_verdict(_prob(bull=80), _smc(), _klines_run_tail(),
                          {"dom_pressure": 0.5}, depth, tape, cfg)
    check("tail: вход в хвост движения -> блок", v["blocked"] and "хвост" in v["blocked_reason"], str(v))
    # без фильтра (0) тот же вход разрешён
    cfg0 = _cfg()
    cfg0.entry_tape_min_ratio = 0.0
    v2 = direction_verdict(_prob(bull=80), _smc(), _klines_run_tail(),
                           {"dom_pressure": 0.5}, depth, tape, cfg0)
    check("tail: порог 0 -> фильтр выключен", v2["side"] == "long", str(v2))


def _klines_flat():
    """Цена почти не двинулась за последние 3 минуты (боковик) — вход раньше начала движения.
    Цена 99.0 внутри bull_ob (98.8-99.2), предыдущие свечи дают положительный ATR."""
    rows = []
    for i in range(27):
        rows.append([i * 60_000, 99.0, 99.1, 98.9, 99.0, 1000.0])
    for j in range(3):
        rows.append([rows[-1][0] + 60_000, 99.0, 99.0, 99.0, 99.0, 1000.0])
    return rows


def test_direction_verdict_min_run_filter():
    cfg = _cfg()
    cfg.entry_tape_min_ratio = 0.0
    cfg.entry_min_run_atr = 0.25
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    # боковик: пробег 0 ATR от экстремума за 3 минуты -> блок (движение ещё не началось)
    v = direction_verdict(_prob(bull=80), _smc(), _klines_flat(),
                          {"dom_pressure": 0.5}, depth, tape, cfg)
    check("min_run: вход раньше начала движения -> блок",
          v["blocked"] and "раньше начала" in v["blocked_reason"], str(v))
    # обычный тренд _klines_bull: пробег > 0.25 ATR -> вход разрешён
    v2 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape, cfg)
    check("min_run: начало движения -> вход", v2["side"] == "long", str(v2))
    # порог 0 -> фильтр выключен, боковик проходит
    cfg0 = _cfg()
    cfg0.entry_tape_min_ratio = 0.0
    v3 = direction_verdict(_prob(bull=80), _smc(), _klines_flat(),
                           {"dom_pressure": 0.5}, depth, tape, cfg0)
    check("min_run: порог 0 -> фильтр выключен", v3["side"] == "long", str(v3))


def _klines_stall():
    """Рынок замер: история волатильная (ATR большой), последние 2 свечи микро-шаги."""
    rows = []
    for i in range(27):
        o = 100.0 + i * 0.05
        rows.append([i * 60_000, o, o + 0.2, o - 0.1, o + 0.05, 1000.0])
    last = rows[-1][4]
    for j in range(2):
        rows.append([rows[-1][0] + 60_000, last, last + 0.005, last - 0.005, last + 0.002, 1000.0])
    return rows


def test_direction_verdict_no_expansion():
    cfg = _cfg()
    cfg.entry_tape_min_ratio = 0.0
    cfg.entry_min_run_atr = 0.0
    cfg.entry_min_bar_range_atr = 0.7
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    # рынок стоит (свечи 0.02 ATR) -> блок "движение не разгоняется"
    v = direction_verdict(_prob(bull=80), _smc(), _klines_stall(),
                          {"dom_pressure": 0.5}, depth, tape, cfg)
    check("no_expansion: стоячий рынок -> блок",
          v["blocked"] and "не разгоняется" in v["blocked_reason"], str(v))
    # разгоняющийся тренд -> вход разрешён
    v2 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape, cfg)
    check("no_expansion: движение идёт -> вход", v2["side"] == "long", str(v2))
    # фильтр выключен (0) -> стоячий рынок проходит
    cfg0 = _cfg()
    cfg0.entry_tape_min_ratio = 0.0
    cfg0.entry_min_run_atr = 0.0
    v3 = direction_verdict(_prob(bull=80), _smc(), _klines_stall(),
                           {"dom_pressure": 0.5}, depth, tape, cfg0)
    check("no_expansion: порог 0 -> фильтр выключен", v3["side"] == "long", str(v3))


def _book_with_wall_ahead():
    """Бычий стакан, но на +0.5% выше цены стоит большая ask-стена (500 vs медиана 10)."""
    bids = [[100.00, 10.0] for _ in range(6)]
    asks = [[100.01, 10.0], [100.02, 10.0], [100.03, 10.0],
            [100.50, 500.0], [100.51, 10.0], [100.52, 10.0]]
    return {"bids": bids, "asks": asks}


def _book_open_path():
    """Стакан без стен впереди: ровные мелкие уровни на +2%."""
    bids = [[100.00, 10.0] for _ in range(8)]
    asks = [[100.01 + i * 0.01, 8.0 + i * 0.5] for i in range(20)]
    return {"bids": bids, "asks": asks}


def test_direction_verdict_htf_hard_filter():
    cfg = _cfg()
    cfg.entry_tape_min_ratio = 0.0
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    # все компоненты бычьи, но HTF медвежий -> хард-блок
    v = direction_verdict(_prob(bull=80, htf=-1), _smc(), _klines_bull(),
                          {"dom_pressure": 0.5}, depth, tape, cfg)
    check("htf: против стороны входа -> блок",
          v["blocked"] and "HTF" in v["blocked_reason"], str(v))
    # HTF совпадает -> вход разрешён
    v2 = direction_verdict(_prob(bull=80, htf=1), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape, cfg)
    check("htf: по тренду -> вход", v2["side"] == "long", str(v2))
    # HTF нейтральный (0) -> не блокирует
    smc_sweep = dict(_smc())
    smc_sweep["last_lo"] = 100.5  # low 100.35 < 100.5 и close 100.5 >= 100.5: sweep бычий
    v3 = direction_verdict(_prob(bull=80, htf=0), smc_sweep, _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape, cfg)
    check("htf: нейтральный -> вход", v3["side"] == "long", str(v3))


def test_liquidity_ahead():
    la = liquidity_ahead(_book_with_wall_ahead(), side=1, max_pct=0.02, wall_mult=3.0)
    check("ahead: стена впереди найдена", la.get("ready") and la.get("wall_ahead") is not None,
          str(la))
    check("ahead: дистанция стены ~0.5%",
          la.get("wall_dist_pct") is not None and 0.4 < la["wall_dist_pct"] < 0.6,
          f"dist={la.get('wall_dist_pct')}")
    # пустой путь: стены нет
    la2 = liquidity_ahead(_book_open_path(), side=1, max_pct=0.02, wall_mult=3.0)
    check("ahead: открытый путь без стен", la2.get("ready") and la2.get("wall_ahead") is None,
          str(la2))
    # пустой стакан
    la3 = liquidity_ahead({"bids": [], "asks": []}, side=1)
    check("ahead: пустой стакан -> not ready", not la3.get("ready"), str(la3))


def test_direction_verdict_wall_ahead_filter():
    cfg = _cfg()
    cfg.entry_tape_min_ratio = 0.0
    cfg.entry_wall_ahead_pct = 0.8
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    # индикаторы бычьи, но впереди на 0.5% стена -> блок
    v = direction_verdict(_prob(bull=80), _smc(), _klines_bull(), {"dom_pressure": 0.5},
                          depth, tape, cfg, orderbook=_book_with_wall_ahead())
    check("wall_ahead: стена впереди -> блок",
          v["blocked"] and "стена" in v["blocked_reason"], str(v))
    # путь открыт -> вход разрешён
    v2 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(), {"dom_pressure": 0.5},
                           depth, tape, cfg, orderbook=_book_open_path())
    check("wall_ahead: открытый путь -> вход", v2["side"] == "long", str(v2))
    # фильтр выключен (0) -> стена впереди не блокирует
    cfg0 = _cfg()
    cfg0.entry_tape_min_ratio = 0.0
    v3 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(), {"dom_pressure": 0.5},
                           depth, tape, cfg0, orderbook=_book_with_wall_ahead())
    check("wall_ahead: порог 0 -> фильтр выключен", v3["side"] == "long", str(v3))


def test_exit_reason():
    cfg = _cfg()
    raw = {"indicator1_probability": _prob(bull=90, htf=1, trend=1, eq=100.5, price=99.5)}
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    pos = type("P", (), {
        "side": "long", "opened_at": time.time(),
        "features": {"entry_strength": 150.0, "entry_wall_mass": 500.0,
                     "entry_imbalance": 0.8, "entry_tape_ratio": 0.9},
    })()
    check("exit: сила есть -> держим", exit_reason(pos, raw, depth, tape, cfg) is None)
    # разворот индикатора
    raw2 = {"indicator1_probability": _prob(bull=35, htf=-1, trend=-1, eq=100.5, price=100.5)}
    r = exit_reason(pos, raw2, depth, tape, cfg)
    check("exit: разворот индикатора", r is not None and "разворот" in r, str(r))
    # стакан потерял поддержку
    raw3 = {"indicator1_probability": _prob(bull=50, htf=1, trend=1, eq=100.5, price=99.5),
            "verdict": {"strength": 60.0}}
    depth_bear = orderbook_facts(book(bid_qty=10.0, ask_qty=500.0))
    tape_bear = tape_facts(make_trades(side="sell"))
    r2 = exit_reason(pos, raw3, depth_bear, tape_bear, cfg)
    check("exit: стакан против + ослабление", r2 is not None, str(r2))
    # защита победы: после +1R разворот индикатора не закрывает
    cfg_p = _cfg()
    cfg_p.exit_protect_r = 1.0
    r3 = exit_reason(pos, raw2, depth, tape, cfg_p, r_multiple=1.2)
    check("exit: +1.2R защита -> держим несмотря на разворот", r3 is None, str(r3))
    # ниже порога защиты — разворот закрывает
    r4 = exit_reason(pos, raw2, depth, tape, cfg_p, r_multiple=0.8)
    check("exit: +0.8R без защиты -> разворот закрывает", r4 is not None, str(r4))


def test_atr_sl():
    from strategies.facts_confluence import FactsConfluenceStrategy
    f = FactsConfluenceStrategy
    atr = 0.5
    # без структуры: SL = entry ∓ 1.5xATR
    sl_l = f._stop_loss("long", 100.0, {}, {}, atr, atr_mult=1.5)
    sl_s = f._stop_loss("short", 100.0, {}, {}, atr, atr_mult=1.5)
    check("atr_sl: long = entry - 1.5*ATR", abs(sl_l - 99.25) < 1e-9, str(sl_l))
    check("atr_sl: short = entry + 1.5*ATR", abs(sl_s - 100.75) < 1e-9, str(sl_s))
    # структура дальше 1.5*ATR -> стоп по структуре
    sl2 = f._stop_loss("long", 100.0, {"last_lo": 98.5}, {}, atr, atr_mult=1.5)
    check("atr_sl: структура дальше -> по структуре", abs(sl2 - 98.5) < 1e-9, str(sl2))
    # структура ближе 1.5*ATR -> ATR-стоп (дышит в шуме)
    sl3 = f._stop_loss("long", 100.0, {"last_lo": 99.9}, {}, atr, atr_mult=1.5)
    check("atr_sl: близкая структура -> ATR-стоп", abs(sl3 - 99.25) < 1e-9, str(sl3))
    # минимальная дистанция в % от цены
    sl4 = f._stop_loss("long", 100.0, {}, {}, 1e-4, atr_mult=1.5, min_dist_pct=0.2)
    check("atr_sl: минимум 0.2% цены", abs(100.0 - sl4 - 0.2) < 1e-9, str(sl4))


def test_volatility_filter():
    from types import SimpleNamespace
    from main import TradingBot

    bot = object.__new__(TradingBot)
    bot.cfg = SimpleNamespace(min_24h_volatility=10.0)
    bot.volatility_map = {"VOLATILE": 25.0, "QUIET": 2.0, "NONE": None}
    symbols = ["VOLATILE", "QUIET", "NONE"]
    keep = bot._filter_low_volatility(symbols)
    check("24h фильтр: волатильная монета осталась", "VOLATILE" in keep, str(keep))
    check("24h фильтр: тихая монета отсечена", "QUIET" not in keep, str(keep))
    bot.cfg.min_24h_volatility = 0.0
    keep_all = bot._filter_low_volatility(symbols)
    check("24h фильтр: порог 0 = фильтр выключен", len(keep_all) == 3, str(keep_all))


def _klines_retest():
    """Боковик (99.0), импульс вверх (99.2 -> 100.2), затем откат к 99.5 (середина хода).
    Последняя свеча: low 99.3 < last_lo 99.4, close 99.5 >= 99.4 -> sweep бычий."""
    rows = []
    for i in range(6):
        rows.append([i * 60_000, 99.0, 99.1, 98.9, 99.0, 1000.0])
    for i in range(6):
        o = 99.0 + (i + 1) * 0.2
        rows.append([rows[-1][0] + 60_000, o, o + 0.2, o - 0.1, o + 0.15, 1000.0 + i])
    o = rows[-1][4]
    for drop in (0.15, 0.3, 0.4):
        rows.append([rows[-1][0] + 60_000, o, o + 0.2, o - 0.6, o - drop, 900.0])
        o = o - drop
    return rows


def test_retest_verdict():
    cfg = _cfg()
    cfg.entry_tape_min_ratio = 0.0
    cfg.entry_retest_enabled = True
    cfg.retest_min_impulse_atr = 2.0
    cfg.retest_min_pullback = 0.25
    cfg.retest_max_pullback = 0.75
    cfg.retest_require_sweep = False
    cfg.retest_require_trigger = False
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    # откат после импульса + sweep -> ретест-вход разрешён
    smc = dict(_smc())
    smc["last_lo"] = 99.4
    v = direction_verdict(_prob(bull=80), smc, _klines_retest(), {"dom_pressure": 0.5},
                          depth, tape, cfg)
    check("retest: откат после импульса -> вход", v["side"] == "long", str(v))
    check("retest: retest-данные в вердикте",
          (v.get("retest") or {}).get("pullback") is not None and v["retest"]["pullback"] > 0.2, str(v))
    # цена у экстремума (без отката) -> ретест не применился, fallback на пробой
    v2 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(), {"dom_pressure": 0.5},
                           depth, tape, cfg)
    check("retest: нет отката -> fallback на пробой, вход не блокируется",
          not v2["blocked"] and v2["side"] == "long"
          and "retest_zone" in (v2.get("retest") or {}).get("missing_triggers", []), str(v2))
    # требует sweep: без sweep -> блок
    cfg_s = _cfg()
    cfg_s.entry_tape_min_ratio = 0.0
    cfg_s.entry_retest_enabled = True
    cfg_s.retest_min_impulse_atr = 2.0
    cfg_s.retest_min_pullback = 0.25
    cfg_s.retest_require_sweep = True
    v3 = direction_verdict(_prob(bull=80), smc, _klines_retest(), {"dom_pressure": 0.5},
                           depth, tape, cfg_s)
    check("retest: sweep обязателен -> входит с sweep", v3["side"] == "long", str(v3))
    cfg_ns = _cfg()
    cfg_ns.entry_tape_min_ratio = 0.0
    cfg_ns.entry_retest_enabled = True
    cfg_ns.retest_min_impulse_atr = 2.0
    cfg_ns.retest_min_pullback = 0.25
    cfg_ns.retest_require_sweep = True
    v4 = direction_verdict(_prob(bull=80), _smc(), _klines_retest(), {"dom_pressure": 0.5},
                           depth, tape, cfg_ns)
    check("retest: без sweep -> fallback на пробой, вход не блокируется",
          not v4["blocked"] and "sweep_reclaim" in (v4.get("retest") or {}).get("missing_triggers", []),
          str(v4))


def test_rr_to_wall():
    cfg = _cfg()
    cfg.entry_tape_min_ratio = 0.0
    cfg.entry_min_rr_to_wall = 2.0
    cfg.entry_sl_atr_mult = 1.5
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    # стена на +0.5% ближе 2R (R ~0.45%) -> блок
    v = direction_verdict(_prob(bull=80), _smc(), _klines_bull(), {"dom_pressure": 0.5},
                          depth, tape, cfg, orderbook=_book_with_wall_ahead())
    check("rr: стена ближе 2R -> блок",
          v["blocked"] and "R:R" in v["blocked_reason"], str(v))
    # путь открыт -> вход
    v2 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(), {"dom_pressure": 0.5},
                           depth, tape, cfg, orderbook=_book_open_path())
    check("rr: открытый путь -> вход", v2["side"] == "long", str(v2))
    # порог 0 -> фильтр выключен
    cfg0 = _cfg()
    cfg0.entry_tape_min_ratio = 0.0
    v3 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(), {"dom_pressure": 0.5},
                           depth, tape, cfg0, orderbook=_book_with_wall_ahead())
    check("rr: порог 0 -> фильтр выключен", v3["side"] == "long", str(v3))


def test_absorption():
    oracle = DepthOracle(min_seen=3)
    book_wall = {"bids": [[100.0, 500.0], [99.99, 20.0], [99.98, 15.0], [99.97, 10.0], [99.96, 8.0]],
                 "asks": [[100.01, 20.0], [100.02, 15.0], [100.03, 10.0], [100.04, 8.0], [100.05, 6.0]]}
    tape_sell = {"ready": True, "cvd_slope": -0.6}
    tape_buy = {"ready": True, "cvd_slope": 0.6}
    f1 = oracle.update("BTC/USDT", book_wall, tape=tape_sell)
    f2 = oracle.update("BTC/USDT", book_wall, tape=tape_sell)
    f3 = oracle.update("BTC/USDT", book_wall, tape=tape_sell)
    check("absorption: bid-стена держится под продажами -> +1", f3.get("absorption_side") == 1.0,
          f"abs={f3.get('absorption_side')}")
    # давление сменилось на покупки -> сброс
    f4 = oracle.update("BTC/USDT", book_wall, tape=tape_buy)
    check("absorption: смена давления -> сброс", f4.get("absorption_side") != 1.0,
          f"abs={f4.get('absorption_side')}")
    # мягкий режим: достаточно 2 сканов подряд
    oracle2 = DepthOracle(min_seen=3, min_absorption_ticks=2)
    oracle2.update("BTC/USDT", book_wall, tape=tape_sell)
    f5 = oracle2.update("BTC/USDT", book_wall, tape=tape_sell)
    check("absorption: 2 скана с min_absorption_ticks=2 -> +1",
          f5.get("absorption_side") == 1.0, f"abs={f5.get('absorption_side')}")


def test_delta_divergence():
    now = time.time() * 1000.0
    falling_with_buys = [{"price": 100.0 - i * 0.01, "amount": 1.0, "side": "buy",
                          "ts": now - (20 - i) * 500.0} for i in range(20)]
    t = tape_facts(falling_with_buys)
    check("delta_div: цена падает, покупки растут -> +1", t.get("delta_divergence") == 1.0,
          f"div={t.get('delta_divergence')}")
    rising_with_sells = [{"price": 100.0 + i * 0.01, "amount": 1.0, "side": "sell",
                          "ts": now - (20 - i) * 500.0} for i in range(20)]
    t2 = tape_facts(rising_with_sells)
    check("delta_div: цена растёт, продажи давят -> -1", t2.get("delta_divergence") == -1.0,
          f"div={t2.get('delta_divergence')}")
    plain = make_trades(side="buy")
    t3 = tape_facts(plain)
    check("delta_div: без расхождения -> 0", t3.get("delta_divergence") == 0.0,
          f"div={t3.get('delta_divergence')}")


def test_adaptive_atr_sl():
    from strategies.facts_confluence import FactsConfluenceStrategy
    f = FactsConfluenceStrategy
    # высоковолатильная монета: ATR/цена >= 0.002 -> atr_mult 2.0
    sl = f._stop_loss("long", 100.0, {}, {}, 0.3, atr_mult=1.5,
                      atr_mult_hi=2.0, atr_mult_lo=1.2, atr_rel_hi=0.002, atr_rel_lo=0.0002)
    check("sl_adapt: волатильная -> 2.0xATR", abs(sl - 99.4) < 1e-9, str(sl))
    # низколиквидная: ATR/цена <= 0.0002 -> atr_mult 1.2, но floor 0.2% цены
    sl2 = f._stop_loss("long", 100.0, {}, {}, 0.001, atr_mult=1.5,
                       atr_mult_hi=2.0, atr_mult_lo=1.2, atr_rel_hi=0.002, atr_rel_lo=0.0002)
    check("sl_adapt: низколиквидная -> 1.2xATR, минимум 0.2%", abs(sl2 - 99.8) < 1e-9, str(sl2))
    # средняя: базовый множитель (min_dist_pct=0, чтобы проверить именно множитель)
    sl3 = f._stop_loss("long", 100.0, {}, {}, 0.05, atr_mult=1.5, min_dist_pct=0.0,
                       atr_mult_hi=2.0, atr_mult_lo=1.2, atr_rel_hi=0.002, atr_rel_lo=0.0002)
    check("sl_adapt: средняя -> 1.5xATR", abs(sl3 - 99.925) < 1e-9, str(sl3))
    # буфер спреда
    sl4 = f._stop_loss("long", 100.0, {}, {}, 0.3, atr_mult=1.5, spread_buffer=0.01)
    check("sl_adapt: буфер спреда", abs(sl4 - 99.54) < 1e-9, str(sl4))
    # кластер: вторая стена дальше ATR-дистанции -> стоп за второй стеной
    depth2 = {"bid_walls": [{"price": 99.95, "size": 10.0}, {"price": 99.75, "size": 20.0}],
              "spread": 0.01}
    sl5 = f._stop_loss("long", 100.0, {}, depth2, 0.05, atr_mult=1.5)
    check("sl_adapt: стоп за второй стеной", abs(sl5 - 99.75) < 1e-9, str(sl5))


def test_atr_5m_wider():
    from strategies.facts_confluence import FactsConfluenceStrategy
    f = FactsConfluenceStrategy
    rows5 = []
    for i in range(30):
        o = 100.0 + i * 0.1
        rows5.append([i * 300_000, o, o + 0.4, o - 0.2, o + 0.1, 5000.0])
    raw5 = {"indicator_klines_5m": rows5}
    check("atr5: ATR по 5m > 0", f._atr_5m(raw5) > 0.1, f"atr5={f._atr_5m(raw5)}")
    raw_empty = {}
    check("atr5: пусто -> 0", f._atr_5m(raw_empty) == 0.0, str(f._atr_5m(raw_empty)))


def test_scale_out():
    import tempfile
    from pathlib import Path
    from execution.engine import ExecutionEngine
    from learning.journal import TradeJournal
    from core.models import Signal
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    journal = TradeJournal(db_path=Path(tmp.name))
    engine = ExecutionEngine(None, journal, paper=True, scale_out_enabled=True,
                             scale_out_r=1.0, scale_out_fraction=0.5, take_profit_enabled=False)
    sig = Signal(symbol="BTC/USDT", side="long", entry=100.0, stop_loss=99.0, take_profit=None,
                 confidence=0.8, strategy="facts_confluence", timeframe="1m", features={})
    pos = engine.open(sig, qty=2.0)
    engine.mark({"BTC/USDT": 101.0})  # +1R
    engine.check_positions({"BTC/USDT": 101.0}, {})
    check("scale_out: половина закрыта", pos.status == "open" and abs(pos.qty - 1.0) < 1e-9,
          f"qty={pos.qty}")
    check("scale_out: фиксация зафиксирована", (pos.features or {}).get("scaled_at_r") == 1.0,
          str(pos.features))
    check("scale_out: стоп перенесён на безубыток", pos.stop_loss == 100.0, f"sl={pos.stop_loss}")
    # вторая фиксация не происходит
    engine.mark({"BTC/USDT": 102.0})
    engine.check_positions({"BTC/USDT": 102.0}, {})
    check("scale_out: повторная фиксация не выполняется", abs(pos.qty - 1.0) < 1e-9,
          f"qty={pos.qty}")
    journal.close()
    os.unlink(tmp.name)


def test_blocked_codes():
    cfg = _cfg()
    cfg.entry_tape_min_ratio = 0.0
    cfg.entry_probability_min = 95
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    # вероятность ниже порога -> код probability
    v = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                          {"dom_pressure": 0.5}, depth, tape, cfg)
    check("codes: вероятность -> blocked_code probability",
          v["blocked"] and v.get("blocked_code") == "probability", str(v))
    # HTF против -> код htf
    cfg2 = _cfg()
    cfg2.entry_tape_min_ratio = 0.0
    v2 = direction_verdict(_prob(bull=80, htf=-1), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape, cfg2)
    check("codes: HTF против -> blocked_code htf",
          v2["blocked"] and v2.get("blocked_code") == "htf", str(v2))
    # противоречие индикаторов: HTF(-1) против бычьего большинства при разнобое
    # non_htf (premium против) -> хард-фильтр пропускает, ловит конфликт
    cfg3 = _cfg()
    cfg3.entry_tape_min_ratio = 0.0
    v3 = direction_verdict(_prob(bull=80, htf=-1, trend=1, eq=0.0), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape, cfg3)
    check("codes: конфликт -> blocked_code indicator_conflict",
          v3["blocked"] and v3.get("blocked_code") == "indicator_conflict",
          f"{v3.get('blocked_reason')}")
    # одиночный лёгкий против (Premium/Discount) -> мягкий конфликт, вход не блокируется
    v3b = direction_verdict(_prob(bull=80, htf=0, trend=1, eq=99.0), _smc(), _klines_retest(),
                            {"dom_pressure": 0.5}, depth, tape, cfg3)
    check("codes: одиночный лёгкий против -> вход проходит с minor_conflict",
          not v3b["blocked"] and v3b.get("minor_conflict") is True and v3b["side"] == "long",
          f"{v3b.get('blocked_reason')} {v3b.get('side')}")
    # лента против -> код tape
    cfg4 = _cfg()
    cfg4.entry_tape_min_ratio = 0.53
    tape_bear = tape_facts(make_trades(side="sell"))
    v4 = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                           {"dom_pressure": 0.5}, depth, tape_bear, cfg4)
    check("codes: лента -> blocked_code tape",
          v4["blocked"] and v4.get("blocked_code") == "tape", str(v4))
    # ретест: глубокий откат -> fallback на пробой, retest_zone в missing_triggers
    cfg5 = _cfg()
    cfg5.entry_tape_min_ratio = 0.0
    cfg5.entry_retest_enabled = True
    cfg5.retest_min_impulse_atr = 2.0
    cfg5.retest_min_pullback = 0.25
    cfg5.retest_max_pullback = 0.6
    v5 = direction_verdict(_prob(bull=80), _smc(), _klines_retest(),
                           {"dom_pressure": 0.5}, depth, tape, cfg5)
    check("codes: глубокий откат -> fallback, вход не блокируется",
          not v5["blocked"] and "retest_zone" in (v5.get("retest") or {}).get("missing_triggers", []),
          str(v5))
    # микротриггер: missing_triggers заполнен, но не блокирует
    cfg6 = _cfg()
    cfg6.entry_tape_min_ratio = 0.0
    cfg6.entry_retest_enabled = True
    cfg6.retest_min_impulse_atr = 2.0
    cfg6.retest_min_pullback = 0.25
    cfg6.retest_require_trigger = True
    smc_plain = dict(_smc())
    v6 = direction_verdict(_prob(bull=80), smc_plain, _klines_retest(),
                           {"dom_pressure": 0.5}, depth, tape, cfg6)
    missing = (v6.get("retest") or {}).get("missing_triggers") or []
    check("codes: триггер не сработал -> missing_triggers, но вход не блокируется",
          not v6["blocked"] and len(missing) >= 2,
          f"missing={missing}")


def _flow(short_usd=0.0, long_usd=0.0, oi_change=None, funding=0.0, premium_pct=0.0,
          age_s=None, prem_n=1):
    now = time.time()
    last_ts = (now - age_s) if age_s is not None else now
    windows = {}
    for w in (5, 15, 60, 300):
        windows[str(w)] = {"long_usd": long_usd, "short_usd": short_usd,
                           "total_usd": long_usd + short_usd, "n": 2}
    return {
        "liq": {"windows": windows, "last_ts": last_ts, "age_s": age_s},
        "oi": {"change_pct": oi_change, "age_s": 300.0, "oi": 1e6},
        "premium": {"mark": 100.0, "index": 100.0, "funding": funding,
                    "predicted": funding, "premium_pct": premium_pct,
                    "mean": 0.0, "std": 0.0, "n": prem_n, "ts": time.time()},
        "fast": {"subscribed": False},
    }


def _flow_cfg():
    cfg = _cfg()
    cfg.flow_liq_enabled = True
    cfg.flow_liq_window = 300
    cfg.flow_liq_min_usd = 50000.0
    cfg.flow_liq_min_fast_usd = 20000.0
    cfg.flow_liq_decay_sec = 120.0
    cfg.flow_funding_max_abs = 0.0004
    cfg.flow_premium_max_pct = 0.3
    cfg.flow_premium_z = 2.0
    cfg.flow_oi_min_change = 0.5
    cfg.flow_oi_min_age = 120
    cfg.flow_oi_max_age = 1800.0
    return cfg


def test_flow_components():
    from features.direction import flow_components
    cfg = _flow_cfg()
    # нет данных микроструктуры -> нейтрально, ничего не ломает
    comps = flow_components(None, 1, cfg)
    check("flow: None -> пусто", comps == [], str(comps))
    comps = flow_components({}, -1, cfg)
    check("flow: {} -> пусто", comps == [], str(comps))
    # свежий быстрый каскад шортов (15с окно, полный вес) -> усиливает лонг
    comps = flow_components(_flow(short_usd=200_000), 1, cfg)
    check("flow: свежий каскад шортов усиливает лонг",
          any(c["side"] == 1 and c["weight"] >= 14.9 for c in comps), str(comps))
    # старый каскад (2 минуты назад) -> вес сгорел decay'ом, компонента нет
    comps = flow_components(_flow(short_usd=200_000, age_s=300), 1, cfg)
    check("flow: старый каскад погас decay'ом",
          not any("каскад" in c["name"].lower() for c in comps), str(comps))
    # каскад лонгов (давление вниз) -> против лонга
    comps = flow_components(_flow(long_usd=200_000), 1, cfg)
    check("flow: каскад лонгов против лонга",
          any(c["side"] == -1 and "каскад" in c["name"].lower() for c in comps), str(comps))
    # каскад лонгов усиливает шорт
    comps = flow_components(_flow(long_usd=200_000), -1, cfg)
    check("flow: каскад лонгов усиливает шорт",
          any(c["side"] == 1 and "каскад" in c["name"].lower() for c in comps), str(comps))
    # маленький объём ликвидаций -> ничего
    comps = flow_components(_flow(short_usd=1_000), 1, cfg)
    check("flow: малый объём -> пусто по ликвидациям",
          not any("каскад" in c["name"].lower() for c in comps), str(comps))
    # OI растёт -> усиливает лонг
    comps = flow_components(_flow(oi_change=1.2), 1, cfg)
    check("flow: рост OI усиливает лонг",
          any(c["side"] == 1 and "OI" in c["name"] for c in comps), str(comps))
    # OI падает -> против лонга
    comps = flow_components(_flow(oi_change=-1.2), 1, cfg)
    check("flow: падение OI против лонга",
          any(c["side"] == -1 and "OI" in c["name"] for c in comps), str(comps))
    # OI чуть изменился -> ничего
    comps = flow_components(_flow(oi_change=0.2), 1, cfg)
    check("flow: малый рост OI -> пусто по OI",
          not any("OI" in c["name"] for c in comps), str(comps))
    # funding перегрев против лонга
    comps = flow_components(_flow(funding=0.001), 1, cfg)
    check("flow: funding перегрев против лонга",
          any(c["side"] == -1 and "Funding" in c["name"] for c in comps), str(comps))
    # funding в пользу лонга (шорты перегружены)
    comps = flow_components(_flow(funding=-0.001), 1, cfg)
    check("flow: funding за лонг",
          any(c["side"] == 1 and "Funding" in c["name"] for c in comps), str(comps))
    # premium перегрев против лонга (фикс-порог при малой истории)
    comps = flow_components(_flow(premium_pct=0.5), 1, cfg)
    check("flow: премия против лонга",
          any(c["side"] == -1 and "индекса" in c["name"] for c in comps), str(comps))
    # premium в пользу лонга
    comps = flow_components(_flow(premium_pct=-0.5), 1, cfg)
    check("flow: дисконт в пользу лонга",
          any(c["side"] == 1 and "индекса" in c["name"] for c in comps), str(comps))
    # z-score режим: большая история, премия в 3 сигмах -> штраф
    comps = flow_components(_flow(premium_pct=0.06, prem_n=30), 1, cfg)
    prem = {"mark": 100.0, "index": 100.0, "funding": 0.0, "predicted": 0.0,
            "premium_pct": 0.06, "mean": 0.0, "std": 0.02, "n": 30, "ts": time.time()}
    flow = _flow(premium_pct=0.06, prem_n=30)
    flow["premium"] = prem
    comps = flow_components(flow, 1, cfg)
    check("flow: z-score перегрев против лонга",
          any(c["side"] == -1 and "индекса" in c["name"] for c in comps), str(comps))
    # умеренный z (в пределах 2 сигм) -> нейтрально
    prem2 = dict(prem, premium_pct=0.02)
    flow2 = _flow(premium_pct=0.02, prem_n=30)
    flow2["premium"] = prem2
    comps = flow_components(flow2, 1, cfg)
    check("flow: малый z -> нейтрально",
          not any("индекса" in c["name"] for c in comps), str(comps))


def test_flow_in_verdict():
    cfg = _flow_cfg()
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    base = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                             {"dom_pressure": 0.5}, depth, tape, cfg)
    check("flow: вердикт без flow не падает", base["side"] == "long" and not base["blocked"], str(base))
    boost = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                              {"dom_pressure": 0.5}, depth, tape, cfg,
                              flow=_flow(short_usd=200_000, oi_change=1.2, funding=-0.001, premium_pct=-0.5))
    check("flow: микроструктура усилила вход", boost["strength"] > base["strength"], f"{base['strength']} vs {boost['strength']}")
    check("flow: компоненты в вердикте", len(boost.get("flow_components") or []) >= 3, str(boost.get("flow_components")))
    hit = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                            {"dom_pressure": 0.5}, depth, tape, cfg,
                            flow=_flow(long_usd=200_000, oi_change=-1.2, funding=0.001, premium_pct=0.5))
    check("flow: контр-микроструктура ослабила вход", hit["strength"] < base["strength"], f"{base['strength']} vs {hit['strength']}")
    check("flow: ослабление не блокирует", not hit["blocked"], hit["blocked_reason"])
    # старый каскад не усиливает вход
    stale = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                              {"dom_pressure": 0.5}, depth, tape, cfg,
                              flow=_flow(short_usd=200_000, age_s=300))
    stale_strength = sum(c["weight"] * c["side"] for c in stale.get("flow_components") or [])
    boost_strength = sum(c["weight"] * c["side"] for c in boost.get("flow_components") or [])
    check("flow: stale не даёт бонус каскада", stale_strength < boost_strength,
          f"{stale_strength} vs {boost_strength}")


def test_flow_feed_aggregator():
    from data.flow import FlowFeed
    feed = FlowFeed(log=lambda *a, **k: None)
    now = time.time()
    feed._liq["BTC/USDT:USDT"] = deque_fake([
        (now - 10, "long", 100_000.0, "k1"),
        (now - 20, "short", 60_000.0, "k2"),
        (now - 200, "short", 500_000.0, "k3"),
    ])
    s = feed.liq_stats("BTC/USDT:USDT")
    w5 = s["windows"]["5"]
    check("flow: ликвидации агрегируются по окнам",
          w5["long_usd"] == 0.0 and s["windows"]["60"]["long_usd"] == 100_000.0
          and s["windows"]["60"]["short_usd"] == 60_000.0, str(s))
    check("flow: окно 300 включает всё", s["windows"]["300"]["total_usd"] == 660_000.0, str(s["windows"]["300"]))
    check("flow: last_ts свежий", s["last_ts"] == now - 10, str(s))
    s2 = feed.liq_stats("NOPE/USDT:USDT")
    check("flow: неизвестный символ -> пусто", s2["windows"]["60"]["n"] == 0, str(s2))


def deque_fake(items):
    from collections import deque
    return deque(items)


def test_fast_feed_helpers():
    from data.flow import FastFeed, _stream_params, _spread_bps
    feed = FastFeed(log=lambda *a, **k: None)
    targets, drop = feed.update_subscriptions(["BTC/USDT:USDT", "ETH/USDT:USDT"])
    check("fast: подписки добавлены", targets == ["BTC/USDT:USDT", "ETH/USDT:USDT"] and drop == [], str((targets, drop)))
    check("fast: is_subscribed", feed.is_subscribed("BTC/USDT:USDT"))
    targets2, drop2 = feed.update_subscriptions(["ETH/USDT:USDT", "SOL/USDT:USDT"])
    check("fast: приоритет держащимся", "ETH/USDT:USDT" in targets2, str(targets2))
    check("fast: остывший снят", "BTC/USDT:USDT" in drop2, str(drop2))
    feed._subs["ETH/USDT:USDT"]["trades"].append((time.time() - 1, "buy", 100.0, 2.0))
    feed._subs["ETH/USDT:USDT"]["book"] = {"b": 99.0, "a": 100.0, "_ts": time.time()}
    tr = feed.trades("ETH/USDT:USDT", max_age_s=10)
    check("fast: сделки отдаются в формате tape", len(tr) == 1 and tr[0]["side"] == "buy" and tr[0]["price"] == 100.0, str(tr))
    book = feed.book("ETH/USDT:USDT")
    check("fast: best bid/ask", book.get("b") == 99.0 and book.get("a") == 100.0, str(book))
    snap = feed.snapshot("ETH/USDT:USDT")
    check("fast: snapshot свежий", snap["subscribed"] and snap["trades_10s"] == 1, str(snap))
    check("fast: spread bps", abs(_spread_bps(99.0, 100.0) - 101.01) < 0.1)
    check("fast: stream params", "@trade" in _stream_params(["BTC/USDT:USDT"])[0]
          and "@bookTicker" in _stream_params(["BTC/USDT:USDT"])[1], str(_stream_params(["BTC/USDT:USDT"])))


def _fast_flow(buy_ratio_3=0.5, buy_ratio_5=0.5, cvd_3=0.0, cvd_accel=0.0,
               burst_side=None, burst_usd=0.0, trade_age=1.0, book_age=0.5,
               spread_slope=None, mid_slope=None, vol_5=100_000.0, vol_10=250_000.0):
    return {"subscribed": True, "now": time.time(),
            "last_msg_age": book_age, "last_trade_age": trade_age,
            "trades_1": 10, "trades_3": 30, "trades_5": 50, "trades_10": 100,
            "buy_ratio_1": buy_ratio_5, "buy_ratio_3": buy_ratio_3,
            "buy_ratio_5": buy_ratio_5, "buy_ratio_10": buy_ratio_5,
            "cvd_1": cvd_3, "cvd_3": cvd_3, "cvd_5": cvd_3, "cvd_10": cvd_3,
            "cvd_accel": cvd_accel, "burst_side": burst_side,
            "burst_usd_1s": burst_usd, "burst_age": 0.0,
            "bid": 100.0, "ask": 100.1, "bid_qty": 10.0, "ask_qty": 10.0,
            "bid_slope": 0.0, "ask_slope": 0.0, "spread_bps": 10.0,
            "vol_1": vol_5 / 5, "vol_3": vol_5 * 0.6, "vol_5": vol_5, "vol_10": vol_10,
            "spread_slope_bps": spread_slope, "mid_slope_pct_10s": mid_slope}


def test_micro_trigger_and_gate():
    from features.direction import micro_gate, micro_trigger
    cfg = _cfg()
    # свежий burst в сторону входа -> сильный триггер
    tr = micro_trigger(_fast_flow(burst_side="buy", burst_usd=10_000, cvd_accel=5000), 1, cfg)
    check("micro: burst по входу -> свежий триггер",
          tr["fresh"] and tr["score"] > 50, str(tr))
    # нет данных -> триггера нет, вход не по триггеру
    tr = micro_trigger({"subscribed": False}, 1, cfg)
    check("micro: без данных триггер пустой", not tr["fresh"] and tr["score"] == 0, str(tr))
    # устаревшая лента -> не свежий
    tr = micro_trigger(_fast_flow(trade_age=12.0), 1, cfg)
    check("micro: старая лента -> не свежий", not tr["fresh"], str(tr))
    # gate: sell-burst против лонга
    blocked, reason = micro_gate(_fast_flow(burst_side="sell", burst_usd=30_000), 1, cfg)
    check("micro: sell-burst блокирует лонг", blocked and "burst" in reason, reason)
    # gate: лента против (покупатели отсутствуют)
    blocked, reason = micro_gate(_fast_flow(buy_ratio_3=0.30), 1, cfg)
    check("micro: лента против -> блок лонга", blocked, reason)
    # gate: шорт при доминирующих покупках
    blocked, reason = micro_gate(_fast_flow(buy_ratio_3=0.70), -1, cfg)
    check("micro: покупки блокируют шорт", blocked, reason)
    # gate: нейтральный поток не блокирует
    blocked, reason = micro_gate(_fast_flow(buy_ratio_3=0.55), 1, cfg)
    check("micro: нейтральная лента не блокирует", not blocked, reason)
    # gate: без данных не блокирует
    blocked, reason = micro_gate({"subscribed": False}, 1, cfg)
    check("micro: без данных gate открыт", not blocked, reason)


def _micro_pos(side="long", entry=100.0, stop=96.0):
    from core.models import Position
    return Position(id="m1", symbol="BTC/USDT:USDT", side=side, entry=entry, qty=1.0,
                    stop_loss=stop, take_profit=None, strategy="test",
                    opened_at=time.time(),
                    features={"initial_stop_loss": stop, "initial_risk": abs(entry - stop)})


def _hold(state, seconds=2.1):
    """Форсирует удержание микро-сигнала (условие держится seconds подряд)."""
    state["against_since"] = time.time() - seconds


def test_micro_exit():
    from execution.engine import ExecutionEngine
    from learning.journal import TradeJournal
    cfg = _cfg()
    eng = ExecutionEngine(None, TradeJournal(), paper=True)
    # поток хороший -> не закрываем
    pos = _micro_pos()
    eng.positions[pos.id] = pos
    eng.prices[pos.symbol] = 101.0
    closed = eng.check_micro_exit(pos, _fast_flow(buy_ratio_5=0.60, cvd_3=2000), 101.0, cfg)
    check("micro-exit: хороший поток держит позицию", closed is None and pos.status == "open", str(closed))
    # тонкая лента: против-сигнал на 2-3 сделках — шум, не выходим
    pos = _micro_pos()
    eng.positions[pos.id] = pos
    eng.prices[pos.symbol] = 100.0
    eng.check_micro_exit(pos, _fast_flow(cvd_3=-100_000), 100.0, cfg)  # best = 100.0
    _hold(pos.features["micro_state"])
    closed = eng.check_micro_exit(pos, _fast_flow(cvd_3=-100_000, vol_5=500.0, vol_10=1500.0), 99.5, cfg)
    check("micro-exit: тонкая лента -> сигнал игнорируется",
          closed is None and pos.status == "open", str(closed))
    # CVD против + подтверждение ценой + удержание -> выход
    pos = _micro_pos()
    eng.positions[pos.id] = pos
    eng.prices[pos.symbol] = 101.0
    eng.check_micro_exit(pos, _fast_flow(cvd_3=-100_000), 101.0, cfg)
    _hold(pos.features["micro_state"])
    closed = eng.check_micro_exit(pos, _fast_flow(cvd_3=-100_000), 100.5, cfg)
    check("micro-exit: CVD против + подтверждение -> выход",
          closed is pos and pos.status == "closed" and "CVD" in pos.reason, getattr(pos, "reason", ""))
    # ratio против без подтверждения ценой -> только warning (уровень 1)
    pos = _micro_pos()
    eng.positions[pos.id] = pos
    eng.prices[pos.symbol] = 101.0
    closed = eng.check_micro_exit(pos, _fast_flow(buy_ratio_5=0.19), 101.0, cfg)
    check("micro-exit: ratio против без подтверждения ценой -> держим",
          closed is None and pos.status == "open", str(closed))
    # ratio против + подтверждение ценой + удержание -> выход
    pos = _micro_pos()
    eng.positions[pos.id] = pos
    eng.prices[pos.symbol] = 101.0
    eng.check_micro_exit(pos, _fast_flow(buy_ratio_5=0.19), 101.0, cfg)
    _hold(pos.features["micro_state"])
    closed = eng.check_micro_exit(pos, _fast_flow(buy_ratio_5=0.19), 100.5, cfg)
    check("micro-exit: ratio против + подтверждение -> выход",
          closed is pos and pos.status == "closed" and "buy_ratio" in pos.reason, pos.reason)
    # burst против + подтверждение ценой -> быстрый выход (burst_hold 0.25с)
    pos = _micro_pos()
    eng.positions[pos.id] = pos
    eng.prices[pos.symbol] = 101.0
    eng.check_micro_exit(pos, _fast_flow(burst_side="sell", burst_usd=50_000), 101.0, cfg)
    _hold(pos.features["micro_state"])
    closed = eng.check_micro_exit(pos, _fast_flow(burst_side="sell", burst_usd=50_000), 100.5, cfg)
    check("micro-exit: burst против + подтверждение -> выход",
          closed is pos and pos.status == "closed" and "burst" in pos.reason, pos.reason)
    # спред вздулся + подтверждение ценой -> выход
    pos = _micro_pos()
    eng.positions[pos.id] = pos
    eng.prices[pos.symbol] = 101.0
    eng.check_micro_exit(pos, _fast_flow(spread_slope=9.0), 101.0, cfg)
    _hold(pos.features["micro_state"])
    closed = eng.check_micro_exit(pos, _fast_flow(spread_slope=9.0), 100.5, cfg)
    check("micro-exit: спред вздулся + подтверждение -> выход",
          closed is pos and pos.status == "closed" and "спред" in pos.reason, pos.reason)
    # защита прибыли: best_r >= 0.5R -> выход не выполняется, стоп подтягивается к безубытку
    pos = _micro_pos()
    eng.positions[pos.id] = pos
    eng.prices[pos.symbol] = 103.0
    eng.check_micro_exit(pos, _fast_flow(cvd_3=-100_000), 103.0, cfg)  # best_r = 0.75
    closed = eng.check_micro_exit(pos, _fast_flow(cvd_3=-100_000), 102.5, cfg)
    check("micro-exit: прибыль 0.75R защищена — держим и ужесточаем стоп",
          closed is None and pos.status == "open" and pos.stop_loss > 96.0, str(getattr(pos, "reason", "")))
    # тайм-стоп 3м без прогресса (старая позиция, цена у входа)
    pos = _micro_pos()
    pos.opened_at = time.time() - 200
    eng.positions[pos.id] = pos
    closed = eng.check_micro_exit(pos, _fast_flow(buy_ratio_5=0.55), 100.1, cfg)
    check("micro-exit: 3м без прогресса -> тайм-стоп",
          closed is pos and pos.status == "closed" and "тайм-стоп" in pos.reason, pos.reason)
    # тайм-стоп 5м при слабом R
    pos = _micro_pos()
    pos.opened_at = time.time() - 320
    eng.positions[pos.id] = pos
    closed = eng.check_micro_exit(pos, _fast_flow(buy_ratio_5=0.55), 100.6, cfg)
    check("micro-exit: 5м слабый R -> тайм-стоп",
          closed is pos and pos.status == "closed" and "тайм-стоп" in pos.reason, pos.reason)
    # хороший прогресс переживает тайм-стоп 3м
    pos = _micro_pos()
    pos.opened_at = time.time() - 200
    eng.positions[pos.id] = pos
    eng.prices[pos.symbol] = 102.0
    closed = eng.check_micro_exit(pos, _fast_flow(buy_ratio_5=0.55), 102.0, cfg)
    check("micro-exit: 3м с прогрессом 2R держит позицию", closed is None, str(closed))
    # нет микро-данных -> не закрываем по микро
    pos = _micro_pos()
    eng.positions[pos.id] = pos
    closed = eng.check_micro_exit(pos, {"subscribed": False}, 101.0, cfg)
    check("micro-exit: без данных позиция держится", closed is None, str(closed))


def test_fast_pin():
    from data.flow import FastFeed
    feed = FastFeed(log=lambda *a, **k: None)
    feed.pin(["BTC/USDT:USDT", "ETH/USDT:USDT"])
    check("fast: pin регистрирует все символы",
          set(feed.pinned()) == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
          and feed.is_subscribed("BTC/USDT:USDT") and feed.is_subscribed("ETH/USDT:USDT"),
          str(feed.pinned()))
    feed.update_subscriptions(["SOL/USDT:USDT"])
    check("fast: pinned не снимаются", feed.is_subscribed("BTC/USDT:USDT"), str(feed.pinned()))
    feed.unpin(["BTC/USDT:USDT"])
    check("fast: unpin освобождает", "BTC/USDT:USDT" not in feed.pinned(), str(feed.pinned()))


def _klines_small_expansion():
    """Слабое движение: последние 2 свечи ~0.5 ATR (меньше порога 0.7, но проходят
    ослабленный порог при свежем микро-триггере)."""
    rows = []
    for i in range(27):
        rows.append([i * 60_000, 99.0, 99.1, 98.9, 99.0, 1000.0])
    for j in range(2):
        rows.append([rows[-1][0] + 60_000, 99.0, 99.1, 99.0, 99.08, 1000.0])
    return rows


def test_verdict_micro_trigger_weakens_no_expansion():
    from features.direction import direction_verdict
    cfg = _cfg()
    cfg.entry_min_bar_range_atr = 0.7
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    flow = _flow(short_usd=200_000)
    # без триггера: слабый размах свечей -> блок no_expansion
    verdict = direction_verdict(_prob(bull=80), _smc(), _klines_small_expansion(),
                                {"dom_pressure": 0.5}, depth, tape, cfg, flow=flow)
    check("micro-verdict: без триггера блок no_expansion",
          verdict["blocked"] and verdict["blocked_code"] == "no_expansion", verdict["blocked_reason"])
    # свежий сильный триггер ослабляет требование -> вход открыт
    flow["fast"] = _fast_flow(burst_side="buy", burst_usd=10_000, cvd_accel=5000)
    verdict = direction_verdict(_prob(bull=80), _smc(), _klines_small_expansion(),
                                {"dom_pressure": 0.5}, depth, tape, cfg, flow=flow)
    check("micro-verdict: свежий триггер ослабляет no_expansion",
          not verdict["blocked"], verdict["blocked_reason"])
    check("micro-verdict: триггер в вердикте", verdict.get("micro_trigger", {}).get("fresh"), str(verdict.get("micro_trigger")))
    check("micro-verdict: триггер добавил силы", verdict["strength"] >= 25, f"strength {verdict['strength']}")
    # старый триггер не ослабляет
    flow["fast"] = _fast_flow(burst_side="buy", burst_usd=10_000, trade_age=12.0)
    verdict = direction_verdict(_prob(bull=80), _smc(), _klines_small_expansion(),
                                {"dom_pressure": 0.5}, depth, tape, cfg, flow=flow)
    check("micro-verdict: старый триггер не ослабляет",
          verdict["blocked"] and verdict["blocked_code"] == "no_expansion", verdict["blocked_reason"])


def test_oi_price_alignment():
    from features.direction import flow_components
    cfg = _flow_cfg()
    # OI растёт, но цена идёт вниз -> ловушка против лонга
    comps = flow_components(_flow(oi_change=1.2), 1, cfg, price_move=-0.5)
    check("oi: рост OI против падающей цены -> ловушка",
          any(c["side"] == -1 and "против цены" in c["name"] for c in comps), str(comps))
    # OI растёт + цена вверх -> усиливает лонг
    comps = flow_components(_flow(oi_change=1.2), 1, cfg, price_move=0.5)
    check("oi: рост OI по тренду цены -> усиливает лонг",
          any(c["side"] == 1 and "по тренду" in c["name"] for c in comps), str(comps))
    # без цены (0) -> нейтрально-согласовано с 0, т.е. не ловушка
    comps = flow_components(_flow(oi_change=1.2), 1, cfg, price_move=0.0)
    check("oi: без цены -> не ловушка",
          not any("против цены" in c["name"] for c in comps), str(comps))


def _liq_flow(short_5=0.0, short_60=0.0, short_age=1.0):
    now = time.time()
    flow = _flow(short_usd=short_60)
    liq = flow["liq"]
    liq["windows"]["5"] = {"long_usd": 0.0, "short_usd": short_5,
                           "total_usd": short_5, "n": 2}
    liq["short_last_age_s"] = short_age
    liq["long_last_age_s"] = None
    liq["short_accel"] = (short_5 / max(short_60 / 12.0, 1e-9)) if short_60 > 0 else 0.0
    liq["long_accel"] = 0.0
    return flow


def test_liq_acceleration():
    from features.direction import flow_components
    cfg = _flow_cfg()
    # лавина: 5с объём в 4 раза выше среднего темпа -> усиленный вес (>20)
    accel = flow_components(_liq_flow(short_5=60_000, short_60=180_000), 1, cfg)
    fast_accel = next((c["weight"] for c in accel if c["side"] == 1), 0.0)
    check("liq: ускорение каскада усиливает вес",
          fast_accel >= 20.0, f"weight {fast_accel}")
    # без разгона — обычный вес каскада (< 20)
    no_accel = flow_components(_liq_flow(short_5=10_000, short_60=200_000), 1, cfg)
    base_w = next((c["weight"] for c in no_accel if c["side"] == 1), 0.0)
    check("liq: без разгона вес обычный", base_w < 20.0, f"weight {base_w}")


def test_btc_gate_in_verdict():
    from features.direction import btc_gate, direction_verdict
    cfg = _cfg()
    # BTC валится вниз -> блок лонга
    blocked, reason = btc_gate(_fast_flow(mid_slope=-0.3, book_age=0.5), 1, cfg)
    check("btc: падение BTC блокирует лонг", blocked and "BTC" in reason, reason)
    # BTC растёт -> блок шорта
    blocked, reason = btc_gate(_fast_flow(mid_slope=0.3, book_age=0.5), -1, cfg)
    check("btc: рост BTC блокирует шорт", blocked, reason)
    # слабое движение BTC -> не блокирует
    blocked, reason = btc_gate(_fast_flow(mid_slope=-0.05, book_age=0.5), 1, cfg)
    check("btc: малое движение не блокирует", not blocked, reason)
    # BTC по входа -> не блокирует
    blocked, reason = btc_gate(_fast_flow(mid_slope=0.3, book_age=0.5), 1, cfg)
    check("btc: BTC в сторону входа не блокирует", not blocked, reason)
    # нет данных -> не блокирует
    blocked, reason = btc_gate({"subscribed": False}, 1, cfg)
    check("btc: без данных не блокирует", not blocked, reason)
    # в вердикте: BTC против -> блок btc_against
    depth = orderbook_facts(wall_book())
    tape = tape_facts(make_trades(side="buy"))
    flow = _flow(short_usd=200_000)
    flow["btc"] = _fast_flow(mid_slope=-0.3, book_age=0.5)
    verdict = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                                {"dom_pressure": 0.5}, depth, tape, cfg, flow=flow)
    check("btc: в вердикте блок btc_against",
          verdict["blocked"] and verdict["blocked_code"] == "btc_against", verdict["blocked_reason"])
    # BTC в сторону входа -> вход проходит
    flow["btc"] = _fast_flow(mid_slope=0.3, book_age=0.5)
    verdict = direction_verdict(_prob(bull=80), _smc(), _klines_bull(),
                                {"dom_pressure": 0.5}, depth, tape, cfg, flow=flow)
    check("btc: BTC по входа -> вход открыт", verdict["side"] == "long" and not verdict["blocked"],
          verdict["blocked_reason"])


def test_stop_behind_bar():
    from strategies.facts_confluence import FactsConfluenceStrategy
    f = FactsConfluenceStrategy
    # лонг, бар дальше ATR-дистанции: стоп ЗА низом свечи + буфер ATR×0.25×1.5
    sl = f._stop_loss("long", 100.0, {}, {}, 0.5, atr_mult=1.5,
                      min_dist_pct=0.0, bar_extremes={"hi": 100.8, "lo": 99.2})
    check("stop: за низом свечи с буфером", abs(sl - (99.2 - 0.5 * 1.5 * 0.25)) < 1e-9, str(sl))
    # шорт: стоп ЗА верхом пробойной свечи
    sl = f._stop_loss("short", 100.0, {}, {}, 0.5, atr_mult=1.5,
                      min_dist_pct=0.0, bar_extremes={"hi": 100.8, "lo": 99.2})
    check("stop: за верхом свечи с буфером", abs(sl - (100.8 + 0.5 * 1.5 * 0.25)) < 1e-9, str(sl))
    # без бар-экстремумов — старое поведение (ATR)
    sl = f._stop_loss("long", 100.0, {}, {}, 0.5, atr_mult=1.5, min_dist_pct=0.0)
    check("stop: без свечи старый ATR-стоп", abs(sl - 99.25) < 1e-9, str(sl))
    # дальний бар -> стоп на баре с буфером
    sl = f._stop_loss("long", 100.0, {}, {}, 0.5, atr_mult=1.5, min_dist_pct=0.0,
                      bar_extremes={"hi": 100.8, "lo": 97.0})
    check("stop: дальний бар -> стоп на баре", abs(sl - (97.0 - 0.5 * 1.5 * 0.25)) < 1e-9, str(sl))
    # бар ближе ATR-дистанции -> ATR-стоп (дышит)
    sl = f._stop_loss("long", 100.0, {}, {}, 0.5, atr_mult=1.5, min_dist_pct=0.0,
                      bar_extremes={"hi": 100.8, "lo": 99.95})
    check("stop: близкий бар -> ATR-дистанция", abs(sl - 99.25) < 1e-9, str(sl))


def test_all():
    print("== orderbook facts ==")
    test_orderbook_facts()
    print("== tape facts ==")
    test_tape_facts()
    print("== icebergs ==")
    test_icebergs()
    print("== direction verdict ==")
    test_direction_verdict_agrees()
    test_direction_verdict_conflicts()
    test_direction_verdict_tape_filter()
    test_direction_verdict_tail_filter()
    test_direction_verdict_htf_hard_filter()
    test_direction_verdict_no_expansion()
    test_retest_verdict()
    test_rr_to_wall()
    test_absorption()
    test_delta_divergence()
    test_adaptive_atr_sl()
    test_atr_5m_wider()
    test_scale_out()
    test_blocked_codes()
    print("== flow (микроструктура) ==")
    test_flow_components()
    test_flow_in_verdict()
    test_flow_feed_aggregator()
    test_fast_feed_helpers()
    test_fast_pin()
    print("== micro (быстрый вход/выход) ==")
    test_micro_trigger_and_gate()
    test_micro_exit()
    test_verdict_micro_trigger_weakens_no_expansion()
    test_oi_price_alignment()
    test_liq_acceleration()
    test_btc_gate_in_verdict()
    test_stop_behind_bar()
    print("== exit ==")
    test_exit_reason()
    print("== 6h volatility filter ==")
    test_volatility_filter()
    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:", FAILED)
        raise SystemExit(1)


if __name__ == "__main__":
    test_all()