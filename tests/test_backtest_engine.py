from datetime import UTC, datetime, timedelta
from decimal import Decimal


from loophedge.backtest.engine import (
    MAX_DD_THRESHOLD,
    MIN_TRADES,
    SHARPE_THRESHOLD,
    T_STAT_THRESHOLD,
    BacktestResult,
    detect_lookahead,
    run_backtest,
)
from loophedge.models import Bar
from loophedge.risk.caps import HARD_KILL_SWITCH_DD_PCT


def _bars(n=200, start_price=60000, drift=10):
    out = []
    ts = datetime(2026, 6, 1, tzinfo=UTC)
    for i in range(n):
        p = Decimal(str(start_price + i * drift))
        out.append(Bar(symbol="BTCUSDT", timeframe="5m",
                       ts=ts + timedelta(minutes=5 * i),
                       open=p, high=p, low=p, close=p, volume=Decimal("1")))
    return out


def _buy_every_10_bars(bars, hyperparams):
    sigs = []
    for i, b in enumerate(bars):
        if i % 10 == 0:
            sigs.append({"symbol": b.symbol, "side": "long",
                          "size_pct": Decimal("0.01"), "ts": b.ts})
    return sigs


def _no_op_strategy(bars, hyperparams):
    return []


def test_backtest_returns_result_dataclass():
    result = run_backtest(_bars(), _buy_every_10_bars, {})
    assert isinstance(result, BacktestResult)
    assert result.trade_count == 20  # 200 bars / 10
    assert isinstance(result.sharpe, Decimal)
    assert isinstance(result.max_dd_pct, Decimal)
    assert isinstance(result.t_stat, Decimal)


def test_backtest_no_trades_returns_zeros():
    result = run_backtest(_bars(), _no_op_strategy, {})
    assert result.trade_count == 0
    assert result.sharpe == Decimal("0")
    assert result.max_dd_pct == Decimal("0")
    assert not result.passed


def test_backtest_uptrending_market_positive_sharpe():
    """Buying into a steadily rising market should produce positive sharpe."""
    result = run_backtest(_bars(n=300, drift=720), _buy_every_10_bars, {})
    assert result.sharpe > Decimal("0")
    assert len(result.equity_curve) == 300


def test_backtest_passed_requires_sharpe_and_drawdown_thresholds():
    """passed=True conjoins all four promotion gates."""
    # Whether sharpe + t-stat clear the bar depends on the price path. Assert
    # the gating logic against the live constants rather than duplicating their
    # values, so tightening a threshold cannot silently desync this test.
    result = run_backtest(_bars(n=300, drift=20), _buy_every_10_bars, {})
    expected_passed = (result.sharpe >= SHARPE_THRESHOLD
                       and result.max_dd_pct < MAX_DD_THRESHOLD
                       and result.t_stat >= T_STAT_THRESHOLD
                       and result.trade_count >= MIN_TRADES)
    assert result.passed == expected_passed


def test_drawdown_gate_never_exceeds_live_kill_switch():
    """A backtest must not promote a strategy the live circuit breaker would kill."""
    assert MAX_DD_THRESHOLD <= HARD_KILL_SWITCH_DD_PCT


def test_backtest_strategy_violating_hard_cap_marked_failed():
    """A strategy that emits size_pct > 5% should be auto-failed without scoring."""
    def oversized(bars, hyperparams):
        return [{"symbol": "BTCUSDT", "side": "long",
                 "size_pct": Decimal("0.10"), "ts": bars[0].ts}]
    result = run_backtest(_bars(), oversized, {})
    assert not result.passed
    assert "hard cap" in result.notes.lower()


def _peeking_strategy(bars, hyperparams):
    """Look-ahead bug: decides at bar i using the close 5 bars in the future."""
    sigs = []
    for i in range(len(bars) - 5):
        if bars[i + 5].close > bars[i].close:
            sigs.append({"symbol": bars[i].symbol, "side": "long",
                          "size_pct": Decimal("0.01"), "ts": bars[i].ts})
    return sigs


def test_detect_lookahead_flags_a_peeking_strategy():
    assert detect_lookahead(_bars(n=200), _peeking_strategy, {}) is not None


