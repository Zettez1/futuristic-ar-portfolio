import asyncio
import ctypes
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.config import Config
from core.counters import entry_counters
from core.dashboard import Dashboard
from core.logger import get_logger
from core.models import Position, Signal
from core.reporter import Reporter
from data.binance import BinanceClient
from data.mexc import MexcClient
from execution.engine import ExecutionEngine
from learning.journal import TradeJournal
from risk.manager import RiskManager
from strategies.ma_cross import MaCrossStrategy


log = get_logger("main")
BOT_DIR = os.path.dirname(os.path.abspath(__file__))


def make_client(cfg):
    if cfg.exchange == "binance":
        return BinanceClient(cfg.binance_api_key, cfg.binance_secret, cfg.futures)
    return MexcClient(cfg.mexc_api_key, cfg.mexc_secret, cfg.futures)


def _tf_minutes(timeframe: str) -> int:
    value = str(timeframe or "").lower()
    if value.endswith("m"):
        return max(1, int(value[:-1] or 0))
    if value.endswith("h"):
        return max(1, int(value[:-1] or 0) * 60)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 3


def _drop_forming(rows: list, tf_minutes: int) -> list:
    """Оставляет только ЗАКРЫТЫЕ свечи: последнюю формирующуюся отбрасывает."""
    if not rows:
        return []
    step_ms = int(tf_minutes) * 60_000
    now = int(time.time() * 1000.0)
    return [row for row in rows if now >= int(row[0]) + step_ms]


def build_reversal_signal(pos, price, rows_ltf=None, strategy=None):
    """Разворот после выбития по НАЧАЛЬНОМУ стоп-лоссу при 3m-перекрёсте против.

    Условия:
      - позиция закрыта по стоп-лоссу (не по трейлинг-стопу);
      - выбило именно по НАЧАЛЬНОМУ стопу (SL не был подтянут);
      - во время открытой позиции 3m-индикатор пересёк в противоположную сторону
        (лонг выбит + 3m вниз -> моментальный шорт, зеркально для шорта);
      - ИЛИ: EMAs очень близки друг к другу и цена уже против позиции на момент выбития
        (почти перекрёст — см. MaCrossStrategy.near_cross_against).

    SL разворота — та же дистанция, что и у закрытой позиции.
    Возвращает Signal или None.
    """
    if getattr(pos, "status", "open") != "closed":
        return None
    if pos.reason not in ("Стоп-лосс", "Стоп-лосс Binance"):
        return None
    if not price or price <= 0:
        return None
    feats = pos.features or {}
    cross_dir = int(feats.get("ltf_cross_dir") or 0)
    initial_stop = float(feats.get("initial_stop_loss") or 0.0)
    if initial_stop <= 0:
        return None
    tolerance = max(abs(pos.entry) * 1e-4, 1e-12)
    if abs(float(pos.stop_loss) - initial_stop) > tolerance:
        return None  # стоп уже подтянут трейлингом — это не начальный стоп
    distance = abs(pos.entry - initial_stop)
    if distance <= 0:
        return None
    side = None
    reason = None
    if pos.side == "long" and cross_dir == -1:
        side, reason = "short", f"3m-перекрёст ВНИЗ во время позиции"
    elif pos.side == "short" and cross_dir == 1:
        side, reason = "long", "3m-перекрёст ВВЕРХ во время позиции"
    elif rows_ltf is not None and strategy is not None:
        if strategy.near_cross_against(pos.side, price, rows_ltf):
            d = 1 if pos.side == "long" else -1
            gap = strategy.ema_gap_pct(rows_ltf) * d
            side = "short" if pos.side == "long" else "long"
            reason = (f"EMAs почти перекрестились (зазор {gap:+.3f}% против позиции), "
                      f"цена уже против — разворот без закрытого перекрёста")
    if side is None:
        return None
    stop_loss = price + distance if side == "short" else price - distance
    return Signal(
        symbol=pos.symbol, side=side, entry=price, stop_loss=stop_loss,
        take_profit=None, confidence=0.8, strategy=pos.strategy, timeframe="3m",
        reason=(f"разворот: {pos.side.upper()} выбита по начальному стоп-лоссу, "
                f"{reason} -> {side.upper()}"),
        features={"reversal_of": pos.id, "reversal_cross_dir": cross_dir,
                  "reversal_sl_distance": distance},
    )


