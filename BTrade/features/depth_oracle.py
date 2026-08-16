"""Факты реального стакана (L2) и ленты сделок. Без ML/AI — только цифры.

Из стакана берём: дисбаланс покупателей/продавцов, стены, айсберги,
плотность уровней, спред, разрывы ликвидности (куда цена может
"прыгнуть") и маршрут цены по уровням. Из ленты сделок: агрессивный
buy/sell, скорость и объём на секунду.
"""

import time
from collections import deque


def _levels(rows):
    out = []
    for row in rows or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            try:
                price, size = float(row[0]), float(row[1])
            except (TypeError, ValueError):
                continue
            if size > 0:
                out.append((price, size))
    return out


def liquidity_ahead(orderbook, side: int, max_pct: float = 0.02, wall_mult: float = 3.0):
    """Жидкие зоны впереди цены по направлению движения (side: 1 long, -1 short).

    Анализирует ВЕСЬ стакан (не только ближние 10 уровней): где впереди пустоты
    (цена проскочит быстро) и где большие лимитные стены (цена упрётся и отскочит).
    """
    bids = _levels(orderbook.get("bids"))
    asks = _levels(orderbook.get("asks"))
    if not bids or not asks:
        return {"ready": False}
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    mid = (best_bid + best_ask) / 2
    limit = mid * (1 + max_pct) if side > 0 else mid * (1 - max_pct)
    ahead = []
    if side > 0:
        for price, size in asks:
            if best_ask < price <= limit:
                ahead.append((price, size))
    else:
        for price, size in bids:
            if limit <= price < best_bid:
                ahead.append((price, size))
    if not ahead:
        return {"ready": True, "mid": float(mid), "levels_ahead": 0,
                "wall_ahead": None, "wall_dist_pct": None, "air_pocket_levels": 0,
                "ahead_density": 1.0, "ahead_total": 0.0}
    sizes = [s for _, s in ahead]
    median = sorted(sizes)[len(sizes) // 2] if sizes else 0.0
    floor = median * wall_mult if median > 0 else 0.0
    wall = None
    wall_dist_pct = None
    for price, size in ahead:
        if median > 0 and size >= floor:
            wall = {"price": float(price), "size": float(size)}
            wall_dist_pct = (price / mid - 1) * 100 if side > 0 else (1 - price / mid) * 100
            break
    weak = median * 0.35 if median > 0 else 0.0
    air_run, best_air = 0, 0
    for _, size in ahead:
        if size < weak:
            air_run += 1
            best_air = max(best_air, air_run)
        else:
            air_run = 0
    dense = sum(1 for s in sizes if median > 0 and s >= median * 0.5) / len(sizes)
    return {
        "ready": True,
        "mid": float(mid),
        "levels_ahead": len(ahead),
        "wall_ahead": wall,
        "wall_dist_pct": float(wall_dist_pct) if wall_dist_pct is not None else None,
        "air_pocket_levels": int(best_air),
        "ahead_density": float(dense),
        "ahead_total": float(sum(sizes)),
    }


def orderbook_facts(orderbook, depth: int = 10, wall_mult: float = 3.0, mintick: float = 1e-12):
    """Снимок фактов стакана на один момент времени."""
    bids = _levels(orderbook.get("bids"))
    asks = _levels(orderbook.get("asks"))
    if not bids and not asks:
        return {"ready": False}

    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    mid = (best_bid + best_ask) / 2 if (best_bid and best_ask) else (best_bid or best_ask or 0.0)
    tick = max(mintick, abs(mid) * 1e-6)
    spread = (best_ask - best_bid) if (best_ask and best_bid and best_ask > best_bid) else tick
    spread_rel = spread / mid if mid else 0.0

    bid_rows = bids[:depth]
    ask_rows = asks[:depth]
    bid_vol = sum(q for _, q in bid_rows)
    ask_vol = sum(q for _, q in ask_rows)
    total = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total if total > 0 else 0.0

    sizes = sorted(q for _, q in bid_rows + ask_rows)
    median = sizes[len(sizes) // 2] if sizes else 0.0
    floor = median * wall_mult

    bid_walls = [{"price": p, "size": q} for p, q in bid_rows if median > 0 and q >= floor]
    ask_walls = [{"price": p, "size": q} for p, q in ask_rows if median > 0 and q >= floor]
    bid_wall_mass = sum(w["size"] for w in bid_walls)
    ask_wall_mass = sum(w["size"] for w in ask_walls)
    wall_side = 1 if bid_wall_mass > ask_wall_mass else (-1 if ask_wall_mass > bid_wall_mass else 0)

    nearest_bid_wall = bid_walls[0] if bid_walls else None
    nearest_ask_wall = ask_walls[0] if ask_walls else None

    dense = sizes
    density = sum(1 for q in dense if median > 0 and q >= median * 0.5) / max(len(dense), 1)

    def _gap_run(rows, direction):
        best_run, run = 0, 0
        best_price = None
        ordered = rows if direction > 0 else list(reversed(rows))
        for price, size in ordered:
            if median > 0 and size >= median * 0.35:
                if run > best_run:
                    best_run = run
                    best_price = price
                run = 0
            else:
                run += 1
        if run > best_run:
            best_run = run
            best_price = ordered[-1][0] if ordered else None
        return best_run if best_run >= 3 else 0, best_price

    gap_up, gap_up_price = _gap_run(ask_rows, 1)
    gap_down, gap_down_price = _gap_run(bid_rows, -1)

    below = list(bid_rows)
    above = list(ask_rows)
    route = []
    b_i, a_i = 0, 0
    while (b_i < len(below) or a_i < len(above)) and len(route) < 8:
        if a_i >= len(above):
            p, q = below[b_i]
            b_i += 1
            side = "bid"
        elif b_i >= len(below):
            p, q = above[a_i]
            a_i += 1
            side = "ask"
        else:
            pb, qb = below[b_i]
            pa, qa = above[a_i]
            if (mid - pb) <= (pa - mid):
                p, q = pb, qb
                b_i += 1
                side = "bid"
            else:
                p, q = pa, qa
                a_i += 1
                side = "ask"
        wall = bool(
            (side == "bid" and nearest_bid_wall and abs(p - nearest_bid_wall["price"]) <= tick)
            or (side == "ask" and nearest_ask_wall and abs(p - nearest_ask_wall["price"]) <= tick)
        )
        route.append({"price": p, "size": q, "side": side, "wall": wall})

    return {
        "ready": True,
        "source": "l2_depth",
        "best_bid": float(best_bid) if best_bid else None,
        "best_ask": float(best_ask) if best_ask else None,
        "mid": float(mid),
        "spread": float(spread),
        "spread_rel": float(spread_rel),
        "bid_volume": float(bid_vol),
        "ask_volume": float(ask_vol),
        "imbalance": float(imbalance),
        "median_size": float(median),
        "floor": float(floor),
        "density": float(density),
        "bid_walls": [{"price": float(w["price"]), "size": float(w["size"])} for w in bid_walls],
        "ask_walls": [{"price": float(w["price"]), "size": float(w["size"])} for w in ask_walls],
        "bid_wall_mass": float(bid_wall_mass),
        "ask_wall_mass": float(ask_wall_mass),
        "wall_side": float(wall_side),
        "nearest_bid_wall": nearest_bid_wall,
        "nearest_ask_wall": nearest_ask_wall,
        "gap_up": int(gap_up),
        "gap_up_price": float(gap_up_price) if gap_up_price else None,
        "gap_down": int(gap_down),
        "gap_down_price": float(gap_down_price) if gap_down_price else None,
        "route": route,
    }


def tape_facts(trades, window_s: float = 30.0):
    """Факты ленты сделок: агрессивный buy/sell, скорость, крупные сделки."""
    if not trades:
        return {"ready": False}
    now_ms = time.time() * 1000.0
    recent = [t for t in trades if t.get("ts") and now_ms - float(t["ts"]) <= window_s * 1000.0]
    if len(recent) < 2:
        return {"ready": False}
    buy = 0.0
    sell = 0.0
    n_buy = 0
    n_sell = 0
    sizes = []
    ts_min = float(recent[0]["ts"])
    ts_max = ts_min
    for t in recent:
        amount = float(t.get("amount") or 0.0)
        size = abs(amount)
        sizes.append(size)
        ts = float(t.get("ts") or 0.0)
        ts_min = min(ts_min, ts)
        ts_max = max(ts_max, ts)
        if str(t.get("side") or "").lower() == "buy":
            buy += size
            n_buy += 1
        else:
            sell += size
            n_sell += 1
    total = buy + sell
    span_s = max((ts_max - ts_min) / 1000.0, 1e-6)
    sizes.sort()
    median = sizes[len(sizes) // 2] if sizes else 0.0
    big_buy = sum(1 for t in recent if median > 0 and abs(float(t.get("amount") or 0.0)) >= median * 3
                  and str(t.get("side") or "").lower() == "buy")
    big_sell = sum(1 for t in recent if median > 0 and abs(float(t.get("amount") or 0.0)) >= median * 3
                   and str(t.get("side") or "").lower() == "sell")
    delta_div = 0.0
    traded_prices = [float(t["price"]) for t in recent if t.get("price") is not None]
    if len(traded_prices) >= 2 and total > 0:
        move = traded_prices[-1] - traded_prices[0]
        delta = (buy - sell) / total
        if move <= -1e-12 and delta > 0.15:
            delta_div = 1.0
        elif move >= 1e-12 and delta < -0.15:
            delta_div = -1.0
    return {
        "ready": True,
        "n_trades": len(recent),
        "buy_volume": float(buy),
        "sell_volume": float(sell),
        "buy_ratio": float(buy / total) if total > 0 else 0.5,
        "cvd_slope": float((buy - sell) / total) if total > 0 else 0.0,
        "n_buy": n_buy,
        "n_sell": n_sell,
        "velocity": float(len(recent) / span_s),
        "volume_per_sec": float((buy + sell) / span_s),
        "median_size": float(median),
        "big_buy": int(big_buy),
        "big_sell": int(big_sell),
        "delta_divergence": float(delta_div),
        "window_s": window_s,
    }


class DepthOracle:
    """История стаканов: детектит айсберги и динамику дисбаланса между снимками."""

    def __init__(self, min_seen: int = 3, max_hist: int = 12, min_absorption_ticks: int = 3):
        self.min_seen = max(2, int(min_seen))
        self.max_hist = max(4, int(max_hist))
        self.min_absorption_ticks = max(1, int(min_absorption_ticks))
        self._hist = {}
        self._track = {}
        self._abs = {}

    def update(self, symbol, book, depth: int = 10, wall_mult: float = 3.0, mintick: float = 1e-12,
               tape: dict = None):
        facts = orderbook_facts(book, depth=depth, wall_mult=wall_mult, mintick=mintick)
        if not facts.get("ready"):
            return facts
        hist = self._hist.setdefault(symbol, deque(maxlen=self.max_hist))
        prev = hist[-1] if hist else None
        now = time.time()

        track = self._track.setdefault(symbol, {"bid": {}, "ask": {}})
        bids = _levels(book.get("bids"))[:depth]
        asks = _levels(book.get("asks"))[:depth]
        floor = float(facts.get("floor") or 0.0)
        best_ask, best_bid = facts.get("best_ask"), facts.get("best_bid")
        for side, levels, crossed_when in (("bid", bids, best_ask), ("ask", asks, best_bid)):
            cur = {}
            for price, size in levels:
                key = float(price)
                prev_rec = track[side].get(key)
                if size >= floor and floor > 0:
                    count = (prev_rec.get("count", 0) if prev_rec else 0) + 1
                    crossed = bool(prev_rec and prev_rec.get("crossed"))
                    if crossed_when is not None:
                        if (side == "bid" and crossed_when <= price) or (side == "ask" and crossed_when >= price):
                            crossed = True
                    cur[key] = {"count": count, "crossed": crossed, "size": size}
            track[side] = cur

        iceberg_bids = [p for p, r in track["bid"].items() if r["count"] >= self.min_seen and r["crossed"]]
        iceberg_asks = [p for p, r in track["ask"].items() if r["count"] >= self.min_seen and r["crossed"]]
        ice_bid_mass = sum(r["size"] for r in track["bid"].values() if r["count"] >= self.min_seen and r["crossed"])
        ice_ask_mass = sum(r["size"] for r in track["ask"].values() if r["count"] >= self.min_seen and r["crossed"])
        facts["iceberg_bids"] = sorted(iceberg_bids)
        facts["iceberg_asks"] = sorted(iceberg_asks)
        facts["iceberg_side"] = 1.0 if ice_bid_mass > ice_ask_mass else (-1.0 if ice_ask_mass > ice_bid_mass else 0.0)

        facts["absorption_side"] = self._absorption(symbol, facts, tape)

        if prev:
            facts["imbalance_velocity"] = (facts["imbalance"] - prev["imbalance"]) / max(now - prev["ts"], 1.0)
            facts["spread_change"] = facts["spread"] - prev["spread"]
        else:
            facts["imbalance_velocity"] = 0.0
            facts["spread_change"] = 0.0
        facts["prev"] = bool(prev)

        hist.append({"ts": now, "imbalance": facts["imbalance"], "spread": facts["spread"],
                     "ice_bids": len(iceberg_bids), "ice_asks": len(iceberg_asks)})
        return facts

    def _absorption(self, symbol, facts: dict, tape: dict) -> float:
        """Поглощение: стена держится 3+ скана под агрессивным давлением против неё.

        bid-стена растёт/держится, пока цена давит на неё продажами -> покупки
        абсорбируют (absorption_side=+1, бычий триггер). Аналогично ask-стена.
        """
        t = tape or {}
        if not t.get("ready"):
            self._abs[symbol] = {"bid": 0, "ask": 0}
            return 0.0
        slope = float(t.get("cvd_slope") or 0.0)
        abs_t = self._abs.setdefault(symbol, {"bid": 0, "ask": 0})
        nbid = facts.get("nearest_bid_wall") or {}
        nask = facts.get("nearest_ask_wall") or {}
        best_ask = facts.get("best_ask")
        best_bid = facts.get("best_bid")
        if nbid:
            bid_holds = best_ask is not None and best_ask > float(nbid["price"])
            if bid_holds and slope < -0.1:
                abs_t["bid"] += 1
            else:
                abs_t["bid"] = 0
        else:
            abs_t["bid"] = 0
        if nask:
            ask_holds = best_bid is not None and best_bid < float(nask["price"])
            if ask_holds and slope > 0.1:
                abs_t["ask"] += 1
            else:
                abs_t["ask"] = 0
        else:
            abs_t["ask"] = 0
        min_scans = self.min_absorption_ticks
        if abs_t["bid"] >= min_scans:
            return 1.0
        if abs_t["ask"] >= min_scans:
            return -1.0
        return 0.0