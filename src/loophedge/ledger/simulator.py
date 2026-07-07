import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

SLIPPAGE_BPS = Decimal("5")
BPS = Decimal("10000")


@dataclass
class Fill:
    id: str
    ts: datetime
    symbol: str
    side: str
    qty: Decimal
    price: Decimal
    fees: Decimal
    venue: str = "simulator"


@dataclass
class _Position:
    symbol: str
    qty: Decimal = Decimal("0")
    avg_entry: Decimal = Decimal("0")


@dataclass
class Simulator:
    starting_cash: Decimal
    cash: Decimal = field(init=False)
    positions: dict[str, _Position] = field(default_factory=dict)

    def __post_init__(self):
        self.cash = self.starting_cash

    def apply_fill(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        ref_price: Decimal,
        ts: datetime,
        fee_bps: Decimal = Decimal("10"),
    ) -> Fill:
        slip = ref_price * SLIPPAGE_BPS / BPS
        fill_price = ref_price + slip if side == "long" else ref_price - slip
        notional = fill_price * qty
        fees = (notional * fee_bps / BPS).quantize(Decimal("0.00000001"))

        signed_qty = qty if side == "long" else -qty
        pos = self.positions.setdefault(symbol, _Position(symbol))
        # weighted avg entry, treating opposite-side fills as reductions
        new_qty = pos.qty + signed_qty
        if pos.qty == 0 or (pos.qty > 0) == (signed_qty > 0):
            total_cost = pos.avg_entry * abs(pos.qty) + fill_price * abs(signed_qty)
            pos.avg_entry = total_cost / abs(new_qty) if new_qty != 0 else Decimal("0")
        pos.qty = new_qty

        self.cash -= signed_qty * fill_price + fees
        return Fill(id=str(uuid.uuid4()), ts=ts, symbol=symbol, side=side,
                    qty=qty, price=fill_price, fees=fees)

    def equity(self, mark_prices: dict[str, Decimal]) -> Decimal:
        unrealized = sum(
            (mark_prices.get(p.symbol, p.avg_entry) - p.avg_entry) * p.qty
            for p in self.positions.values()
        )
        return self.cash + Decimal(unrealized)