def parse_hhmm(s: str):
    parts = str(s or "").strip().split(":")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return 9, 0


def _next_weekday(day, h, m):
    """Ближайший будний день (пн-пт) в h:m после day."""
    nxt = (day + timedelta(days=1)).replace(hour=h, minute=m)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def schedule_state(now, wake: str = "09:00", sleep: str = "20:30", weekdays_only: bool = True):
    """Торговое окно: wake <= now < sleep (локальное «наивное» время).

    При weekdays_only=True в субботу/воскресенье бот спит до понедельника wake.
    Возвращает (active, sleep_until): active — торговать ли сейчас,
    sleep_until — datetime, когда надо проснуться (None, если окно активное).
    """
    wh, wm = parse_hhmm(wake)
    sh, sm = parse_hhmm(sleep)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    w = day.replace(hour=wh, minute=wm)
    s = day.replace(hour=sh, minute=sm)
    if weekdays_only and now.weekday() >= 5:
        return False, _next_weekday(day, wh, wm)
    if w <= now < s:
        return True, None
    if now < w:
        return False, w
    if not weekdays_only:
        return False, w + timedelta(days=1)
    return False, _next_weekday(day, wh, wm)


def disable_quickedit():
    """Prevent a mouse click from pausing the Windows console process."""
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value & ~0x0040 | 0x0080)
    except Exception:
        pass


def write_heartbeat(scan_no: int):
    try:
        with open(os.path.join(BOT_DIR, "db", "heartbeat.json"), "w") as heartbeat:
            heartbeat.write(f'{{"scan": {scan_no}, "ts": {time.time()}}}\n')
    except Exception:
        pass


