from dataclasses import replace

import httpx
import pytest

from app.binance import BinanceClient, BinanceError, ImportTooLarge
from app.config import Settings


def candle(open_time: int) -> list:
    return [open_time, "10", "12", "9", "11", "3", open_time + 999, "30", 2, "1", "10", "0"]


def test_paginates_using_last_open_even_for_calendar_interval():
    requests = []
    page_one = [candle(index * 1000) for index in range(1000)]

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=page_one if len(requests) == 1 else [candle(1_000_000)])

    with httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = BinanceClient(http, Settings(request_pause=0))
        rows = client.fetch_klines("BTCUSDT", "1M", 0, 1_001_000)

    assert len(rows) == 1001
    assert [int(request.url.params["startTime"]) for request in requests] == [0, 999_001]
    assert all(request.url.params["endTime"] == "1000999" for request in requests)
    assert all(request.url.params["limit"] == "1000" for request in requests)
    assert all(request.url.params["interval"] == "1M" for request in requests)


def test_stale_page_cannot_loop_forever():
    page = [candle(index * 1000) for index in range(1000)]
    with httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=page)),
    ) as http:
        with pytest.raises(BinanceError):
            BinanceClient(http, Settings(request_pause=0)).fetch_klines(
                "BTCUSDT", "1s", 0, 2_000_000
            )


def test_import_candle_limit_is_enforced():
    settings = replace(Settings(), max_candles_per_import=1, request_pause=0)
    with httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=[candle(0), candle(1000)])
        ),
    ) as http:
        with pytest.raises(ImportTooLarge):
            BinanceClient(http, settings).fetch_klines("BTCUSDT", "1s", 0, 2000)


@pytest.mark.parametrize("status", [418, 429])
def test_rate_limit_stops_without_retry(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Retry-After": "120"}, json={"msg": "limited"})

    with httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as http:
        with pytest.raises(BinanceError) as error:
            BinanceClient(http, Settings(request_pause=0)).server_time()
    assert len(requests) == 1
    assert error.value.status_code == 503
    assert error.value.retry_after == "120"


def test_transient_failure_is_retried_but_bounded(monkeypatch):
    monkeypatch.setattr("app.binance.time.sleep", lambda _: None)
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(503, json={"msg": "temporarily unavailable"})

    with httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    ) as http:
        with pytest.raises(BinanceError) as error:
            BinanceClient(http, Settings(request_pause=0)).server_time()
    assert len(attempts) == 3
    assert error.value.status_code == 502
