"""Последовательность Binance -> Pandas -> SQLite; запись после полной проверки."""

from app.binance import BinanceClient
from app.database import Database
from app.processing import prepare_candles
from app.schemas import ImportRequest, ImportResult, to_milliseconds


class DataService:
    def __init__(self, binance: BinanceClient, database: Database) -> None:
        self.binance = binance
        self.database = database

    def import_candles(self, request: ImportRequest) -> ImportResult:
        server_time = self.binance.server_time()
        start_ms = to_milliseconds(request.start)
        end_ms = min(to_milliseconds(request.end) if request.end else server_time, server_time)
        if start_ms >= end_ms:
            raise ValueError("start должен быть раньше текущего времени Binance")
        rows = self.binance.fetch_klines(request.symbol, request.interval.value, start_ms, end_ms)
        frame = prepare_candles(rows, start_ms=start_ms, end_ms=end_ms, server_time_ms=server_time)
        inserted = self.database.save_candles(request.symbol, request.interval.value, frame)
        return ImportResult(
            symbol=request.symbol,
            interval=request.interval,
            start_ms=start_ms,
            effective_end_ms=end_ms,
            server_time_ms=server_time,
            fetched=len(rows),
            prepared=len(frame),
            inserted=inserted,
            duplicates=len(frame) - inserted,
            excluded=len(rows) - len(frame),
        )
