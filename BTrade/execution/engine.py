import time
import uuid

from core.logger import get_logger
from core.models import Position, Signal
from learning.journal import TradeJournal

log = get_logger("execution")


class ExecutionEngine:
    def __init__(self, client, journal: TradeJournal, paper: bool = True,
                 slippage_pct: float = 0.0005, on_close=None,
                 trail_enabled: bool = False, trail_activation: float = 0.005, trail_distance: float = 0.003,
                 trail_break_even_r: float = 0.8, trail_start_r: float = 1.25, trail_distance_r: float = 0.75,
                 exit_evaluator=None, take_profit_enabled: bool = True,
                 scale_out_enabled: bool = False, scale_out_r: float = 1.0,
                 scale_out_fraction: float = 0.5):
        self.client = client
        self.journal = journal
        self.paper = paper
        self.slippage_pct = slippage_pct
        self.on_close = on_close
        self.trail_enabled = trail_enabled
        self.trail_activation = trail_activation
        self.trail_distance = trail_distance
        self.trail_break_even_r = trail_break_even_r
        self.trail_start_r = trail_start_r
        self.trail_distance_r = trail_distance_r
        self.exit_evaluator = exit_evaluator
        self.take_profit_enabled = take_profit_enabled
        self.scale_out_enabled = scale_out_enabled
        self.scale_out_r = scale_out_r
        self.scale_out_fraction = scale_out_fraction
        self.positions = {}
        self.grid_orders = {}
        self.grid_legs = {}
        self.prices = {}
        self.sl_cooldown = {}
        self.scaled = {}

    def sl_cooldown_active(self, symbol: str, side: str, minutes: float = 15.0) -> bool:
        t = self.sl_cooldown.get((symbol, side))
        return t is not None and (time.time() - t) < minutes * 60

    def has_active_grid(self, symbol: str) -> bool:
        return symbol in self.grid_orders

    def mark(self, prices: dict):
        self.prices.update(prices)

    @staticmethod
    def _order_fee(order: dict) -> float:
        fee = order.get("fee") or {}
        if not fee and order.get("fees"):
            fee = (order.get("fees") or [{}])[0]
        currency = str(fee.get("currency") or "USDT").upper()
        if currency not in ("USDT", "USD", "BUSD"):
            return 0.0
        return abs(float(fee.get("cost") or 0.0))

    def _settle_exchange_exit(self, pos: Position, fallback_price: float):
        """Берёт фактический fill закрытия, а при недоступности API использует цену проверки."""
        exit_price = float(fallback_price or pos.entry)
        fees = float(getattr(pos, "fees", 0.0) or 0.0)
        info = None
        fetch_exit = getattr(self.client, "fetch_position_exit", None)
        if not self.paper and fetch_exit:
            try:
                info = fetch_exit(pos.symbol, pos.side, pos.qty, pos.opened_at, entry_price=pos.entry)
            except TypeError:
                info = fetch_exit(pos.symbol, pos.side, pos.qty, pos.opened_at)
            if info and info.get("price"):
                exit_price = float(info["price"])
                if fees <= 0:
                    fees += float(info.get("entry_fee_usdt") or 0.0)
                fees += float(info.get("exit_fee_usdt") or 0.0)
        gross = ((exit_price - pos.entry) * pos.qty if pos.side == "long"
                 else (pos.entry - exit_price) * pos.qty)
        return exit_price, gross - fees, fees, info

    @staticmethod
    def _external_exit_reason(pos: Position, exit_price: float, exact: bool) -> str:
        if not exact:
            return "внешнее закрытие Binance (цена проверки)"
        tolerance = max(abs(pos.entry) * 1e-4, 1e-12)
        if pos.side == "long":
            if exit_price <= pos.stop_loss + tolerance:
                return "Стоп-лосс Binance"
            if pos.take_profit is not None and exit_price >= pos.take_profit - tolerance:
                return "Тейк-профит Binance"
        else:
            if exit_price >= pos.stop_loss - tolerance:
                return "Стоп-лосс Binance"
            if pos.take_profit is not None and exit_price <= pos.take_profit + tolerance:
                return "Тейк-профит Binance"
        return "внешнее закрытие Binance"

    def reconcile_live_positions(self) -> list:
        """Сверяет локальные позиции с Binance и удаляет вручную закрытые позиции."""
        if self.paper:
            return []
        checked = getattr(self.client, "position_size_checked", None)
        if not checked:
            return []
        external = []
        for pid, pos in list(self.positions.items()):
            if pos.status != "open":
                continue
            live_qty = checked(pos.symbol, pos.side)
            if live_qty is None:
                log.warning(f"{pos.symbol}: не удалось сверить позицию с Binance, локальное состояние сохранено")
                continue
            if live_qty <= 0:
                price, pnl, fees, fill = self._settle_exchange_exit(pos, self.prices.get(pos.symbol, pos.entry))
                pos.status = "closed"
                pos.realized_pnl = pnl
                pos.fees = fees
                pos.reason = self._external_exit_reason(pos, price, bool(fill))
                self.journal.close_external(pos, exit_price=price, pnl=pnl, fees=fees, reason=pos.reason)
                self.positions.pop(pid, None)
                external.append(pos)
                log.info(f"{pos.symbol}: позиция закрыта на Binance, локальная запись синхронизирована "
                         f"({pos.reason}, цена {price:g}, PnL {pnl:+.4f}, комиссии {fees:.4f} USDT)")
                if self.on_close:
                    self.on_close(pnl)
            elif abs(live_qty - pos.qty) > max(abs(pos.qty) * 1e-6, 1e-12):
                log.warning(f"{pos.symbol}: размер на Binance {live_qty:g}, локально {pos.qty:g}; размер обновлён")
                pos.qty = live_qty
        return external

    def adopt_live_positions(self, live_positions: list) -> list:
        """Подхватывает реальные позиции, которые не попали в локальный журнал."""
        if self.paper or live_positions is None:
            return []
        adopted = []
        known = {(p.symbol, p.side) for p in self.positions.values() if p.status == "open"}
        for live in live_positions:
            symbol = live["symbol"]
            side = live["side"]
            if (symbol, side) in known:
                continue
            entry = live["entry"]
            qty = live["qty"]
            context = self.journal.signal_context(symbol, side, entry)
            stop_loss = context["stop_loss"] if context else 0.0
            valid_stop = ((side == "long" and 0 < stop_loss < entry)
                          or (side == "short" and stop_loss > entry))
            if not valid_stop:
                stop_loss = entry * (0.99 if side == "long" else 1.01)
            risk = abs(entry - stop_loss)
            take_profit = None
            features = dict(context.get("features", {}) if context else {})
            features["adopted_from_exchange"] = 1.0
            features["initial_stop_loss"] = stop_loss
            features["initial_risk"] = abs(entry - stop_loss)
            strategy = context["strategy"] if context else "external"
            pos = Position(id="live-" + uuid.uuid4().hex[:10], symbol=symbol, side=side,
                           entry=entry, qty=qty, stop_loss=stop_loss, take_profit=take_profit,
                           strategy=strategy, opened_at=time.time(), features=features)
            self.positions[pos.id] = pos
            self.journal.open_trade(pos)
            placed, errors = [], []
            protect = getattr(self.client, "place_position_protection", None)
            if protect:
                placed, errors = protect(symbol, side, qty, stop_loss, take_profit)
            if errors or "SL" not in placed:
                log.error(f"{symbol}: реальная позиция подхвачена, но защита неполная: {errors or placed}")
            else:
                log.info(f"{symbol}: реальная позиция подхвачена из Binance ({side} {qty:g}), защита: {', '.join(placed)}")
            adopted.append(pos)
            known.add((symbol, side))
        return adopted

    def unrealized_pnl(self, pos: Position) -> tuple:
        price = self.prices.get(pos.symbol, pos.entry)
        if pos.side == "long":
            pnl = (price - pos.entry) * pos.qty
            pct = (price / pos.entry - 1) * 100 if pos.entry else 0.0
            sl_d = (price / pos.stop_loss - 1) * 100 if pos.stop_loss else 0.0
            tp_d = (pos.take_profit / price - 1) * 100 if pos.take_profit else 0.0
        else:
            pnl = (pos.entry - price) * pos.qty
            pct = (1 - price / pos.entry) * 100 if pos.entry else 0.0
            sl_d = (1 - price / pos.stop_loss) * 100 if pos.stop_loss else 0.0
            tp_d = (1 - pos.take_profit / price) * 100 if pos.take_profit else 0.0
        return pnl, pct, sl_d, tp_d

    def open(self, signal: Signal, qty: float = None, risk_usdt: float = None) -> Position:
        if self.paper:
            entry = signal.entry
            if qty is None and risk_usdt:
                distance = abs(entry - signal.stop_loss)
                if distance <= 0:
                    return None
                qty = risk_usdt / distance
            if not qty or qty <= 0:
                return None
            features = {**(signal.features or {}), "open_reason": signal.reason or "",
                        "initial_stop_loss": signal.stop_loss,
                        "initial_risk": abs(signal.entry - signal.stop_loss)}
            pos = Position(id=uuid.uuid4().hex[:10], symbol=signal.symbol, side=signal.side, entry=entry, qty=qty,
                           stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                           strategy=signal.strategy, opened_at=time.time(), features=features)
            self.positions[pos.id] = pos
            self.journal.open_trade(pos)
            return pos
        if not qty or qty <= 0:
            return None
        if not self.paper:
            sanitize = getattr(self.client, "sanitize_qty", None)
            if sanitize:
                qty = sanitize(signal.symbol, qty, signal.entry)
        side = "buy" if signal.side == "long" else "sell"
        self.client.cancel_all(signal.symbol)
        if hasattr(self.client, "open_market_with_stops"):
            res = self.client.open_market_with_stops(signal.symbol, side, qty, signal.stop_loss, signal.take_profit)
        else:
            res = self.client.create_order(signal.symbol, side, qty, None, "market")
        if res.get("error"):
            log.error(f"ОШИБКА ОРДЕРА {signal.symbol}: {res['error']}")
            return None
        entry = float(res.get("average") or res.get("price") or signal.entry)
        if res.get("warning"):
            log.warning(f"{signal.symbol}: {res['warning']}")
        features = {**(signal.features or {}), "open_reason": signal.reason or "",
                    "initial_stop_loss": signal.stop_loss,
                    "initial_risk": abs(signal.entry - signal.stop_loss)}
        pos = Position(id=uuid.uuid4().hex[:10], symbol=signal.symbol, side=signal.side, entry=entry, qty=qty,
                       stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                       strategy=signal.strategy, opened_at=time.time(), features=features,
                       fees=self._order_fee(res))
        log.info(f"{signal.symbol}: защита Binance после входа: {', '.join(res.get('protect') or []) or 'не указана'}")
        self.positions[pos.id] = pos
        self.journal.open_trade(pos)
        return pos

    def check_positions(self, prices: dict, bundles: dict = None) -> list:
        self.manage_trailing(prices)
        closed = []
        for pid, pos in list(self.positions.items()):
            price = prices.get(pos.symbol)
            if not price or pos.status != "open":
                continue
            initial_risk = float((pos.features or {}).get("initial_risk") or 0.0)
            if initial_risk <= 0:
                initial_stop = float((pos.features or {}).get("initial_stop_loss") or pos.stop_loss or 0.0)
                initial_risk = abs(pos.entry - initial_stop)
            profit_distance = (price - pos.entry) if pos.side == "long" else (pos.entry - price)
            r_multiple = profit_distance / initial_risk if initial_risk > 0 else 0.0
            hit_sl = price <= pos.stop_loss if pos.side == "long" else price >= pos.stop_loss
            hit_tp = self.take_profit_enabled and pos.take_profit is not None and (
                price >= pos.take_profit if pos.side == "long" else price <= pos.take_profit
            )
            if hit_sl or hit_tp:
                self.close(pid, price, "Стоп-лосс" if hit_sl else "Тейк-профит")
                closed.append(pos)
                continue
            if self.scale_out_enabled and pid not in self.scaled and r_multiple >= self.scale_out_r:
                self._scale_out(pid, pos, price, r_multiple)
            bundle = (bundles or {}).get(pos.symbol)
            exit_reason = None
            if self.exit_evaluator:
                try:
                    exit_reason = self.exit_evaluator(pos, bundle, r_multiple)
                except Exception as exc:
                    log.warning(f"{pos.symbol}: AI exit evaluator failed: {exc}")
            if exit_reason:
                self.close(pid, price, exit_reason)
                closed.append(pos)
                continue
        return closed

    def _scale_out(self, pos_id: str, pos: Position, price: float, r_multiple: float):
        """Частичная фиксация: закрывает долю позиции на scale_out_r и переносит стоп на безубыток.

        Остаток продолжает жить по текущей логике (exit по фактам / трейлинг).
        """
        fraction = max(0.05, min(0.95, float(self.scale_out_fraction)))
        partial_qty = pos.qty * fraction
        if partial_qty <= 0:
            return
        if pos.side == "long":
            pnl_part = (price - pos.entry) * partial_qty
        else:
            pnl_part = (pos.entry - price) * partial_qty
        if not self.paper:
            close_pos = getattr(self.client, "close_position", None)
            if not close_pos or not close_pos(pos.symbol, pos.side, partial_qty):
                log.error(f"{pos.symbol}: scale-out не удался на бирже — оставляю позицию целиком")
                return
            fee_est = price * partial_qty * 0.0004
            pnl_part -= fee_est
            pos.fees = float(getattr(pos, "fees", 0.0) or 0.0) + fee_est
        pos.qty = pos.qty - partial_qty
        pos.realized_pnl = float(getattr(pos, "realized_pnl", 0.0) or 0.0) + pnl_part
        feats = pos.features
        feats["scaled_at_r"] = round(r_multiple, 2)
        feats["partial_pnl"] = round(pnl_part, 6)
        old_sl = pos.stop_loss
        if (pos.side == "long" and pos.entry > pos.stop_loss) or (pos.side == "short" and pos.entry < pos.stop_loss):
            if not self.paper:
                upd = getattr(self.client, "update_stop_market", None)
                if upd and not upd(pos.symbol, pos.side, pos.qty, pos.entry):
                    pass  # не смогли перенести — оставляем старый стоп
                else:
                    pos.stop_loss = pos.entry
            else:
                pos.stop_loss = pos.entry
        self.scaled[pos_id] = True
        self.journal.open_trade(pos)
        if self.on_close:
            self.on_close(pnl_part)
        log.info(f"  [SCALE-OUT] {pos.symbol} {pos.side.upper()} +{r_multiple:.2f}R: "
                 f"зафиксировано {partial_qty:g} за {pnl_part:+.4f} USDT, остаток {pos.qty:g}, "
                 f"SL {old_sl:.6f} -> {pos.stop_loss:.6f}")

    def manage_trailing(self, prices: dict):
        """Сначала защищает безубыток, затем подтягивает стоп по R-множителю."""
        if not self.trail_enabled:
            return
        for pid, pos in list(self.positions.items()):
            if pos.status != "open" or not pos.stop_loss or not pos.entry:
                continue
            price = prices.get(pos.symbol)
            if not price:
                continue
            initial_risk = float((pos.features or {}).get("initial_risk") or 0.0)
            if initial_risk <= 0:
                initial_stop = float((pos.features or {}).get("initial_stop_loss") or pos.stop_loss or 0.0)
                initial_risk = abs(pos.entry - initial_stop)
            if initial_risk <= 0:
                continue
            if pos.side == "long":
                profit_distance = price - pos.entry
            else:
                profit_distance = pos.entry - price
            r_multiple = profit_distance / initial_risk
            new_sl = pos.stop_loss
            stage = ""
            fee_buffer = pos.entry * 0.0005
            if r_multiple >= self.trail_break_even_r:
                break_even = pos.entry + fee_buffer if pos.side == "long" else pos.entry - fee_buffer
                if (pos.side == "long" and break_even > new_sl) or (pos.side == "short" and break_even < new_sl):
                    new_sl = break_even
                    stage = "BE"
            if r_multiple >= self.trail_start_r:
                trail_offset = initial_risk * self.trail_distance_r
                candidate = price - trail_offset if pos.side == "long" else price + trail_offset
                if (pos.side == "long" and candidate > new_sl) or (pos.side == "short" and candidate < new_sl):
                    new_sl = candidate
                    stage = "TRAIL"
            if new_sl == pos.stop_loss:
                continue
            old_sl = pos.stop_loss
            pos.stop_loss = new_sl
            if not self.paper:
                upd = getattr(self.client, "update_stop_market", None)
                if upd and not upd(pos.symbol, pos.side, pos.qty, new_sl):
                    pos.stop_loss = old_sl  # биржа не приняла перенос — откат
                    continue
            label = f"{stage} +{r_multiple:.2f}R -> SL {old_sl:.6f} -> {new_sl:.6f}"
            pos.features["last_trail"] = label
            log.info(f"  [TRAIL] {pos.symbol} {pos.side.upper()} {label}")

    def check_micro_exit(self, pos: Position, micro: dict, price: float, cfg) -> Position:
        """Быстрый выход по микроструктуре + тайм-стоп (вызывается каждые ~150 мс).

        Трёхуровневая логика (для лонга; для шорта — зеркально):
        Уровень 0 — активность ленты: vol_5 >= max(abs_min, vol_10 * frac), иначе лента тонкая
                    (сигнал "против" на 2–3 сделках — шум) -> только warning.
        Уровень 1 — сигнал против есть, но нет подтверждения ценой (цена не ушла от лучшей
                    на confirm_bps) или позиция уже в защитной прибыли (best_r >= protect_r):
                    warning + ужесточение стопа (безубыток), без закрытия.
        Уровень 2 — подтверждённый разворот: лента против + цена против + активность,
                    сигнал держится hold_s подряд -> выход.
        Уровень 3 — burst против + подтверждение ценой + активность: немедленный выход
                    (burst_hold_s, по умолчанию 0.25с).
        Тайм-стоп: 60с тревога, 3м без прогресса (<0.5R) — выход, 5м слабый R (<1R) — выход.
        """
        if pos.status != "open" or not micro or not micro.get("subscribed"):
            return None
        side = 1 if pos.side == "long" else -1
        opp = "sell" if pos.side == "long" else "buy"
        feats = pos.features or {}
        state = feats.setdefault("micro_state", {})
        now = time.time()
        opened = state.get("opened_at") or pos.opened_at or now
        if not price or price <= 0:
            price = pos.entry
        best = state.get("best_price")
        if best is None:
            best = price
            state["best_price"] = best
        elif (pos.side == "long" and price > best) or (pos.side == "short" and price < best):
            best = price
            state["best_price"] = best
        initial_risk = float(feats.get("initial_risk") or 0.0)
        if initial_risk <= 0:
            initial_stop = float(feats.get("initial_stop_loss") or pos.stop_loss or 0.0)
            initial_risk = abs(pos.entry - initial_stop)
        best_r = ((best - pos.entry) if pos.side == "long" else (pos.entry - best)) / initial_risk \
            if initial_risk > 0 else 0.0
        state["best_r"] = best_r
        age = now - opened
        cfg_get = getattr(cfg, "get", None)

        def cval(key, default):
            return cfg_get(key, default) if cfg_get else getattr(cfg, key, default)

        # 3. тайм-стоп (без изменений)
        alert_at = float(cval("time_stop_alert_s", 60.0))
        no_progress_at = float(cval("time_stop_no_progress_s", 180.0))
        weak_at = float(cval("time_stop_weak_s", 300.0))
        last_alert = state.get("last_alert")
        if alert_at > 0 and age >= alert_at and (last_alert is None or age - last_alert >= alert_at):
            state["last_alert"] = age
            log.warning(f"{pos.symbol}: позиция {pos.side.upper()} {age:.0f}с — микро-прогресс {best_r:.2f}R")
        if no_progress_at > 0 and age >= no_progress_at and best_r < 0.5:
            return self._micro_close(pos, price,
                                     f"тайм-стоп {age/60:.0f}м: лучший ход всего {best_r:.2f}R (< 0.5R)")
        if weak_at > 0 and age >= weak_at and best_r < 1.0:
            return self._micro_close(pos, price,
                                     f"тайм-стоп {age/60:.0f}м: слабый ход {best_r:.2f}R (< 1R)")

        # 0. активность ленты: минимальный объём в окне 5с
        vol5 = float(micro.get("vol_5") or 0.0)
        vol10 = float(micro.get("vol_10") or 0.0)
        min_vol = float(cval("micro_exit_min_vol_usd", 8000.0))
        vol_frac = float(cval("micro_exit_vol_frac", 0.25))
        active = vol5 >= max(min_vol, vol10 * vol_frac) and vol10 > 0

        # 1. контр-поток: собираем сигналы против позиции
        cvd3 = float(micro.get("cvd_3") or 0.0)
        cvd_abs = float(cval("micro_exit_cvd_usd", 15000.0))
        cvd_th = max(cvd_abs, vol10 * 0.35)
        cvd_against = (side == 1 and cvd3 <= -cvd_th) or (side == -1 and cvd3 >= cvd_th)
        ratio5 = float(micro.get("buy_ratio_5") or 0.5)
        ratio_th = float(cval("micro_exit_ratio", 0.42))
        ratio_against = (side == 1 and ratio5 <= ratio_th) or (side == -1 and ratio5 >= 1.0 - ratio_th)
        burst_usd = float(micro.get("burst_usd_1s") or 0.0)
        burst_th = float(cval("micro_exit_burst_usd", 30000.0))
        burst_against = micro.get("burst_side") == opp and burst_usd >= burst_th
        spread_slope = micro.get("spread_slope_bps")
        spread_th = float(cval("micro_exit_spread_slope_bps", 6.0))
        spread_against = spread_slope is not None and spread_slope >= spread_th

        if not (cvd_against or ratio_against or burst_against or spread_against):
            state["against_since"] = None
            return None

        # 2. подтверждение ценой: цена ушла против от лучшей на confirm_bps
        confirm_bps = float(cval("micro_exit_confirm_bps", 15.0))
        if pos.side == "long":
            price_against = price <= best * (1.0 - confirm_bps / 10000.0)
        else:
            price_against = price >= best * (1.0 + confirm_bps / 10000.0)

        # 4. защита прибыли: позиция в хорошем плюсе — не режем импульс
        protect_r = float(cval("micro_exit_protect_r", 0.5))
        if best_r >= protect_r:
            self._tighten_to_break_even(pos)
            self._micro_warn(state, pos, micro, best_r, "прибыль защищена (уровень 1)")
            return None

        # тонкая лента: сигнал против на 2–3 сделках — шум, только warning
        if not active:
            self._micro_warn(state, pos, micro, best_r, "лента тонкая — сигнал игнорируется",
                             cooldown=60.0)
            return None

        # удержание сигнала: условие должно держаться hold_s подряд
        if not price_against:
            state["against_since"] = None
            self._micro_warn(state, pos, micro, best_r, "лента против, ждём подтверждения ценой",
                             cooldown=15.0)
            return None
        if state.get("against_since") is None:
            state["against_since"] = now
        held = now - state["against_since"]
        hold_s = float(cval("micro_exit_hold_s", 2.0))
        burst_hold_s = float(cval("micro_exit_burst_hold_s", 0.25))
        if burst_against and held >= burst_hold_s:
            return self._micro_close(pos, price, f"burst {opp} {burst_usd:.0f} USDT/с против (подтверждено)")
        if held >= hold_s:
            parts = []
            if ratio_against:
                parts.append(f"buy_ratio 5с {ratio5:.2f}")
            if cvd_against:
                parts.append(f"CVD 3с {cvd3:+.0f} USDT")
            if spread_against:
                parts.append(f"спред +{spread_slope:.1f} bps")
            return self._micro_close(pos, price, f"лента против ({', '.join(parts)}) — подтверждено ценой")
        return None

    def _micro_warn(self, state: dict, pos: Position, micro: dict, best_r: float, note: str,
                    cooldown: float = 15.0):
        """Предупреждение уровня 1 без закрытия (не чаще одного раза за cooldown)."""
        now = time.time()
        last = state.get("last_warn_at") or 0.0
        if now - last < cooldown:
            return
        state["last_warn_at"] = now
        ratio5 = float(micro.get("buy_ratio_5") or 0.5)
        cvd3 = float(micro.get("cvd_3") or 0.0)
        vol5 = float(micro.get("vol_5") or 0.0)
        log.warning(f"{pos.symbol}: МИКРО-СИГНАЛ против {pos.side.upper()} (уровень 1, {note}): "
                    f"ratio5 {ratio5:.2f}, cvd3 {cvd3:+.0f}, vol5 {vol5:.0f}, best {best_r:.2f}R — выход не выполняется")

    def _tighten_to_break_even(self, pos: Position):
        """Ужесточение: подтягивает стоп к безубытку, если позиция уже в плюсе и стоп дальше."""
        if not pos.stop_loss or not pos.entry:
            return
        fee_buffer = pos.entry * 0.0005
        new_sl = pos.entry + fee_buffer if pos.side == "long" else pos.entry - fee_buffer
        if (pos.side == "long" and new_sl > pos.stop_loss) or (pos.side == "short" and new_sl < pos.stop_loss):
            old_sl = pos.stop_loss
            pos.stop_loss = new_sl
            log.info(f"  [TIGHTEN] {pos.symbol} {pos.side.upper()}: микро-сигнал против — "
                     f"SL {old_sl:.6f} -> {new_sl:.6f} (безубыток)")

    def _micro_close(self, pos: Position, price: float, reason: str) -> Position:
        """Закрытие по микро-структуре с дельтой к худшей цене (пессимизм)."""
        if pos.side == "long":
            price = min(price, self.prices.get(pos.symbol, price))
        else:
            price = max(price, self.prices.get(pos.symbol, price))
        self.close(pos.id, price, reason)
        return pos

    def close(self, pos_id: str, price: float, reason: str = "ручное закрытие"):
        pos = self.positions.get(pos_id)
        if not pos or pos.status != "open":
            return None
        pnl = 0.0
        if not self.paper:
            if hasattr(self.client, "close_position"):
                if not self.client.close_position(pos.symbol, pos.side, pos.qty):
                    log.error(f"ОШИБКА ЗАКРЫТИЯ {pos.symbol}: не удалось закрыть позицию на бирже")
                    return None
            else:
                side = "sell" if pos.side == "long" else "buy"
                self.client.cancel_all(pos.symbol)
                res = self.client.create_order(pos.symbol, side, pos.qty, None, "market")
                if res.get("error"):
                    log.error(f"ОШИБКА ЗАКРЫТИЯ {pos.symbol}: {res['error']}")
                    return None
            price, pnl, fees, _ = self._settle_exchange_exit(pos, price)
            pos.fees = fees
        else:
            if pos.side == "long":
                pnl = (price - pos.entry) * pos.qty
            else:
                pnl = (pos.entry - price) * pos.qty
        pos.realized_pnl = pnl
        pos.status = "closed"
        pos.reason = reason
        self.journal.close_trade(pos, price, reason)
        if reason == "Стоп-лосс":
            self.sl_cooldown[(pos.symbol, pos.side)] = time.time()
        if self.on_close:
            self.on_close(pnl)
        return pos

    def close_all(self, prices: dict, reason: str = "остановка бота"):
        for pid in list(self.positions.keys()):
            pos = self.positions[pid]
            self.close(pid, prices.get(pos.symbol, pos.entry), reason)

    def place_grid(self, symbol: str, signals: list, qty_fn) -> int:
        if symbol in self.grid_orders:
            return 0
        orders = []
        for sig in signals:
            qty = qty_fn(sig)
            if qty > 0:
                orders.append({"side": sig.side, "price": sig.entry, "qty": qty, "tp": sig.take_profit, "sl": sig.stop_loss})
        if orders:
            self.grid_orders[symbol] = orders
            n_buy = sum(1 for o in orders if o["side"] == "long")
            n_sell = len(orders) - n_buy
            log.info(f"СЕТКА: размещено на {symbol}: {n_buy} покупок + {n_sell} продаж")
        return len(orders)

    def check_grid(self, prices: dict):
        for symbol, orders in list(self.grid_orders.items()):
            price = prices.get(symbol)
            if not price:
                continue
            filled = [
                o for o in orders
                if (o["side"] == "long" and price <= o["price"]) or (o["side"] == "short" and price >= o["price"])
            ]
            if filled:
                self.grid_orders[symbol] = [o for o in orders if o not in filled]
                self.grid_legs.setdefault(symbol, []).extend(filled)
                for o in filled:
                    side = "ПОКУПКА" if o["side"] == "long" else "ПРОДАЖА"
                    log.info(f"СЕТКА {symbol}: исполнена {side} @ {o['price']:,.4f}")
            if not self.grid_orders[symbol]:
                del self.grid_orders[symbol]
        for symbol, legs in list(self.grid_legs.items()):
            price = prices.get(symbol)
            if not price:
                continue
            done = []
            for leg in legs:
                if leg["side"] == "long":
                    if price <= leg["sl"]:
                        done.append((leg, "Стоп-лосс", (leg["sl"] - leg["price"]) * leg["qty"]))
                    elif price >= leg["tp"]:
                        done.append((leg, "Тейк-профит", (leg["tp"] - leg["price"]) * leg["qty"]))
                else:
                    if price >= leg["sl"]:
                        done.append((leg, "Стоп-лосс", (leg["price"] - leg["sl"]) * leg["qty"]))
                    elif price <= leg["tp"]:
                        done.append((leg, "Тейк-профит", (leg["price"] - leg["tp"]) * leg["qty"]))
            for leg, why, pnl in done:
                side = "ПОКУПКА" if leg["side"] == "long" else "ПРОДАЖА"
                log.info(f"СЕТКА {symbol}: нога {side} @ {leg['price']:,.4f} закрыта по {why} | PnL {pnl:+.4f} USDT")
                if why == "Стоп-лосс":
                    self.sl_cooldown[(symbol, leg["side"])] = time.time()
                if self.on_close:
                    self.on_close(pnl)
            if done:
                closed_ids = {id(leg) for leg, _, _ in done}
                self.grid_legs[symbol] = [leg for leg in legs if id(leg) not in closed_ids]
                if not self.grid_legs[symbol]:
                    del self.grid_legs[symbol]
