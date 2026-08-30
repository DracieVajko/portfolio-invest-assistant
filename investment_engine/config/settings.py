from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load .env
    load_dotenv("api.env")  # Also load api.env for Trading212 credentials
except ImportError:
    pass


def _env(key: str, default: str = "") -> str:
    """Get environment variable with default."""
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, "").lower()
    return val in ("1", "true", "yes", "on") if val else default


@dataclass(slots=True)
class MarketRegimeSettings:
    """Configuration for EXI2 market regime detection."""

    enabled: bool = True
    symbol: str = "EXI2.DE"
    cache_ttl_seconds: int = 3600

    # Timeframe configurations
    timeframes: dict = field(default_factory=lambda: {
        "intraday_15m": {"interval": "15m", "period": "1d", "lookback_bars": 96, "enabled": True, "weight": 0.5},
        "intraday_1h": {"interval": "1h", "period": "5d", "lookback_bars": 120, "enabled": True, "weight": 0.7},
        "daily": {"interval": "1d", "period": "3mo", "lookback_bars": 63, "enabled": True, "weight": 1.5},
        "weekly": {"interval": "1wk", "period": "2y", "lookback_bars": 104, "enabled": True, "weight": 2.0},
        "monthly": {"interval": "1mo", "period": "max", "lookback_bars": 120, "enabled": True, "weight": 1.0},
    })

    # Peak/Valley detection
    peak_valley: dict = field(default_factory=lambda: {
        "prominence_pct": 2.0,
        "min_distance": 5,
        "cluster_threshold_pct": 1.0,
        "min_prominence_bars": 3,
    })

    # Regime classification thresholds
    thresholds: dict = field(default_factory=lambda: {
        # PEAK_HOLD
        "peak_hold_rsi_daily_min": 60,
        "peak_hold_rsi_weekly_min": 55,
        "peak_hold_price_above_sma20_daily": True,
        "peak_hold_price_above_sma50_weekly": True,
        "peak_hold_macd_hist_declining": True,
        "peak_hold_consolidation_days_min": 5,
        "peak_hold_consolidation_range_pct": 2.5,

        # DECLINING
        "declining_price_below_sma20_daily": True,
        "declining_price_below_sma50_daily": True,
        "declining_macd_bearish_cross": True,
        "declining_rsi_daily_max": 50,
        "declining_adx_min": 25,
        "declining_lower_highs": True,
        "declining_lower_lows": True,

        # MUST_BUY
        "must_buy_near_200dma_tolerance_pct": 2.0,
        "must_buy_near_52w_low_tolerance_pct": 5.0,
        "must_buy_rsi_daily_max": 35,
        "must_buy_rsi_weekly_max": 40,
        "must_buy_volume_spike_threshold": 2.0,
        "must_buy_supertrend_flip_bullish": True,
        "must_buy_macd_weekly_bullish": True,

        # Confidence
        "min_timeframes_agree": 2,
        "confidence_threshold": 0.75,
    })

    # News settings
    news: dict = field(default_factory=lambda: {
        "max_age_hours": 48,
        "min_relevance_score": 60,
        "max_articles_per_symbol": 5,
    })

    # FinViz enrichment
    finviz: dict = field(default_factory=lambda: {
        "enabled": True,
        "get_fundamentals": True,
        "get_sector_breadth": True,
        "sectors": [
            "Technology", "Healthcare", "Financial", "Consumer Cyclical",
            "Industrial", "Energy", "Utilities", "Real Estate",
            "Basic Materials", "Consumer Defensive", "Communication Services",
        ],
    })

    # Reporting
    reporting: dict = field(default_factory=lambda: {
        "include_in_main_report": True,
        "include_charts": False,
        "include_json": True,
    })


@dataclass(slots=True)
class EngineSettings:
    """Configuration for the new modular investment engine."""

    reasoning_level: str = "structured"
    preferred_themes: list[str] = field(default_factory=lambda: ["long_term", "quality", "risk_control"])
    preferred_sectors: list[str] = field(default_factory=lambda: ["AI", "Semiconductors", "Cloud", "Data Centers", "Renewables"])
    portfolio_strategy: str = "long_term"
    investment_profile: str = "balanced"
    max_news_age_days: int = 7
    ignore_duplicate_news: bool = True
    strict_fact_mode: bool = True
    confidence_threshold: float = 0.6
    research_depth: str = "moderate"
    provider: str = _env("PROVIDER", "auto")
    lm_studio_base_url: str = _env("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    lm_studio_model: str = _env("LM_STUDIO_MODEL", "qwen3.8-4b")
    lm_studio_context_tokens: int = _env_int("LM_STUDIO_CONTEXT_TOKENS", 32768)
    lm_studio_max_output_tokens: int = _env_int("LM_STUDIO_MAX_OUTPUT_TOKENS", 32768)
    request_timeout_seconds: int = _env_int("REQUEST_TIMEOUT_SECONDS", 1800)
    decision_model: str = _env("DECISION_MODEL", "qwen3.8-4b")
    writer_model: str = _env("WRITER_MODEL", "qwen3.8-4b")
    fallback_model: str = _env("FALLBACK_MODEL", "openrouter/free")
    openrouter_base_url: str = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_model: str = _env("OPENROUTER_MODEL", "openrouter/free")
    openrouter_api_key: str = _env("OPENROUTER_API_KEY", "")
    openrouter_context_tokens: int = _env_int("OPENROUTER_CONTEXT_TOKENS", 62000)
    openrouter_max_output_tokens: int = _env_int("OPENROUTER_MAX_OUTPUT_TOKENS", 8192)
    ollama_base_url: str = _env("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = _env("OLLAMA_MODEL", "gemma4:12b")
    ollama_context_tokens: int = _env_int("OLLAMA_CONTEXT_TOKENS", 128000)
    output_folder: str = _env("OUTPUT_FOLDER", "reports")
    archive_folder: str = _env("ARCHIVE_FOLDER", "reports/archive")
    prompt_dir: str = _env("PROMPT_DIR", "investment_engine/prompts")

    # Market regime settings
    market_regime: MarketRegimeSettings = field(default_factory=MarketRegimeSettings)

    @classmethod
    def from_defaults(cls) -> "EngineSettings":
        return cls()

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None = None) -> "EngineSettings":
        """Create settings from the project's JSON config, ignoring unrelated keys."""
        values = values or {}
        aliases = {
            "model": "lm_studio_model",
            "num_ctx": "lm_studio_context_tokens",
            "lm_studio_timeout": "request_timeout_seconds",
        }
        allowed = set(cls.__dataclass_fields__)
        normalized = {
            aliases.get(key, key): value
            for key, value in values.items()
            if aliases.get(key, key) in allowed
        }
        # Handle nested market_regime settings
        if "market_regime" in values and isinstance(values["market_regime"], dict):
            normalized["market_regime"] = MarketRegimeSettings(**values["market_regime"])
        # Ensure secrets are loaded from environment if not in config
        for secret_field in ("openrouter_api_key", "lm_studio_base_url", "openrouter_base_url", "ollama_base_url"):
            if secret_field not in normalized:
                normalized[secret_field] = _env(secret_field.upper(), "")
        return cls(**normalized)

    def prompt_dir_path(self) -> Path:
        return Path(self.prompt_dir)