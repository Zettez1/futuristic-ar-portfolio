"""Поток микроструктуры: ликвидации, OI, premium/funding + быстрые WS-ленты.

Слои:
- Ликвидации: WS !forceOrder на все монеты одним соединением. События хранятся
  с event-time биржи (не локальным), дедуплицируются по (symbol, T, qty, ap).
  Окна 5/15/60/300с считаются на лету — ускорение каскада видно сразу.
- OI: REST-ползучая очередь по пулу; свежесть замера считается в скоринге.
- Premium/funding: один REST-запрос на все монеты (кэш 60с), адаптивные
  пороги через EMA mean/std (z-score) по каждому символу.
- FastFeed (гибрид): WS bookTicker + aggTrade для топ-N кандидатов.
  Подписки динамические: кандидат остыл — поток закрывается. Даёт боту
  живые сделки и best bid/ask там, где есть потенциал импульса.

При недоступности любого слоя его факторы просто нейтральны.
"""

import asyncio
import json
import math
import threading
import time
from collections import deque

try:
    import websockets
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

LIQ_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
STREAM_URL = "wss://fstream.binance.com/ws"
PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"

LIQ_WINDOWS = (5, 15, 60, 300)

_LOG_TAG = "[МИКРОСТРУКТУРА]"


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _symbol_to_fapi(symbol: str) -> str:
    return str(symbol or "").replace("/", "").replace(":USDT", "")


def _symbol_from_fapi(fapi: str) -> str:
    return str(fapi or "").replace("USDT", "/USDT:USDT")


