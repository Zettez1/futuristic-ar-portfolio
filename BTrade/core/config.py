import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# Какой .env использовать: задаётся через переменную окружения DOTENV
# (например "set DOTENV=.env.mexc-paper" в .bat). Иначе берётся ROOT/.env.
dotenv_override = os.environ.get("DOTENV", "")
if dotenv_override:
    dotenv_path = Path(dotenv_override).resolve() if Path(dotenv_override).is_absolute() else ROOT / dotenv_override
else:
    dotenv_path = ROOT / ".env"
_load_dotenv(dotenv_path)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _csv(name: str, default: str) -> list:
    return [value.strip() for value in _env(name, default).split(",") if value.strip()]


def _flag(name: str, default: str = "0") -> bool:
    return _env(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    exchange: str = field(default_factory=lambda: _env("EXCHANGE", "binance"))
    mexc_api_key: str = field(default_factory=lambda: _env("MEXC_API_KEY"))
    mexc_secret: str = field(default_factory=lambda: _env("MEXC_SECRET"))
    binance_api_key: str = field(default_factory=lambda: _env("BINANCE_API_KEY"))
    binance_secret: str = field(default_factory=lambda: _env("BINANCE_SECRET"))
    mistral_api_key: str = field(default_factory=lambda: _env("MISTRAL_API_KEY"))
    mistral_model: str = field(default_factory=lambda: _env("MISTRAL_MODEL", "mistral-large-latest"))
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    openrouter_model: str = field(default_factory=lambda: _env("OPENROUTER_MODEL", "google/gemini-2.0-flash-001"))
    cerebras_api_key: str = field(default_factory=lambda: _env("CEREBRAS_API_KEY"))
    cerebras_model: str = field(default_factory=lambda: _env("CEREBRAS_MODEL", "llama-3.3-70b"))
    deepinfra_api_key: str = field(default_factory=lambda: _env("DEEPINFRA_API_KEY", _env("DEEPINFRA_API_TOKEN")))
    deepinfra_model: str = field(default_factory=lambda: _env("DEEPINFRA_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct"))
    cohere_api_key: str = field(default_factory=lambda: _env("COHERE_API_KEY"))
    cohere_model: str = field(default_factory=lambda: _env("COHERE_MODEL", "command-a-plus-05-2026"))
    nvidia_api_key: str = field(default_factory=lambda: _env("NVIDIA_API_KEY"))
    nvidia_model: str = field(default_factory=lambda: _env("NVIDIA_MODEL", "minimaxai/minimax-m3"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.0-flash"))
    paper_trading: bool = field(default_factory=lambda: _env("PAPER_TRADING", "1") == "1")
    equity: float = field(default_factory=lambda: float(_env("EQUITY", "1000")))
    paper_reset_equity: bool = field(default_factory=lambda: _flag("PAPER_RESET_EQUITY", "0"))
    paper_no_daily_limit: bool = field(default_factory=lambda: _flag("PAPER_NO_DAILY_LIMIT", "0"))
    no_daily_limit: bool = field(default_factory=lambda: _flag("NO_DAILY_LIMIT", "0"))
    symbols: list = field(default_factory=lambda: _csv("SYMBOLS", "BTC/USDT:USDT"))
    scan_all_symbols: bool = field(default_factory=lambda: _flag("SCAN_ALL_SYMBOLS", "1"))
    min_24h_volatility: float = field(default_factory=lambda: float(_env("MIN_24H_VOLATILITY", "0")))
    max_24h_volatility: float = field(default_factory=lambda: float(_env("MAX_24H_VOLATILITY", "200")))

    # ── Единственный индикатор: пересечение MA(7)/MA(25) (30m -> 3m) ──
    ma_cross_fast: int = field(default_factory=lambda: int(_env("MA_CROSS_FAST", "7")))
    ma_cross_slow: int = field(default_factory=lambda: int(_env("MA_CROSS_SLOW", "25")))
    ma_cross_ma_type: str = field(default_factory=lambda: _env("MA_CROSS_MA_TYPE", "ema"))
    ma_cross_htf: str = field(default_factory=lambda: _env("MA_CROSS_HTF", "30m"))
    ma_cross_ltf: str = field(default_factory=lambda: _env("MA_CROSS_LTF", "3m"))
    ma_cross_max_age_bars: int = field(default_factory=lambda: int(_env("MA_CROSS_MAX_AGE_BARS", "5")))
    # Фильтры входа «как у прибыльных сделок» (см. анализ истории: чейз/волатильность/объём/диапазон)
    ma_cross_filters_enabled: bool = field(default_factory=lambda: _flag("MA_CROSS_FILTERS", "0"))
    ma_cross_max_2h_move: float = field(default_factory=lambda: float(_env("MA_CROSS_MAX_2H_MOVE", "5")))
    ma_cross_max_atr3_pct: float = field(default_factory=lambda: float(_env("MA_CROSS_MAX_ATR3_PCT", "1.2")))
    ma_cross_max_vol_ratio: float = field(default_factory=lambda: float(_env("MA_CROSS_MAX_VOL_RATIO", "3")))
    ma_cross_max_range_pos_long: float = field(default_factory=lambda: float(_env("MA_CROSS_MAX_RANGE_POS_LONG", "75")))
    ma_cross_max_24h_move: float = field(default_factory=lambda: float(_env("MA_CROSS_MAX_24H_MOVE", "20")))
    # Разворот: запас — если EMAs очень близки и цена уже против позиции на выбитии SL
    ma_cross_reversal_close_gap_pct: float = field(default_factory=lambda: float(_env("MA_CROSS_REVERSAL_CLOSE_GAP_PCT", "0.2")))
    # Расписание работы бота: просыпается в SCHEDULE_WAKE, засыпает в SCHEDULE_SLEEP (зона SCHEDULE_ZONE)
    schedule_enabled: bool = field(default_factory=lambda: _flag("SCHEDULE_ENABLED", "1"))
    schedule_zone: str = field(default_factory=lambda: _env("SCHEDULE_ZONE", "Europe/Kyiv"))
    schedule_wake: str = field(default_factory=lambda: _env("SCHEDULE_WAKE", "09:00"))
    schedule_sleep: str = field(default_factory=lambda: _env("SCHEDULE_SLEEP", "20:30"))
    schedule_close_positions: bool = field(default_factory=lambda: _flag("SCHEDULE_CLOSE_POSITIONS", "1"))
    min_6h_volatility: float = field(default_factory=lambda: float(_env("MIN_6H_VOLATILITY", "0")))
    min_24h_volume_usd: float = field(default_factory=lambda: float(_env("MIN_24H_VOLUME_USD", "10")))
    flow_liq_enabled: bool = field(default_factory=lambda: _flag("FLOW_LIQ_ENABLED", "1"))
    flow_liq_window: float = field(default_factory=lambda: float(_env("FLOW_LIQ_WINDOW", "300")))
    flow_liq_min_usd: float = field(default_factory=lambda: float(_env("FLOW_LIQ_MIN_USD", "50000")))
    flow_liq_min_fast_usd: float = field(default_factory=lambda: float(_env("FLOW_LIQ_MIN_FAST_USD", "20000")))
    flow_liq_decay_sec: float = field(default_factory=lambda: float(_env("FLOW_LIQ_DECAY_SEC", "120")))
    flow_funding_max_abs: float = field(default_factory=lambda: float(_env("FLOW_FUNDING_MAX_ABS", "0.0004")))
    flow_premium_max_pct: float = field(default_factory=lambda: float(_env("FLOW_PREMIUM_MAX_PCT", "0.3")))
    flow_premium_z: float = field(default_factory=lambda: float(_env("FLOW_PREMIUM_Z", "2.0")))
    flow_oi_min_change: float = field(default_factory=lambda: float(_env("FLOW_OI_MIN_CHANGE", "0.5")))
    flow_oi_min_age: float = field(default_factory=lambda: float(_env("FLOW_OI_MIN_AGE", "120")))
    flow_oi_max_age: float = field(default_factory=lambda: float(_env("FLOW_OI_MAX_AGE", "1800")))
    fast_enabled: bool = field(default_factory=lambda: _flag("FAST_ENABLED", "1"))
    fast_max_symbols: int = field(default_factory=lambda: int(_env("FAST_MAX_SYMBOLS", "8")))
    fast_stay_seconds: float = field(default_factory=lambda: float(_env("FAST_STAY_SECONDS", "300")))
    fast_min_score: float = field(default_factory=lambda: float(_env("FAST_MIN_SCORE", "30")))
    timeframes: list = field(default_factory=lambda: _csv("TIMEFRAMES", "1m,5m,15m,30m,1h"))
    indicator1_timeframe: str = field(default_factory=lambda: _env("INDICATOR1_TIMEFRAME", "1m"))
    scan_interval: float = field(default_factory=lambda: float(_env("SCAN_INTERVAL", "10")))
    scan_top_n: int = field(default_factory=lambda: int(_env("SCAN_TOP_N", "0")))
    scan_pool_workers: int = field(default_factory=lambda: int(_env("SCAN_POOL_WORKERS", "10")))
    retrain_interval: float = field(default_factory=lambda: float(_env("RETRAIN_INTERVAL", "60")))
    futures: bool = field(default_factory=lambda: _env("FUTURES", "0") == "1")
    max_risk_per_trade: float = field(default_factory=lambda: float(_env("MAX_RISK_PER_TRADE", "0.01")))
    entry_position_pct: float = field(default_factory=lambda: float(_env("ENTRY_POSITION_PCT", "40")))
    min_notional: float = field(default_factory=lambda: float(_env("MIN_NOTIONAL", "5")))
    max_daily_loss: float = field(default_factory=lambda: float(_env("MAX_DAILY_LOSS", "0.05")))
    max_positions: int = field(default_factory=lambda: int(_env("MAX_POSITIONS", "10")))
    max_leverage: float = field(default_factory=lambda: float(_env("MAX_LEVERAGE", "1")))
    max_margin_frac: float = field(default_factory=lambda: float(_env("MAX_MARGIN_FRAC", "0.25")))
    max_wallet_usage: float = field(default_factory=lambda: float(_env("MAX_WALLET_USAGE", "0.5")))
    impulse_only: bool = field(default_factory=lambda: _env("IMPULSE_ONLY", "0") == "1")
    trail_enabled: bool = field(default_factory=lambda: _env("TRAIL_ENABLED", "0") == "1")
    trail_activation: float = field(default_factory=lambda: float(_env("TRAIL_ACTIVATION_PCT", "0.5")) / 100.0)
    trail_distance: float = field(default_factory=lambda: float(_env("TRAIL_DISTANCE_PCT", "0.3")) / 100.0)
    trail_break_even_r: float = field(default_factory=lambda: float(_env("TRAIL_BREAK_EVEN_R", "0.8")))
    trail_start_r: float = field(default_factory=lambda: float(_env("TRAIL_START_R", "1.25")))
    trail_distance_r: float = field(default_factory=lambda: float(_env("TRAIL_DISTANCE_R", "0.75")))
    lock_port: int = field(default_factory=lambda: int(_env("LOCK_PORT", "47777")))
    zone_swing_length: int = field(default_factory=lambda: int(_env("ZONE_SWING_LENGTH", "10")))
    zone_history: int = field(default_factory=lambda: int(_env("ZONE_HISTORY", "20")))
    zone_box_width: float = field(default_factory=lambda: float(_env("ZONE_BOX_WIDTH", "2.5")))
    zone_min_timeframes: int = field(default_factory=lambda: int(_env("ZONE_MIN_TIMEFRAMES", "2")))
    zone_near_atr: float = field(default_factory=lambda: float(_env("ZONE_NEAR_ATR", "0.5")))
    mirror_enabled: bool = field(default_factory=lambda: _flag("MIRROR_ENABLED", "1"))
    mirror_dom_threshold: float = field(default_factory=lambda: float(_env("MIRROR_DOM_THRESHOLD", "0.5")))
    mirror_near_atr: float = field(default_factory=lambda: float(_env("MIRROR_NEAR_ATR", "1.0")))
    indicator_htf_timeframe: str = field(default_factory=lambda: _env("INDICATOR_HTF_TIMEFRAME", "240m"))
    probability_threshold: float = field(default_factory=lambda: float(_env("PROBABILITY_THRESHOLD", "55")))
    orderbook_vp_len: int = field(default_factory=lambda: int(_env("ORDERBOOK_VP_LEN", "300")))
    orderbook_levels: int = field(default_factory=lambda: int(_env("ORDERBOOK_LEVELS", "80")))
    orderbook_dom_levels: int = field(default_factory=lambda: int(_env("ORDERBOOK_DOM_LEVELS", "16")))
    orderbook_wall_mult: float = field(default_factory=lambda: float(_env("ORDERBOOK_WALL_MULT", "2.0")))
    orderbook_fast_power: float = field(default_factory=lambda: float(_env("ORDERBOOK_FAST_POWER", "2.2")))
    orderbook_fast_decay: float = field(default_factory=lambda: float(_env("ORDERBOOK_FAST_DECAY", "0.72")))
    orderbook_limit: int = field(default_factory=lambda: int(_env("ORDERBOOK_LIMIT", "100")))

    # ── Стакан / лента / комитет фактов ──
    ob_depth_levels: int = field(default_factory=lambda: int(_env("OB_DEPTH_LEVELS", "10")))
    ob_wall_mult: float = field(default_factory=lambda: float(_env("OB_WALL_MULT", "3.0")))
    ob_tape_window: float = field(default_factory=lambda: float(_env("OB_TAPE_WINDOW", "30")))
    ob_tape_min_velocity: float = field(default_factory=lambda: float(_env("OB_TAPE_MIN_VELOCITY", "2.0")))
    ob_iceberg_min_seen: int = field(default_factory=lambda: int(_env("OB_ICEBERG_MIN_SEEN", "3")))
    entry_min_absorption_ticks: int = field(default_factory=lambda: int(_env("ENTRY_MIN_ABSORPTION_TICKS", "3")))
    indicator_min_agree: int = field(default_factory=lambda: int(_env("INDICATOR_MIN_AGREE", "3")))
    entry_probability_min: float = field(default_factory=lambda: float(_env("ENTRY_PROBABILITY_MIN", "60")))
    book_block_abs: float = field(default_factory=lambda: float(_env("BOOK_BLOCK_ABS", "0.2")))
    entry_tape_min_ratio: float = field(default_factory=lambda: float(_env("ENTRY_TAPE_MIN_RATIO", "0.0")))
    entry_max_run_atr: float = field(default_factory=lambda: float(_env("ENTRY_MAX_RUN_ATR", "0.0")))
    entry_min_run_atr: float = field(default_factory=lambda: float(_env("ENTRY_MIN_RUN_ATR", "0.0")))
    entry_min_bar_range_atr: float = field(default_factory=lambda: float(_env("ENTRY_MIN_BAR_RANGE_ATR", "0.0")))
    entry_wall_ahead_pct: float = field(default_factory=lambda: float(_env("ENTRY_WALL_AHEAD_PCT", "0.0")))
    entry_wall_ahead_mult: float = field(default_factory=lambda: float(_env("ENTRY_WALL_AHEAD_MULT", "0.0")))
    reentry_cooldown_sec: int = field(default_factory=lambda: int(_env("REENTRY_COOLDOWN_SEC", "3600")))
    reversal_enabled: bool = field(default_factory=lambda: _flag("REVERSAL_ENABLED", "0"))
    sl_min_skip: int = field(default_factory=lambda: int(_env("SL_MIN_SKIP", "2")))
    impulse_enabled: bool = field(default_factory=lambda: _flag("IMPULSE_ENABLED", "1"))
    exit_reversal_prob: float = field(default_factory=lambda: float(_env("EXIT_REVERSAL_PROB", "45")))
    exit_protect_r: float = field(default_factory=lambda: float(_env("EXIT_PROTECT_R", "0.0")))
    exit_hold_prob: float = field(default_factory=lambda: float(_env("EXIT_HOLD_PROB", "55")))
    exit_weaken_ratio: float = field(default_factory=lambda: float(_env("EXIT_WEAKEN_RATIO", "0.5")))
    exit_book_flip_abs: float = field(default_factory=lambda: float(_env("EXIT_BOOK_FLIP_ABS", "0.2")))
    exit_grace_seconds: float = field(default_factory=lambda: float(_env("EXIT_GRACE_SECONDS", "90")))
    scale_out_enabled: bool = field(default_factory=lambda: _flag("SCALE_OUT_ENABLED", "0"))
    scale_out_r: float = field(default_factory=lambda: float(_env("SCALE_OUT_R", "1.0")))
    scale_out_fraction: float = field(default_factory=lambda: float(_env("SCALE_OUT_FRACTION", "0.5")))
    entry_retest_enabled: bool = field(default_factory=lambda: _flag("ENTRY_RETEST_ENABLED", "0"))
    retest_min_impulse_atr: float = field(default_factory=lambda: float(_env("RETEST_MIN_IMPULSE_ATR", "0.8")))
    retest_min_pullback: float = field(default_factory=lambda: float(_env("RETEST_MIN_PULLBACK", "0.25")))
    retest_max_pullback: float = field(default_factory=lambda: float(_env("RETEST_MAX_PULLBACK", "0.75")))
    retest_require_sweep: bool = field(default_factory=lambda: _flag("RETEST_REQUIRE_SWEEP", "1"))
    retest_require_trigger: bool = field(default_factory=lambda: _flag("RETEST_REQUIRE_TRIGGER", "1"))
    entry_min_rr_to_wall: float = field(default_factory=lambda: float(_env("ENTRY_MIN_RR_TO_WALL", "0.0")))
    entry_sl_atr_mult: float = field(default_factory=lambda: float(_env("ENTRY_SL_ATR_MULT", "1.5")))
    entry_sl_min_pct: float = field(default_factory=lambda: float(_env("ENTRY_SL_MIN_PCT", "0.2")))
    micro_exit_min_vol_usd: float = field(default_factory=lambda: float(_env("MICRO_EXIT_MIN_VOL_USD", "8000")))
    micro_gate_min_vol_usd: float = field(default_factory=lambda: float(_env("MICRO_GATE_MIN_VOL_USD", "1000")))
    micro_exit_vol_frac: float = field(default_factory=lambda: float(_env("MICRO_EXIT_VOL_FRAC", "0.25")))
    micro_exit_confirm_bps: float = field(default_factory=lambda: float(_env("MICRO_EXIT_CONFIRM_BPS", "12")))
    micro_exit_hold_s: float = field(default_factory=lambda: float(_env("MICRO_EXIT_HOLD_S", "0.8")))
    micro_exit_burst_hold_s: float = field(default_factory=lambda: float(_env("MICRO_EXIT_BURST_HOLD_S", "0.25")))
    micro_exit_protect_r: float = field(default_factory=lambda: float(_env("MICRO_EXIT_PROTECT_R", "1.0")))
    micro_exit_cvd_usd: float = field(default_factory=lambda: float(_env("MICRO_EXIT_CVD_USD", "15000")))
    micro_exit_ratio: float = field(default_factory=lambda: float(_env("MICRO_EXIT_RATIO", "0.42")))
    micro_exit_burst_usd: float = field(default_factory=lambda: float(_env("MICRO_EXIT_BURST_USD", "30000")))
    micro_exit_spread_slope_bps: float = field(default_factory=lambda: float(_env("MICRO_EXIT_SPREAD_SLOPE_BPS", "6")))
    run_atr_adaptive: float = field(default_factory=lambda: float(_env("RUN_ATR_ADAPTIVE", "0")))
    run_atr_rel_base: float = field(default_factory=lambda: float(_env("RUN_ATR_REL_BASE", "0.001")))
    ai_provider_order: list = field(default_factory=lambda: _csv(
        "AI_PROVIDER_ORDER", "openrouter,mistral,cerebras,deepinfra,cohere"
    ))
    ai_required: bool = field(default_factory=lambda: _flag("AI_REQUIRED", "1"))
    ai_probe_on_start: bool = field(default_factory=lambda: _flag("AI_PROBE_ON_START", "1"))
    ai_timeout: float = field(default_factory=lambda: float(_env("AI_TIMEOUT", "12")))
    ai_json_mode: bool = field(default_factory=lambda: _flag("AI_JSON_MODE", "1"))
    ai_min_confidence: float = field(default_factory=lambda: float(_env("AI_MIN_CONFIDENCE", "0.55")))
    ai_exit_min_confidence: float = field(default_factory=lambda: float(_env("AI_EXIT_MIN_CONFIDENCE", "0.60")))
    ai_exit_interval: float = field(default_factory=lambda: float(_env("AI_EXIT_INTERVAL", "30")))
    nn_min_samples: int = field(default_factory=lambda: int(_env("NN_MIN_SAMPLES", "50")))
    nn_min_probability: float = field(default_factory=lambda: float(_env("NN_MIN_PROBABILITY", "0.50")))
    nn_filter_enabled: bool = field(default_factory=lambda: _flag("NN_FILTER_ENABLED", "1"))

    @property
    def zone_timeframes(self) -> list:
        """Only the 1-minute through 1-hour frames participate in confluence."""
        selected = []
        for timeframe in self.timeframes:
            value = str(timeframe).lower()
            try:
                minutes = int(value[:-1]) * (60 if value.endswith("h") else 1)
            except (TypeError, ValueError):
                continue
            if 0 < minutes <= 60:
                selected.append(timeframe)
        return selected or ["1m", "5m", "15m", "30m", "1h"]

    @property
    def base_symbols(self) -> list:
        return [s.split(":")[0] for s in self.symbols]

    @property
    def trade_symbols(self) -> list:
        return self.symbols if self.futures else self.base_symbols
