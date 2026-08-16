WEIGHTS = {
    "trend_up": {"impulse": 1.35, "breakout": 1.2, "trend_follow": 0.8, "daytrade": 0.5, "scalping": 0.2, "mean_reversion": 0.0, "grid": 0.0},
    "trend_down": {"impulse": 1.35, "breakout": 1.2, "trend_follow": 0.8, "daytrade": 0.5, "scalping": 0.2, "mean_reversion": 0.0, "grid": 0.0},
    "range": {"impulse": 1.0, "breakout": 0.7, "grid": 0.5, "mean_reversion": 0.2, "scalping": 0.1, "trend_follow": 0.0, "daytrade": 0.1},
    "volatile": {"impulse": 1.5, "breakout": 1.35, "scalping": 0.2, "daytrade": 0.2, "mean_reversion": 0.0, "trend_follow": 0.3, "grid": 0.0},
}


def detect_regime(bundle) -> str:
    s = bundle.scores
    trend = s.get("trend", 0.0)
    vol = s.get("volatility", 0.0)
    if vol > 8:
        return "volatile"
    if trend > 20:
        return "trend_up"
    if trend < -20:
        return "trend_down"
    return "range"


def rank_signals(signals: list, bundle) -> list:
    regime = detect_regime(bundle)
    weights = WEIGHTS.get(regime, WEIGHTS["range"])
    ranked = []
    for sig in signals:
        w = weights.get(sig.strategy, 0.5)
        score = sig.confidence * (0.5 + w)
        ranked.append((sig, score, regime))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def market_consensus(signal, bundle, min_votes: int = 4):
    """Проверяет, согласованы ли основные независимые группы рыночных признаков."""
    raw = bundle.raw or {}
    scores = bundle.scores or {}
    features = signal.features or {}
    direction = 1 if signal.side == "long" else -1
    structure = raw.get("structure", "")
    trend = float(scores.get("trend", 0.0) or 0.0)
    momentum = float(scores.get("momentum", 0.0) or 0.0)
    cvd = float(raw.get("cvd_slope", 0.0) or 0.0)
    imbalance = float(raw.get("ob_imbalance", 0.0) or 0.0)
    depth = float(raw.get("ob_depth_ratio", 1.0) or 1.0)
    volume = float(features.get("impulse_volume_ratio", raw.get("vol_ratio", 0.0)) or 0.0)
    expansion = float(features.get("impulse_expansion_atr", 0.0) or 0.0)

    votes = {
        "structure": structure == ("bullish" if direction > 0 else "bearish")
                     or (structure == "neutral" and trend * direction >= 0),
        "trend": trend * direction >= 0,
        "momentum": momentum * direction >= 0,
        "volume": volume >= 1.8 and (signal.strategy != "impulse" or expansion >= 1.25),
        "flow": cvd * direction >= 0.04,
        "orderbook": (imbalance >= 0.10 and depth >= 1.05) if direction > 0
                     else (imbalance <= -0.10 and depth <= 0.95),
    }
    hard_against = (structure == ("bearish" if direction > 0 else "bullish")
                    or trend * direction < -10
                    or cvd * direction < -0.08)
    passed = sum(votes.values())
    ok = not hard_against and passed >= min_votes
    detail = ", ".join(f"{name}={'Y' if value else 'N'}" for name, value in votes.items())
    return ok, passed, f"комитет {passed}/{len(votes)} ({detail})"


def market_exit_reason(pos, bundle, r_multiple: float):
    """Возвращает причину раннего выхода при подтверждённом развороте рынка."""
    if not bundle or r_multiple < 0.8:
        return None
    raw = bundle.raw or {}
    scores = bundle.scores or {}
    direction = 1 if pos.side == "long" else -1
    structure = raw.get("structure", "")
    trend = float(scores.get("trend", 0.0) or 0.0)
    momentum = float(scores.get("momentum", 0.0) or 0.0)
    cvd = float(raw.get("cvd_slope", 0.0) or 0.0)
    imbalance = float(raw.get("ob_imbalance", 0.0) or 0.0)
    opposing = []
    if structure == ("bearish" if direction > 0 else "bullish"):
        opposing.append("structure")
    if trend * direction < -5:
        opposing.append("trend")
    if momentum * direction < -0.10:
        opposing.append("momentum")
    if cvd * direction < -0.04:
        opposing.append("CVD")
    if imbalance * direction < -0.12:
        opposing.append("orderbook")
    needed = 2 if r_multiple >= 1.5 else 3
    if len(opposing) >= needed:
        return f"комитет: подтверждённый разворот ({'/'.join(opposing)}, +{r_multiple:.2f}R)"
    return None
