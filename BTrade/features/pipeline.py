import math
import time
from dataclasses import dataclass, field

import numpy as np

from core.models import MarketSnapshot
from features.order_book import indicator1_snapshot
from features.smc import indicator1_probability, smc_snapshot
from features.tradingview_zones import build_mtf_confluence

FEATURE_NAMES = [
    "zone_demand_confluence", "zone_supply_confluence",
    "zone_demand_distance_atr", "zone_supply_distance_atr",
    "zone_demand_width_atr", "zone_supply_width_atr",
    "zone_demand_tf_1m", "zone_demand_tf_5m", "zone_demand_tf_15m",
    "zone_demand_tf_30m", "zone_demand_tf_1h",
    "zone_supply_tf_1m", "zone_supply_tf_5m", "zone_supply_tf_15m",
    "zone_supply_tf_30m", "zone_supply_tf_1h",
    "dom_pressure", "dom_buy_pct", "dom_delta_ratio", "dom_cvd",
    "dom_poc_distance_atr", "dom_value_area_position", "dom_wall_bid", "dom_wall_ask",
]

KLINE_TTL_SECONDS = {
    "1m": 30.0,
    "5m": 60.0,
    "15m": 180.0,
    "30m": 300.0,
    "1h": 600.0,
    "240m": 600.0,
}
INDICATOR1_TIMEFRAME = "1m"
INDICATOR_HTF_TIMEFRAME = "240m"


@dataclass
class FeatureBundle:
    symbol: str
    values: np.ndarray
    scores: dict
    raw: dict
    names: list = field(default_factory=lambda: FEATURE_NAMES)

    def to_dict(self) -> dict:
        return dict(zip(self.names, [float(v) if not math.isnan(v) else 0.0 for v in self.values]))


def _safe(x, default=0.0):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return default
    return float(x)


def _nearest_zone(zones: dict, key: str) -> dict:
    value = zones.get(key)
    return value if isinstance(value, dict) else {}


def _tf_flags(zone: dict, prefix: str) -> list[float]:
    timeframes = set(zone.get("timeframes", ())) if zone else set()
    return [1.0 if tf in timeframes else 0.0 for tf in ("1m", "5m", "15m", "30m", "1h")]