class TradingBot:
    def __init__(self):
        self.cfg = Config()
        self.reporter = Reporter(self.cfg)
        self.dashboard = Dashboard()
        self.risk = RiskManager(self.cfg)
        self.counters = entry_counters
        self.client = make_client(self.cfg)
        self.volatility_map = {}
        self.refresh_symbol_universe()
        self.journal = TradeJournal()
        self.scan_cursor = 0
        self._schedule_active = False
        self._schedule_sleep_logged = False

        self.engine = ExecutionEngine(
            self.client,
            self.journal,
            paper=self.cfg.paper_trading,
            on_close=self.risk.record_pnl,
            trail_enabled=self.cfg.trail_enabled,
            trail_activation=self.cfg.trail_activation,
            trail_distance=self.cfg.trail_distance,
            trail_break_even_r=self.cfg.trail_break_even_r,
            trail_start_r=self.cfg.trail_start_r,
            trail_distance_r=self.cfg.trail_distance_r,
            exit_evaluator=None,
            take_profit_enabled=False,
            scale_out_enabled=False,
        )
        self.live_equity_ready = self.cfg.paper_trading
        reset_ts = float(os.environ.get("DAY_RESET_TS") or 0)
        if self.cfg.paper_trading:
            if self.cfg.paper_reset_equity:
                self.risk.set_equity(self.cfg.equity)
                log.info(f"Бумажный счёт сброшен: капитал {self.cfg.equity:,.2f} USDT (PAPER_RESET_EQUITY=1)")
            else:
                self.risk.set_equity(self.cfg.equity + self.journal.total_pnl())
            now = time.localtime()
            day_start = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
            if reset_ts > 0:
                day_start = max(day_start, reset_ts)
            day_pnl = self.journal.pnl_since(day_start)
            self.risk.restore_day(day_start, day_pnl)
            if self.risk.is_killed:
                log.warning(f"Дневной лимит убытка уже пробит ({day_pnl:+.2f} USDT за сегодня) — "
                            f"новые входы заблокированы до конца дня")
        else:
            try:
                wallet = self.client.account_equity() if hasattr(self.client, "account_equity") else 0.0
            except Exception:
                wallet = 0.0
            if wallet > 0:
                self.risk.set_equity(wallet)
                self.live_equity_ready = True
                log.info(f"Реальный баланс фьючерс-кошелька: {wallet:,.2f} USDT")
            else:
                self.risk.set_equity(0.0)
                log.error("Не удалось получить баланс счёта — реальные входы заблокированы")
            if reset_ts > 0:
                day_start = max(self.risk.day_start, reset_ts)
                day_pnl = self.journal.pnl_since(day_start)
                self.risk.restore_day(day_start, day_pnl)
                if self.risk.is_killed:
                    log.warning(f"Дневной лимит убытка уже пробит ({day_pnl:+.2f} USDT за сегодня) — "
                                f"новые входы заблокированы до конца дня")

        purged = self.journal.purge_stale_open(self.cfg.trade_symbols)
        if purged:
            log.info(f"Удалены позиции вне списка монет: {purged}")
        restored = 0
        for trade in self.journal.open_trades():
            if trade["symbol"] not in self.cfg.trade_symbols:
                continue
            pos = Position(
                id=trade["id"], symbol=trade["symbol"], side=trade["side"], entry=trade["entry"], qty=trade["qty"],
                stop_loss=trade["stop_loss"], take_profit=None, strategy=trade["strategy"],
                opened_at=trade["opened_at"],
                features={"initial_stop_loss": trade["stop_loss"], "initial_risk": abs(trade["entry"] - trade["stop_loss"])},
                fees=float(trade.get("fees") or 0.0),
            )
            self.engine.positions[pos.id] = pos
            restored += 1
        if restored:
            log.info(f"Восстановлено открытых позиций: {restored}")

        self.strategies = [MaCrossStrategy(self.cfg)]
        self.tf_ltf = self.strategies[0].tf_ltf
        self.tf_htf = self.strategies[0].tf_htf
        self.kline_cfg = {
            self.tf_ltf: max(30.0, 10.0 * self.strategies[0].ltf_minutes),
            self.tf_htf: max(60.0, 10.0 * self.strategies[0].htf_minutes),
        }
        self.symbols_state = {}
        self.kline_cache = {}
        self.last_no_entry_summary = ""
        self.snapshot_pool = ThreadPoolExecutor(max_workers=max(1, self.cfg.scan_pool_workers),
                                                thread_name_prefix="market")

    def refresh_symbol_universe(self):
        """Все активные USDT-рынки Binance, кроме стейблкоинов, с |24h change| >= порога."""
        if not self.cfg.scan_all_symbols or self.cfg.exchange != "binance":
            return
        discover = getattr(self.client, "discover_trade_symbols", None)
        if not discover:
            log.warning("Автосканирование монет недоступно, используется SYMBOLS из .env")
            return
        self.client.load_markets()
        discovered = discover(exclude_stablecoins=True)
        if not discovered:
            log.warning("Binance не вернул подходящие рынки, используется SYMBOLS из .env")
            return
        self.volatility_map = {}
        self._sort_symbols_by_volatility(discovered)
        discovered = self._filter_low_volatility(discovered)
        self.cfg.symbols = discovered
        log.info(f"Universe Binance: {len(discovered)} активных USDT-рынков, "
                 f"стейблкоины исключены, |24h change| "
                 f"в диапазоне [{self.cfg.min_24h_volatility:g}% .. {self.cfg.max_24h_volatility:g}%]")

    def _sort_symbols_by_volatility(self, symbols: list) -> None:
        """Сортирует список рынков по 24h волатильности (убывание)."""
        try:
            tickers = self.client.ex.fetch_tickers()
        except Exception as exc:
            log.warning(f"Сортировка по волатильности недоступна, оставляю алфавитный порядок: {exc}")
            return
        change = {}
        for sym, t in (tickers or {}).items():
            h = t.get("high")
            l = t.get("low")
            if h and l and float(h) > 0 and float(l) > 0:
                mid = (float(h) + float(l)) / 2.0
                change[sym] = (float(h) - float(l)) / mid * 100.0
            else:
                change[sym] = abs(float(t.get("percentage") or 0.0))
        self.volatility_map = dict(change)
        symbols.sort(key=lambda s: change.get(s, 0.0), reverse=True)
        log.info("Список рынков отсортирован по 24h волатильности (убывание)")

    def _filter_low_volatility(self, symbols: list) -> list:
        min_th = float(getattr(self.cfg, "min_24h_volatility", 0.0))
        max_th = float(getattr(self.cfg, "max_24h_volatility", 0.0))
        if min_th <= 0 and max_th <= 0:
            return symbols
        keep = []
        dropped_low = 0
        dropped_high = 0
        for s in symbols:
            v = float(self.volatility_map.get(s) or 0.0)
            if min_th > 0 and v < min_th:
                dropped_low += 1
                continue
            if max_th > 0 and v > max_th:
                dropped_high += 1
                continue
            keep.append(s)
        parts = []
        if dropped_low:
            parts.append(f"< {min_th:g}%: {dropped_low}")
        if dropped_high:
            parts.append(f"> {max_th:g}%: {dropped_high}")
        if parts:
            log.info(f"Фильтр волатильности: отсеяно {dropped_low + dropped_high} монет "
                     f"({' | '.join(parts)})")
        return keep

    def fetch_market(self, symbol: str):
        """Единственные данные для решения: свечи LTF и HTF + последняя цена."""
        try:
            klines = {}
            now = time.time()
            for tf, ttl in self.kline_cfg.items():
                key = f"{symbol}|{tf}"
                cached = self.kline_cache.get(key)
                if cached is None or now - cached[0] >= ttl:
                    rows = self.client.fetch_klines(symbol, tf, limit=500 if tf == self.tf_ltf else 100)
                    rows = _drop_forming(rows, _tf_minutes(tf))
                    self.kline_cache[key] = (now, rows)
                klines[tf] = self.kline_cache[key][1]
            last_price = 0.0
            ticker = self.client.fetch_ticker(symbol)
            if ticker:
                last_price = float(ticker.get("last") or 0.0)
            if not last_price:
                rows = klines.get(self.tf_ltf) or []
                last_price = float(rows[-1][4]) if rows else 0.0
            return {"symbol": symbol, "klines": klines, "ticker": ticker, "last_price": last_price}
        except Exception as exc:
            log.warning(f"{symbol}: не удалось собрать данные: {exc}")
            return None

    async def fetch_market_async(self, symbol: str):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.snapshot_pool, self.fetch_market, symbol)

    def ensure_stop_only_protection(self):
        if self.cfg.paper_trading:
            return
        cancel_all = getattr(self.client, "cancel_all", None)
        protect = getattr(self.client, "place_position_protection", None)
        if not cancel_all or not protect:
            return
        for pos in list(self.engine.positions.values()):
            if pos.status != "open":
                continue
            try:
                cancel_all(pos.symbol)
                placed, errors = protect(pos.symbol, pos.side, pos.qty, pos.stop_loss, None)
                if errors or "SL" not in placed:
                    log.error(f"{pos.symbol}: stop-only protection incomplete: {errors or placed}")
                else:
                    log.info(f"{pos.symbol}: stale TP orders removed; protection: SL")
            except Exception as exc:
                log.error(f"{pos.symbol}: failed to refresh stop-only protection: {exc}")

    def _maybe_reverse(self, pos, price):
        """Разворот: стоп-лосс по НАЧАЛЬНОМУ стопу + 3m-перекрёст против -> вход наоборот."""
        if not self.cfg.reversal_enabled:
            return None
        market = self.symbols_state.get(pos.symbol) or {}
        rows_ltf = (market.get("klines") or {}).get(self.tf_ltf) or []
        signal = build_reversal_signal(pos, price, rows_ltf=rows_ltf, strategy=self.strategies[0])
        if signal is None:
            return None
        log.info(f"  [РАЗВОРОТ] {pos.symbol}: {pos.side.upper()} выбита по начальному SL"
                 f" ({signal.reason.split('разворот: ')[-1]})")
        return self._consider_signal(signal, bypass_cooldown=True)

    def _consider_signal(self, signal, bypass_cooldown=False):
        """Риск-проверки и открытие позиции (вход решает только стратегия)."""
        self.journal.log_signal(signal.symbol, signal.side, signal.strategy, signal.entry,
                                signal.stop_loss, signal.features)
        cooldown_sec = getattr(self.cfg, "reentry_cooldown_sec", 0)
        loss_info = None
        if not bypass_cooldown:
            loss_info = self.journal.recent_losing_close(signal.symbol, cooldown_sec)
        open_positions = [p for p in self.engine.positions.values() if p.status == "open"]
        if loss_info:
            self.counters.inc("blocked_by_cooldown")
            self.reporter.signal(signal, False,
                                 f"повторный вход после убытка: {signal.symbol} закрыт "
                                 f"{loss_info['pnl']:+.4f} {cooldown_sec} c назад")
            return None
        leverage_ok = self.ensure_symbol_leverage(signal.symbol)
        if leverage_ok:
            risk_ok, risk_reason = self.risk.can_open(signal, open_positions)
        else:
            risk_ok, risk_reason = False, "не удалось установить допустимое плечо на бирже"
        if signal.features.get("reversal_of"):
            accept_msg = "вход принят: разворот после стоп-лосса (3m против позиции)"
        else:
            accept_msg = "вход принят: 30m трипл + первое 3m пересечение"
        self.reporter.signal(signal, risk_ok, risk_reason if not risk_ok else accept_msg)
        if not risk_ok:
            return None
        qty = self.risk.position_size(signal)
        pos = self.engine.open(signal, qty=qty)
        if pos:
            self.strategies[0].confirm_entry(signal.symbol)
            self.reporter.entry(pos, self.risk.equity)
        return pos

    def ensure_symbol_leverage(self, symbol: str) -> bool:
        if not self.cfg.futures:
            return True
        applied = getattr(self.client, "applied_leverage", {})
        if symbol in applied:
            self.risk.set_leverage(symbol, applied[symbol])
            return True
        if not self.client.set_leverage(symbol, self.cfg.max_leverage):
            self.risk.set_leverage(symbol, 1.0)
            return False
        actual = applied.get(symbol, self.cfg.max_leverage)
        self.risk.set_leverage(symbol, actual)
        if actual < self.cfg.max_leverage:
            log.info(f"Плечо {symbol}: {actual:g}x по лимиту контракта (запрошено {self.cfg.max_leverage:g}x)")
        return True

    async def run(self):
        log.info("Инициализация Binance markets...")
        self.client.load_markets()
        fetch_live = None if self.cfg.paper_trading else getattr(self.client, "fetch_live_positions", None)
        if fetch_live:
            log.info("Получение открытых позиций Binance...")
            live_positions = fetch_live() if self.cfg.scan_all_symbols else fetch_live(self.cfg.trade_symbols)
            log.info(f"Открытых позиций Binance: {len(live_positions or [])}")
            self.engine.adopt_live_positions(live_positions)
        self.engine.reconcile_live_positions()
        self.ensure_stop_only_protection()
        if self.cfg.futures and not self.cfg.paper_trading:
            open_symbols = sorted({
                position.symbol for position in self.engine.positions.values()
                if position.status == "open"
            })
            log.info(f"Подготовка плеча для открытых позиций: {len(open_symbols)}")
            for symbol in open_symbols:
                self.ensure_symbol_leverage(symbol)
            log.info("Плечо для новых символов будет установлено непосредственно перед входом")

        self.reporter.startup([strategy.name for strategy in self.strategies], equity=self.risk.equity)
        if self.cfg.schedule_enabled:
            log.info(f"Расписание работы: {self.cfg.schedule_wake} – {self.cfg.schedule_sleep} "
                     f"({self.cfg.schedule_zone})" + (" | закрытие позиций на сон" if self.cfg.schedule_close_positions else ""))
        log.info("Бот запущен. Индикатор один: пересечение MA 7/25 (30m -> 3m). "
                 "Выход только по трейлинг-стопу или стоп-лоссу.")
        try:
            while True:
                try:
                    if self.cfg.schedule_enabled:
                        try:
                            tz = ZoneInfo(self.cfg.schedule_zone)
                        except Exception:
                            tz = ZoneInfo("Europe/Kyiv")
                        now = datetime.now(tz).replace(tzinfo=None)
                        active, sleep_until = schedule_state(
                            now, self.cfg.schedule_wake, self.cfg.schedule_sleep,
                            self.cfg.schedule_weekdays_only)
                        if not active:
                            if self._schedule_active and self.cfg.schedule_close_positions:
                                open_n = len([p for p in self.engine.positions.values()
                                              if p.status == "open"])
                                log.info(f"Бот засыпает до {sleep_until:%d.%m %H:%M} ({self.cfg.schedule_zone}): "
                                         f"закрываю открытые позиции ({open_n})")
                                prices = self.engine.prices
                                self.engine.close_all(prices, "сон бота: закрытие в 20:30")
                                self.journal.update_signal_outcomes(prices)
                            self._schedule_active = False
                            if not self._schedule_sleep_logged:
                                log.info(f"Бот спит (вне окна работы), проснусь "
                                         f"{sleep_until:%d.%m %H:%M} ({self.cfg.schedule_zone})")
                                self._schedule_sleep_logged = True
                                self.dashboard.sleep_splash(sleep_until, self.cfg)
                            while True:
                                now = datetime.now(tz).replace(tzinfo=None)
                                active, sleep_until = schedule_state(
                                    now, self.cfg.schedule_wake, self.cfg.schedule_sleep,
                                    self.cfg.schedule_weekdays_only)
                                if active:
                                    break
                                secs = (sleep_until - now).total_seconds()
                                await asyncio.sleep(min(max(secs, 0.0), 300.0))
                            await asyncio.sleep(self.cfg.scan_interval)
                            continue
                        self._schedule_active = True
                        self._schedule_sleep_logged = False
                    scan_started = time.monotonic()
                    self.kline_cache = {
                        key: value for key, value in self.kline_cache.items()
                        if time.time() - value[0] <= 3600.0
                    }
                    prices = {}
                    active_symbols = []
                    open_positions = [p for p in self.engine.positions.values() if p.status == "open"]
                    entered = False
                    if open_positions:
                        self.counters.inc("blocked_by_open_position")
                        targets = list(dict.fromkeys(p.symbol for p in open_positions))
                        log.info(f"Мониторинг позиций ({len(targets)}): {', '.join(targets)} — новые входы не ищем")
                        for symbol in targets:
                            market = await self.fetch_market_async(symbol)
                            if market is None:
                                continue
                            self.symbols_state[symbol] = market
                            prices[symbol] = market["last_price"]
                            active_symbols.append(symbol)
                            if self.cfg.reversal_enabled:
                                rows_ltf = market["klines"].get(self.tf_ltf) or []
                                for p in open_positions:
                                    if p.symbol != symbol or p.status != "open":
                                        continue
                                    since_ms = int(float(p.opened_at or 0.0) * 1000.0)
                                    p.features["ltf_cross_dir"] = (
                                        self.strategies[0].ltf_cross_since(rows_ltf, since_ms))
                    else:
                        n = len(self.cfg.trade_symbols)
                        if self.cfg.scan_top_n > 0:
                            n = min(n, self.cfg.scan_top_n)
                        checked = 0
                        batch_size = max(1, self.snapshot_pool._max_workers)
                        while checked < n:
                            take = min(batch_size, n - checked)
                            batch_symbols = [
                                self.cfg.trade_symbols[(self.scan_cursor + i) % len(self.cfg.trade_symbols)]
                                for i in range(take)
                            ]
                            self.scan_cursor += take
                            checked += take
                            markets = await asyncio.gather(
                                *(self.fetch_market_async(s) for s in batch_symbols))
                            for symbol, market in zip(batch_symbols, markets):
                                if market is None:
                                    continue
                                self.symbols_state[symbol] = market
                                prices[symbol] = market["last_price"]
                                active_symbols.append(symbol)
                                rows_htf = market["klines"].get(self.tf_htf) or []
                                rows_ltf = market["klines"].get(self.tf_ltf) or []
                                signal = self.strategies[0].evaluate(
                                    symbol, rows_htf, rows_ltf, price=market["last_price"])
                                if signal:
                                    self._consider_signal(signal)
                                    if any(p.status == "open" for p in self.engine.positions.values()):
                                        entered = True
                                        break
                            if entered:
                                log.info(f"Вход открыт: {symbol} — проход по списку остановлен")
                                break
                            if checked % 100 == 0:
                                log.info(f"Проход по списку: проверено {checked}/{n} рынков, "
                                         f"курсор {self.scan_cursor % len(self.cfg.trade_symbols)}")
                        if not entered and self.scan_cursor >= n:
                            self.scan_cursor = 0
                            log.info(f"Полный проход по списку завершён: входов не найдено, начинаем новый проход")

                    self.engine.mark(prices)
                    if fetch_live:
                        live_positions = fetch_live() if self.cfg.scan_all_symbols else fetch_live(self.cfg.trade_symbols)
                        self.engine.adopt_live_positions(live_positions)
                    external_closed = self.engine.reconcile_live_positions()
                    for pos in external_closed:
                        self._maybe_reverse(pos, prices.get(pos.symbol))
                        self.reporter.exit(pos, self.risk, self.journal.closed_count())

                    closed = self.engine.check_positions(prices)
                    self.risk.reset_day()
                    for pos in closed:
                        self._maybe_reverse(pos, prices.get(pos.symbol))
                        self.reporter.exit(pos, self.risk, self.journal.closed_count())
                    self.journal.update_signal_outcomes(prices)

                    self.dashboard.render(self.reporter.scan_no, self.risk, self.engine)
                    unreal = sum(
                        self.engine.unrealized_pnl(position)[0]
                        for position in self.engine.positions.values()
                        if position.status == "open"
                    )
                    self.reporter.scan_header(self.risk, unreal, self.journal)
                    for symbol in active_symbols:
                        market = self.symbols_state[symbol]
                        state = self.strategies[0].diagnose(symbol)
                        if state.get("armed") or state.get("skip_first_3m_passed"):
                            self.reporter.symbol_state(symbol, market, state, self.engine)

                    open_positions = [p for p in self.engine.positions.values() if p.status == "open"]
                    if open_positions:
                        for pos in open_positions:
                            self.reporter.position_track(self.engine, pos)
                        log.info(
                            f"  В позиции ({len(open_positions)}): новые точки входа не ищем — "
                            "только трейлинг-стоп и стоп-лосс"
                        )
                        self.last_no_entry_summary = "позиция открыта — входы не ищем"
                    else:
                        if not active_symbols:
                            summary = "проход по списку рынков выполняется — совпадений пока нет"
                        else:
                            summary = "нет 30m трипл-импульса либо 3m-подтверждения в ту же сторону"
                        if summary != self.last_no_entry_summary or self.reporter.scan_no % 15 == 0:
                            log.info(f"Входов нет: {summary}")
                            self.last_no_entry_summary = summary

                    if self.reporter.scan_no % 15 == 0:
                        self.reporter.stats_periodic(self.journal)

                    self.counters.log_if_due(interval_s=600, label="Блокировки входа за 10 минут")

                    duration = time.monotonic() - scan_started
                    if duration > self.cfg.scan_interval:
                        log.warning(f"Скан занял {duration:.1f} сек при интервале {self.cfg.scan_interval:g} сек")
                    await asyncio.sleep(self.cfg.scan_interval)
                except KeyboardInterrupt:
                    break
                except Exception as exc:
                    log.exception(f"Ошибка цикла: {exc}")
                    await asyncio.sleep(5)
            write_heartbeat(self.reporter.scan_no)
        finally:
            self.engine.close_all(self.engine.prices)
            self.journal.close()
            self.counters.log_summary("Итог: блокировки входа за сессию")
            self.snapshot_pool.shutdown(wait=True)
            self.dashboard.finish()
            log.info("Бот остановлен. Открытые позиции закрыты, журнал сохранён.")


def main():
    disable_quickedit()
    if "--reset-day" in sys.argv:
        reset_ts = int(time.time())
        os.environ["DAY_RESET_TS"] = str(reset_ts)
        log.info(f"Дневной лимит сброшен: DAY_RESET_TS={reset_ts}")
    cfg = Config()
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", cfg.lock_port))
        lock.listen(1)
    except OSError:
        log.info(f"Другой экземпляр уже работает на порту {cfg.lock_port}.")
        sys.exit(0)
    bot = TradingBot()
    try:
        asyncio.run(bot.run())
    except BaseException as exc:
        log.exception(f"Критическое исключение: {exc!r}")
        raise
    finally:
        lock.close()


if __name__ == "__main__":
    main()