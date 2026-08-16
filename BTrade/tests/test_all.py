import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_LOG_FILE", str(Path(tempfile.gettempdir()) / "omni_trading_tests.log"))

import numpy as np

from core.config import Config
from core.models import MarketSnapshot, Position, Signal
from execution.engine import ExecutionEngine
from features import indicators as ind
from features import smc as smc_mod
from features import volume_profile as vp
from features.pipeline import FEATURE_NAMES, compute_bundle
from learning.journal import TradeJournal
from ml.neural import MLP
from ml.trainer import Trainer
from risk.manager import RiskManager

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


def make_klines(n=120, start=100.0, drift=0.05, vol=0.5):
    ts = int(time.time() * 1000) - n * 60000
    out = []
    price = start
    for i in range(n):
        price += drift + vol * np.random.default_rng(i).normal()
        o = price
        h = price + 1.0
        l = price - 1.0
        c = price + 0.2
        v = 1000.0 + i % 7 * 100
        out.append([ts + i * 60000, o, h, l, c, v])
    return out


def make_trades(n=50, base_ts=0):
    out = []
    for i in range(n):
        side = "buy" if i % 2 == 0 else "sell"
        out.append({"price": 100.0, "amount": 1.0, "side": side, "ts": base_ts + i * 1000})
    return out


def make_snapshot(klines=None, orderbook=None, trades=None):
    klines = klines or {"15m": make_klines()}
    return MarketSnapshot(
        symbol="BTC/USDT", klines=klines,
        ticker={"last": 100.0, "change24h": 1.0}, orderbook=orderbook or {"bids": [], "asks": []},
        trades=trades or make_trades(),
        funding_rate=0.0001, open_interest=1e6, last_price=100.0,
        sentiment={"fear_greed": 55.0, "avg_funding": 0.0001}, onchain={"market_cap_rank": 1, "price_change_24h": 1.0},
    )


def test_indicators():
    c = [float(x) for x in range(100, 200)]
    r = ind.last(ind.rsi(c))
    check("rsi trend-up > 50", r > 50, f"rsi={r}")
    m = ind.last(ind.macd(c)[2])
    check("macd hist > 0 uptrend", m > 0, f"hist={m}")
    b = ind.bollinger(c)
    check("bollinger upper > lower", ind.last(b[1]) > ind.last(b[2]))


def test_smc():
    k = make_klines(n=120, drift=0.1)
    o = [k[i][1] for i in range(len(k))]
    h = [k[i][2] for i in range(len(k))]
    l = [k[i][3] for i in range(len(k))]
    c = [k[i][4] for i in range(len(k))]
    v = [k[i][5] for i in range(len(k))]
    res = smc_mod.smc_analysis(o, h, l, c, v, c[-1])
    check("smc returns keys", all(kk in res for kk in ["structure", "n_fvgs", "nearest_fvg"]))
    fvgs = smc_mod.find_fvgs(o, h, l, c)
    for f in fvgs:
        if f["type"] == "bull":
            check("bull fvg geometry", f["bottom"] < f["top"], str(f))


def test_volume_profile():
    closes = [100 + i * 0.5 for i in range(50)] + [120] * 50
    vols = [10.0] * 100
    p = vp.volume_profile(closes, vols)
    check("profile POC exists", "poc" in p and p["poc"] > 0)
    check("profile ordering", p["val"] <= p["poc"] <= p["vah"], str(p))
    tr = make_trades(20)
    slope = vp.cvd_slope_metric(tr)
    check("cvd slope in [-1,1]", -1.0 <= slope <= 1.0, f"slope={slope}")
    tr_buy = [{"price": 1, "amount": 1, "side": "buy", "ts": i} for i in range(10)]
    s = vp.cvd_slope_metric(tr_buy)
    check("cvd slope positive for buys", s > 0.9, f"s={s}")
    ob = {"bids": [[100, 10], [99, 5]], "asks": [[101, 5], [102, 10]]}
    imb = vp.imbalance(ob)
    check("imbalance computes", imb == 0.0, f"imb={imb}")
    ob3 = {"bids": [[100, 10, 3]], "asks": [[101, 5, 2]]}
    check("imbalance handles 3-elem rows", abs(vp.imbalance(ob3) - 1/3) < 1e-9, f"{vp.imbalance(ob3)}")


