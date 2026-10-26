import sqlite3

import pandas as pd
import pytest

from app.database import CANDLE_COLUMNS, Database


def frame(*open_times):
    return pd.DataFrame(
        [[time, time + 59_999, 10.0, 12.0, 9.0, 11.0, 5.0] for time in open_times],
        columns=CANDLE_COLUMNS,
    ).astype({"open_time": "int64", "close_time": "int64"})


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "data" / "candles.sqlite3")
    database.initialize()
    return database


def test_persistence_duplicate_isolation_and_metadata(database):
    assert database.save_candles("BTCUSDT", "1m", frame(120_000, 0, 60_000)) == 3
    assert database.save_candles("BTCUSDT", "1m", frame(0, 60_000)) == 0
    assert database.save_candles("ETHUSDT", "1m", frame(0)) == 1
    reopened = Database(database.path)
    reopened.initialize()
    reopened.healthcheck()
    assert [row["open_time"] for row in reopened.read_candles("BTCUSDT", "1m")] == [
        0,
        60_000,
        120_000,
    ]
    assert reopened.list_datasets() == [
        {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "candle_count": 3,
            "first_open_time": 0,
            "last_open_time": 120_000,
        },
        {
            "symbol": "ETHUSDT",
            "interval": "1m",
            "candle_count": 1,
            "first_open_time": 0,
            "last_open_time": 0,
        },
    ]


def test_read_range_cursor_limit_and_parameterized_symbol(database):
    database.save_candles("BTCUSDT", "1m", frame(0, 60_000, 120_000, 180_000))
    rows = database.read_candles("BTCUSDT", "1m", start_ms=60_000, end_ms=180_000, limit=1)
    assert [row["open_time"] for row in rows] == [60_000]
    rows = database.read_candles("BTCUSDT", "1m", end_ms=180_000, after_open_time=60_000)
    assert [row["open_time"] for row in rows] == [120_000]
    assert database.read_candles("BTCUSDT' OR 1=1 --", "1m") == []
    assert database.read_candles("BTCUSDT", "1h") == []


@pytest.mark.parametrize(
    "column,value",
    [("high", 8.0), ("volume", -1.0), ("close", float("inf")), ("open", float("nan"))],
)
def test_bad_batch_rolls_back_every_row(database, column, value):
    data = frame(0, 60_000)
    data.loc[1, column] = value
    with pytest.raises(sqlite3.IntegrityError):
        database.save_candles("BTCUSDT", "1m", data)
    assert database.read_candles("BTCUSDT", "1m") == []


def test_fractional_timestamps_are_not_silently_truncated(database):
    data = frame(0)
    data["open_time"] = [0.5]
    with pytest.raises(ValueError, match="integer dtype"):
        database.save_candles("BTCUSDT", "1m", data)


def test_unknown_schema_is_preserved(tmp_path):
    path = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE important_data (value TEXT)")
        connection.execute("INSERT INTO important_data VALUES ('preserved')")
    with pytest.raises(RuntimeError, match="non-empty"):
        Database(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM important_data").fetchone()[0] == "preserved"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_future_schema_version_is_rejected(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(RuntimeError, match="version: 2"):
        Database(path).initialize()
