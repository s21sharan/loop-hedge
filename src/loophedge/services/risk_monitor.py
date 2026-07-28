from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from loophedge.bus import CH_CIRCUIT_BROKEN, Bus
from loophedge.models import Bar, EquitySnapshot, Fill, Position, RiskEvent
from loophedge.schemas import CircuitBroken


def compute_equity(session: Session, starting_capital: Decimal) -> Decimal:
    """Mark-to-market equity from persisted state: cash plus open positions.

    Cash is reconstructed from every fill's signed notional and fees, so
    realized losses and cumulative trading costs are included. Summing only the
    unrealized PnL of currently-open positions would leave the kill switch blind
    to realized losses -- a strategy that repeatedly opened and closed at a loss
    would report zero drawdown forever.
    """
    signed_notional = case(
        (Fill.side == "long", -(Fill.qty * Fill.price)),
        else_=Fill.qty * Fill.price,
    )
    flow = session.execute(
        select(func.coalesce(func.sum(signed_notional - Fill.fees), 0))
    ).scalar()
    equity = starting_capital + Decimal(str(flow or 0))

    for p in session.execute(select(Position)).scalars().all():
        if p.qty == Decimal("0"):
            continue
        last_bar = session.execute(
            select(Bar).where(Bar.symbol == p.symbol)
            .order_by(Bar.ts.desc()).limit(1)
        ).scalar()
        mark = last_bar.close if last_bar is not None else p.avg_entry
        equity += mark * p.qty

    return equity


class RiskMonitor:
    def __init__(self, bus: Bus, session_factory: sessionmaker, kill_dd_pct: Decimal):
        self.bus = bus
        self.session_factory = session_factory
        self.kill_dd_pct = kill_dd_pct

    async def tick(self, now: datetime, current_equity: Decimal) -> CircuitBroken | None:
        window_start = now - timedelta(days=30)
        with self.session_factory() as s:
            recent_high_row = s.execute(
                select(EquitySnapshot.equity)
                .where(EquitySnapshot.ts >= window_start)
                .order_by(EquitySnapshot.equity.desc())
                .limit(1)
            ).scalar()
            rolling_high = max(recent_high_row or Decimal("0"), current_equity)

            if rolling_high > Decimal("0"):
                dd = (rolling_high - current_equity) / rolling_high
            elif current_equity <= Decimal("0"):
                # No prior baseline and equity is zero or below: treat as full drawdown.
                dd = Decimal("1")
            else:
                # First ever tick with positive equity and no baseline yet.
                dd = Decimal("0")

            existing = s.get(EquitySnapshot, now)
            if existing is None:
                s.add(EquitySnapshot(ts=now, cash=Decimal("0"),
                                      equity=current_equity, drawdown_pct=dd))
            else:
                # idempotent: refresh metrics on retry
                existing.cash = Decimal("0")
                existing.equity = current_equity
                existing.drawdown_pct = dd
            s.commit()

            if dd >= self.kill_dd_pct:
                event_payload = {"drawdown_pct": str(dd), "equity": str(current_equity),
                                  "rolling_high": str(rolling_high)}
                event = CircuitBroken(ts=now, drawdown_pct=dd, action="flatten_all")
                await self.bus.publish(CH_CIRCUIT_BROKEN, event)
                s.add(RiskEvent(ts=now, kind="circuit_broken",
                                 payload=event_payload,
                                 actions_taken={"action": "flatten_all"}))
                s.commit()
                return event
        return None