def test_pipeline():
    snap = make_snapshot()
    b = compute_bundle(snap)
    check("bundle has 24 values", len(b.values) == 24, f"len={len(b.values)}")
    check("bundle values finite", np.isfinite(b.values).all())
    d = b.to_dict()
    check("to_dict 24 keys", len(d) == 24 and len(set(d) & set(FEATURE_NAMES)) == 24)
    check("scores present", "trend" in b.scores)


def test_journal_and_training():
    with tempfile.TemporaryDirectory() as tmp:
        j = TradeJournal(Path(tmp) / "t.db")
        feats = {n: 0.1 for n in FEATURE_NAMES}
        for k in range(20):
            pos = Position(id=f"p{k}", symbol="BTC/USDT", side="long", entry=100, qty=1,
                           stop_loss=90, take_profit=120, strategy="test", opened_at=time.time(), features=feats)
            j.open_trade(pos)
            pos.realized_pnl = 2.0 if k % 2 == 0 else -1.0
            j.close_trade(pos, 105, "TP")
        samples = j.training_samples()
        check("training samples aligned to 24", all(len(s["features"]) == 24 for s in samples), f"lens={set(len(s['features']) for s in samples)}")
        check("closed_count", j.closed_count() == 20)
        net = MLP(24)
        t = Trainer(j, net, min_samples=10)
        r = t.retrain()
        check("retrain runs", r["status"] == "ok", str(r))
        p = t.filter_signal(feats)
        check("filter in [0,1]", 0.0 <= p <= 1.0, f"p={p}")
        j.close()

        j_fee = TradeJournal(Path(tmp) / "fees.db")
        fee_pos = Position(id="fee", symbol="BTC/USDT", side="long", entry=100, qty=1,
                           stop_loss=99, take_profit=103, strategy="test", opened_at=time.time(),
                           features={"initial_stop_loss": 99}, fees=0.02)
        j_fee.open_trade(fee_pos)
        fee_pos.realized_pnl = 0.98
        j_fee.close_external(fee_pos, exit_price=101, pnl=0.98, fees=0.03,
                             reason="Тейк-профит Binance")
        row = j_fee.conn.execute("SELECT exit, pnl, fees, reason FROM trades WHERE id='fee'").fetchone()
        check("journal: external close stores price, pnl and fees", row == (101.0, 0.98, 0.03, "Тейк-профит Binance"), str(row))
        j_fee.close()


