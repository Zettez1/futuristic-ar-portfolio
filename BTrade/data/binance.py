import asyncio
import time

import ccxt

from core.logger import get_logger

log = get_logger("binance")

STABLECOIN_BASES = frozenset({
    "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDP", "USDE", "USD1", "USDS",
    "UST", "USTC", "EUR", "EURI", "TRY", "BRL", "GBP", "AEUR",
})


_INTERVAL_MAP = {
    "240m": "4h",
    "240": "4h",
}


def _exchange_timeframe(timeframe: str) -> str:
    return _INTERVAL_MAP.get(str(timeframe or "").strip().lower(), timeframe)


def _price_tick(exchange, symbol: str, price: float) -> float:
    """Минимальный шаг цены в валюте (тик), защищённый от плохих precision."""
    try:
        market = exchange.market(symbol)
        precision = market.get("precision")
        if isinstance(precision, dict):
            p = precision.get("price")
            if isinstance(p, dict):
                p = p.get("tickSize")
            if isinstance(p, (int, float)) and float(p) > 0:
                if float(p) >= 1:
                    return 10.0 ** -float(p)
                return float(p)
    except Exception:
        pass
    return max(float(price) * 1e-6, 1e-8)


def _algo_order_type(order: dict) -> str:
    """Тип algo-ордера Binance: поле orderType (Algo API) либо type (ccxt)."""
    return str(order.get("orderType") or order.get("type") or "").upper()


