from datetime import UTC, datetime
from decimal import Decimal

from loophedge.ledger.simulator import Fill, Simulator


def test_buy_reduces_cash_creates_position():
    sim = Simulator(starting_cash=Decimal("100000"))
    fill = sim.apply_fill(symbol="BTCUSDT", side="long", qty=Decimal("0.1"),
                           ref_price=Decimal("60000"), ts=datetime.now(UTC))
    assert isinstance(fill, Fill)
    assert fill.price > Decimal("60000")  # buy slips up
    assert sim.cash < Decimal("100000")
    assert sim.positions["BTCUSDT"].qty == Decimal("0.1")


def test_sell_increases_cash_closes_position():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "long", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    sim.apply_fill("BTCUSDT", "short", Decimal("0.1"), Decimal("65000"), datetime.now(UTC))
    assert sim.positions["BTCUSDT"].qty == Decimal("0")
    assert sim.cash > Decimal("100000")


def test_fees_deducted_from_cash():
    sim = Simulator(starting_cash=Decimal("100000"))
    fill = sim.apply_fill("BTCUSDT", "long", Decimal("1"), Decimal("60000"),
                          datetime.now(UTC), fee_bps=Decimal("10"))
    # fees on slipped fill price: 60000 * 1.0005 * 0.001 = 60.03
    assert fill.fees == Decimal("60.03000000")


def test_equity_marks_to_market():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "long", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    e = sim.equity({"BTCUSDT": Decimal("65000")})
    assert e > sim.cash


def test_short_sells_uncovered():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "short", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    assert sim.positions["BTCUSDT"].qty == Decimal("-0.1")


def test_flip_resets_avg_entry():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "long", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    sim.apply_fill("BTCUSDT", "short", Decimal("0.3"), Decimal("70000"), datetime.now(UTC))
    pos = sim.positions["BTCUSDT"]
    assert pos.qty == Decimal("-0.2")
    # avg_entry should be the SHORT fill price (with -5bps slippage on short)
    expected_short_price = Decimal("70000") - Decimal("70000") * Decimal("5") / Decimal("10000")
    assert pos.avg_entry == expected_short_price


def test_equity_is_cash_plus_market_value_not_cash_plus_pnl():
    """Opening a position must not move equity by its cost basis."""
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "long", Decimal("1"), Decimal("60000"), datetime.now(UTC))
    entry = sim.positions["BTCUSDT"].avg_entry
    # Marked at the fill price, equity is starting cash less fees only.
    flat = sim.equity({"BTCUSDT": entry})
    assert Decimal("99930") < flat <= Decimal("100000")


def test_equity_tracks_a_price_move_one_for_one():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "long", Decimal("1"), Decimal("60000"), datetime.now(UTC))
    entry = sim.positions["BTCUSDT"].avg_entry
    before = sim.equity({"BTCUSDT": entry})
    after = sim.equity({"BTCUSDT": entry + Decimal("1000")})
    assert after - before == Decimal("1000")


def test_short_equity_falls_as_price_rises():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "short", Decimal("1"), Decimal("60000"), datetime.now(UTC))
    entry = sim.positions["BTCUSDT"].avg_entry
    before = sim.equity({"BTCUSDT": entry})
    after = sim.equity({"BTCUSDT": entry + Decimal("1000")})
    assert after - before == Decimal("-1000")


def test_round_trip_leaves_equity_equal_to_cash():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "long", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    sim.apply_fill("BTCUSDT", "short", Decimal("0.1"), Decimal("65000"), datetime.now(UTC))
    assert sim.equity({"BTCUSDT": Decimal("65000")}) == sim.cash
