import time

from core.config import Config
from core.models import Signal


class RiskManager:
    def __init__(self, config: Config):
        self.cfg = config
        self.equity = config.equity
        self.day_start_equity = config.equity
        self.day_start = time.time()
        self.day_pnl = 0.0
        self.kill_switch = False
        self.leverage_by_symbol = {}

    @property
    def is_killed(self) -> bool:
        return self.kill_switch

    def reset_day(self):
        now = time.time()
        if now - self.day_start > 86400:
            self.day_start = now
            self.day_start_equity = self.equity
            self.day_pnl = 0.0

    def set_equity(self, equity: float):
        """Устанавливает актуальный капитал и начинает новый лимит дня от него."""
        self.equity = max(float(equity or 0.0), 0.0)
        self.day_start_equity = self.equity
        self.day_start = time.time()
        self.day_pnl = 0.0
        self.kill_switch = False

    def restore_day(self, day_start_ts: float, day_pnl: float):
        """Восстановление дневного лимита после рестарта (история дня из журнала).

        Вызывается сразу после set_equity: возвращает фактический PnL дня,
        базовую линию дня и включает kill_switch, если лимит уже пробит.
        """
        self.day_pnl = float(day_pnl)
        self.day_start = float(day_start_ts)
        self.day_start_equity = self.equity - self.day_pnl
        if self.cfg.paper_trading and self.cfg.paper_no_daily_limit:
            self.kill_switch = False
            return
        if self.cfg.no_daily_limit:
            self.kill_switch = False
            return
        if self.day_start_equity <= 0:
            self.kill_switch = True
        elif self.day_pnl <= -self.cfg.max_daily_loss * self.day_start_equity:
            self.kill_switch = True

    def set_leverage(self, symbol: str, leverage: float):
        try:
            value = max(1.0, min(float(leverage), float(self.cfg.max_leverage)))
        except (TypeError, ValueError):
            value = 1.0
        self.leverage_by_symbol[symbol] = value

    def leverage_for(self, symbol: str) -> float:
        return max(1.0, float(self.leverage_by_symbol.get(symbol, self.cfg.max_leverage)))

    def record_pnl(self, pnl: float):
        self.equity += pnl
        self.day_pnl += pnl
        if self.cfg.paper_trading and self.cfg.paper_no_daily_limit:
            return
        if self.cfg.no_daily_limit:
            return
        if self.day_pnl <= -self.cfg.max_daily_loss * max(self.day_start_equity, 0.0):
            self.kill_switch = True

    def can_open(self, signal: Signal, open_positions: list) -> tuple:
        if self.kill_switch:
            return False, "daily loss limit hit"
        if self.equity <= 0:
            return False, "equity unavailable"
        if len(open_positions) >= self.cfg.max_positions:
            return False, "max positions reached"
        if signal.stop_loss <= 0 or signal.entry <= 0:
            return False, "invalid SL"
        risk_pct = signal.risk_pct()
        if risk_pct > 0.25:
            return False, f"SL too wide: {risk_pct:.1%}"
        same = [p for p in open_positions if p.symbol == signal.symbol]
        if same:
            return False, "позиция уже открыта на символе"
        notional = self._entry_notional(self.leverage_for(signal.symbol))
        min_notional = float(getattr(self.cfg, "min_notional", 5.0) or 5.0)
        if notional < min_notional:
            return False, (f"номинал {notional:.2f} USDT < минимума биржи {min_notional:g} USDT "
                           f"(депозит {self.equity:.2f} USDT × {self.cfg.entry_position_pct:g}% × плечо)")
        used = self.margin_used(open_positions) + self.prospective_margin(signal)
        if used > self.wallet_budget():
            return False, (f"бюджет кошелька исчерпан: маржа {used:.2f} USDT из {self.wallet_budget():.2f} "
                           f"({self.cfg.max_wallet_usage:.0%} кошелька)")
        return True, "ok"

    def wallet_budget(self) -> float:
        """Максимальная суммарная маржа открытых позиций (доля кошелька)."""
        return self.equity * self.cfg.max_wallet_usage

    def _entry_notional(self, leverage: float) -> float:
        """Номинал позиции: (ENTRY_POSITION_PCT% депозита) × плечо.

        Маржа позиции = equity × pct/100, сам номинал увеличен плечом,
        сверху ограничен лимитом маржи под плечо.
        """
        if self.equity <= 0:
            return 0.0
        target = self.equity * (self.cfg.entry_position_pct / 100.0) * leverage
        cap = self.equity * leverage * self.cfg.max_margin_frac
        return max(0.0, min(target, cap))

    def margin_used(self, open_positions: list) -> float:
        total = 0.0
        for p in (open_positions or []):
            if getattr(p, "status", "open") == "open":
                total += p.qty * p.entry / self.leverage_for(p.symbol)
        return total

    def prospective_margin(self, signal: Signal) -> float:
        """Оценка маржи, которую займёт сигнальная позиция (по тем же правилам размера, что и position_size)."""
        if self.equity <= 0:
            return 0.0
        leverage = self.leverage_for(signal.symbol)
        return self._entry_notional(leverage) / leverage

    def trade_risk_usdt(self) -> float:
        return self.equity * (self.cfg.entry_position_pct / 100.0) * self.cfg.max_leverage

    def position_size(self, signal: Signal) -> float:
        if signal.entry <= 0 or self.equity <= 0:
            return 0.0
        leverage = self.leverage_for(signal.symbol)
        notional = self._entry_notional(leverage)
        return notional / signal.entry

    def qty_for_usdt(self, price: float, usdt: float) -> float:
        if price <= 0:
            return 0.0
        return usdt / price