class BinanceClient:
    def __init__(self, api_key: str = "", secret: str = "", futures: bool = False):
        self.futures = futures
        params = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "timeout": 20000,
            "options": {
                "defaultType": "swap" if futures else "spot",
                "adjustForTimeDifference": True,
            },
        }
        self.ex = ccxt.binanceusdm(params) if futures else ccxt.binance(params)
        self.trade_feed = {}
        self.depth_feed = {}
        self.applied_leverage = {}
        self.leverage_limits = {}
        self._leverage_limits_loaded = False

    def load_markets(self):
        try:
            self.ex.load_markets()
        except Exception:
            pass

    def discover_trade_symbols(self, exclude_stablecoins: bool = True) -> list:
        """Return active Binance USDT markets suitable for the configured market type."""
        markets = self.ex.markets or {}
        symbols = []
        for market in markets.values():
            if market.get("active") is False:
                continue
            if self.futures:
                if not market.get("contract") or not (market.get("swap") or market.get("future")):
                    continue
                if market.get("settle") != "USDT" and market.get("quote") != "USDT":
                    continue
            else:
                if market.get("spot") is False or market.get("quote") != "USDT":
                    continue
            base = str(market.get("base") or "").upper()
            if exclude_stablecoins and base in STABLECOIN_BASES:
                continue
            symbol = market.get("symbol")
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        return sorted(symbols)

    def set_leverage(self, symbol: str, leverage: float) -> bool:
        if not self.futures:
            return False
        self._load_leverage_limits()
        market_id = None
        try:
            market_id = self.ex.market(symbol)["id"]
        except Exception:
            market_id = symbol.replace("/", "").replace(":USDT", "")
        requested = max(1, int(round(leverage)))
        maximum = self.leverage_limits.get(market_id)
        lev = min(requested, maximum) if maximum else requested
        try:
            self.ex.set_margin_mode("cross", symbol)
        except Exception:
            pass  # уже cross либо открыта позиция — не критично
        candidates = [lev] + [value for value in (10, 5, 3, 2, 1) if value < lev]
        last_err = None
        for candidate in candidates:
            try:
                self.ex.set_leverage(candidate, symbol)
                self.applied_leverage[symbol] = candidate
                if candidate != requested:
                    log.debug(f"set_leverage({symbol}): {requested}x недоступно, установлено {candidate}x")
                return True
            except Exception as exc:
                last_err = exc
            try:
                self.ex.fapiPrivatePostLeverage({"symbol": market_id, "leverage": candidate})
                self.applied_leverage[symbol] = candidate
                if candidate != requested:
                    log.debug(f"set_leverage({symbol}): {requested}x недоступно, установлено {candidate}x")
                return True
            except Exception as exc:
                last_err = exc
        log.warning(f"set_leverage({symbol}, requested={requested}x) не удалось: {last_err!r}")
        return False

    def _load_leverage_limits(self):
        if self._leverage_limits_loaded or not self.futures:
            return
        self._leverage_limits_loaded = True
        try:
            raw = self.ex.fapiPrivateGetLeverageBracket({})
            rows = raw.get("data", []) if isinstance(raw, dict) else raw
            for row in rows or []:
                brackets = row.get("brackets") or []
                if brackets and row.get("symbol"):
                    self.leverage_limits[str(row["symbol"])] = max(
                        int(float(bracket.get("initialLeverage") or 1)) for bracket in brackets
                    )
        except Exception as exc:
            log.warning(f"Не удалось получить leverage brackets Binance, будет применён fallback: {exc!r}")

    def account_equity(self) -> float:
        """Реальный баланс USDT на фьючерс-кошельке Binance."""
        try:
            bal = self.ex.fetch_balance()
            return float(bal.get("USDT", {}).get("total") or 0.0)
        except Exception as e:
            log.warning(f"fetch_balance: не удалось получить баланс кошелька: {e!r}")
            return 0.0

    def fetch_klines(self, symbol: str, timeframe: str, limit: int = 300) -> list:
        try:
            interval = _exchange_timeframe(timeframe)
            return self.ex.fetch_ohlcv(symbol, interval, limit=limit)
        except Exception:
            return []

    def fetch_ticker(self, symbol: str) -> dict:
        try:
            t = self.ex.fetch_ticker(symbol)
            return {
                "last": t.get("last"),
                "bid": t.get("bid"),
                "ask": t.get("ask"),
                "volume": t.get("baseVolume"),
                "quoteVolume": t.get("quoteVolume"),
                "change24h": t.get("percentage"),
            }
        except Exception:
            return {}

    def fetch_orderbook(self, symbol: str, limit: int = 50) -> dict:
        try:
            ob = self.ex.fetch_order_book(symbol, limit=limit)
            return {"bids": ob.get("bids", [])[:limit], "asks": ob.get("asks", [])[:limit]}
        except Exception:
            return {"bids": [], "asks": []}

    def fetch_recent_trades(self, symbol: str, limit: int = 300) -> list:
        try:
            tr = self.ex.fetch_trades(symbol, limit=limit)
            return [
                {"price": t["price"], "amount": t["amount"], "side": t["side"], "ts": t["timestamp"]}
                for t in tr
            ]
        except Exception:
            return []

    def fetch_funding_rate(self, symbol: str):
        if not self.futures:
            return None
        try:
            fr = self.ex.fetch_funding_rate(symbol)
            return fr.get("fundingRate")
        except Exception:
            return None

    def fetch_open_interest(self, symbol: str):
        if not self.futures:
            return None
        try:
            oi = self.ex.fetch_open_interest(symbol)
            return oi.get("openInterestAmount") or oi.get("openInterestValue")
        except Exception:
            return None

    def fetch_balance(self) -> dict:
        try:
            return self.ex.fetch_balance()
        except Exception:
            return {}

    def create_order(self, symbol: str, side: str, amount: float, price: float = None, order_type: str = "limit",
                     reduce_only: bool = False) -> dict:
        try:
            params = {"reduceOnly": True} if reduce_only else {}
            if order_type == "market":
                return self.ex.create_order(symbol, "market", side, amount, None, params)
            return self.ex.create_order(symbol, "limit", side, amount, price, params)
        except Exception as e:
            return {"error": str(e)}

    def cancel_all(self, symbol: str):
        try:
            self.ex.cancel_all_orders(symbol)
        except Exception:
            pass
        try:
            market_id = self.ex.market(symbol)["id"]
            if hasattr(self.ex, "fapiPrivateDeleteAlgoOpenOrders"):
                self.ex.fapiPrivateDeleteAlgoOpenOrders({"symbol": market_id})
            else:
                self.ex.cancel_all_orders(symbol, {"conditional": True})
        except Exception:
            pass

    def fetch_open_algo_orders(self, symbol: str) -> list:
        """Получает активные условные ордера через Binance Algo Order API."""
        try:
            if not self.ex.markets:
                self.load_markets()
            market_id = self.ex.market(symbol)["id"]
            if hasattr(self.ex, "fapiPrivateGetOpenAlgoOrders"):
                response = self.ex.fapiPrivateGetOpenAlgoOrders({"symbol": market_id})
                if isinstance(response, dict):
                    return response.get("orders") or []
                return response or []
        except Exception as exc:
            log.warning(f"{symbol}: fetch_open_algo_orders: {exc!r}")
        return []

    def lot_step(self, symbol: str, default: float = 1e-8) -> float:
        try:
            for f in self.ex.market(symbol).get("info", {}).get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    return float(f.get("stepSize") or default)
        except Exception:
            pass
        return default

    def min_notional(self, symbol: str, default: float = 5.0) -> float:
        try:
            for f in self.ex.market(symbol).get("info", {}).get("filters", []):
                if f.get("filterType") == "MIN_NOTIONAL":
                    return float(f.get("notional") or f.get("minNotional") or default)
        except Exception:
            pass
        return default

    def sanitize_qty(self, symbol: str, qty: float, price: float) -> float:
        """Округляет объём вверх до шага лота и до минимального ноционала Binance (>=5 USDT)."""
        import math
        if not qty or qty <= 0:
            return qty
        step = self.lot_step(symbol)
        price = price or 0.0
        target = qty
        if price > 0:
            target = max(qty, self.min_notional(symbol) / price)
        if step and step > 0:
            return math.ceil(target / step - 1e-9) * step
        return target

    def position_size(self, symbol: str, side: str) -> float:
        """Размер позиции на бирже для символа (0 = нет позиции). Работает и в one-way, и в hedge-режиме."""
        size = self.position_size_checked(symbol, side)
        return 0.0 if size is None else size

    def position_size_checked(self, symbol: str, side: str):
        """Возвращает размер позиции или None, если запрос состояния биржи завершился ошибкой."""
        try:
            positions = self.ex.fetch_positions([symbol])
            for p in positions:
                size = float(p.get("contracts") or 0.0)
                if size == 0:
                    continue
                pside = p.get("side")
                if pside is not None:
                    if pside == side:
                        return abs(size)
                elif (side == "long" and size > 0) or (side == "short" and size < 0):
                    return abs(size)
        except Exception:
            return None
        return 0.0

    def fetch_live_positions(self, symbols=None):
        """Возвращает реальные открытые позиции или None при ошибке запроса."""
        try:
            positions = self.ex.fetch_positions(symbols)
        except Exception as e:
            log.warning(f"fetch_positions: не удалось получить реальные позиции: {e!r}")
            return None
        allowed = set(symbols or [])
        out = []
        for p in positions:
            symbol = p.get("symbol")
            if allowed and symbol not in allowed:
                continue
            contracts = abs(float(p.get("contracts") or 0.0))
            if contracts <= 0:
                continue
            side = p.get("side")
            if side not in ("long", "short"):
                raw_amt = float((p.get("info") or {}).get("positionAmt") or 0.0)
                side = "long" if raw_amt >= 0 else "short"
            entry = float(p.get("entryPrice") or p.get("average") or 0.0)
            if not symbol or entry <= 0:
                continue
            out.append({
                "symbol": symbol,
                "side": side,
                "qty": contracts,
                "entry": entry,
                "mark": float(p.get("markPrice") or 0.0),
            })
        return out

    def fetch_position_exit(self, symbol: str, side: str, qty: float, opened_at: float,
                            entry_price: float = None):
        """Возвращает фактический fill закрытия и комиссии по уже исчезнувшей позиции."""
        close_side = "sell" if side == "long" else "buy"
        entry_side = "buy" if side == "long" else "sell"
        try:
            since = max(0, int((opened_at - 2.0) * 1000))
            trades = self.ex.fetch_my_trades(symbol, since=since, limit=1000)
        except Exception as e:
            log.warning(f"{symbol}: не удалось получить fills закрытия: {e!r}")
            return None

        def trade_ts(trade):
            return int(trade.get("timestamp") or 0)

        def amount(trade):
            try:
                return abs(float(trade.get("amount") or 0.0))
            except Exception:
                return 0.0

        def fee_usdt(trade):
            fee = trade.get("fee") or {}
            if not fee and trade.get("fees"):
                fee = (trade.get("fees") or [{}])[0]
            currency = str(fee.get("currency") or "USDT").upper()
            if currency not in ("USDT", "USD", "BUSD"):
                return 0.0
            return abs(float(fee.get("cost") or 0.0))

        close_fills = [t for t in sorted(trades, key=trade_ts)
                       if t.get("side") == close_side and amount(t) > 0]
        if not close_fills:
            return None

        remaining = abs(float(qty or 0.0))
        matched = []
        for trade in close_fills:
            if remaining <= 1e-12:
                break
            take = min(remaining, amount(trade))
            if take <= 0:
                continue
            matched.append((trade, take))
            remaining -= take
        if not matched:
            return None

        total_qty = sum(take for _, take in matched)
        price = sum(float(trade.get("price") or 0.0) * take for trade, take in matched) / total_qty
        first_close_ts = trade_ts(matched[0][0])
        entry_fills = [t for t in sorted(trades, key=trade_ts)
                       if t.get("side") == entry_side and amount(t) > 0
                       and trade_ts(t) <= first_close_ts]
        if not entry_fills and entry_price:
            try:
                history = self.ex.fetch_my_trades(symbol, limit=1000)
                entry_fills = [t for t in sorted(history, key=trade_ts)
                               if t.get("side") == entry_side and amount(t) > 0
                               and trade_ts(t) <= first_close_ts
                               and abs(float(t.get("price") or 0.0) - entry_price) / max(entry_price, 1e-12) <= 0.002]
            except Exception:
                entry_fills = []
        entry_remaining = abs(float(qty or 0.0))
        entry_fee = 0.0
        for trade in reversed(entry_fills):
            if entry_remaining <= 1e-12:
                break
            take = min(entry_remaining, amount(trade))
            entry_remaining -= take
            entry_fee += fee_usdt(trade) * (take / amount(trade))
        exit_fee = sum(fee_usdt(trade) * (take / amount(trade)) for trade, take in matched)
        return {
            "price": price,
            "qty": total_qty,
            "entry_fee_usdt": entry_fee,
            "exit_fee_usdt": exit_fee,
            "order_ids": [trade.get("order") for trade, _ in matched if trade.get("order")],
        }

    def open_market_with_stops(self, symbol: str, side: str, amount: float,
                               stop_loss: float = None, take_profit: float = None,
                               wait_s: float = 10.0) -> dict:
        """Рыночный вход на Binance Futures with a protective stop only."""
        try:
            self.cancel_all(symbol)
            entry = self.ex.create_order(symbol, "market", side, amount)
            confirmed = self._wait_position(symbol, side, wait_s)
            placed, protection_errors = self.place_position_protection(
                symbol, "long" if side == "buy" else "short", amount, stop_loss, take_profit)
            result = dict(entry, protect=placed)
            if not confirmed:
                result["warning"] = "позиция не подтверждена на бирже — защитные ордера выставлены без подтверждения"
            if protection_errors:
                result["warning"] = "защита выставлена частично: " + "; ".join(protection_errors)
            if stop_loss and not any(str(p).startswith("SL") for p in placed):
                closed = self.close_position(symbol, "long" if side == "buy" else "short", amount)
                result["error"] = ("рыночный вход отменён: стоп-лосс не выставлен"
                                    if closed else
                                    "рыночный вход выполнен, но стоп-лосс не выставлен и аварийное закрытие не подтверждено")
            return result
        except Exception as e:
            return {"error": str(e)}

    def _place_protect_order(self, symbol: str, side: str, amount: float, order_type: str, trigger_price: float):
        """Размещает close-position условный ордер через новый Binance Algo Order API."""
        market_id = self.ex.market(symbol)["id"]
        trigger = self.ex.price_to_precision(symbol, trigger_price)
        params = {
            "algoType": "CONDITIONAL",
            "symbol": market_id,
            "side": side.upper(),
            "type": order_type,
            "triggerPrice": trigger,
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        }
        if hasattr(self.ex, "fapiPrivatePostAlgoOrder"):
            return self.ex.fapiPrivatePostAlgoOrder(params)
        return self.ex.create_order(
            symbol, order_type, side, amount, None,
            {"triggerPrice": trigger, "closePosition": True, "workingType": "MARK_PRICE"},
        )

    def place_position_protection(self, symbol: str, side: str, amount: float,
                                  stop_loss: float = None, take_profit: float = None):
        """Ставит защитные close-position ордера и возвращает (размещённые, ошибки)."""
        close_side = "sell" if side == "long" else "buy"
        placed = []
        errors = []
        existing = self.fetch_open_algo_orders(symbol)
        existing_types = {_algo_order_type(o) for o in existing}
        for label, order_type, trigger in (("SL", "STOP_MARKET", stop_loss),):
            if not trigger:
                continue
            if order_type in existing_types:
                placed.append(label)
                continue
            try:
                self._place_protect_order(symbol, close_side, amount, order_type, trigger)
                placed.append(label)
            except Exception as e:
                retried = False
                msg = str(e)
                if ("OrderImmediatelyFillable" in msg) or ("-2021" in msg):
                    try:
                        last = float(self.fetch_ticker(symbol).get("last") or 0.0)
                        if last > 0:
                            tick = _price_tick(self.ex, symbol, last)
                            buff = max(last * 1e-4, 4 * tick)
                            retry_trigger = (min(trigger, last - buff) if close_side == "sell"
                                             else max(trigger, last + buff))
                            if retry_trigger > 0:
                                self._place_protect_order(symbol, close_side, amount, order_type, retry_trigger)
                                placed.append(f"{label}*")
                                retried = True
                    except Exception as e2:
                        log.error(f"{symbol}: ретрай SL не удался: {e2!r}")
                if not retried:
                    errors.append(f"{label}: {e}")
                    log.error(f"{symbol}: не удалось выставить {label} на Binance: {e!r}")
        return placed, errors

    def _wait_position(self, symbol: str, side: str, timeout: float) -> bool:
        position_side = "long" if side == "buy" else ("short" if side == "sell" else side)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                positions = self.ex.fetch_positions([symbol])
                for p in positions:
                    size = float(p.get("contracts") or 0.0)
                    if size == 0:
                        continue
                    pside = p.get("side")
                    if pside is not None:
                        if pside == position_side:
                            return True
                    elif ((position_side == "long" and size > 0)
                          or (position_side == "short" and size < 0)):
                        return True
            except Exception:
                pass
            time.sleep(0.4)
        return False

    def update_stop_market(self, symbol: str, side: str, amount: float, new_sl: float) -> bool:
        """Переносит единственный защитный стоп-лосс на бирже."""
        try:
            current = None
            orders = self.fetch_open_algo_orders(symbol)
            for o in orders:
                t = _algo_order_type(o)
                if t in ("STOP", "STOP_MARKET"):
                    current = o.get("stopPrice") or o.get("triggerPrice") or o.get("price")
                    break
            if current is not None and abs(float(current) - new_sl) / new_sl < 5e-4:
                return True  # близко к текущему — не трогаем и не спамим API
            for o in orders:
                t = _algo_order_type(o)
                if t in ("STOP", "STOP_MARKET"):
                    algo_id = o.get("algoId") or o.get("id")
                    market_id = self.ex.market(symbol)["id"]
                    if hasattr(self.ex, "fapiPrivateDeleteAlgoOrder"):
                        self.ex.fapiPrivateDeleteAlgoOrder({"symbol": market_id, "algoId": algo_id})
                    else:
                        self.ex.cancel_order(algo_id, symbol, {"conditional": True})
            close_side = "sell" if side == "long" else "buy"
            self._place_protect_order(symbol, close_side, amount, "STOP_MARKET", new_sl)
            return True
        except Exception as exc:
            log.warning(f"{symbol}: перенос SL не удался: {exc!r}")
            return False

    def close_position(self, symbol: str, side: str, qty: float) -> bool:
        """Закрывает позицию на бирже рыночным ордером. True если позиции уже нет либо закрыта."""
        try:
            self.cancel_all(symbol)
            size = self.position_size(symbol, side)
            if size <= 0:
                return True
            reduce_side = "sell" if side == "long" else "buy"
            res = self.create_order(symbol, reduce_side, min(qty, size) or size, None, "market", reduce_only=True)
            return not res.get("error")
        except Exception:
            return False

    async def ws_stream(self, symbols: list, on_trade=None, on_depth=None):
        return