def test_risk_and_engine():
    cfg = Config()
    cfg.equity = 1000.0
    cfg.paper_no_daily_limit = False
    cfg.no_daily_limit = False
    r = RiskManager(cfg)
    sig = Signal(symbol="BTC/USDT", side="long", entry=100, stop_loss=99, take_profit=103,
                 confidence=0.7, strategy="test", timeframe="15m")
    ok, _ = r.can_open(sig, [])
    check("risk allows", ok)
    qty = r.position_size(sig)
    check("size > 0", qty > 0, f"qty={qty}")
    ok2, reason = r.can_open(sig, [Position(id="x", symbol="BTC/USDT", side="long", entry=1, qty=1,
                                            stop_loss=1, take_profit=2, strategy="test", opened_at=0)])
    check("risk blocks same symbol", not ok2, reason)
    r.record_pnl(-60.0)
    check("kill switch after -6%", r.is_killed)
    check("equity tracked", abs(r.equity - 940.0) < 1e-9, f"eq={r.equity}")

    j = TradeJournal(Path(tempfile.gettempdir()) / "trades_engine_test.db")
    eng = ExecutionEngine(None, j, paper=True, on_close=r.record_pnl)
    pos = eng.open(sig, 1.0)
    check("paper open", pos is not None)
    closed = eng.check_positions({"BTC/USDT": 98.5})
    check("SL close", len(closed) == 1 and closed[0].realized_pnl < 0, f"pnl={closed[0].realized_pnl if closed else 'none'}")

    j_trail = TradeJournal(Path(tempfile.gettempdir()) / "trades_smart_trail_test.db")
    eng_trail = ExecutionEngine(None, j_trail, paper=True, trail_enabled=True,
                                trail_break_even_r=0.8, trail_start_r=1.25, trail_distance_r=0.75)
    trail_pos = eng_trail.open(sig, 1.0)
    eng_trail.check_positions({"BTC/USDT": 100.81})
    check("smart exit: moves to break-even", trail_pos.stop_loss > 100.0, f"sl={trail_pos.stop_loss}")
    eng_trail.check_positions({"BTC/USDT": 101.5})
    check("smart exit: locks profit after 1.25R", trail_pos.stop_loss > 100.5, f"sl={trail_pos.stop_loss}")
    j_trail.close()

    cfg2 = Config()
    cfg2.equity = 1000.0
    cfg2.max_leverage = 10.0
    cfg2.max_margin_frac = 1.0
    cfg2.entry_position_pct = 40.0
    r2 = RiskManager(cfg2)
    tight = Signal(symbol="BTC/USDT", side="long", entry=100, stop_loss=99.9, take_profit=102,
                   confidence=0.7, strategy="test", timeframe="1m")
    qty = r2.position_size(tight)
    check("entry size: номинал = 40% депозита × плечо", abs(qty * 100 - 4000) < 1e-4,
          f"qty={qty} notional={qty*100}")
    check("entry size: риск мал при 40% номинала × плечо", qty * 0.1 < 8.0, f"risk={qty*0.1}")
    r2.cfg.min_notional = 5.0
    tiny = Signal(symbol="BTC/USDT", side="long", entry=100, stop_loss=99, take_profit=103,
                  confidence=0.7, strategy="test", timeframe="1m")
    r2.set_equity(4.99)
    r2.cfg.max_wallet_usage = 1.0
    r2.cfg.max_margin_frac = 1.0
    r2.cfg.max_leverage = 1.0
    ok3, reason3 = r2.can_open(tiny, [])
    check("entry size: номиал < минимума биржи блокирует", not ok3 and "минимума" in reason3, reason3)
    eng2 = ExecutionEngine(None, j, paper=True)
    grid_sig = Signal(symbol="BTC/USDT", side="long", entry=95.0, stop_loss=50.0, take_profit=150.0,
                      confidence=0.55, strategy="grid", timeframe="range")
    n = eng2.place_grid("BTC/USDT", [grid_sig, grid_sig], lambda s: 0.5)
    check("grid placed", n == 2)
    eng2.check_grid({"BTC/USDT": 94.0})
    check("grid filled and legs tracked", len(eng2.grid_legs.get("BTC/USDT", [])) == 2, str(eng2.grid_legs))
    eng2.check_grid({"BTC/USDT": 45.0})
    check("grid SL closed legs", len(eng2.grid_legs.get("BTC/USDT", [])) == 0, str(eng2.grid_legs))

    class FlatClient:
        def position_size_checked(self, symbol, side):
            return 0.0

    j3 = TradeJournal(Path(tempfile.gettempdir()) / "trades_reconcile_test.db")
    eng3 = ExecutionEngine(FlatClient(), j3, paper=False)
    pos3 = Position(id="external", symbol="BTC/USDT:USDT", side="long", entry=100, qty=1,
                    stop_loss=99, take_profit=103, strategy="impulse", opened_at=time.time())
    eng3.positions[pos3.id] = pos3
    j3.open_trade(pos3)
    external = eng3.reconcile_live_positions()
    check("reconcile: manual close removed", len(external) == 1 and not eng3.positions and not j3.open_trades())
    j3.close()


def test_impulse_strategy():
    from features.pipeline import FeatureBundle
    from strategies.impulse import ImpulseStrategy

    def make_snapshot(k5):
        return MarketSnapshot(
            symbol="TEST/USDT:USDT", klines={"5m": k5}, ticker={"change24h": 2.0},
            orderbook={"bids": [[102.3, 5000], [102.2, 5000]], "asks": [[102.5, 1000], [102.6, 1000]]},
            trades=[], funding_rate=0.0, open_interest=None, last_price=k5[-1][4],
        )

    def make_klines(breakout_vol, breakout_move=1.3):
        ts = int(time.time() * 1000) - 36 * 300000
        k = []
        for i in range(15):
            k.append([ts + i * 300000, 100.0, 102.0, 98.0, 100.0 + (i % 3) * 0.1, 1000.0])
        base = 101.0
        for i in range(25):
            k.append([ts + (15 + i) * 300000, base, base + 0.1, base - 0.1, base + 0.05, 1000.0])
        k.append([ts + 40 * 300000, base + 0.1, base + 0.1 + breakout_move,
                  base + 0.05, base + breakout_move, breakout_vol])
        return k

    strat = ImpulseStrategy()
    bundle = FeatureBundle(symbol="TEST/USDT:USDT", values=[], scores={}, raw={"cvd_slope": 0.06})

    sigs = strat.signals(make_snapshot(make_klines(6000.0)), bundle, None)
    longs = [s for s in sigs if s.side == "long"]
    check("impulse: лонг на пробое сжатия", len(longs) == 1, str(sigs))
    if longs:
        s = longs[0]
        check("impulse: вход по цене пробоя", abs(s.entry - (101.0 + 1.3)) < 1e-9, f"entry={s.entry}")
        check("impulse: SL за уровнем пробоя", s.stop_loss < 101.1, f"sl={s.stop_loss}")
        check("impulse: TP выше входа", s.take_profit > s.entry, f"tp={s.take_profit}")

    no_vol = strat.signals(make_snapshot(make_klines(1000.0)), bundle, None)
    check("impulse: без объёма нет сигнала", len(no_vol) == 0, str(no_vol))

    late = strat.signals(make_snapshot(make_klines(6000.0, breakout_move=3.0)), bundle, None)
    check("impulse: поздний расширенный вход отсекается", len(late) == 0, str(late))


