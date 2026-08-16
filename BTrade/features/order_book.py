"""Python port of the VAP/DOM calculations from ``Indikator 1.txt``.

The Pine indicator calls this display ORDER BOOK, but its source is traded
volume-at-price (not exchange level-2 resting orders).  The bot therefore
uses the same 1-minute OHLCV classification and does not mix in another
order-flow indicator.
"""

import math


def _bar_values(row):
    return float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5] or 0.0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value if math.isfinite(value) else 0.0, high))


def _fmt_level(price: float) -> dict:
    return {"price": float(price)}


def indicator1_snapshot(
    klines: list,
    vp_len: int = 300,
    n_levels: int = 80,
    dom_levels: int = 16,
    va_pct: float = 70.0,
    wall_mult: float = 2.0,
    fast_dom: bool = True,
    fast_power: float = 2.2,
    fast_decay: float = 0.72,
    mintick: float = 1e-12,
    source_timeframe: str = "1m",
) -> dict:
    """Return the current state used by the Pine DOM and dashboard."""
    rows = list(klines or [])[-max(1, int(vp_len)):]
    if len(rows) < 3:
        return {
            "ready": False, "rows": [], "source": "indicator1_vap_dom",
            "source_timeframe": source_timeframe, "buy_volume": 0.0, "sell_volume": 0.0,
        }

    bars = [_bar_values(row) for row in rows]
    b_volumes = [volume if close >= open_ else 0.0 for open_, _, _, close, volume in bars]
    s_volumes = [volume if close < open_ else 0.0 for open_, _, _, close, volume in bars]
    last_open, last_high, last_low, last_close, last_volume = bars[-1]
    b_v = b_volumes[-1]
    s_v = s_volumes[-1]
    p_hi = max(row[1] for row in bars)
    p_lo = min(row[2] for row in bars)
    rng = p_hi - p_lo
    if rng <= 0:
        return {
            "ready": False, "rows": [], "source": "indicator1_vap_dom",
            "source_timeframe": source_timeframe, "buy_volume": b_v, "sell_volume": s_v,
            "delta": b_v - s_v, "cvd": sum(b_volumes) - sum(s_volumes),
        }

    n_levels = max(6, int(n_levels))
    step = rng / n_levels
    b_profile = [0.0] * n_levels
    buy_profile = [0.0] * n_levels
    sell_profile = [0.0] * n_levels

    for (_, high, low, _, volume), buy, sell in zip(bars, b_volumes, s_volumes):
        lo_idx = max(0, min(n_levels - 1, int(math.floor((low - p_lo) / step))))
        hi_idx = max(0, min(n_levels - 1, int(math.floor((high - p_lo) / step))))
        span = max(1, hi_idx - lo_idx + 1)
        v_per = volume / span
        buy_per = buy / span
        sell_per = sell / span
        for index in range(lo_idx, hi_idx + 1):
            b_profile[index] += v_per
            buy_profile[index] += buy_per
            sell_profile[index] += sell_per

    poc_idx = max(range(n_levels), key=lambda index: b_profile[index])
    max_volume = b_profile[poc_idx]
    total_volume = sum(b_profile)
    total_buy = sum(buy_profile)
    total_sell = sum(sell_profile)
    avg_volume = total_volume / n_levels
    poc_price = p_lo + (poc_idx + 0.5) * step

    va_target = total_volume * float(va_pct) / 100.0
    accumulated = b_profile[poc_idx]
    low_va = poc_idx
    high_va = poc_idx
    guard = 0
    while accumulated < va_target and guard < n_levels * 2:
        guard += 1
        up_volume = b_profile[high_va + 1] if high_va < n_levels - 1 else -1.0
        down_volume = b_profile[low_va - 1] if low_va > 0 else -1.0
        if up_volume < 0 and down_volume < 0:
            break
        if up_volume >= down_volume:
            high_va += 1
            accumulated += up_volume
        else:
            low_va -= 1
            accumulated += down_volume
    vah_price = p_lo + (high_va + 1) * step
    val_price = p_lo + low_va * step

    last_level = max(0, min(n_levels - 1, int(math.floor((last_close - p_lo) / step))))
    cur_total = max(b_v + s_v, 1.0)
    bar_range = max(last_high - last_low, mintick)
    volume_pressure = (b_v - s_v) / cur_total
    close_pressure = ((last_close - last_low) / bar_range - 0.5) * 2.0
    body_pressure = (last_close - last_open) / bar_range
    dom_pressure = _clamp(
        (volume_pressure * 0.62 + close_pressure * 0.23 + body_pressure * 0.15) * fast_power,
        -1.0,
        1.0,
    )
    buy_pulse = max(dom_pressure, 0.0) * cur_total if fast_dom else 0.0
    sell_pulse = max(-dom_pressure, 0.0) * cur_total if fast_dom else 0.0

    half = max(2, int(dom_levels) // 2)
    asks = []
    bids = []
    max_display = 0.0
    for offset in range(half):
        ask_index = last_level + half - offset
        if 0 <= ask_index < n_levels:
            distance = abs(ask_index - last_level)
            pulse = sell_pulse * (fast_decay ** distance)
            value = b_profile[ask_index] + pulse
            max_display = max(max_display, value)
        bid_index = last_level - 1 - offset
        if 0 <= bid_index < n_levels:
            distance = abs(bid_index - last_level)
            pulse = buy_pulse * (fast_decay ** distance)
            value = b_profile[bid_index] + pulse
            max_display = max(max_display, value)
    max_display = max(max_display, 1.0)

    for offset in range(half):
        ask_index = last_level + half - offset
        if 0 <= ask_index < n_levels:
            distance = abs(ask_index - last_level)
            pulse = sell_pulse * (fast_decay ** distance)
            value = b_profile[ask_index] + pulse
            asks.append({
                **_fmt_level(p_lo + (ask_index + 0.5) * step),
                "size": float(value),
                "base_size": float(b_profile[ask_index]),
                "pulse": float(pulse),
                "depth": int(round(value / max_display * 12)),
                "wall": bool(value > avg_volume * wall_mult),
                "active": bool(fast_dom and pulse > cur_total * 0.04),
            })
        bid_index = last_level - 1 - offset
        if 0 <= bid_index < n_levels:
            distance = abs(bid_index - last_level)
            pulse = buy_pulse * (fast_decay ** distance)
            value = b_profile[bid_index] + pulse
            bids.append({
                **_fmt_level(p_lo + (bid_index + 0.5) * step),
                "size": float(value),
                "base_size": float(b_profile[bid_index]),
                "pulse": float(pulse),
                "depth": int(round(value / max_display * 12)),
                "wall": bool(value > avg_volume * wall_mult),
                "active": bool(fast_dom and pulse > cur_total * 0.04),
            })

    bid_walls = [row for row in bids if row["wall"]]
    ask_walls = [row for row in asks if row["wall"]]
    wall_direction = 1.0 if len(bid_walls) > len(ask_walls) else -1.0 if len(ask_walls) > len(bid_walls) else 0.0
    value_area_position = 1.0 if last_close > vah_price else -1.0 if last_close < val_price else 0.0
    total = total_buy + total_sell
    previous_close = bars[-2][3]
    poc_cross = (previous_close < poc_price <= last_close) or (previous_close > poc_price >= last_close)
    return {
        "ready": True,
        "source": "indicator1_vap_dom",
        "source_timeframe": source_timeframe,
        "rows": len(rows),
        "p_lo": float(p_lo),
        "p_hi": float(p_hi),
        "step": float(step),
        "poc": float(poc_price),
        "vah": float(vah_price),
        "val": float(val_price),
        "buy_volume": float(b_v),
        "sell_volume": float(s_v),
        "profile_buy_volume": float(total_buy),
        "profile_sell_volume": float(total_sell),
        "profile_total_volume": float(total_volume),
        "buy_pct": float(total_buy / total * 100.0) if total > 0 else 50.0,
        "delta": float(b_v - s_v),
        "delta_ratio": float((b_v - s_v) / cur_total),
        "cvd": float(total_buy - total_sell),
        "dom_pressure": float(dom_pressure),
        "volume_pressure": float(volume_pressure),
        "close_pressure": float(close_pressure),
        "body_pressure": float(body_pressure),
        "value_area_position": float(value_area_position),
        "wall_bid_count": len(bid_walls),
        "wall_ask_count": len(ask_walls),
        "wall_bid_size": float(max((row["size"] for row in bid_walls), default=0.0)),
        "wall_ask_size": float(max((row["size"] for row in ask_walls), default=0.0)),
        "wall_direction": float(wall_direction),
        "last_level": int(last_level),
        "asks": asks,
        "bids": bids,
        "best_ask": float(p_lo + (min(n_levels - 1, last_level + 1) + 0.5) * step),
        "best_bid": float(p_lo + (max(0, last_level - 1) + 0.5) * step),
        "mid_spread": float(step),
        "poc_cross": bool(poc_cross),
    }


calculate_order_book = indicator1_snapshot
