import numpy as np
import tempfile
from pathlib import Path

from core.models import MarketSnapshot
from core.models import Signal
from data.binance import BinanceClient
from execution.engine import ExecutionEngine
from features.order_book import indicator1_snapshot
from features.pipeline import FEATURE_NAMES, compute_bundle
from features.smc import indicator1_probability
from features.tradingview_zones import build_mtf_confluence, detect_supply_demand_zones, pine_atr
from learning.journal import TradeJournal
from strategies.confluence import SupplyDemandConfluenceStrategy


def _zone_rows(count=100):
    rows = []
    for index in range(count):
        high = 101.0
        low = 99.0
        if index == 55:
            high = 110.0
        rows.append([index * 60_000, 100.0, high, low, 100.0, 1000.0])
    return rows


def _flow_rows(count=40):
    rows = []
    for index in range(count):
        open_price = 100.0
        close = 101.0 if index % 2 == 0 else 99.0
        rows.append([index * 60_000, open_price, 102.0, 98.0, close, 10.0 + index])
    return rows


def test_supply_zone_matches_pine_buffer():
    rows = _zone_rows()
    zones = detect_supply_demand_zones(rows, "1m")
    supply = next(zone for zone in zones if zone.zone_type == "supply" and zone.pivot_index == 55)
    high = np.asarray([row[2] for row in rows], dtype=float)
    low = np.asarray([row[3] for row in rows], dtype=float)
    close = np.asarray([row[4] for row in rows], dtype=float)
    expected_atr = pine_atr(high, low, close, 50)[65]
    assert supply.top == 110.0
    assert abs(supply.width - expected_atr * 2.5 / 10.0) < 1e-12


def test_mtf_confluence_requires_two_timeframes():
    rows = _zone_rows()
    one = build_mtf_confluence({"1m": rows}, 100.0)
    assert one["confluences"] == []
    two = build_mtf_confluence({"1m": rows, "5m": rows}, 100.0)
    assert two["confluences"]
    assert all(zone["timeframe_count"] >= 2 for zone in two["confluences"])


def test_indicator1_dom_and_pipeline_use_only_two_sources():
    rows = _flow_rows()
    dom = indicator1_snapshot(rows)
    assert dom["ready"]
    assert dom["buy_volume"] == 0.0
    assert dom["sell_volume"] == rows[-1][5]
    assert dom["source"] == "indicator1_vap_dom"

    snapshot = MarketSnapshot(symbol="TEST/USDT", klines={"1m": rows, "5m": rows},
                              ticker={"last": rows[-1][4]}, last_price=rows[-1][4])
    bundle = compute_bundle(snapshot, timeframes=("1m", "5m"))
    assert len(bundle.values) == len(FEATURE_NAMES) == 24
    assert np.isfinite(bundle.values).all()
    assert set(bundle.raw) >= {"zones", "order_book", "near_demand", "near_supply"}


def test_confluence_signal_has_no_fixed_take_profit():
    rows = _zone_rows()
    snapshot = MarketSnapshot(symbol="TEST/USDT", klines={"1m": rows, "5m": rows},
                              ticker={"last": 100.0}, last_price=100.0)
    bundle = compute_bundle(snapshot, timeframes=("1m", "5m"))
    # The synthetic supply zone is far from price, so provide the near state
    # directly to isolate the signal's protection contract.
    bundle.raw["near_supply"] = {
        "type": "supply", "bottom": 100.0, "top": 101.0, "atr": 2.0,
        "distance_atr": 0.0, "timeframes": ("1m", "5m"), "timeframe_count": 2,
    }
    signal = SupplyDemandConfluenceStrategy(probability_threshold=0).signals(snapshot, bundle, None)[0]
    assert signal.take_profit is None


