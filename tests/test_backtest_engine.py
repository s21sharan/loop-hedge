from datetime import UTC, datetime, timedelta
from decimal import Decimal


from loophedge.backtest.engine import BacktestResult, run_backtest
from loophedge.models import Bar


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
    """passed=True requires sharpe>=1.0, max_dd<12%, t_stat>=1.5, trades>=30."""
    # _buy_every_10_bars on 300 bars yields 30 trades; whether sharpe + t-stat
    # clear the bar depends on the price path. Just assert the gating logic.
    result = run_backtest(_bars(n=300, drift=20), _buy_every_10_bars, {})
    expected_passed = (result.sharpe >= Decimal("1.0")
                       and result.max_dd_pct < Decimal("0.12")
                       and result.t_stat >= Decimal("1.5")
                       and result.trade_count >= 30)
    assert result.passed == expected_passed


def test_backtest_strategy_violating_hard_cap_marked_failed():
    """A strategy that emits size_pct > 5% should be auto-failed without scoring."""
    def oversized(bars, hyperparams):
        return [{"symbol": "BTCUSDT", "side": "long",
                 "size_pct": Decimal("0.10"), "ts": bars[0].ts}]
    result = run_backtest(_bars(), oversized, {})
    assert not result.passed
    assert "hard cap" in result.notes.lower()
