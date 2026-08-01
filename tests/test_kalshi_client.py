import pytest
from unittest.mock import AsyncMock, patch

from loophedge.services.kalshi_client import fetch_candles, fetch_weather_markets


@pytest.mark.asyncio
async def test_fetch_weather_markets_filters_to_nyc_daily_high():
    fake_response = {
        "markets": [
            {"ticker": "KXHIGHNY-26AUG05-B82.5", "event_ticker": "KXHIGHNY-26AUG05",
             "status": "open", "close_time": "2026-08-05T22:00:00Z",
             "expiration_time": "2026-08-05T22:00:00Z"},
            {"ticker": "KXHIGHLAX-26AUG05-B75.5", "event_ticker": "KXHIGHLAX-26AUG05",
             "status": "open", "close_time": "2026-08-05T22:00:00Z",
             "expiration_time": "2026-08-05T22:00:00Z"},
            {"ticker": "SOME-OTHER-TICKER", "event_ticker": "OTHER",
             "status": "open"},
        ]
    }

    class MockResponse:
        def __init__(self, data): self._data = data
        def raise_for_status(self): pass
        def json(self): return self._data

    class MockClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return MockResponse(fake_response)

    with patch("loophedge.services.kalshi_client.httpx.AsyncClient", MockClient):
        markets = await fetch_weather_markets(["NYC", "LAX"])
    tickers = {m["ticker"] for m in markets}
    assert "KXHIGHNY-26AUG05-B82.5" in tickers
    assert "KXHIGHLAX-26AUG05-B75.5" in tickers
    assert "SOME-OTHER-TICKER" not in tickers


@pytest.mark.asyncio
async def test_fetch_candles_normalizes_cents_to_dollars():
    fake_response = {
        "candlesticks": [
            {"end_period_ts": 1754433900,
             "price": {"open": 42, "high": 47, "low": 40, "close": 45},
             "volume": 1000, "open_interest": 500},
        ]
    }

    class MockResponse:
        def __init__(self, data): self._data = data
        def raise_for_status(self): pass
        def json(self): return self._data

    class MockClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return MockResponse(fake_response)

    from decimal import Decimal
    with patch("loophedge.services.kalshi_client.httpx.AsyncClient", MockClient):
        candles = await fetch_candles("KXHIGHNY-26AUG05-B82.5", resolution_min=5)
    assert len(candles) == 1
    c = candles[0]
    # cents → dollars
    assert c["open"] == Decimal("0.42")
    assert c["close"] == Decimal("0.45")
    assert c["volume"] == Decimal("1000")
