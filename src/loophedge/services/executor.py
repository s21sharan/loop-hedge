import asyncio
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
                 simulator: Simulator, latest_prices: dict[str, Decimal],
                 venue: str = "simulator"):
        self.bus = bus
        self.session_factory = session_factory
        self.simulator = simulator
        self.latest_prices = latest_prices
        self.venue = venue

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
                          price=fill.price, fees=fill.fees, venue=self.venue)
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


class ExecutorService:
    """Long-running subscriber to signal.verified and circuit.broken."""

    def __init__(self, executor: Executor, bus: Bus, simulator: Simulator,
                  session_factory: sessionmaker):
        self.executor = executor
        self.bus = bus
        self.simulator = simulator
        self.session_factory = session_factory
        self._stop = asyncio.Event()

    async def run(self) -> None:
        signal_task = asyncio.create_task(self._consume_signals())
        circuit_task = asyncio.create_task(self._consume_circuit())
        try:
            await self._stop.wait()
        finally:
            signal_task.cancel()
            circuit_task.cancel()
            await asyncio.gather(signal_task, circuit_task, return_exceptions=True)

    async def stop(self) -> None:
        self._stop.set()

    async def _consume_signals(self) -> None:
        from loophedge.bus import CH_SIGNAL_VERIFIED
        async for payload in self.bus.subscribe(CH_SIGNAL_VERIFIED):
            try:
                await self.handle_signal(payload)
            except Exception:
                continue

    async def _consume_circuit(self) -> None:
        from loophedge.bus import CH_CIRCUIT_BROKEN
        async for payload in self.bus.subscribe(CH_CIRCUIT_BROKEN):
            try:
                await self.handle_circuit(payload)
            except Exception:
                continue

    async def handle_signal(self, payload: dict) -> None:
        from loophedge.schemas import SignalCandidate, SignalVerified
        verified = SignalVerified.model_validate(payload)
        with self.session_factory() as s:
            sig = s.get(Signal, verified.signal_id)
            if sig is None:
                return
            candidate = SignalCandidate(
                signal_id=sig.id, strategy_id=sig.strategy_id,
                symbol=sig.symbol, side=sig.side, size_pct=sig.size_pct,
                reasoning=(sig.maker_payload or {}).get("reasoning", ""),
            )
        await self.executor.handle_verified(verified, candidate)

    async def handle_circuit(self, payload: dict) -> None:
        snapshot = list(self.simulator.positions.items())
        for symbol, pos in snapshot:
            if pos.qty == Decimal("0"):
                continue
            side = "short" if pos.qty > 0 else "long"
            ref = self.executor.latest_prices.get(symbol, pos.avg_entry)
            fill = self.simulator.apply_fill(symbol, side, abs(pos.qty), ref,
                                              datetime.now(UTC))
            from loophedge.models import Fill as FillRow
            from loophedge.models import Position as PositionRow
            with self.session_factory() as s:
                s.add(FillRow(id=fill.id, signal_id=None,
                              ts=fill.ts, symbol=fill.symbol, side=fill.side,
                              qty=fill.qty, price=fill.price, fees=fill.fees,
                              venue=self.executor.venue))
                p = s.get(PositionRow, symbol)
                if p is not None:
                    p.qty = self.simulator.positions[symbol].qty
                    p.avg_entry = self.simulator.positions[symbol].avg_entry
                    p.unrealized_pnl = Decimal("0")
                    p.updated_at = fill.ts
                s.commit()
