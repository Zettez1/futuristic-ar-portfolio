import asyncio
import json
import time
from collections import deque

import ccxt
import websockets

SPOT_WS = "wss://wbs.mexc.com/ws"


class MexcClient:
    def __init__(self, api_key: str = "", secret: str = "", futures: bool = False):
        self.futures = futures
        params = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "timeout": 20000,
        }
        self.ex = ccxt.mexc(params)
        self.ex.options["defaultType"] = "swap" if futures else "spot"
        self.trade_feed = {}
        self.depth_feed = {}

    def load_markets(self):
        try:
            self.ex.load_markets()
        except Exception:
            pass

    def set_leverage(self, symbol: str, leverage: float):
        if not self.futures:
            return False
        try:
            for position_type in (1, 2):
                self.ex.set_leverage(leverage, symbol, {"openType": 2, "positionType": position_type})
            return True
        except Exception:
            return False

    def fetch_klines(self, symbol: str, timeframe: str, limit: int = 300) -> list:
        try:
            return self.ex.fetch_ohlcv(symbol, timeframe, limit=limit)
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

    def create_order(self, symbol: str, side: str, amount: float, price: float = None, order_type: str = "limit") -> dict:
        try:
            if order_type == "market":
                return self.ex.create_order(symbol, "market", side, amount)
            return self.ex.create_order(symbol, "limit", side, amount, price)
        except Exception as e:
            return {"error": str(e)}

    def cancel_all(self, symbol: str):
        try:
            self.ex.cancel_all_orders(symbol)
        except Exception:
            pass

    def sanitize_qty(self, symbol: str, qty: float, price: float) -> float:
        return qty

    def position_size(self, symbol: str, side: str) -> float:
        return 0.0

    def open_market_with_stops(self, symbol: str, side: str, amount: float,
                               stop_loss: float = None, take_profit: float = None,
                               wait_s: float = 10.0) -> dict:
        try:
            self.cancel_all(symbol)
            return self.create_order(symbol, side, amount, None, "market")
        except Exception as e:
            return {"error": str(e)}

    def close_position(self, symbol: str, side: str, qty: float) -> bool:
        try:
            self.cancel_all(symbol)
            reduce_side = "sell" if side == "long" else "buy"
            res = self.create_order(symbol, reduce_side, qty, None, "market")
            return not res.get("error")
        except Exception:
            return False

    async def ws_stream(self, symbols: list, on_trade=None, on_depth=None):
        if self.futures:
            return
        params = []
        for s in symbols:
            params.append(f"spot@public.deals.v3.api@{s}")
            params.append(f"spot@public.increase.depth.v3.api@{s}")
        sub = {"method": "SUBSCRIPTION", "params": params, "id": 1}
        while True:
            try:
                async with websockets.connect(SPOT_WS, ping_interval=20, max_size=2 ** 23) as ws:
                    await ws.send(json.dumps(sub))
                    while True:
                        msg = json.loads(await ws.recv())
                        if msg.get("method") == "PING":
                            await ws.send(json.dumps({"method": "PONG"}))
                            continue
                        channel = msg.get("c", "")
                        if not channel:
                            continue
                        symbol = channel.split("@")[-1]
                        d = msg.get("d", {}) or {}
                        if "deals" in channel:
                            for t in d.get("deals", []):
                                side = "buy" if t.get("S") == 1 else "sell"
                                self.trade_feed.setdefault(symbol, deque(maxlen=2000)).appendleft(
                                    {"price": float(t["p"]), "amount": float(t["v"]), "side": side, "ts": int(t.get("t", 0))}
                                )
                                if on_trade:
                                    on_trade(symbol, self.trade_feed[symbol])
                        elif "depth" in channel:
                            self.depth_feed[symbol] = d
                            if on_depth:
                                on_depth(symbol, d)
            except Exception:
                await asyncio.sleep(5)