class FlowFeed:
    """Фоновый сбор ликвидаций, OI и premium/funding. thread-safe."""

    def __init__(self, log=print, oi_interval: float = 4.0):
        self.log = log
        self.oi_interval = oi_interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads = []
        # ликвидации: symbol -> deque[(event_ts, side, notional_usd, key)]
        self._liq = {}
        self._liq_keys = {}
        # последний event по стороне: symbol -> {"long": ts, "short": ts}
        self._liq_last = {}
        # OI: symbol -> (oi, ts, prev_oi)
        self._oi = {}
        # premium: symbol -> {mark, index, funding, premium_pct, mean, std, n, ts}
        self._premium = {}
        self._symbols = []
        self._oi_cursor = 0
        self._started = False
        self.fast = FastFeed(log=log)

    def start(self, symbols: list):
        if self._started:
            self._symbols = list(symbols)
            return
        self._started = True
        self._symbols = list(symbols)
        # BTC всегда в быстрых потоках: направление BTC — глобальный фильтр входа
        self.fast.pin(["BTC/USDT:USDT"])
        for name, target in (("flow-liq", self._run_liq), ("flow-oi", self._run_oi_loop),
                             ("flow-premium", self._run_premium_loop), ("flow-fast", self.fast._run)):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        self.log(f"{_LOG_TAG} потоки запущены: ликвидации WS + OI/premium REST + FastFeed WS")

    def stop(self):
        self._stop.set()
        self.fast.stop()

    # ------------------------------------------------------------------ ликвидации
    def _run_liq(self):
        if not WS_AVAILABLE:
            self.log(f"{_LOG_TAG} websockets не установлен — ликвидации недоступны")
            return
        while not self._stop.is_set():
            try:
                asyncio.run(self._liq_loop())
            except Exception as exc:
                self.log(f"{_LOG_TAG} WS-поток ликвидаций упал: {exc}; переподключение через 5с")
            self._stop.wait(5)

    async def _liq_loop(self):
        async with websockets.connect(LIQ_URL, ping_interval=20) as ws:
            while not self._stop.is_set():
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                except asyncio.TimeoutError:
                    continue
                o = (msg.get("o") or {})
                symbol = _symbol_from_fapi(str(o.get("s") or ""))
                if not symbol:
                    continue
                side = "short" if o.get("S") == "BUY" else "long"  # BUY = ликвидируют шорт
                qty = _f(o.get("z") or o.get("q"))
                price = _f(o.get("ap"))
                notional = qty * price
                if notional <= 0 or not math.isfinite(notional):
                    continue
                event_ts = float(o.get("T") or time.time() * 1000.0) / 1000.0
                key = f"{symbol}|{o.get('T')}|{qty:.8g}|{price:.8g}"
                with self._lock:
                    seen = self._liq_keys.get(symbol)
                    now = time.time()
                    if seen and key in seen:
                        continue
                    self._liq_keys.setdefault(symbol, set()).add(key)
                    items = self._liq.setdefault(symbol, deque())
                    items.append((event_ts, side, notional, key))
                    self._liq_last.setdefault(symbol, {})[side] = event_ts
                    self._trim_liq(symbol, now)

    def _trim_liq(self, symbol: str, now: float):
        items = self._liq.get(symbol)
        cutoff = now - max(LIQ_WINDOWS)
        while items and items[0][0] < cutoff:
            items.popleft()
        keys = self._liq_keys.get(symbol)
        if keys and len(keys) > 10000:
            for k in list(keys)[:len(keys) - 8000]:
                keys.discard(k)

    def liq_stats(self, symbol: str) -> dict:
        """Объёмы ликвидаций по окнам 5/15/60/300с + тайминг и ускорение по сторонам.

        Дополнительно к окнам:
        - long_last_age_s / short_last_age_s: давность последнего события каждой стороны;
        - long_accel / short_accel: ускорение каскада — объём за 5с против среднего
          темпа за 60с (>= 2 = лавина началась прямо сейчас).
        """
        now = time.time()
        with self._lock:
            items = list(self._liq.get(symbol) or [])
            last_by_side = self._liq_last.get(symbol) or {}
        windows = {}
        last_ts = None
        for it in items:
            if last_ts is None or it[0] > last_ts:
                last_ts = it[0]
        for w in LIQ_WINDOWS:
            cutoff = now - w
            ws_items = [it for it in items if it[0] >= cutoff]
            long_v = sum(it[2] for it in ws_items if it[1] == "long")
            short_v = sum(it[2] for it in ws_items if it[1] == "short")
            windows[str(w)] = {"long_usd": long_v, "short_usd": short_v,
                               "total_usd": long_v + short_v, "n": len(ws_items)}
        out = {"windows": windows, "last_ts": last_ts,
               "age_s": (now - last_ts) if last_ts else None}
        for side_str in ("long", "short"):
            ts = last_by_side.get(side_str)
            out[f"{side_str}_last_age_s"] = (now - ts) if ts else None
            w5 = windows.get("5") or {}
            w60 = windows.get("60") or {}
            fast = float(w5.get(f"{side_str}_usd") or 0.0)
            mid = float(w60.get(f"{side_str}_usd") or 0.0)
            baseline = mid / 12.0
            out[f"{side_str}_accel"] = (fast / baseline) if baseline > 0 else 0.0
        return out

    # ------------------------------------------------------------------ OI
    def _run_oi_loop(self):
        try:
            import requests
        except ImportError:
            self.log(f"{_LOG_TAG} requests недоступен — OI не собирается")
            return
        while not self._stop.is_set():
            symbols = list(self._symbols)
            if not symbols:
                self._stop.wait(5)
                continue
            with self._lock:
                if self._oi_cursor >= len(symbols):
                    self._oi_cursor = 0
                symbol = symbols[self._oi_cursor]
                self._oi_cursor += 1
            try:
                r = requests.get(OI_URL, params={"symbol": _symbol_to_fapi(symbol)}, timeout=5)
                data = r.json()
                oi = _f(data.get("openInterest"))
                if oi > 0:
                    with self._lock:
                        prev = self._oi.get(symbol)
                        self._oi[symbol] = (oi, time.time(), prev[0] if prev else None)
            except Exception:
                pass
            self._stop.wait(self.oi_interval)

    def oi_delta(self, symbol: str, min_age_s: float = 120.0) -> dict:
        with self._lock:
            entry = self._oi.get(symbol)
            if not entry or len(entry) < 3 or entry[2] is None:
                return {"change_pct": None, "age_s": None, "oi": None}
            oi, ts, prev = entry[0], entry[1], entry[2]
        age = time.time() - ts
        if age < min_age_s or prev is None or prev <= 0:
            return {"change_pct": None, "age_s": age, "oi": oi}
        return {"change_pct": (oi / prev - 1.0) * 100.0, "age_s": age, "oi": oi}

    # ------------------------------------------------------------------ premium / funding
    def _run_premium_loop(self):
        try:
            import requests
        except ImportError:
            return
        while not self._stop.is_set():
            try:
                r = requests.get(PREMIUM_URL, timeout=10)
                data = r.json() if isinstance(r.json(), list) else []
                now = time.time()
                with self._lock:
                    for row in data:
                        symbol = _symbol_from_fapi(str(row.get("symbol") or ""))
                        if not symbol:
                            continue
                        mark = _f(row.get("markPrice"))
                        index = _f(row.get("indexPrice"))
                        premium = (mark / index - 1.0) * 100.0 if index > 0 else 0.0
                        entry = self._premium.get(symbol)
                        if entry and entry.get("n", 0) > 0:
                            alpha = 0.1
                            mean = entry["mean"] + alpha * (premium - entry["mean"])
                            m2 = entry["m2"] + alpha * (premium - entry["mean"]) * (premium - mean)
                            n = entry["n"] + 1
                        else:
                            mean, m2, n = premium, 0.0, 1
                        std = math.sqrt(max(m2 / max(n, 1), 0.0)) if n > 1 else 0.0
                        self._premium[symbol] = {
                            "mark": mark, "index": index,
                            "funding": _f(row.get("lastFundingRate")),
                            "predicted": _f(row.get("predictedFundingRate")),
                            "premium_pct": premium, "mean": mean, "std": std, "n": n,
                            "ts": now,
                        }
            except Exception:
                pass
            self._stop.wait(60)

    def premium_funding(self, symbol: str, max_age_s: float = 300.0) -> dict:
        with self._lock:
            entry = self._premium.get(symbol)
            if not entry:
                return {}
            entry = dict(entry)
        if time.time() - entry["ts"] > max_age_s:
            return {}
        return entry

    def flow_snapshot(self, symbol: str) -> dict:
        """Сводка микроструктуры для снапшота (сырьё для скоринга)."""
        return {
            "liq": self.liq_stats(symbol),
            "oi": self.oi_delta(symbol),
            "premium": self.premium_funding(symbol),
            "fast": self.fast.snapshot(symbol),
            "micro": self.fast.micro(symbol),
            "btc": self.fast.micro("BTC/USDT:USDT"),
            "ts": time.time(),
        }

    def set_pool(self, symbols: list):
        with self._lock:
            self._symbols = list(symbols)


