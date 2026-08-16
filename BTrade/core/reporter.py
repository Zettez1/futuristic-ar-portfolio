import time

from core.logger import get_logger
from learning.journal import DB_PATH as db_path

log = get_logger("bot")

STRATEGY_LABELS = {
    "ma_cross": "пересечение MA 7/25 (30m → 3m)",
    "facts": "факты (стакан L2 + лента + инди 1m)",
    "supply_demand_confluence": "Supply/Demand confluence (2+ TF)",
    "scalping": "скальпинг (1м, стакан+лента)",
    "daytrade": "интрадей (15м)",
    "trend_follow": "следование за трендом (1ч)",
    "mean_reversion": "контртренд / отскок (15м)",
    "breakout": "пробой уровней (1ч)",
    "grid": "сетка (боковик)",
    "impulse": "импульс (5м, пробой сжатия)",
}


def fmt_price(v):
    if not v or v <= 0:
        return "—"
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:.4f}"
    return f"{v:.6f}"


class Reporter:
    def __init__(self, cfg):
        self.cfg = cfg
        self.scan_no = 0
        self.prev_price = {}

    def startup(self, strategy_names: list, equity: float = None):
        cfg = self.cfg
        capital = equity if equity is not None else cfg.equity
        log.info("=" * 64)
        log.info("  TRIPLE IMPULSE BOT — ЗАПУСК")
        log.info("=" * 64)
        if cfg.paper_trading:
            log.info("  Режим: БУМАЖНАЯ ТОРГОВЛЯ (симуляция, реальные ордера НЕ отправляются)")
        else:
            log.info("  Режим: РЕАЛЬНАЯ ТОРГОВЛЯ — БУДЬТЕ ОСТОРОЖНЫ")
        log.info(f"  Рынок: {'ФЬЮЧЕРСЫ (перпетуалы)' if cfg.futures else 'СПОТ'} | плечо: x{cfg.max_leverage:g}")
        log.info(f"  Капитал: {capital:,.2f} USDT" + ("" if equity is not None else "  (из .env, баланс не получен)"))
        log.info(f"  Сумма входа: {cfg.entry_position_pct:.0f}% депозита (номинал) | дневной лимит убытка: {cfg.max_daily_loss:.1%} | макс. позиций: {cfg.max_positions} | маржа на сделку: до {cfg.max_margin_frac:.0%} лимита плеча | бюджет кошелька: до {cfg.max_wallet_usage:.0%}")
        log.info(f"  Индикатор: ОДИН — пересечение MA{cfg.ma_cross_fast}/MA{cfg.ma_cross_slow} "
                 f"({cfg.ma_cross_htf} → {cfg.ma_cross_ltf})")
        log.info(f"  Вход: 30m пересечение MA{cfg.ma_cross_fast}/MA{cfg.ma_cross_slow} -> "
                 f"первый {cfg.ma_cross_ltf} пересечение в ту же сторону. "
                 f"Больше ничего не сканируется (без стакана, ленты и комитета фактов).")
        log.info(f"  Стоп-лосс: {cfg.entry_sl_atr_mult:g}×ATR({cfg.ma_cross_ltf}), "
                 f"минимум {cfg.entry_sl_min_pct:.2f}% от цены")
        if cfg.trail_enabled:
            log.info(f"  Выход: ТОЛЬКО трейлинг-стоп (безубыток +{cfg.trail_break_even_r:g}R, "
                     f"трейл +{cfg.trail_start_r:g}R, дистанция {cfg.trail_distance_r:g}R) и стоп-лосс. "
                     f"Тейк-профит отключён.")
        else:
            log.info("  Выход: только стоп-лосс (трейлинг выключен TRAIL_ENABLED=0)")
        if cfg.reversal_enabled:
            log.info("  Разворот: выбило по НАЧАЛЬНОМУ стоп-лоссу + 3m-перекрёст в противоположную "
                     "сторону -> моментальный вход наоборот (REVERSAL_ENABLED=1)")
        if getattr(cfg, "ma_cross_filters_enabled", False):
            log.info(f"  Фильтры входа «как у прибыльных сделок» (MA_CROSS_FILTERS=1): "
                     f"анти-чейз 2h ≤ {cfg.ma_cross_max_2h_move:g}%, "
                     f"ATR(3m) ≤ {cfg.ma_cross_max_atr3_pct:g}%, "
                     f"объём ≤ {cfg.ma_cross_max_vol_ratio:g}x, "
                     f"лонг ≤ {cfg.ma_cross_max_range_pos_long:g}% 24h-диапазона, "
                     f"24h-перекос ≤ {cfg.ma_cross_max_24h_move:g}%")
        if cfg.scan_all_symbols and cfg.exchange == "binance":
            log.info(f"  Монеты под наблюдением: все активные USDT-рынки Binance ({len(cfg.trade_symbols)}) "
                     f"с |24h change| >= {cfg.min_24h_volatility:g}% (стейблкоины исключены)")
        else:
            log.info(f"  Монеты под наблюдением: {', '.join(cfg.base_symbols)}")
        log.info(f"  Сканирование каждые {cfg.scan_interval:g} сек, "
                 f"{'все монеты за проход' if cfg.scan_top_n == 0 else f'пачками по {cfg.scan_top_n} монет'}")
        log.info(f"  Стратегии: {', '.join(STRATEGY_LABELS.get(s, s) for s in strategy_names)}")
        log.info(f"  Журнал сделок: {db_path} | подробный лог: logs/bot.log")
        log.info("=" * 64)

    def scan_header(self, risk=None, unrealized=0.0, journal=None):
        self.scan_no += 1
        log.info(f"----- СКАН №{self.scan_no} · {time.strftime('%H:%M:%S')} -----")
        if risk is not None:
            total = risk.equity + unrealized
            log.info(f"  Капитал: {total:,.2f} USDT (реализовано {risk.equity:,.2f} + открытые позиции {unrealized:+.2f})"
                     f" | дневной PnL: {risk.day_pnl:+.2f} USDT")
        if journal is not None and risk is not None:
            by_strat = journal.pnl_by_strategy_since(risk.day_start)
            if by_strat:
                parts = [f"{STRATEGY_LABELS.get(s, s).split(' (')[0]} {v:+.1f}" for s, v in
                         sorted(by_strat.items(), key=lambda kv: kv[1], reverse=True)]
                log.info("  за день по стратегиям: " + " | ".join(parts))

    def symbol_state(self, symbol, market, state, engine):
        """Состояние монеты по единственному индикатору: MA-пересечения 30m / 3m."""
        price = float(market.get("last_price") or 0.0)
        if not price:
            return
        prev = self.prev_price.get(symbol)
        delta = (price / prev - 1) * 100 if prev else 0.0
        self.prev_price[symbol] = price
        dir30 = int(state.get("dir30") or 0)
        dir3 = int(state.get("dir3") or 0)
        armed = int(state.get("armed") or 0)
        if not dir30 and armed:
            dir30 = armed
        lights = []
        lights.append("30m:" + ("▲" if dir30 == 1 else "▼" if dir30 == -1 else "—"))
        lights.append("3m:" + ("▲" if dir3 == 1 else "▼" if dir3 == -1 else "—"))
        if armed:
            lights.append("ВООРУЖЁН " + ("▲" if armed == 1 else "▼") + " — жду 3m-пересечение")
        elif state.get("skip_first_3m_passed"):
            lights.append("первый 3m-перекрёст УЖЕ прошёл — монета пропущена")
        else:
            lights.append("жду 30m-пересечение")
        state_line = (f"    {symbol}  цена {fmt_price(price)}" + (f"  (за скан {delta:+.2f}%)" if prev else "") +
                      f"  | {', '.join(lights)} | ATR30 {state.get('atr30', 0):.4g} ATR3 {state.get('atr3', 0):.4g}")
        if state.get("filter_reject"):
            state_line += f" | ВХОД ОТКЛОНЁН ФИЛЬТРОМ: {state['filter_reject']}"
        log.info(state_line)
        for pos in engine.positions.values():
            if pos.symbol == symbol and pos.status == "open":
                pnl, pct, sl_d, _ = engine.unrealized_pnl(pos)
                log.info(f"    [ПОЗИЦИЯ] {pos.side.upper()} {pos.qty:g} @ {fmt_price(pos.entry)} "
                         f"(стратегия «{STRATEGY_LABELS.get(pos.strategy, pos.strategy)}»)"
                         f" | PnL {pnl:+.4f} USDT ({pct:+.2f}%) | до SL {sl_d:.1f}% | выход: трейлинг/стоп-лосс")

    def signal(self, sig, ok, reason):
        strategy = STRATEGY_LABELS.get(sig.strategy, sig.strategy)
        verdict = "ГОТОВ К ВХОДУ" if ok else f"ПРОПУЩЕН: {reason}"
        log.info(f"    [СИГНАЛ] {sig.symbol} -> {sig.side.upper()} (стратегия «{strategy}»)")
        log.info(f"      причина: {sig.reason}")
        log.info(f"      итог: {verdict}")

    def entry(self, pos, equity=None):
        risk_pct = abs(pos.entry - pos.stop_loss) / pos.entry * 100 if pos.entry else 0.0
        mode = "БУМАГА" if self.cfg.paper_trading else "РЕАЛЬНЫЙ ОРДЕР"
        log.info(f"  [ВХОД] {pos.symbol} {pos.side.upper()} {pos.qty:g} @ {fmt_price(pos.entry)} ({mode}) | стратегия «{STRATEGY_LABELS.get(pos.strategy, pos.strategy)}»")
        log.info(f"    Стоп-лосс: {fmt_price(pos.stop_loss)} ({risk_pct:.2f}% от входа) | выход: трейлинг/стоп-лосс")
        if equity is not None:
            log.info(f"    Реализованный капитал: {equity:,.2f} USDT")

    def exit(self, pos, risk, closed_count):
        initial_stop = (pos.features or {}).get("initial_stop_loss", pos.stop_loss)
        risk_base = abs(pos.entry - initial_stop) * pos.qty
        r = pos.realized_pnl / max(risk_base, 1e-12)
        mode = "БУМАГА" if self.cfg.paper_trading else "РЕАЛЬНЫЙ"
        log.info(f"  [ВЫХОД] {pos.symbol} {pos.side.upper()} закрыта ({mode}) по причине: {pos.reason} | {pos.qty:g} @ {fmt_price(pos.entry)}")
        log.info(f"    PnL: {pos.realized_pnl:+.4f} USDT | R-множитель: {r:+.2f} (риск был {abs(pos.entry - pos.stop_loss):.4f} на единицу)")
        log.info(f"    Капитал: {risk.equity:,.2f} USDT | дневной PnL: {risk.day_pnl:+.2f} USDT | закрыто сделок всего: {closed_count}")

    def position_track(self, engine, pos, bundle=None):
        if pos.status != "open":
            return
        pnl, pct, sl_d, _ = engine.unrealized_pnl(pos)
        initial_stop = (pos.features or {}).get("initial_stop_loss") or pos.stop_loss
        risk_base = abs(pos.entry - initial_stop) * pos.qty
        r = pnl / max(risk_base, 1e-12)
        mark = (getattr(pos, "current_price", None)
                or engine.prices.get(pos.symbol)
                or pos.entry)
        last_trail = (pos.features or {}).get("last_trail", "")
        trail_suffix = f" | последний трейлинг: {last_trail}" if last_trail else ""
        log.info(
            f"  [ПОЗИЦИЯ-ТРЕКИНГ] {pos.symbol} {pos.side.upper():5} {pos.qty:g} @ {fmt_price(pos.entry)} | "
            f"mark {fmt_price(mark)} | PnL {pnl:+.4f} USDT ({pct:+.2f}%, {r:+.2f}R) | до SL {sl_d:.1f}% | "
            f"SL {fmt_price(pos.stop_loss)} | выход: трейлинг/стоп-лосс{trail_suffix}"
        )

    def stats_periodic(self, journal):
        stats = journal.stats()
        if not stats["total_trades"]:
            return
        log.info(f"  -- СВОДКА ЗА ВСЁ ВРЕМЯ --")
        log.info(f"    Сделок: {stats['total_trades']} | суммарный PnL: {stats['total_pnl']:+.4f} USDT")
        for s in stats["by_strategy"]:
            wr = s["wins"] / s["trades"] * 100 if s["trades"] else 0.0
            log.info(f"    «{STRATEGY_LABELS.get(s['strategy'], s['strategy'])}»: {s['trades']} сделок, побед {wr:.0f}%, PnL {s['pnl']:+.4f}, средний R {s['avg_r']:+.2f}")