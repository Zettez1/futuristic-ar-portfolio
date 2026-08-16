"""TradingView-compatible supply/demand zones from ``Indicator.txt``.

The implementation intentionally mirrors the Pine script's stateful order:
confirmed pivots create zones, nearby zones are rejected, and broken zones
are removed on the same bar.  No trend, momentum, or unrelated indicator is
used here.
"""

from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class SupplyDemandZone:
    timeframe: str
    zone_type: str
    top: float
    bottom: float
    poi: float
    atr: float
    pivot_index: int
    pivot_time: int

    @property
    def width(self) -> float:
        return self.top - self.bottom

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "type": self.zone_type,
            "top": float(self.top),
            "bottom": float(self.bottom),
            "poi": float(self.poi),
            "width": float(self.width),
            "atr": float(self.atr),
            "pivot_index": int(self.pivot_index),
            "pivot_time": int(self.pivot_time),
        }


def _as_float_array(values: Iterable[float]) -> np.ndarray:
    return np.asarray(list(values), dtype=float)


def pine_rma(values: Iterable[float], length: int) -> np.ndarray:
    """Wilder RMA with Pine's SMA seed."""
    values = _as_float_array(values)
    out = np.full(len(values), np.nan, dtype=float)
    if length <= 0 or len(values) < length:
        return out
    for i in range(length - 1, len(values)):
        if i == length - 1:
            seed = values[:length]
            if not np.isfinite(seed).all():
                continue
            out[i] = float(seed.mean())
        elif np.isfinite(values[i]) and np.isfinite(out[i - 1]):
            out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def pine_atr(high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int = 50) -> np.ndarray:
    """Equivalent of ``ta.atr(length)`` for OHLC arrays."""
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    tr = np.full(len(high), np.nan, dtype=float)
    if len(high) == 0:
        return tr
    tr[0] = high[0] - low[0]
    if len(high) > 1:
        tr[1:] = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )
    return pine_rma(tr, length)


def _pivot_high(values: np.ndarray, pivot: int, left: int, right: int) -> bool:
    if pivot < left or pivot + right >= len(values):
        return False
    window = values[pivot - left:pivot + right + 1]
    return np.isfinite(values[pivot]) and values[pivot] == np.nanmax(window)


def _pivot_low(values: np.ndarray, pivot: int, left: int, right: int) -> bool:
    if pivot < left or pivot + right >= len(values):
        return False
    window = values[pivot - left:pivot + right + 1]
    return np.isfinite(values[pivot]) and values[pivot] == np.nanmin(window)


def _zone_overlaps_poi(new_poi: float, zones: list, atr: float) -> bool:
    """Port of f_check_overlapping(): existing POI +/- 2 * ATR."""
    threshold = atr * 2.0
    for zone in zones:
        if new_poi >= zone.poi - threshold and new_poi <= zone.poi + threshold:
            return True
    return False


def _add_zone(zones: list, zone: SupplyDemandZone, history: int) -> None:
    if _zone_overlaps_poi(zone.poi, zones, zone.atr):
        return
    zones.insert(0, zone)
    del zones[history:]


def _remove_broken(zones: list, close: float, zone_type: str) -> None:
    if zone_type == "supply":
        zones[:] = [zone for zone in zones if close < zone.top]
    else:
        zones[:] = [zone for zone in zones if close > zone.bottom]


def detect_supply_demand_zones(
    klines: list,
    timeframe: str,
    swing_length: int = 10,
    history: int = 20,
    box_width: float = 2.5,
    current_price: float = None,
) -> list[SupplyDemandZone]:
    """Reproduce the active zones from the Supply/Demand POI Pine section."""
    if not klines or swing_length < 1 or history < 1:
        return []
    try:
        opens = np.asarray([float(row[1]) for row in klines], dtype=float)
        highs = np.asarray([float(row[2]) for row in klines], dtype=float)
        lows = np.asarray([float(row[3]) for row in klines], dtype=float)
        closes = np.asarray([float(row[4]) for row in klines], dtype=float)
        times = np.asarray([int(row[0]) for row in klines], dtype=np.int64)
    except (TypeError, ValueError, IndexError):
        return []

    if len(closes) < swing_length * 2 + 1:
        return []

    del opens  # The Pine zone section does not use open prices.
    atr = pine_atr(highs, lows, closes, 50)
    supply: list[SupplyDemandZone] = []
    demand: list[SupplyDemandZone] = []

    # ta.pivothigh/low becomes available on the confirmation bar pivot+R.
    for confirmation_index in range(len(closes)):
        pivot_index = confirmation_index - swing_length
        if pivot_index >= swing_length and np.isfinite(atr[confirmation_index]):
            atr_value = float(atr[confirmation_index])
            buffer = atr_value * (box_width / 10.0)
            if _pivot_high(highs, pivot_index, swing_length, swing_length):
                top = float(highs[pivot_index])
                zone = SupplyDemandZone(
                    timeframe=timeframe,
                    zone_type="supply",
                    top=top,
                    bottom=top - buffer,
                    poi=top - buffer / 2.0,
                    atr=atr_value,
                    pivot_index=pivot_index,
                    pivot_time=int(times[pivot_index]),
                )
                _add_zone(supply, zone, history)
            elif _pivot_low(lows, pivot_index, swing_length, swing_length):
                bottom = float(lows[pivot_index])
                zone = SupplyDemandZone(
                    timeframe=timeframe,
                    zone_type="demand",
                    top=bottom + buffer,
                    bottom=bottom,
                    poi=bottom + buffer / 2.0,
                    atr=atr_value,
                    pivot_index=pivot_index,
                    pivot_time=int(times[pivot_index]),
                )
                _add_zone(demand, zone, history)

        # f_sd_to_bos() is called after a possible new zone on every bar.
        _remove_broken(supply, float(closes[confirmation_index]), "supply")
        _remove_broken(demand, float(closes[confirmation_index]), "demand")

    if current_price is not None and np.isfinite(current_price):
        _remove_broken(supply, float(current_price), "supply")
        _remove_broken(demand, float(current_price), "demand")
    return supply + demand


