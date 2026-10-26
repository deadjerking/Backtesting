"""Публичный Binance Spot REST API: пагинация, таймауты и ошибки."""

import time
from typing import Any

import httpx

from app.config import Settings


class BinanceError(Exception):
    def __init__(
        self, message: str, *, status_code: int = 502, retry_after: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class ImportTooLarge(Exception):
    pass


class BinanceClient:
    def __init__(self, client: httpx.Client, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def _get(self, path: str, params: dict | None = None) -> Any:
        for attempt in range(3):
            try:
                response = self.client.get(path, params=params)
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise BinanceError("Не удалось соединиться с Binance") from exc
                time.sleep(2**attempt)
                continue

            # На 429/418 сразу прекращаем импорт: повторные запросы продлят бан IP.
            if response.status_code in {418, 429}:
                raise BinanceError(
                    "Binance ограничил запросы. Повторите после Retry-After.",
                    status_code=503,
                    retry_after=response.headers.get("Retry-After", "60"),
                )
            if response.status_code >= 500 and attempt < 2:
                time.sleep(2**attempt)
                continue
            if response.status_code == 451:
                raise BinanceError(
                    "Binance недоступен в этом регионе или сети (HTTP 451)",
                    status_code=503,
                )
            if not response.is_success:
                try:
                    payload = response.json()
                    message = str(payload.get("msg", "Ошибка API"))[:200]
                    code = payload.get("code")
                except (ValueError, AttributeError):
                    message, code = "Некорректный ответ Binance", None
                raise BinanceError(
                    f"Binance HTTP {response.status_code}: {message}",
                    status_code=400 if code in {-1121, -1120, -1100, -1102} else 502,
                )
            try:
                return response.json()
            except ValueError as exc:
                raise BinanceError("Binance вернул некорректный JSON") from exc
        raise BinanceError("Не удалось получить данные Binance")

    def server_time(self) -> int:
        payload = self._get("/api/v3/time")
        value = payload.get("serverTime") if isinstance(payload, dict) else None
        if type(value) is not int or not 0 < value < 2**63:
            raise BinanceError("Некорректное серверное время Binance")
        return value

    def fetch_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
        """Получить [start_ms, end_ms); следующий cursor работает и для 1M."""
        rows: list[list] = []
        cursor = start_ms
        while cursor < end_ms:
            if self.settings.request_pause:
                time.sleep(self.settings.request_pause)
            page = self._get(
                "/api/v3/klines",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms - 1,
                    "limit": 1000,
                },
            )
            if not isinstance(page, list) or len(page) > 1000:
                raise BinanceError("Некорректная страница свечей Binance")
            if not page:
                break
            previous = cursor - 1
            for row in page:
                if (
                    not isinstance(row, list)
                    or len(row) != 12
                    or type(row[0]) is not int
                    or not previous < row[0] < end_ms
                ):
                    raise BinanceError("Неверная структура или порядок свечей Binance")
                previous = row[0]
            rows.extend(page)
            if len(rows) > self.settings.max_candles_per_import:
                raise ImportTooLarge(
                    f"Лимит {self.settings.max_candles_per_import} свечей за импорт. "
                    "Разбейте период на несколько запросов."
                )
            cursor = page[-1][0] + 1
            if len(page) < 1000:
                break
        return rows