def test_mirror_short_from_bearish_dom():
    rows = _zone_rows()
    snapshot = MarketSnapshot(symbol="TEST/USDT", klines={"1m": rows, "5m": rows},
                              ticker={"last": 100.0}, last_price=100.0)
    bundle = compute_bundle(snapshot, timeframes=("1m", "5m"))
    bundle.raw["near_demand"] = {
        "type": "demand", "bottom": 99.5, "top": 100.5, "atr": 2.0,
        "distance_atr": 0.1, "timeframes": ("1m", "5m"), "timeframe_count": 2,
    }
    bundle.raw["near_supply"] = None
    bundle.raw["zones"]["confluences"] = []
    bundle.raw["order_book"]["dom_pressure"] = -0.8
    strategy = SupplyDemandConfluenceStrategy(mirror_enabled=True, mirror_dom_threshold=0.5, probability_threshold=0)
    signals = strategy.signals(snapshot, bundle, None)
    longs = [s for s in signals if s.side == "long"]
    shorts = [s for s in signals if s.side == "short"]
    assert len(longs) == 1
    assert len(shorts) == 1
    assert shorts[0].features.get("mirrored") is True
    assert shorts[0].features.get("zone_type") == "demand"
    assert shorts[0].take_profit is None


def test_mirror_long_from_bullish_dom_at_same_supply_wall():
    rows = _zone_rows()
    snapshot = MarketSnapshot(symbol="TEST/USDT", klines={"1m": rows, "5m": rows},
                              ticker={"last": 100.0}, last_price=100.0)
    bundle = compute_bundle(snapshot, timeframes=("1m", "5m"))
    bundle.raw["near_supply"] = {
        "type": "supply", "bottom": 100.0, "top": 101.0, "atr": 2.0,
        "distance_atr": 0.1, "timeframes": ("1m", "5m"), "timeframe_count": 2,
    }
    bundle.raw["near_demand"] = None
    bundle.raw["zones"]["confluences"] = []
    bundle.raw["order_book"]["dom_pressure"] = 0.9
    strategy = SupplyDemandConfluenceStrategy(mirror_enabled=True, mirror_dom_threshold=0.5, probability_threshold=0)
    signals = strategy.signals(snapshot, bundle, None)
    longs = [s for s in signals if s.side == "long"]
    shorts = [s for s in signals if s.side == "short"]
    assert len(shorts) == 1
    assert len(longs) == 1
    assert longs[0].features.get("mirrored") is True
    assert longs[0].features.get("zone_type") == "supply"


def test_mirror_disabled_keeps_original_candidate_only():
    rows = _zone_rows()
    snapshot = MarketSnapshot(symbol="TEST/USDT", klines={"1m": rows, "5m": rows},
                              ticker={"last": 100.0}, last_price=100.0)
    bundle = compute_bundle(snapshot, timeframes=("1m", "5m"))
    bundle.raw["near_demand"] = {
        "type": "demand", "bottom": 99.5, "top": 100.5, "atr": 2.0,
        "distance_atr": 0.1, "timeframes": ("1m", "5m"), "timeframe_count": 2,
    }
    bundle.raw["near_supply"] = None
    bundle.raw["order_book"]["dom_pressure"] = -0.8
    strategy = SupplyDemandConfluenceStrategy(mirror_enabled=False, probability_threshold=0)
    signals = strategy.signals(snapshot, bundle, None)
    assert [s.side for s in signals] == ["long"]


def test_disabled_take_profit_waits_for_ai_or_stop():
    with tempfile.TemporaryDirectory() as tmp:
        journal = TradeJournal(Path(tmp) / "t.db")
        engine = ExecutionEngine(None, journal, paper=True, take_profit_enabled=False)
        signal = Signal(symbol="TEST/USDT", side="long", entry=100.0, stop_loss=95.0,
                        take_profit=101.0, confidence=0.8, strategy="test", timeframe="1m")
        position = engine.open(signal, qty=1.0)
        assert position is not None
        assert engine.check_positions({"TEST/USDT": 102.0}) == []
        assert position.status == "open"
        engine.close(position.id, 95.0, "stop")
        journal.close()


