"""Постоянное хранилище свечей с обычным SQLite3-интерфейсом для будущего C++."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

CANDLE_COLUMNS = ["open_time", "close_time", "open", "high", "low", "close", "volume"]
SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Создать новую БД; неизвестную версию или чужую схему не изменять."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, SCHEMA_VERSION):
                    raise RuntimeError(f"Unsupported SQLite schema version: {version}")
                if version == 0:
                    objects = connection.execute(
                        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                    if objects:
                        raise RuntimeError(
                            "Refusing to initialize a non-empty, unversioned database"
                        )
                    connection.execute(
                        Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
                    )
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._validate_schema(connection)
            connection.execute("PRAGMA journal_mode = WAL")

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        columns = connection.execute("PRAGMA table_info(candles)").fetchall()
        expected = [
            ("symbol", "TEXT", 1),
            ("interval", "TEXT", 2),
            ("open_time", "INTEGER", 3),
            ("close_time", "INTEGER", 0),
            ("open", "REAL", 0),
            ("high", "REAL", 0),
            ("low", "REAL", 0),
            ("close", "REAL", 0),
            ("volume", "REAL", 0),
        ]
        actual = [(row["name"], row["type"], row["pk"]) for row in columns]
        if actual != expected or any(row["notnull"] != 1 for row in columns):
            raise RuntimeError("SQLite candles schema does not match version 1")

    def save_candles(self, symbol: str, interval: str, frame: pd.DataFrame) -> int:
        """Атомарно добавить DataFrame; существующие закрытые свечи пропустить."""
        if list(frame.columns) != CANDLE_COLUMNS:
            raise ValueError(f"Expected DataFrame columns in order: {CANDLE_COLUMNS}")
        if frame.empty:
            return 0
        if not all(pd.api.types.is_integer_dtype(frame[column]) for column in CANDLE_COLUMNS[:2]):
            raise ValueError("Candle timestamps must have integer dtype (Unix milliseconds)")
        rows = (
            (symbol, interval, int(row[0]), int(row[1]), *(float(value) for value in row[2:]))
            for row in frame.itertuples(index=False, name=None)
        )
        with self._connection() as connection:
            with connection:
                before = connection.total_changes
                connection.executemany(
                    """INSERT INTO candles
                    (symbol, interval, open_time, close_time, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, interval, open_time) DO NOTHING""",
                    rows,
                )
                return connection.total_changes - before

    def read_candles(
        self,
        symbol: str,
        interval: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        after_open_time: int | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Читать серию по open_time: [start_ms, end_ms), после after_open_time."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        conditions = ["symbol = ?", "interval = ?"]
        parameters: list = [symbol, interval]
        for clause, value in (
            ("open_time >= ?", start_ms),
            ("open_time < ?", end_ms),
            ("open_time > ?", after_open_time),
        ):
            if value is not None:
                conditions.append(clause)
                parameters.append(value)
        parameters.append(limit)
        query = (
            "SELECT symbol, interval, open_time, close_time, open, high, low, close, volume "
            "FROM candles WHERE " + " AND ".join(conditions) + " ORDER BY open_time ASC LIMIT ?"
        )
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    def list_datasets(self) -> list[dict]:
        """Вернуть покрытие каждой пары symbol/interval."""
        with self._connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """SELECT symbol, interval, COUNT(*) AS candle_count,
                MIN(open_time) AS first_open_time, MAX(open_time) AS last_open_time
                FROM candles GROUP BY symbol, interval ORDER BY symbol, interval"""
                )
            ]

    def healthcheck(self) -> None:
        """Проверить доступность хранилища и совместимость его схемы."""
        with self._connection() as connection:
            self._validate_schema(connection)
            connection.execute("SELECT 1 FROM candles LIMIT 1").fetchone()
