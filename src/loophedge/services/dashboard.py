from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from loophedge.models import EquitySnapshot, Position, RiskEvent, Signal

_TEMPLATE = """\
<!doctype html>
<html><head><title>loop-hedge</title>
<script src="https://unpkg.com/htmx.org@2.0.3"></script>
<style>body{font-family:system-ui;margin:2em;background:#0a0a0a;color:#eee}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #333;padding:6px 10px;text-align:left}
.card{background:#161616;padding:1em;margin:1em 0;border:1px solid #333}</style>
</head><body>
<h1>loop-hedge</h1>
<div class="card"><h2>Equity</h2>
<div hx-get="/api/equity" hx-trigger="load, every 5s" hx-swap="innerHTML"></div></div>
<div class="card"><h2>Positions</h2>
<div hx-get="/api/positions" hx-trigger="load, every 5s" hx-swap="innerHTML"></div></div>
<div class="card"><h2>Recent signals</h2>
<div hx-get="/api/signals" hx-trigger="load, every 5s" hx-swap="innerHTML"></div></div>
</body></html>"""


def build_app(session_factory: sessionmaker) -> FastAPI:
    app = FastAPI(title="loop-hedge")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def root():
        return _TEMPLATE

    @app.get("/api/equity")
    def equity():
        with session_factory() as s:
            rows = s.execute(
                select(EquitySnapshot).order_by(EquitySnapshot.ts.desc()).limit(200)
            ).scalars().all()
            return [{"ts": r.ts.isoformat(), "equity": str(r.equity),
                     "drawdown_pct": str(r.drawdown_pct)} for r in rows]

    @app.get("/api/positions")
    def positions():
        with session_factory() as s:
            rows = s.execute(select(Position)).scalars().all()
            return [{"symbol": r.symbol, "qty": str(r.qty),
                     "avg_entry": str(r.avg_entry),
                     "unrealized_pnl": str(r.unrealized_pnl)} for r in rows]

    @app.get("/api/signals")
    def signals(limit: int = 50):
        with session_factory() as s:
            rows = s.execute(
                select(Signal).order_by(Signal.ts_created.desc()).limit(limit)
            ).scalars().all()
            return [{"id": r.id, "strategy_id": r.strategy_id, "symbol": r.symbol,
                     "side": r.side, "size_pct": str(r.size_pct),
                     "status": r.status,
                     "rejection_reason": r.rejection_reason} for r in rows]

    @app.get("/api/risk-events")
    def risk_events():
        with session_factory() as s:
            rows = s.execute(
                select(RiskEvent).order_by(RiskEvent.ts.desc()).limit(50)
            ).scalars().all()
            return [{"id": r.id, "ts": r.ts.isoformat(), "kind": r.kind,
                     "payload": r.payload, "actions_taken": r.actions_taken}
                    for r in rows]

    return app
