from dataclasses import dataclass
from decimal import Decimal

HARD_MAX_POSITION_PCT = Decimal("0.05")
HARD_MAX_STRATEGY_ALLOC_PCT = Decimal("0.25")
HARD_KILL_SWITCH_DD_PCT = Decimal("0.15")


@dataclass(frozen=True)
class ProposedTrade:
    strategy_id: str
    symbol: str
    side: str
    size_pct: Decimal


@dataclass(frozen=True)
class CapVerdict:
    allowed: bool
    reason: str | None = None


def enforce_pretrade(
    equity: Decimal,
    current_positions: dict[str, Decimal],
    strategy_allocations: dict[str, Decimal],
    proposed: ProposedTrade,
) -> CapVerdict:
    if proposed.size_pct <= 0:
        return CapVerdict(False, "non-positive size_pct rejected")
    if proposed.size_pct > HARD_MAX_POSITION_PCT:
        return CapVerdict(False, f"position size {proposed.size_pct} exceeds hard cap {HARD_MAX_POSITION_PCT}")
    current = strategy_allocations.get(proposed.strategy_id, Decimal("0"))
    if current + proposed.size_pct > HARD_MAX_STRATEGY_ALLOC_PCT:
        return CapVerdict(False, f"strategy {proposed.strategy_id} would exceed alloc cap {HARD_MAX_STRATEGY_ALLOC_PCT}")
    return CapVerdict(True)