def test_binance_position_confirmation():
    from data.binance import BinanceClient

    class FakeExchange:
        def fetch_positions(self, symbols):
            return [{"contracts": 1.0, "side": "short"}]

    client = BinanceClient.__new__(BinanceClient)
    client.ex = FakeExchange()
    check("binance: sell подтверждается как short", client._wait_position("WLD/USDT:USDT", "sell", 0.01))

    class AlgoExchange:
        def market(self, symbol):
            return {"id": "DOGEUSDT"}

        def price_to_precision(self, symbol, price):
            return f"{price:.4f}"

        def fapiPrivatePostAlgoOrder(self, params):
            self.params = params
            return {"algoId": "1"}

    algo_client = BinanceClient.__new__(BinanceClient)
    algo_client.ex = AlgoExchange()
    algo_client._place_protect_order("DOGE/USDT:USDT", "sell", 10, "STOP_MARKET", 0.07)
    check("binance: protection uses Algo Order API",
          algo_client.ex.params["algoType"] == "CONDITIONAL"
          and algo_client.ex.params["closePosition"] == "true"
          and algo_client.ex.params["type"] == "STOP_MARKET", str(algo_client.ex.params))


def test_market_committee():
    from features.pipeline import FeatureBundle
    from strategies.meta import market_consensus, market_exit_reason

    bundle = FeatureBundle(
        symbol="TEST/USDT:USDT", values=[],
        scores={"trend": 12.0, "momentum": 0.4},
        raw={"structure": "bullish", "cvd_slope": 0.08, "ob_imbalance": 0.2, "ob_depth_ratio": 1.2,
             "vol_ratio": 2.0},
    )
    sig = Signal(symbol="TEST/USDT:USDT", side="long", entry=100, stop_loss=99,
                 take_profit=104, confidence=0.8, strategy="impulse", timeframe="5m",
                 features={"impulse_volume_ratio": 2.0, "impulse_expansion_atr": 1.5})
    ok, votes, _ = market_consensus(sig, bundle)
    check("market committee: aligned long passes", ok and votes >= 4)

    opposite = FeatureBundle(symbol="TEST/USDT:USDT", values=[], scores={"trend": -15.0, "momentum": -0.4},
                             raw={"structure": "bearish", "cvd_slope": -0.1, "ob_imbalance": -0.2,
                                  "ob_depth_ratio": 0.7, "vol_ratio": 2.0})
    ok2, _, _ = market_consensus(sig, opposite)
    check("market committee: opposing market blocks", not ok2)
    pos = Position(id="committee", symbol="TEST/USDT:USDT", side="long", entry=100, qty=1,
                   stop_loss=99, take_profit=110, strategy="impulse", opened_at=0,
                   features={"initial_risk": 1.0, "initial_stop_loss": 99})
    check("market committee: profitable reversal exits", market_exit_reason(pos, opposite, 1.0) is not None)


