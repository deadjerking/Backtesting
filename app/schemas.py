"""Внешний контракт API. Даты должны содержать часовой пояс."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


def normalize_symbol(value: str) -> str:
    return value.strip().upper()


def validate_symbol(value: str) -> str:
    if not 2 <= len(value) <= 30 or not value.isascii() or not value.isalnum():
        raise ValueError("symbol должен содержать 2–30 латинских букв или цифр")
    return value


Symbol = Annotated[str, AfterValidator(normalize_symbol), AfterValidator(validate_symbol)]


class Interval(StrEnum):
    SECOND_1 = "1s"
    MINUTE_1 = "1m"
    MINUTE_3 = "3m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1 = "1h"
    HOUR_2 = "2h"
    HOUR_4 = "4h"
    HOUR_6 = "6h"
    HOUR_8 = "8h"
    HOUR_12 = "12h"
    DAY_1 = "1d"
    DAY_3 = "3d"
    WEEK_1 = "1w"
    MONTH_1 = "1M"


def to_milliseconds(value: datetime) -> int:
    """Целые миллисекунды без округления float timestamp()."""
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000


def validate_date(value: datetime) -> datetime:
    try:
        milliseconds = to_milliseconds(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Дата выходит за поддерживаемый диапазон UTC") from exc
    if milliseconds < 0:
        raise ValueError("Дата должна быть не раньше 1970-01-01")
    if value.microsecond % 1000:
        raise ValueError("Поддерживается точность даты до миллисекунд")
    return value


MarketDatetime = Annotated[AwareDatetime, AfterValidator(validate_date)]


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: Symbol = Field(examples=["BTCUSDT"])
    interval: Interval = Field(examples=["1h"])
    start: MarketDatetime = Field(examples=["2024-01-01T00:00:00Z"])
    end: MarketDatetime | None = Field(
        default=None,
        description="Исключительная верхняя граница; по умолчанию время Binance",
        examples=["2024-01-02T00:00:00Z"],
    )

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end is not None and self.start >= self.end:
            raise ValueError("start должен быть раньше end")
        return self


class ImportResult(BaseModel):
    symbol: str
    interval: Interval
    start_ms: int
    effective_end_ms: int
    server_time_ms: int
    fetched: int
    prepared: int
    inserted: int
    duplicates: int
    excluded: int


class Candle(BaseModel):
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class CandlePage(BaseModel):
    symbol: str
    interval: Interval
    candles: list[Candle]
    next_after_open_time: int | None


class Dataset(BaseModel):
    symbol: str
    interval: Interval
    candle_count: int
    first_open_time: int
    last_open_time: int
