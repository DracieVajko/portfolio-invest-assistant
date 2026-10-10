"""Strategy base - deterministic, no look-ahead, config-driven."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd


@dataclass
class Signal:
    date: pd.Timestamp
    symbol: str
    side: str  # BUY only for Phase 1
    reason: str


class StrategyBase(ABC):
    """Base for all experimental strategies."""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Given normalized OHLCV DataFrame (with EUR columns), return DataFrame of signals.
        Must be deterministic, no future access, shifted for execution at next open.
        Output columns: date, symbol, signal (1 for entry), reason
        """
        raise NotImplementedError

    def validate_config(self) -> list[str]:
        """Return list of config errors, empty if ok."""
        return []