def test_signal_feedback_loop():
    with tempfile.TemporaryDirectory() as tmp:
        j = TradeJournal(Path(tmp) / "sig.db")
        feats = {n: 0.1 + 0.01 * i for i, n in enumerate(FEATURE_NAMES)}
        for k in range(30):
            ts_old = time.time() - 2000
            j.conn.execute(
                "INSERT INTO signals_log (ts, symbol, side, strategy, entry, stop_loss, features) VALUES (?,?,?,?,?,?,?)",
                (ts_old, "BTC/USDT", "long", "scalping", 100.0, 98.0, __import__("json").dumps(feats)))
        j.conn.commit()
        n = j.update_signal_outcomes({"BTC/USDT": 102.0})
        check("сигналы размечены исходом", n == 30, f"n={n}")
        labels = {s["label"] for s in j.signal_training_samples()}
        check("все сигналы успешны (ход +2% при риске 2%)", labels == {1.0}, str(labels))

        j2 = TradeJournal(Path(tmp) / "sig2.db")
        for k in range(30):
            j2.conn.execute(
                "INSERT INTO signals_log (ts, symbol, side, strategy, entry, stop_loss, features) VALUES (?,?,?,?,?,?,?)",
                (time.time() - 2000, "BTC/USDT", "long", "scalping", 100.0, 98.0, __import__("json").dumps(feats)))
        j2.conn.commit()
        n2 = j2.update_signal_outcomes({"BTC/USDT": 98.5})
        check("исходы без движения: 0", n2 == 30 and all(s["label"] == 0.0 for s in j2.signal_training_samples()), f"n={n2}")

        net = MLP(24)
        t = Trainer(j2, net, min_samples=10)
        r = t.retrain()
        check("переобучение на сигналах", r["status"] == "ok", str(r))
        p = t.filter_signal(feats)
        check("фильтр после сигналов в [0,1]", 0.0 <= p <= 1.0, f"p={p}")
        check("нормировщик сохранён", net.mean.shape == (24,) and net.std.shape == (24,))
        j.close()
        j2.close()


def test_cooldown_and_purge():
    j = TradeJournal(Path(tempfile.gettempdir()) / "trades_cd_test.db")
    eng = ExecutionEngine(None, j, paper=True)
    sig = Signal(symbol="BTC/USDT", side="long", entry=100, stop_loss=99, take_profit=103,
                 confidence=0.7, strategy="test", timeframe="15m")
    pos = eng.open(sig, 1.0)
    check("cooldown: позиция открыта", pos is not None)
    check("cooldown: не активен до SL", not eng.sl_cooldown_active("BTC/USDT", "long"))
    eng.close(eng.open(sig, 1.0).id, 103.0, "Тейк-профит")
    check("cooldown: TP не включает кулдаун", not eng.sl_cooldown_active("BTC/USDT", "long"))
    eng.close(pos.id, 99.0, "Стоп-лосс")
    check("cooldown: активен после SL", eng.sl_cooldown_active("BTC/USDT", "long"))

    j2 = TradeJournal(Path(tempfile.gettempdir()) / "trades_purge_test.db")
    pos_a = Position(id="a", symbol="OLD/USDT", side="long", entry=1, qty=1, stop_loss=0.5,
                     take_profit=2, strategy="test", opened_at=time.time())
    pos_b = Position(id="b", symbol="NEW/USDT:USDT", side="long", entry=1, qty=1, stop_loss=0.5,
                     take_profit=2, strategy="test", opened_at=time.time())
    j2.open_trade(pos_a)
    j2.open_trade(pos_b)
    n = j2.purge_stale_open(["NEW/USDT:USDT"])
    check("purge: удалён призрак старого символа", n == 1, f"n={n}")
    left = j2.open_trades()
    check("purge: активный символ остался", [t["symbol"] for t in left] == ["NEW/USDT:USDT"], str(left))
    j.close()
    j2.close()


def test_all():
    print("== indicators ==")
    test_indicators()
    print("== smc ==")
    test_smc()
    print("== volume profile ==")
    test_volume_profile()
    print("== pipeline ==")
    test_pipeline()
    print("== journal/training ==")
    test_journal_and_training()
    print("== risk/engine ==")
    test_risk_and_engine()
    print("== impulse strategy ==")
    test_impulse_strategy()
    print("== Binance position confirmation ==")
    test_binance_position_confirmation()
    print("== market committee ==")
    test_market_committee()
    print("== cooldown/purge ==")
    test_cooldown_and_purge()
    print("== signal feedback loop ==")
    test_signal_feedback_loop()
    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:", FAILED)
        sys.exit(1)


if __name__ == "__main__":
    test_all()
