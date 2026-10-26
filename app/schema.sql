-- Время: INTEGER, Unix milliseconds, UTC. Цены/объём: IEEE-754 double (REAL).
-- Составной ключ одновременно служит индексом для чтения серии по времени.
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL CHECK (length(symbol) > 0),
    interval TEXT NOT NULL CHECK (length(interval) > 0),
    open_time INTEGER NOT NULL CHECK (typeof(open_time) = 'integer' AND open_time >= 0),
    close_time INTEGER NOT NULL CHECK (typeof(close_time) = 'integer' AND close_time >= open_time),
    open REAL NOT NULL CHECK (open > 0 AND open <= 1.7976931348623157e308),
    high REAL NOT NULL CHECK (high > 0 AND high <= 1.7976931348623157e308),
    low REAL NOT NULL CHECK (low > 0 AND low <= 1.7976931348623157e308),
    close REAL NOT NULL CHECK (close > 0 AND close <= 1.7976931348623157e308),
    volume REAL NOT NULL CHECK (volume >= 0 AND volume <= 1.7976931348623157e308),
    PRIMARY KEY (symbol, interval, open_time),
    CHECK (low <= open AND low <= close AND low <= high),
    CHECK (high >= open AND high >= close)
) WITHOUT ROWID;