def test_binance_universe_excludes_stablecoin_bases():
    class FakeExchange:
        markets = {
            "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT", "settle": "USDT", "contract": True, "swap": True, "active": True},
            "USDC/USDT:USDT": {"symbol": "USDC/USDT:USDT", "base": "USDC", "quote": "USDT", "settle": "USDT", "contract": True, "swap": True, "active": True},
            "ETH/BUSD:BUSD": {"symbol": "ETH/BUSD:BUSD", "base": "ETH", "quote": "BUSD", "settle": "BUSD", "contract": True, "swap": True, "active": True},
            "OLD/USDT:USDT": {"symbol": "OLD/USDT:USDT", "base": "OLD", "quote": "USDT", "settle": "USDT", "contract": True, "swap": True, "active": False},
        }

    client = BinanceClient.__new__(BinanceClient)
    client.futures = True
    client.ex = FakeExchange()
    assert client.discover_trade_symbols() == ["BTC/USDT:USDT"]


def test_binance_leverage_falls_back_to_contract_limit():
    class FakeExchange:
        def set_margin_mode(self, mode, symbol):
            return None

        def set_leverage(self, leverage, symbol):
            if leverage > 10:
                raise ValueError("invalid leverage")

    client = BinanceClient.__new__(BinanceClient)
    client.futures = True
    client.ex = FakeExchange()
    client.applied_leverage = {}
    client.leverage_limits = {"BOBUSDT": 10}
    client._leverage_limits_loaded = True
    assert client.set_leverage("BOB/USDT:USDT", 15)
    assert client.applied_leverage["BOB/USDT:USDT"] == 10


def _trend_rows(count=100, rising=True, base=100.0):
    rows = []
    close = base
    for index in range(count):
        open_price = close
        close = base + index * (0.5 if rising else -0.5)
        high = max(open_price, close) + 0.3
        low = min(open_price, close) - 0.3
        rows.append([index * 60_000, open_price, high, low, close, 1000.0])
    return rows


def _htf_rows(count=300, rising=True, base=100.0):
    rows = []
    close = base
    for index in range(count):
        open_price = close
        close = base + index * (0.5 if rising else -0.5)
        rows.append([index * 3_600_000, open_price, close + 0.2, close - 0.2, close, 1000.0])
    return rows


def test_indicator1_probability_rewards_bullish_setup():
    rows = _trend_rows(rising=True)
    prob = indicator1_probability(rows, _htf_rows(rising=True))
    assert prob["ready"]
    assert prob["bull_prob"] >= 60
    assert prob["bull_prob"] + prob["bear_prob"] == 100


def test_indicator1_probability_rewards_bearish_setup():
    rows = _trend_rows(rising=False)
    prob = indicator1_probability(rows, _htf_rows(rising=False))
    assert prob["ready"]
    assert prob["bear_prob"] >= 60


def test_entry_filtered_by_indicator_probability():
    rows = _zone_rows()
    snapshot = MarketSnapshot(symbol="TEST/USDT", klines={"1m": rows, "5m": rows},
                              ticker={"last": 100.0}, last_price=100.0)
    bundle = compute_bundle(snapshot, timeframes=("1m", "5m"))
    bundle.raw["near_demand"] = {
        "type": "demand", "bottom": 99.5, "top": 100.5, "atr": 2.0,
        "distance_atr": 0.1, "timeframes": ("1m", "5m"), "timeframe_count": 2,
    }
    bundle.raw["near_supply"] = {
        "type": "supply", "bottom": 100.0, "top": 101.0, "atr": 2.0,
        "distance_atr": 0.1, "timeframes": ("1m", "5m"), "timeframe_count": 2,
    }
    bundle.raw["order_book"]["dom_pressure"] = 0.0
    bundle.raw["indicator1_probability"] = {
        "ready": True, "bull_prob": 40, "bear_prob": 60,
        "reasons": ["HTF: бычий тренд", "Структура: нет BOS", "Зона: Premium", "Импульс: RSI < 50"],
    }
    strategy = SupplyDemandConfluenceStrategy(probability_threshold=55)
    sides = [s.side for s in strategy.signals(snapshot, bundle, None)]
    assert sides == ["short"]
    bundle.raw["indicator1_probability"] = dict(
        bundle.raw["indicator1_probability"], bull_prob=70, bear_prob=30)
    sides = [s.side for s in strategy.signals(snapshot, bundle, None)]
    assert sides == ["long"]
    signal = strategy.signals(snapshot, bundle, None)[0]
    assert signal.features["probability_bull"] == 70
    assert signal.features["probability_bear"] == 30