def compute_bundle(
    snap: MarketSnapshot,
    timeframes=None,
    zone_settings: dict = None,
    orderbook_settings: dict = None,
) -> FeatureBundle:
    """Build a feature bundle from only the two requested Pine indicators."""
    available = {str(tf): rows for tf, rows in (snap.klines or {}).items() if rows}
    price = float(snap.last_price or snap.ticker.get("last") or 0.0)
    if not price:
        for rows in available.values():
            if rows:
                price = float(rows[-1][4])
                break

    zone_settings = dict(zone_settings or {})
    configured_timeframes = zone_settings.pop("timeframes", None)
    zone_timeframes = timeframes or configured_timeframes or ("1m", "5m", "15m", "30m", "1h")
    zones = build_mtf_confluence(available, price, timeframes=zone_timeframes, **zone_settings)

    indicator1_tf = str((orderbook_settings or {}).get("source_timeframe") or INDICATOR1_TIMEFRAME)
    orderbook = indicator1_snapshot(
        available.get(indicator1_tf, []),
        **(orderbook_settings or {}),
    )
    smc = smc_snapshot(available.get(indicator1_tf, []), price)
    htf_tf = str((orderbook_settings or {}).get("htf_timeframe") or INDICATOR_HTF_TIMEFRAME)
    probability = indicator1_probability(
        available.get(indicator1_tf, []), available.get(htf_tf), price)
    demand = _nearest_zone(zones, "near_demand")
    supply = _nearest_zone(zones, "near_supply")
    demand_atr = float(demand.get("atr") or 0.0)
    supply_atr = float(supply.get("atr") or 0.0)
    poc = float(orderbook.get("poc") or price or 0.0)
    poc_atr = max(float(orderbook.get("step") or 0.0), 1e-12)
    total_volume = max(float(orderbook.get("profile_total_volume") or 0.0), 1.0)
    cvd = float(orderbook.get("cvd") or 0.0) / total_volume
    values = [
        float(demand.get("timeframe_count") or 0.0),
        float(supply.get("timeframe_count") or 0.0),
        float(demand.get("distance_atr") or 0.0),
        float(supply.get("distance_atr") or 0.0),
        float(demand.get("top", 0.0) - demand.get("bottom", 0.0)) / max(demand_atr, 1e-12) if demand else 0.0,
        float(supply.get("top", 0.0) - supply.get("bottom", 0.0)) / max(supply_atr, 1e-12) if supply else 0.0,
        *_tf_flags(demand, "demand"),
        *_tf_flags(supply, "supply"),
        float(orderbook.get("dom_pressure") or 0.0),
        float(orderbook.get("buy_pct") or 50.0) / 100.0,
        float(orderbook.get("delta_ratio") or 0.0),
        float(cvd),
        (price - poc) / poc_atr if poc else 0.0,
        float(orderbook.get("value_area_position") or 0.0),
        float(orderbook.get("wall_bid_size") or 0.0) / max(total_volume, 1.0),
        float(orderbook.get("wall_ask_size") or 0.0) / max(total_volume, 1.0),
    ]
    values = np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    demand_count = float(demand.get("timeframe_count") or 0.0)
    supply_count = float(supply.get("timeframe_count") or 0.0)
    zone_direction = 1.0 if demand and not supply else -1.0 if supply and not demand else 0.0
    scores = {
        # These are decision scores, not extra indicators.
        "trend": zone_direction * max(demand_count, supply_count),
        "confluence": max(demand_count, supply_count),
        "orderbook": float(orderbook.get("dom_pressure") or 0.0),
    }
    raw = {
        "price": price,
        "zones": zones,
        "zone_confluences": zones.get("confluences", []),
        "near_demand": demand or None,
        "near_supply": supply or None,
        "order_book": orderbook,
        "indicator1": orderbook,
        "indicator1_timeframe": indicator1_tf,
        "smc": smc,
        "indicator1_probability": probability,
        "indicator_htf_timeframe": htf_tf,
        "indicator_klines": available.get(indicator1_tf) or [],
        "indicator_klines_5m": available.get("5m") or [],
        "zone_direction": zone_direction,
        "zone_timeframes": list(zone_timeframes),
    }
    return FeatureBundle(symbol=snap.symbol, values=values, scores=scores, raw=raw)


def _tf_minutes(timeframe: str) -> int:
    value = str(timeframe or "").lower()
    if value.endswith("m"):
        return int(value[:-1] or 0)
    if value.endswith("h"):
        return int(value[:-1] or 0) * 60
    return int(value) if value.isdigit() else 0


def assemble_snapshot(client, symbol, kline_cache: dict, tf_list: list,
                      indicator1_timeframe: str = INDICATOR1_TIMEFRAME,
                      htf_timeframe: str = INDICATOR_HTF_TIMEFRAME) -> MarketSnapshot:
    klines = {}
    now = time.time()
    fetch_timeframes = list(dict.fromkeys(
        [indicator1_timeframe, *(tf_list or []), htf_timeframe]
    ))
    for tf in fetch_timeframes:
        key = f"{symbol}|{tf}"
        cached = kline_cache.get(key)
        ttl = KLINE_TTL_SECONDS.get(tf, 180.0)
        if cached is None or now - cached[0] >= ttl:
            kline_cache[key] = (now, client.fetch_klines(symbol, tf, limit=300))
        klines[tf] = kline_cache[key][1]
    ticker = client.fetch_ticker(symbol)
    k15 = klines.get("15m") or []
    first_tf = next((tf for tf in fetch_timeframes if klines.get(tf)), None)
    first_rows = klines.get(first_tf, []) if first_tf else k15
    last_price = ticker.get("last") or (float(first_rows[-1][4]) if first_rows else 0.0)
    return MarketSnapshot(
        symbol=symbol, klines=klines, ticker=ticker, last_price=last_price,
    )
