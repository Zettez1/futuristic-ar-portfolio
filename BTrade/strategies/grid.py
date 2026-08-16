from core.models import MarketSnapshot
from features.pipeline import FeatureBundle
from strategies.base import Strategy


class GridStrategy(Strategy):
    name = "grid"
    horizon = "range"

    def __init__(self, levels: int = 8, spacing_pct: float = 0.015, qty_usdt: float = 10.0):
        self.levels = levels
        self.spacing_pct = spacing_pct
        self.qty_usdt = qty_usdt

    def signals(self, snap: MarketSnapshot, bundle: FeatureBundle, engine):
        raw = bundle.raw
        vp_raw = raw.get("vp", {})
        price = raw.get("price", 0.0)
        atr_pct = raw.get("atr_pct", 0.01)
        val, vah = vp_raw.get("val"), vp_raw.get("vah")
        if not val or not vah or price <= 0:
            return []
        if vah - val <= 0:
            return []
        spread = (vah - val) / val
        if spread > 0.25:
            return []
        if price < val or price > vah:
            return []
        if engine.has_active_grid(snap.symbol):
            return []
        spacing = max(self.spacing_pct, atr_pct * 0.8)
        sigs = []
        for i in range(1, self.levels + 1):
            buy_price = price * (1 - spacing * i)
            sell_price = price * (1 + spacing * i)
            if buy_price < val * 0.97:
                break
            qty = self.qty_usdt / buy_price
            sigs.append(self._sig(snap, bundle, "long", buy_price, buy_price * 0.85, sell_price, 0.55, self.horizon,
                                  f"сетка: уровень покупки {i} ниже цены на {spacing * i:.1%}"))
            sigs.append(self._sig(snap, bundle, "short", sell_price, sell_price * 1.15, buy_price, 0.55, self.horizon,
                                  f"сетка: уровень продажи {i} выше цены на {spacing * i:.1%}"))
        return [s for s in sigs if s]
