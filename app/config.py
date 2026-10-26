"""Настройки сервиса: пути не зависят от текущей рабочей директории."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_path: Path = PROJECT_ROOT / "data" / "market_data.sqlite3"
    binance_base_url: str = "https://data-api.binance.vision"
    request_timeout: float = 20.0
    max_candles_per_import: int = 100_000
    request_pause: float = 0.1

    @classmethod
    def from_env(cls) -> "Settings":
        path = Path(os.getenv("BACKTEST_DATABASE_PATH", str(cls.database_path)))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return cls(
            database_path=path,
            binance_base_url=os.getenv("BINANCE_BASE_URL", cls.binance_base_url),
        )
