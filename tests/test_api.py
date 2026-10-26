from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

START = 1_704_067_200_000  # 2024-01-01 UTC
HOUR = 3_600_000
PAYLOAD = {
    "symbol": "BTCUSDT",
    "interval": "1h",
    "start": "2024-01-01T00:00:00Z",
    "end": "2024-01-02T00:00:00Z",
}


def candle(open_time: int, duration=HOUR) -> list:
    return [
        open_time,
        "10",
        "12",
        "9",
        "11",
        "3",
        open_time + duration - 1,
        "30",
        2,
        "1",
        "10",
        "0",
    ]


def market_handler(request):
    if request.url.path == "/api/v3/time":
        return httpx.Response(200, json={"serverTime": START + 48 * HOUR})
    assert request.url.params["symbol"] == "BTCUSDT"
    start = int(request.url.params["startTime"])
    end = int(request.url.params["endTime"])
    rows = [candle(START + index * HOUR) for index in range(24)]
    return httpx.Response(200, json=[row for row in rows if start <= row[0] <= end])


def api(tmp_path, handler=market_handler, *, max_candles=100_000):
    settings = replace(
        Settings(),
        database_path=tmp_path / "market.sqlite3",
        request_pause=0,
        max_candles_per_import=max_candles,
    )
    return TestClient(create_app(settings, transport=httpx.MockTransport(handler)))


def test_import_round_trip_repeat_and_keyset_pagination(tmp_path):
    with api(tmp_path) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post("/candles/import", json={**PAYLOAD, "symbol": " btcusdt "})
        assert response.status_code == 200, response.text
        assert response.json() == {
            "symbol": "BTCUSDT",
            "interval": "1h",
            "start_ms": START,
            "effective_end_ms": START + 24 * HOUR,
            "server_time_ms": START + 48 * HOUR,
            "fetched": 24,
            "prepared": 24,
            "inserted": 24,
            "duplicates": 0,
            "excluded": 0,
        }
        repeated = client.post("/candles/import", json=PAYLOAD).json()
        assert repeated["inserted"] == 0
        assert repeated["duplicates"] == 24
        params = {"symbol": "btcusdt", "interval": "1h", "limit": 10}
        page_one = client.get("/candles", params=params).json()
        assert len(page_one["candles"]) == 10
        assert page_one["candles"][0]["open_time"] == START
        assert page_one["next_after_open_time"] == START + 9 * HOUR
        page_two = client.get(
            "/candles", params={**params, "after_open_time": page_one["next_after_open_time"]}
        ).json()
        assert page_two["candles"][0]["open_time"] == START + 10 * HOUR
        final = client.get(
            "/candles", params={**params, "after_open_time": page_two["next_after_open_time"]}
        ).json()
        assert len(final["candles"]) == 4
        assert final["next_after_open_time"] is None
        filtered = client.get(
            "/candles",
            params={**params, "start": "2024-01-01T02:00:00Z", "end": "2024-01-01T04:00:00Z"},
        ).json()
        assert [row["open_time"] for row in filtered["candles"]] == [
            START + 2 * HOUR,
            START + 3 * HOUR,
        ]
        assert client.get("/datasets").json() == [
            {
                "symbol": "BTCUSDT",
                "interval": "1h",
                "candle_count": 24,
                "first_open_time": START,
                "last_open_time": START + 23 * HOUR,
            }
        ]
        assert client.get("/openapi.json").status_code == 200


@pytest.mark.parametrize(
    "patch",
    [
        {"start": "2024-01-01T00:00:00"},
        {"interval": "2m"},
        {"end": "2023-12-31T00:00:00Z"},
        {"symbol": "BTC/USDT"},
        {"start": "2024-01-01T00:00:00.000001Z"},
        {"start": "0001-01-01T00:00:00+14:00"},
        {"end": "9999-12-31T23:59:59-14:00"},
    ],
)
def test_invalid_import_request_never_reaches_binance(tmp_path, patch):
    def handler(_request):
        pytest.fail("Invalid request must be rejected before network access")

    with api(tmp_path, handler) as client:
        assert client.post("/candles/import", json={**PAYLOAD, **patch}).status_code == 422


def test_future_start_is_checked_against_binance_time(tmp_path):
    with api(tmp_path) as client:
        response = client.post(
            "/candles/import", json={**PAYLOAD, "start": "2025-01-01T00:00:00Z", "end": None}
        )
        assert response.status_code == 422
        assert client.get("/datasets").json() == []


@pytest.mark.parametrize("status", [418, 429])
def test_rate_limit_propagates_retry_after_without_writing(tmp_path, status):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(status, headers={"Retry-After": "90"}, json={"msg": "limited"})

    with api(tmp_path, handler) as client:
        response = client.post("/candles/import", json=PAYLOAD)
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "90"
        assert client.get("/datasets").json() == []
    assert len(attempts) == 1


def test_invalid_exchange_symbol_is_client_error(tmp_path):
    def handler(request):
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": START + 48 * HOUR})
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    with api(tmp_path, handler) as client:
        response = client.post("/candles/import", json=PAYLOAD)
        assert response.status_code == 400
        assert client.get("/datasets").json() == []


@pytest.mark.parametrize("corruption", ["shape", "ohlc"])
def test_bad_upstream_data_does_not_change_existing_dataset(tmp_path, corruption):
    broken = False

    def handler(request):
        response = market_handler(request)
        if broken and request.url.path == "/api/v3/klines":
            rows = response.json()
            if corruption == "shape":
                rows[-1].pop()
            else:
                rows[-1][2] = "1"
            return httpx.Response(200, json=rows)
        return response

    with api(tmp_path, handler) as client:
        assert client.post("/candles/import", json=PAYLOAD).status_code == 200
        before = client.get("/datasets").json()
        broken = True
        assert client.post("/candles/import", json=PAYLOAD).status_code == 502
        assert client.get("/datasets").json() == before


def test_import_limit_returns_413_and_writes_nothing(tmp_path):
    with api(tmp_path, max_candles=23) as client:
        assert client.post("/candles/import", json=PAYLOAD).status_code == 413
        assert client.get("/datasets").json() == []


def test_second_page_failure_leaves_database_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr("app.binance.time.sleep", lambda _: None)

    def handler(request):
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": START + 2000 * HOUR})
        if int(request.url.params["startTime"]) == START:
            return httpx.Response(200, json=[candle(START + index * HOUR) for index in range(1000)])
        return httpx.Response(503, json={"msg": "unavailable"})

    with api(tmp_path, handler) as client:
        response = client.post("/candles/import", json={**PAYLOAD, "end": "2024-03-01T00:00:00Z"})
        assert response.status_code == 502
        assert client.get("/datasets").json() == []