def test_detect_lookahead_clears_a_causal_strategy():
    assert detect_lookahead(_bars(n=200), _buy_every_10_bars, {}) is None


def test_backtest_rejects_lookahead_without_scoring_it():
    result = run_backtest(_bars(n=200), _peeking_strategy, {})
    assert not result.passed
    assert result.trade_count == 0
    assert "look-ahead" in result.notes


def test_backtest_can_opt_out_of_the_lookahead_gate():
    result = run_backtest(_bars(n=200), _peeking_strategy, {}, check_lookahead=False)
    assert result.trade_count > 0


def _two_symbol_bars(n=100):
    """AAA climbs steadily, BBB is flat; both print on the same timestamps."""
    out = []
    ts = datetime(2026, 6, 1, tzinfo=UTC)
    for i in range(n):
        at = ts + timedelta(minutes=5 * i)
        rising = Decimal(str(1000 + i * 10))
        out.append(Bar(symbol="AAA", timeframe="5m", ts=at, open=rising,
                       high=rising, low=rising, close=rising, volume=Decimal("1")))
        flat = Decimal("500")
        out.append(Bar(symbol="BBB", timeframe="5m", ts=at, open=flat,
                       high=flat, low=flat, close=flat, volume=Decimal("1")))
    return out


def _buy_aaa_once(bars, hyperparams):
    first = min(b.ts for b in bars)
    return [{"symbol": "AAA", "side": "long",
             "size_pct": Decimal("0.05"), "ts": first}]


def test_multi_symbol_equity_curve_has_one_point_per_timestamp():
    bars = _two_symbol_bars(n=100)
    result = run_backtest(bars, _buy_aaa_once, {})
    assert len(bars) == 200
    assert len(result.equity_curve) == 100


def test_multi_symbol_position_is_marked_to_market_not_to_cost():
    """A held position must revalue even while another symbol's bars stream in."""
    result = run_backtest(_two_symbol_bars(n=100), _buy_aaa_once, {})
    # AAA rises 1000 -> 1990 while BBB stays flat. Marking the AAA position at
    # cost whenever a BBB bar arrived would leave the curve pinned near start.
    assert result.equity_curve[-1] > result.equity_curve[0]
    assert result.sharpe > Decimal("0")


def test_signal_routes_to_its_own_symbol_not_the_current_bar():
    """A signal naming AAA must fill at AAA's price even on a BBB bar."""
    result = run_backtest(_two_symbol_bars(n=100), _buy_aaa_once, {},
                          starting_cash=Decimal("100000"))
    # 5% of 100k at AAA's opening price of 1000 is 5 units; AAA gains 990/unit.
    gain = float(result.equity_curve[-1]) - 100000.0
    assert 4500 < gain < 5000  # ~4950 gross, less slippage and fees


def test_annualization_tracks_observed_sampling_rate():
    from loophedge.backtest.engine import _annualization_factor
    start = datetime(2026, 1, 1, tzinfo=UTC)
    # 5-minute bars across one day: 288 observations/day on a 24/7 venue.
    f_5m = _annualization_factor(start, start + timedelta(days=1), 289)
    assert abs(f_5m - (365.25 * 288) ** 0.5) < 1.0
    # Daily bars across a year annualize by sqrt(365.25), not sqrt(252).
    f_1d = _annualization_factor(start, start + timedelta(days=365), 366)
    assert abs(f_1d - 365.25 ** 0.5) < 0.5


def test_session_bound_bars_do_not_inherit_a_24_hour_day():
    """78 five-minute bars per RTH session must not annualize as if 288/day."""
    from loophedge.backtest.engine import _annualization_factor
    start = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    # A year of US equity sessions: 78 five-minute bars on each of 252 days,
    # spanning 252 calendar days. Only ~78 observations accrue per calendar day
    # of elapsed span... but the span also covers weekends, so the observed rate
    # is 78 * 252 bars per 252 days = 78/day of span.
    factor = _annualization_factor(start, start + timedelta(days=252), 78 * 252)
    expected = (78 * 365.25) ** 0.5  # ~168.8
    assert abs(factor - expected) < 1.0
    # And it is far below the 24/7 figure for the same bar width (~324).
    assert factor < (365.25 * 288) ** 0.5