def _tf_minutes(timeframe: str) -> int:
    value = str(timeframe or "").strip().lower()
    if value.endswith("m"):
        return int(value[:-1] or 0)
    if value.endswith("h"):
        return int(value[:-1] or 0) * 60
    if value.endswith("d"):
        return int(value[:-1] or 0) * 1440
    if value.isdigit():
        return int(value)
    return 0


def _interval_overlap(a: SupplyDemandZone, b: SupplyDemandZone) -> bool:
    return a.zone_type == b.zone_type and a.timeframe != b.timeframe and max(a.bottom, b.bottom) <= min(a.top, b.top)


def _make_confluence(zones: list[SupplyDemandZone], minimum_timeframes: int) -> list[dict]:
    candidates = []
    for i, first in enumerate(zones):
        for second in zones[i + 1:]:
            if not _interval_overlap(first, second):
                continue
            bottom = max(first.bottom, second.bottom)
            top = min(first.top, second.top)
            selected = [first, second]
            selected_tfs = {first.timeframe, second.timeframe}
            for extra in zones:
                if extra.timeframe in selected_tfs or extra.zone_type != first.zone_type:
                    continue
                new_bottom = max(bottom, extra.bottom)
                new_top = min(top, extra.top)
                if new_bottom <= new_top:
                    selected.append(extra)
                    selected_tfs.add(extra.timeframe)
                    bottom, top = new_bottom, new_top
            if len(selected_tfs) < minimum_timeframes:
                continue
            atr_values = [zone.atr for zone in selected if zone.atr > 0]
            atr = min(atr_values) if atr_values else max(top - bottom, 1e-12)
            candidates.append({
                "type": first.zone_type,
                "bottom": float(bottom),
                "top": float(top),
                "poi": float((bottom + top) / 2.0),
                "atr": float(atr),
                "timeframes": tuple(sorted(selected_tfs, key=_tf_minutes)),
                "timeframe_count": len(selected_tfs),
                "zones": [zone.to_dict() for zone in selected],
            })

    # The pair loop can produce the same confluence several times. Keep the
    # widest price intersection for each exact timeframe set and direction.
    unique = {}
    for item in candidates:
        key = (item["type"], item["timeframes"])
        old = unique.get(key)
        if old is None or (item["top"] - item["bottom"]) > (old["top"] - old["bottom"]):
            unique[key] = item
    return list(unique.values())


def _distance(price: float, bottom: float, top: float) -> float:
    if price < bottom:
        return bottom - price
    if price > top:
        return price - top
    return 0.0


def build_mtf_confluence(
    klines_by_timeframe: dict,
    price: float,
    timeframes: Iterable[str] = ("1m", "5m", "15m", "30m", "1h"),
    minimum_timeframes: int = 2,
    near_atr: float = 0.5,
    swing_length: int = 10,
    history: int = 20,
    box_width: float = 2.5,
) -> dict:
    """Calculate active zones and strict multi-timeframe intersections."""
    zones = []
    selected_timeframes = [
        str(tf) for tf in timeframes
        if 0 < _tf_minutes(tf) <= 60 and klines_by_timeframe.get(tf)
    ]
    for timeframe in selected_timeframes:
        zones.extend(detect_supply_demand_zones(
            klines_by_timeframe.get(timeframe) or [], timeframe,
            swing_length=swing_length, history=history, box_width=box_width,
            current_price=price,
        ))

    confluences = _make_confluence(zones, max(2, int(minimum_timeframes)))
    for item in confluences:
        item["distance"] = float(_distance(price, item["bottom"], item["top"]))
        item["distance_atr"] = item["distance"] / max(item["atr"], 1e-12)
        item["near"] = item["distance_atr"] <= max(0.0, float(near_atr))

    demand = sorted((z for z in confluences if z["type"] == "demand"), key=lambda z: z["distance"])
    supply = sorted((z for z in confluences if z["type"] == "supply"), key=lambda z: z["distance"])
    return {
        "zones": [zone.to_dict() for zone in zones],
        "confluences": confluences,
        "near_demand": next((z for z in demand if z["near"]), None),
        "near_supply": next((z for z in supply if z["near"]), None),
        "nearest_demand": demand[0] if demand else None,
        "nearest_supply": supply[0] if supply else None,
        "timeframes": selected_timeframes,
        "minimum_timeframes": int(minimum_timeframes),
        "near_atr": float(near_atr),
    }


# Short aliases make the calculation convenient for tests and callers while
# keeping the descriptive public name above.
find_supply_demand_zones = detect_supply_demand_zones
mtf_supply_demand = build_mtf_confluence
