"""Макро-контекст: BTC тренд, сессии, уровни дня, taker flow."""
import time
from collections import deque

import numpy as np

# Торговые сессии (UTC)
SESSIONS = {
    "asia": (0, 8),
    "london": (7, 16),
    "ny": (13, 21),
    "overlap": (13, 17),
}


def _hour_of_day() -> int:
    return time.gmtime().tm_hour


def detect_session() -> str:
    h = _hour_of_day()
    if 13 <= h < 17:
        return "overlap"
    if 0 <= h < 8:
        return "asia"
    if 7 <= h < 16:
        return "london"
    if 13 <= h < 21:
        return "ny"
    return "quiet"


def btc_trend_15m(klines: list) -> dict:
    """BTC тренд на 15m: направление, сила, волатильность."""
    if not klines or len(klines) < 20:
        return {"direction": "neutral", "strength": 0.0, "atr_pct": 0.0}
    closes = np.array([float(k[4]) for k in klines])
    highs = np.array([float(k[2]) for k in klines])
    lows = np.array([float(k[3]) for k in klines])
    price = closes[-1]
    sma_fast = np.mean(closes[-8:])
    sma_slow = np.mean(closes[-20:])
    trend_strength = (sma_fast / sma_slow - 1) * 100 if sma_slow else 0.0

    tr = np.maximum(highs[-12:] - lows[-12:],
                    np.abs(highs[-12:] - np.roll(closes[-13:-1], 1)[:12]))
    atr = float(np.mean(tr)) if len(tr) else 0.0
    atr_pct = (atr / price * 100) if price else 0.0

    if trend_strength > 0.3:
        direction = "up"
    elif trend_strength < -0.3:
        direction = "down"
    else:
        direction = "flat"

    return {"direction": direction, "strength": trend_strength,
            "atr_pct": atr_pct, "price": price}


def alt_ok_for_long(btc_trend: dict) -> bool:
    """Альт-лонг разрешён только если BTC не падает."""
    if btc_trend["direction"] == "down" and btc_trend["strength"] < -0.5:
        return False
    return True


def alt_ok_for_short(btc_trend: dict) -> bool:
    """Альт-шорт разрешён только если BTC не растёт сильно."""
    if btc_trend["direction"] == "up" and btc_trend["strength"] > 0.5:
        return False
    return True


def daily_levels(klines_15m: list) -> dict:
    """PDH/PDL за последние ~24 часа (96 баров 15m)."""
    if not klines_15m or len(klines_15m) < 48:
        return {"pdh": None, "pdl": None, "mid": None}
    highs = [float(k[2]) for k in klines_15m[-96:]]
    lows = [float(k[3]) for k in klines_15m[-96:]]
    pdh = max(highs)
    pdl = min(lows)
    mid = (pdh + pdl) / 2
    return {"pdh": pdh, "pdl": pdl, "mid": mid}


def taker_flow(trades: list, lookback: int = 100) -> dict:
    """Taker buy/sell ratio из последних сделок."""
    if not trades:
        return {"buy_vol": 0.0, "sell_vol": 0.0, "ratio": 0.5}
    recent = trades[-lookback:]
    buy_vol = sum(float(t.get("amount", 0)) * float(t.get("price", 0))
                  for t in recent if t.get("side") == "buy")
    sell_vol = sum(float(t.get("amount", 0)) * float(t.get("price", 0))
                   for t in recent if t.get("side") == "sell")
    total = buy_vol + sell_vol
    ratio = buy_vol / total if total > 0 else 0.5
    return {"buy_vol": buy_vol, "sell_vol": sell_vol, "ratio": ratio}


def oi_delta_history() -> deque:
    """Кольцевой буфер для отслеживания OI."""
    return deque(maxlen=12)


def oi_trend(oi_history: deque) -> str:
    """Тренд OI: expanding / contracting / flat."""
    if len(oi_history) < 3:
        return "flat"
    recent = list(oi_history)[-5:]
    if len(recent) < 2:
        return "flat"
    change = (recent[-1] - recent[0]) / max(abs(recent[0]), 1.0) * 100
    if change > 1.0:
        return "expanding"
    if change < -1.0:
        return "contracting"
    return "flat"
