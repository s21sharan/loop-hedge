from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from loophedge.models import EquitySnapshot, Position
from loophedge.services.dashboard import build_app


def test_health(session_factory):
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_equity_endpoint(session_factory):
    with session_factory() as s:
        s.add(EquitySnapshot(ts=datetime.now(UTC), cash=Decimal("100000"),
                              equity=Decimal("100000"), drawdown_pct=Decimal("0")))
        s.commit()
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/api/equity")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_positions_endpoint(session_factory):
    with session_factory() as s:
        s.add(Position(symbol="BTCUSDT", qty=Decimal("0.1"),
                       avg_entry=Decimal("60000"),
                       unrealized_pnl=Decimal("0"),
                       updated_at=datetime.now(UTC)))
        s.commit()
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/api/positions")
    assert r.status_code == 200
    assert r.json()[0]["symbol"] == "BTCUSDT"


def test_root_returns_html(session_factory):
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "loop-hedge" in r.text.lower()


def test_ui_positions_returns_html(session_factory):
    with session_factory() as s:
        s.add(Position(symbol="BTCUSDT", qty=Decimal("0.1"),
                       avg_entry=Decimal("60000"),
                       unrealized_pnl=Decimal("0"),
                       updated_at=datetime.now(UTC)))
        s.commit()
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/ui/positions")
    assert r.status_code == 200
    assert "BTCUSDT" in r.text
    assert "<table" in r.text


def test_ui_risk_events_renders_empty_state(session_factory):
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/ui/risk-events")
    assert r.status_code == 200
