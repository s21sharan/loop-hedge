from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from loophedge.bus import Bus
from loophedge.ledger.simulator import Simulator
from loophedge.models import Fill as FillRow
from loophedge.models import Position as PositionRow
from loophedge.models import Signal
from loophedge.risk.caps import ProposedTrade, enforce_pretrade
from loophedge.schemas import SignalCandidate, SignalVerified


class Executor:
    def __init__(self, bus: Bus, session_factory: sessionmaker,
                 simulator: Simulator, latest_prices: dict[str, Decimal]):
        self.bus = bus
        self.session_factory = session_factory
        self.simulator = simulator
        self.latest_prices = latest_prices

    async def handle_verified(self, verified: SignalVerified,
                              candidate: SignalCandidate) -> FillRow | None:
        equity = self.simulator.equity(self.latest_prices)
        allocations = self._current_strategy_allocations(equity)

        verdict = enforce_pretrade(
            equity=equity,
            current_positions={s: p.qty for s, p in self.simulator.positions.items()},
            strategy_allocations=allocations,
            proposed=ProposedTrade(strategy_id=candidate.strategy_id,
                                    symbol=candidate.symbol,
                                    side=candidate.side,
                                    size_pct=candidate.size_pct),
        )

        if not verdict.allowed:
            reason = verdict.reason or "rejected by risk caps"
            self._mark_signal(verified.signal_id, "killed", reason)
            return None

        ref_price = self.latest_prices[candidate.symbol]
        notional = equity * candidate.size_pct
        qty = notional / ref_price

        fill = self.simulator.apply_fill(
            symbol=candidate.symbol, side=candidate.side, qty=qty,
            ref_price=ref_price, ts=datetime.now(UTC),
        )

        with self.session_factory() as s:
            row = FillRow(id=fill.id, signal_id=verified.signal_id, ts=fill.ts,
                          symbol=fill.symbol, side=fill.side, qty=fill.qty,
                          price=fill.price, fees=fill.fees, venue="simulator")
            s.add(row)
            sig = s.get(Signal, verified.signal_id)
            if sig:
                sig.status = "executed"
            pos = s.get(PositionRow, candidate.symbol)
            new_pos = self.simulator.positions[candidate.symbol]
            if pos is None:
                s.add(PositionRow(symbol=candidate.symbol, qty=new_pos.qty,
                                   avg_entry=new_pos.avg_entry,
                                   unrealized_pnl=(ref_price - new_pos.avg_entry) * new_pos.qty,
                                   updated_at=fill.ts))
            else:
                pos.qty = new_pos.qty
                pos.avg_entry = new_pos.avg_entry
                pos.unrealized_pnl = (ref_price - new_pos.avg_entry) * new_pos.qty
                pos.updated_at = fill.ts
            s.commit()
        return row

    def _current_strategy_allocations(self, equity: Decimal) -> dict[str, Decimal]:
        # Deduplicate by (strategy_id, symbol) so a strategy with multiple
        # executed signals on the same symbol counts that position only once.
        owned: dict[str, set[str]] = {}
        with self.session_factory() as s:
            executed = s.query(Signal).filter(Signal.status == "executed").all()
            for sig in executed:
                owned.setdefault(sig.strategy_id, set()).add(sig.symbol)
        out: dict[str, Decimal] = {}
        for strategy_id, symbols in owned.items():
            total = Decimal("0")
            for symbol in symbols:
                pos = self.simulator.positions.get(symbol)
                if pos is None or pos.qty == 0:
                    continue
                ref = self.latest_prices.get(symbol, pos.avg_entry)
                total += abs(pos.qty) * ref
            out[strategy_id] = total / equity if equity else Decimal("0")
        return out

    def _mark_signal(self, signal_id: str, status: str, reason: str) -> None:
        with self.session_factory() as s:
            sig = s.get(Signal, signal_id)
            if sig:
                sig.status = status
                sig.rejection_reason = reason
                s.commit()