class FastFeed:
    """Гибридный быстрый слой: WS bookTicker + trade для топ-N кандидатов.

    Подписки управляются из main: update_subscriptions(wanted) добавляет новые
    и снимает остывшие. Одно WS-соединение с динамическими SUBSCRIBE/UNSUBSCRIBE.
    Символы открытых позиций закрепляются pin() — не снимаются и не вытесняются.
    micro() считает короткие окна ленты (1/3/5/10с): buy_ratio, CVD, burst,
    depletion уровней и направление спреда — сырьё для side gate и быстрого выхода.
    """

    def __init__(self, log=print, max_symbols: int = 5, stay_seconds: float = 300.0,
                 trade_ttl: float = 45.0):
        self.log = log
        self.max_symbols = max_symbols
        self.stay_seconds = stay_seconds
        self.trade_ttl = trade_ttl
        self._lock = threading.Lock()
        self._stop = threading.Event()
        # symbol -> {"added": ts, "last_msg": ts, "trades": deque[(ts, side, price, amount)],
        #            "book": {...}, "book_hist": deque[(ts, b, a, bq, aq)], "last_trade": ts}
        self._subs = {}
        self._pinned = set()
        self._started = False
        self._conn_event = threading.Event()

    def start(self):
        if self._started:
            return
        self._started = True
        t = threading.Thread(target=self._run, name="flow-fast", daemon=True)
        t.start()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------ управление
    def pin(self, symbols: list):
        """Закрепить символы (открытые позиции): всегда подписаны, не снимаются."""
        with self._lock:
            now = time.time()
            for s in symbols:
                if not s:
                    continue
                self._pinned.add(s)
                self._subs.setdefault(s, {"added": now, "last_msg": 0.0,
                                          "trades": deque(maxlen=800), "book": {},
                                          "book_hist": deque(maxlen=200), "last_trade": 0.0})

    def unpin(self, symbols: list):
        with self._lock:
            for s in symbols:
                self._pinned.discard(s)

    def pinned(self) -> list:
        with self._lock:
            return list(self._pinned)

    def update_subscriptions(self, wanted: list):
        """wanted — упорядоченный список кандидатов (приоритет по убыванию).
        Пинованные символы всегда в targets и не вытесняются."""
        wanted = [s for s in wanted if s]
        with self._lock:
            now = time.time()
            pinned = list(self._pinned)
            keep = [s for s in wanted if s in self._subs]  # уже подписанные — приоритет
            new = [s for s in wanted if s not in self._subs]
            stale = [s for s in self._subs if s not in wanted and s not in pinned
                     and now - self._subs[s]["added"] > self.stay_seconds]
            targets = (pinned + keep + new)[: max(self.max_symbols, len(pinned))]
            drop = [s for s in (stale + list(self._subs.keys())) if s not in targets and s not in pinned]
            for s in drop:
                self._subs.pop(s, None)
            for s in targets:
                self._subs.setdefault(s, {"added": now, "last_msg": 0.0,
                                          "trades": deque(maxlen=800), "book": {},
                                          "book_hist": deque(maxlen=200), "last_trade": 0.0})
            return list(targets), drop

    def is_subscribed(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._subs

    # ------------------------------------------------------------------ данные
    def trades(self, symbol: str, max_age_s: float = 10.0) -> list:
        """Живые сделки в формате tape_facts: [{price, amount, side, ts(ms)}]."""
        with self._lock:
            sub = self._subs.get(symbol)
            if not sub:
                return []
            cutoff = time.time() - max_age_s
            items = [(t, side, p, a) for (t, side, p, a) in sub["trades"] if t >= cutoff]
        return [{"price": p, "amount": a, "side": side, "ts": t * 1000.0}
                for (t, side, p, a) in items]

    def book(self, symbol: str, max_age_s: float = 5.0) -> dict:
        with self._lock:
            sub = self._subs.get(symbol)
            if not sub or not sub["book"] or time.time() - sub["book"].get("_ts", 0) > max_age_s:
                return {}
            book = dict(sub["book"])
            book.pop("_ts", None)
            return book

    def snapshot(self, symbol: str) -> dict:
        with self._lock:
            sub = self._subs.get(symbol)
            if not sub:
                return {"subscribed": False}
            trades = sub["trades"]
            recent = sum(1 for t in trades if t[0] >= time.time() - 10)
            book = sub["book"]
            return {
                "subscribed": True,
                "last_msg_age": (time.time() - sub["last_msg"]) if sub["last_msg"] else None,
                "trades_10s": recent,
                "trades_buf": len(trades),
                "bid": book.get("b"), "ask": book.get("a"),
                "spread_bps": _spread_bps(book.get("b"), book.get("a")),
            }

    MICRO_WINDOWS = (1, 3, 5, 10)

    def micro(self, symbol: str, max_age_s: float = 10.0) -> dict:
        """Быстрые метрики по коротким окнам: лента, CVD, burst, стакан, спред.

        - trades_N / buy_ratio_N / cvd_N: сделки, доля покупок, чистый CVD (USDT)
          за N секунд.
        - cvd_accel: ускорение CVD (cvd_1s - cvd_3s/3, USDT/с, + разгон, - торможение).
        - burst_side / burst_usd_1s / burst_age: всплеск агрессии одной стороны.
        - bid_qty / ask_qty: объём на лучших уровнях; bid_slope / ask_slope:
          скорость выедания (USDT/с, отрицательная = уровень едят).
        - spread_bps, spread_slope_bps: текущий спред и его тренд.
        - last_trade_age / last_book_age: свежесть потоков.
        """
        with self._lock:
            sub = self._subs.get(symbol)
            if not sub:
                return {"subscribed": False}
            trades = sub["trades"]
            hist = sub["book_hist"]
            now = time.time()
            out = {"subscribed": True, "now": now,
                   "last_trade_age": (now - sub["last_trade"]) if sub["last_trade"] else None,
                   "last_book_age": (now - sub["last_msg"]) if sub["last_msg"] else None}
            for w in self.MICRO_WINDOWS:
                cutoff = now - w
                items = [t for t in trades if t[0] >= cutoff]
                buy = sum(a * p for (t, s, p, a) in items if s == "buy")
                sell = sum(a * p for (t, s, p, a) in items if s == "sell")
                n = len(items)
                out[f"trades_{w}"] = n
                out[f"buy_ratio_{w}"] = (buy / (buy + sell)) if (buy + sell) > 0 else 0.5
                out[f"cvd_{w}"] = buy - sell
                out[f"vol_{w}"] = buy + sell
            # ускорение CVD: темп за 1с против среднего за 3с (USDT/с)
            c1 = out.get("cvd_1", 0.0)
            c3 = out.get("cvd_3", 0.0)
            out["cvd_accel"] = c1 - c3 / 3.0
            # burst: всплеск агрессии одной стороны за последнюю секунду
            cutoff1 = now - 1.0
            s1 = [t for t in trades if t[0] >= cutoff1]
            b1 = sum(a * p for (t, s, p, a) in s1 if s == "buy")
            se1 = sum(a * p for (t, s, p, a) in s1 if s == "sell")
            if b1 > se1 * 1.6 and b1 >= 1.0:
                out["burst_side"], out["burst_usd_1s"], out["burst_age"] = "buy", round(b1, 1), 0.0
            elif se1 > b1 * 1.6 and se1 >= 1.0:
                out["burst_side"], out["burst_usd_1s"], out["burst_age"] = "sell", round(se1, 1), 0.0
            else:
                out["burst_side"], out["burst_usd_1s"], out["burst_age"] = None, 0.0, None
            # стакан: лучшие уровни и выедание
            book = sub["book"]
            out["bid"], out["ask"] = book.get("b"), book.get("a")
            out["bid_qty"], out["ask_qty"] = book.get("bq"), book.get("aq")
            if hist:
                bq0, aq0 = hist[0][3], hist[0][4]
                span = max(now - hist[0][0], 1e-9)
                out["bid_slope"] = ((out["bid_qty"] or 0.0) - bq0) / span
                out["ask_slope"] = ((out["ask_qty"] or 0.0) - aq0) / span
            spread = _spread_bps(out.get("bid"), out.get("ask"))
            out["spread_bps"] = spread
            if spread is not None and len(hist) >= 5:
                spreads = [(_spread_bps(h[1], h[2])) for h in hist if h[1] and h[2]]
                spreads = [s for s in spreads if s is not None]
                if spreads:
                    avg = sum(spreads) / len(spreads)
                    out["spread_slope_bps"] = spread - avg
            # наклон цены за ~10с (mid по book_hist) — глобальный фильтр направления
            if hist:
                old = next((h for h in hist if h[0] <= now - 10.0), hist[0])
                mid_now = ((out.get("bid") or 0.0) + (out.get("ask") or 0.0)) / 2.0
                mid_old = (float(old[1]) + float(old[2])) / 2.0
                if mid_now > 0 and mid_old > 0:
                    out["mid_slope_pct_10s"] = (mid_now / mid_old - 1.0) * 100.0
            return out

    def price(self, symbol: str, max_age_s: float = 5.0) -> float:
        """Лучшая цена для закрытия: mid bookTicker, иначе последняя сделка."""
        with self._lock:
            sub = self._subs.get(symbol)
            if not sub:
                return 0.0
            book = sub["book"]
            if book and time.time() - book.get("_ts", 0) <= max_age_s:
                b, a = book.get("b"), book.get("a")
                if b and a:
                    return (float(b) + float(a)) / 2.0
            trades = sub["trades"]
            if trades:
                return float(trades[-1][2])
            return 0.0

    def health(self) -> dict:
        with self._lock:
            return {
                "subscribed": list(self._subs.keys()),
                "conn_alive": self._conn_event.is_set(),
            }

    # ------------------------------------------------------------------ WS
    def _run(self):
        if not WS_AVAILABLE:
            self.log(f"{_LOG_TAG} websockets не установлен — FastFeed недоступен")
            return
        while not self._stop.is_set():
            try:
                asyncio.run(self._loop())
            except Exception as exc:
                self._conn_event.clear()
                self.log(f"{_LOG_TAG} FastFeed упал: {exc}; переподключение через 3с")
            self._stop.wait(3)

    async def _loop(self):
        async with websockets.connect(STREAM_URL, ping_interval=20) as ws:
            self._conn_event.set()
            sent = None
            while not self._stop.is_set():
                with self._lock:
                    subs = list(self._subs.keys())
                params = sorted(_stream_params(subs))
                if params and params != sent:
                    await ws.send(json.dumps({"method": "SUBSCRIBE", "params": params, "id": 1}))
                    sent = params
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                except asyncio.TimeoutError:
                    continue
                self._on_message(msg)

    def _on_message(self, msg: dict):
        if not msg:
            return
        # raw-формат: {"e": "bookTicker", "s": "BTCUSDT", ...}
        # enveloped-формат: {"stream": "btcusdt@bookTicker", "data": {...}}
        data = msg.get("data") if isinstance(msg.get("data"), dict) else msg
        if "stream" in msg:
            stream = str(msg.get("stream") or "")
            fapi, channel = stream.rsplit("@", 1) if "@" in stream else ("", "")
        else:
            fapi = str(data.get("s") or "")
            channel = str(data.get("e") or "")
        symbol = _symbol_from_fapi(fapi)
        now = time.time()
        with self._lock:
            sub = self._subs.get(symbol)
            if not sub:
                return
            sub["last_msg"] = now
            if channel == "trade" or channel == "aggTrade":
                m = bool(data.get("m"))
                # m=true: покупатель был maker'ом -> агрессор-ПРОДАВЕЦ (sell)
                side = "sell" if m else "buy"
                price = _f(data.get("p"))
                amount = _f(data.get("q"))
                ts = float(data.get("T") or now * 1000.0) / 1000.0
                if price > 0 and amount > 0:
                    sub["trades"].append((ts, side, price, amount))
                    sub["last_trade"] = now
            elif channel == "bookTicker":
                b = _f(data.get("b"))
                a = _f(data.get("a"))
                if b > 0 and a > 0:
                    sub["book"] = {"b": b, "a": a, "bq": _f(data.get("B")), "aq": _f(data.get("A")),
                                   "_ts": now}
                    sub["book_hist"].append((now, b, a, _f(data.get("B")), _f(data.get("A"))))


def _stream_params(symbols: list) -> list:
    """@trade даёт каждую сделку (на этой сети aggTrade не проходит) + bookTicker."""
    params = []
    for symbol in symbols:
        fapi = _symbol_to_fapi(symbol).lower()
        params.append(f"{fapi}@trade")
        params.append(f"{fapi}@bookTicker")
    return params


def _spread_bps(bid, ask):
    try:
        if bid and ask and bid > 0:
            return (ask - bid) / bid * 10000.0
    except (TypeError, ValueError):
        pass
    return None
