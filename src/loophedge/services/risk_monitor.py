from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from loophedge.bus import CH_CIRCUIT_BROKEN, Bus
from loophedge.models import EquitySnapshot, RiskEvent
from loophedge.schemas import CircuitBroken


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

            dd = (rolling_high - current_equity) / rolling_high if rolling_high else Decimal("0")
            s.add(EquitySnapshot(ts=now, cash=Decimal("0"),
                                  equity=current_equity, drawdown_pct=dd))
            s.commit()

            if dd >= self.kill_dd_pct:
                event_payload = {"drawdown_pct": str(dd), "equity": str(current_equity),
                                  "rolling_high": str(rolling_high)}
                s.add(RiskEvent(ts=now, kind="circuit_broken",
                                 payload=event_payload,
                                 actions_taken={"action": "flatten_all"}))
                s.commit()
                event = CircuitBroken(ts=now, drawdown_pct=dd, action="flatten_all")
                await self.bus.publish(CH_CIRCUIT_BROKEN, event)
                return event
        return None
