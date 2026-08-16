import numpy as np


def volume_profile(closes, volumes, bins=24):
    lo, hi = min(closes), max(closes)
    if lo == hi or not closes:
        return {}
    step = (hi - lo) / bins
    profile = {}
    for c, v in zip(closes, volumes):
        b = int((c - lo) / step)
        b = min(max(b, 0), bins - 1)
        profile[b] = profile.get(b, 0) + v
    if not profile:
        return {}
    poc_bin = max(profile, key=profile.get)
    total = sum(profile.values())
    cum = 0.0
    vah = poc_bin
    for b in sorted(profile, reverse=True):
        cum += profile[b]
        if cum <= total * 0.3:
            vah = b
        else:
            break
    cum = 0.0
    val = poc_bin
    for b in sorted(profile):
        cum += profile[b]
        if cum <= total * 0.3:
            val = b
        else:
            break
    return {
        "poc": lo + (poc_bin + 0.5) * step,
        "vah": lo + (vah + 0.5) * step,
        "val": lo + (val + 0.5) * step,
        "range": hi - lo,
        "profile": profile,
    }


def cvd(trades):
    ordered = sorted(trades, key=lambda t: t.get("ts", 0))
    delta = 0.0
    out = []
    for t in ordered:
        if t["side"] == "buy":
            delta += t["amount"]
        else:
            delta -= t["amount"]
        out.append(delta)
    return out


def cvd_slope_metric(trades):
    if len(trades) < 2:
        return 0.0
    ordered = sorted(trades, key=lambda t: t.get("ts", 0))
    deltas = [t["amount"] if t["side"] == "buy" else -t["amount"] for t in ordered]
    total = sum(abs(d) for d in deltas)
    if total == 0:
        return 0.0
    return sum(deltas) / total


def _pairs(levels, depth):
    out = []
    for row in levels[:depth]:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            out.append((float(row[0]), float(row[1])))
    return out


def imbalance(orderbook, depth=10):
    bids = _pairs(orderbook.get("bids", []), depth)
    asks = _pairs(orderbook.get("asks", []), depth)
    bid_vol = sum(a for _, a in bids)
    ask_vol = sum(a for _, a in asks)
    if bid_vol + ask_vol == 0:
        return 0.0
    return (bid_vol - ask_vol) / (bid_vol + ask_vol)


def depth_ratio(orderbook, depth=10):
    bids = _pairs(orderbook.get("bids", []), depth)
    asks = _pairs(orderbook.get("asks", []), depth)
    bid_vol = sum(a for _, a in bids)
    ask_vol = sum(a for _, a in asks)
    if ask_vol == 0:
        return 1.0 if bid_vol > 0 else 0.0
    return bid_vol / ask_vol


def footprint_delta(klines):
    deltas = []
    for k in klines:
        if k[4] >= k[1]:
            deltas.append(k[5])
        else:
            deltas.append(-k[5])
    return np.asarray(deltas, dtype=float)
