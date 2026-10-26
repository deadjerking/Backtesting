import pandas as pd
import pytest

from app.binance import BinanceError
from app.processing import CANDLE_COLUMNS, prepare_candles


def candle(open_time: int, close_time: int | None = None) -> list:
    return [
        open_time,
        "10.25",
        "12.5",
        "9",
        "11",
        "3.2",
        open_time + 999 if close_time is None else close_time,
        "999999",
        42,
        "7777",
        "8888",
        "unused",
    ]


def prepare(rows, *, start=0, end=10_000, now=10_000):
    return prepare_candles(rows, start_ms=start, end_ms=end, server_time_ms=now)


def test_reduces_to_typed_ohlcv_and_deduplicates_in_time_order():
    frame = prepare([candle(1000), candle(0), candle(1000)])
    assert list(frame.columns) == CANDLE_COLUMNS
    assert frame["open_time"].tolist() == [0, 1000]
    assert frame["open_time"].dtype == "int64"
    assert frame["close_time"].dtype == "int64"
    assert all(frame[column].dtype == "float64" for column in CANDLE_COLUMNS[2:])
    assert frame.iloc[0]["open"] == 10.25
    assert frame.iloc[0]["volume"] == 3.2


def test_keeps_only_fully_closed_candles_inside_range():
    rows = [candle(0), candle(1000), candle(2000), candle(3000)]
    assert prepare(rows, start=1000, end=3000)["open_time"].tolist() == [1000, 2000]
    assert prepare(rows, start=1000, end=3000, now=2500)["open_time"].tolist() == [1000]
    # At a mid-candle end, its eventual close must not enter a backtest.
    assert prepare(rows, start=1000, end=2500)["open_time"].tolist() == [1000]
    assert prepare([candle(0, 1000)], end=1000).empty


def test_empty_response_has_stable_dataframe_schema():
    frame = prepare([])
    assert frame.empty
    assert list(frame.columns) == CANDLE_COLUMNS
    assert pd.api.types.is_integer_dtype(frame["open_time"])


@pytest.mark.parametrize(
    ("field", "value"),
    [(1, "NaN"), (2, "inf"), (5, "-0.1"), (1, "0"), (2, "10"), (3, "11"), (6, -1), (6, 1.5)],
)
def test_corrupted_ohlcv_is_rejected(field, value):
    row = candle(0)
    row[field] = value
    with pytest.raises(BinanceError):
        prepare([row])
