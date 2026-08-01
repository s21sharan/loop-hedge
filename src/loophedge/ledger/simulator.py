import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable

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
class CostModel:
    """Per-venue cost calculation.

    `apply_slippage` returns the fill price after slippage adjustment given
    the reference price and side. `fee` returns the absolute fee amount for
    a fill of `qty` at `fill_price`. Both use Decimal end-to-end.
    """
    apply_slippage: Callable[[Decimal, str], Decimal]
    fee: Callable[[Decimal, Decimal], Decimal]


def _crypto_slippage(ref_price: Decimal, side: str) -> Decimal:
    slip = ref_price * SLIPPAGE_BPS / BPS
    return ref_price + slip if side == "long" else ref_price - slip


def _crypto_fee(qty: Decimal, fill_price: Decimal) -> Decimal:
    # 10 bps of notional
    return (fill_price * qty * Decimal("10") / BPS).quantize(Decimal("0.00000001"))


def _kalshi_slippage(ref_price: Decimal, side: str) -> Decimal:
    # Binary contracts trade at a single tick; no linear slippage model.
    return ref_price


def _kalshi_fee(qty: Decimal, fill_price: Decimal) -> Decimal:
    """Kalshi fee: 0.07 * price * (1 - price) per contract.

    Real Kalshi rounds each contract's fee up to the nearest cent, then sums.
    For simulation we use the mathematical value (matches within a fraction of
    a cent per contract), quantized to 8 decimal places for Decimal hygiene.
    """
    per_contract = Decimal("0.07") * fill_price * (Decimal("1") - fill_price)
    return (per_contract * qty).quantize(Decimal("0.00000001"))


COST_MODELS: dict[str, CostModel] = {
    "binance_us": CostModel(apply_slippage=_crypto_slippage, fee=_crypto_fee),
    "kalshi":     CostModel(apply_slippage=_kalshi_slippage, fee=_kalshi_fee),
}

_DEFAULT_VENUE = "binance_us"


@dataclass
class Simulator:
    starting_cash: Decimal
    session_factory: object = None  # optional sessionmaker for contract lookup
    cash: Decimal = field(init=False)
    positions: dict[str, _Position] = field(default_factory=dict)
    _venue_cache: dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self):
        self.cash = self.starting_cash

    def _lookup_venue(self, symbol: str) -> str:
        if symbol in self._venue_cache:
            return self._venue_cache[symbol]
        if self.session_factory is None:
            self._venue_cache[symbol] = _DEFAULT_VENUE
            return _DEFAULT_VENUE
        from loophedge.models import Contract  # local import avoids cycle
        with self.session_factory() as s:
            row = s.get(Contract, symbol)
        venue = row.venue if row is not None else _DEFAULT_VENUE
        self._venue_cache[symbol] = venue
        return venue

    def apply_fill(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        ref_price: Decimal,
        ts: datetime,
    ) -> Fill:
        venue = self._lookup_venue(symbol)
        model = COST_MODELS.get(venue, COST_MODELS[_DEFAULT_VENUE])

        fill_price = model.apply_slippage(ref_price, side)
        fees = model.fee(qty, fill_price)

        signed_qty = qty if side == "long" else -qty
        pos = self.positions.setdefault(symbol, _Position(symbol))
        new_qty = pos.qty + signed_qty

        if pos.qty == 0:
            pos.avg_entry = fill_price
        elif (pos.qty > 0) == (signed_qty > 0):
            total_cost = pos.avg_entry * abs(pos.qty) + fill_price * abs(signed_qty)
            pos.avg_entry = total_cost / abs(new_qty)
        elif new_qty != 0 and (new_qty > 0) != (pos.qty > 0):
            pos.avg_entry = fill_price
        elif new_qty == 0:
            pos.avg_entry = Decimal("0")

        pos.qty = new_qty
        self.cash -= signed_qty * fill_price + fees
        return Fill(id=str(uuid.uuid4()), ts=ts, symbol=symbol, side=side,
                    qty=qty, price=fill_price, fees=fees)

    def equity(self, mark_prices: dict[str, Decimal]) -> Decimal:
        """Cash plus the market value of open positions.

        apply_fill already debits the full notional from cash, so a position is
        worth qty * mark -- not its unrealized PnL. Adding only the PnL would
        drop reported equity by the whole cost basis the moment a position opens
        and restore it on close, which reads as an enormous spurious drawdown.

        A symbol with no mark is held at cost, which values it at break-even
        rather than at zero.
        """
        position_value = sum(
            mark_prices.get(p.symbol, p.avg_entry) * p.qty
            for p in self.positions.values()
        )
        return self.cash + Decimal(position_value)
