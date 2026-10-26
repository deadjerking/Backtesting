"""HTTP API. Блокирующие HTTP/Pandas/SQLite операции выполняются в thread pool."""

import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.binance import BinanceClient, BinanceError, ImportTooLarge
from app.config import Settings
from app.database import Database
from app.schemas import (
    CandlePage,
    Dataset,
    ImportRequest,
    ImportResult,
    Interval,
    MarketDatetime,
    Symbol,
    to_milliseconds,
)
from app.service import DataService

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, *, transport: httpx.BaseTransport | None = None
) -> FastAPI:
    config = settings or Settings.from_env()
    database = Database(config.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database.initialize()
        with httpx.Client(
            base_url=config.binance_base_url,
            timeout=config.request_timeout,
            transport=transport,
            headers={"User-Agent": "backtesting-data/0.1"},
        ) as client:
            application.state.service = DataService(BinanceClient(client, config), database)
            yield

    application = FastAPI(
        title="Backtesting Market Data API",
        version="0.1.0",
        description="Исторические закрытые Spot-свечи: Binance → Pandas → SQLite3.",
        lifespan=lifespan,
    )

    @application.exception_handler(BinanceError)
    async def binance_error(_request: Request, exc: BinanceError):
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        return JSONResponse(
            status_code=exc.status_code, content={"detail": str(exc)}, headers=headers
        )

    @application.exception_handler(ImportTooLarge)
    async def import_too_large(_request: Request, exc: ImportTooLarge):
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    @application.exception_handler(sqlite3.Error)
    async def database_error(_request: Request, exc: sqlite3.Error):
        logger.error("Ошибка SQLite", exc_info=exc)
        return JSONResponse(status_code=503, content={"detail": "База данных недоступна"})

    @application.get("/health", tags=["Сервис"])
    def health() -> dict[str, str]:
        database.healthcheck()
        return {"status": "ok"}

    @application.post("/candles/import", response_model=ImportResult, tags=["Свечи"])
    def import_candles(payload: ImportRequest, request: Request) -> ImportResult:
        try:
            return request.app.state.service.import_candles(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/candles", response_model=CandlePage, tags=["Свечи"])
    def read_candles(
        symbol: Symbol,
        interval: Interval,
        start: MarketDatetime | None = None,
        end: MarketDatetime | None = None,
        after_open_time: Annotated[int | None, Query(ge=0, le=2**63 - 1)] = None,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
    ) -> CandlePage:
        if start is not None and end is not None and start >= end:
            raise HTTPException(status_code=422, detail="start должен быть раньше end")
        rows = database.read_candles(
            symbol,
            interval.value,
            start_ms=to_milliseconds(start) if start is not None else None,
            end_ms=to_milliseconds(end) if end is not None else None,
            after_open_time=after_open_time,
            limit=limit + 1,
        )
        return CandlePage(
            symbol=symbol,
            interval=interval,
            candles=rows[:limit],
            next_after_open_time=rows[limit - 1]["open_time"] if len(rows) > limit else None,
        )

    @application.get("/datasets", response_model=list[Dataset], tags=["Свечи"])
    def datasets():
        return database.list_datasets()

    return application


app = create_app()
