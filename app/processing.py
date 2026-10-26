"""Явное выделение OHLCV, приведение типов и проверка данных в Pandas."""

import numpy as np
import pandas as pd

from app.binance import BinanceError

RAW_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]
CANDLE_COLUMNS = ["open_time", "close_time", "open", "high", "low", "close", "volume"]
NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]


def prepare_candles(
    rows: list[list], *, start_ms: int, end_ms: int, server_time_ms: int
) -> pd.DataFrame:
    """Проверить свечи; убрать лишние поля и незакрытые свечи, не заполняя пробелы."""
    try:
        if any(
            len(row) != 12 or type(row[0]) is not int or type(row[6]) is not int for row in rows
        ):
            raise ValueError("Ожидались 12 полей и целочисленные timestamps")
        frame = pd.DataFrame(rows, columns=RAW_COLUMNS).loc[:, CANDLE_COLUMNS].copy()
        for column in ("open_time", "close_time"):
            frame[column] = frame[column].astype("int64")
        for column in NUMERIC_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
        if not np.isfinite(frame[NUMERIC_COLUMNS].to_numpy()).all():
            raise ValueError("Цена или объём содержит NaN/Infinity")
        if (
            (frame["open_time"] < 0).any()
            or (frame["close_time"] < frame["open_time"]).any()
            or (frame[["open", "high", "low", "close"]] <= 0).any().any()
            or (frame["volume"] < 0).any()
            or (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any()
        ):
            raise ValueError("Нарушены ограничения OHLCV или времени")
    except (ValueError, TypeError, OverflowError) as exc:
        raise BinanceError(f"Некорректные данные Binance: {exc}") from exc

    return (
        frame.loc[
            (frame["open_time"] >= start_ms)
            & (frame["open_time"] < end_ms)
            & (frame["close_time"] < end_ms)
            & (frame["close_time"] < server_time_ms)
        ]
        .drop_duplicates(subset=["open_time"], keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
