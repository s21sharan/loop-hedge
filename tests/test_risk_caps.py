from decimal import Decimal


from loophedge.risk.caps import (
    HARD_KILL_SWITCH_DD_PCT,
    HARD_MAX_POSITION_PCT,
    HARD_MAX_STRATEGY_ALLOC_PCT,
    ProposedTrade,
    enforce_pretrade,
)


def _pt(size_pct: str, strat: str = "s1", symbol: str = "BTCUSDT", side: str = "long"):
    return ProposedTrade(strategy_id=strat, symbol=symbol, side=side, size_pct=Decimal(size_pct))


def test_constants_match_spec():
    assert HARD_MAX_POSITION_PCT == Decimal("0.05")
    assert HARD_MAX_STRATEGY_ALLOC_PCT == Decimal("0.25")
    assert HARD_KILL_SWITCH_DD_PCT == Decimal("0.15")


def test_allows_within_caps():
    v = enforce_pretrade(equity=Decimal("100000"),
                         current_positions={},
                         strategy_allocations={},
                         proposed=_pt("0.02"))
    assert v.allowed


def test_rejects_oversized_position():
    v = enforce_pretrade(equity=Decimal("100000"),
                         current_positions={},
                         strategy_allocations={},
                         proposed=_pt("0.06"))
    assert not v.allowed
    assert "position size" in v.reason.lower()


def test_rejects_strategy_alloc_breach():
    v = enforce_pretrade(equity=Decimal("100000"),
                         current_positions={},
                         strategy_allocations={"s1": Decimal("0.24")},
                         proposed=_pt("0.02"))
    assert not v.allowed
    assert "strategy" in v.reason.lower()


def test_negative_size_rejected():
    v = enforce_pretrade(equity=Decimal("100000"),
                         current_positions={},
                         strategy_allocations={},
                         proposed=_pt("-0.01"))
    assert not v.allowed
