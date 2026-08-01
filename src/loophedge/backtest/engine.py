from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from math import sqrt
from typing import Any

import numpy as np

from loophedge.ledger.simulator import Simulator
from loophedge.models import Bar
from loophedge.risk.caps import HARD_KILL_SWITCH_DD_PCT, HARD_MAX_POSITION_PCT


# Promotion thresholds.
#
# t >= 3.0 rather than the conventional 2.0: the genesis agent is an automated
# multiple-comparisons machine, and Harvey, Liu & Zhu (RFS 2016) show that
# t > 2.0 is not defensible for a newly discovered factor once data mining is
# accounted for. These gates are necessary but not sufficient -- they do not
# deflate for the number of trials actually run, which requires a trials
# registry we do not yet have.
SHARPE_THRESHOLD = Decimal("1.5")
T_STAT_THRESHOLD = Decimal("3.0")
MIN_TRADES = 100

# Backtest drawdown is gated at the live kill-switch bound rather than an
# independent number, so a strategy can never be promoted on a backtest whose
# drawdown would have tripped the circuit breaker in production.
MAX_DD_THRESHOLD = HARD_KILL_SWITCH_DD_PCT

SECONDS_PER_YEAR = 365.25 * 24 * 3600


@dataclass
class BacktestResult:
    sharpe: Decimal
    max_dd_pct: Decimal
    t_stat: Decimal
    trade_count: int
    equity_curve: list[Decimal] = field(default_factory=list)
    passed: bool = False
    notes: str = ""


def _signal_fingerprint(signals: list[dict]) -> set[tuple]:
    """Canonical, order-independent identity for a set of emitted signals."""
    out = set()
    for s in signals:
        out.add((
            s["ts"],
            s.get("symbol"),
            s.get("side"),
            str(s.get("size_pct")),
        ))
    return out


def detect_lookahead(
    bars: list[Bar],
    strategy_callable: Callable[[list[Bar], dict[str, Any]], list[dict]],
    hyperparams: dict[str, Any],
    split: float = 0.7,
) -> str | None:
    """Detect whether a strategy's signals depend on data after the signal time.

    Re-runs the strategy on a truncated prefix of the bars and compares the
    signals it emits inside that prefix against the signals the full run emitted
    for the same window. A causal strategy cannot tell the difference, so any
    discrepancy means it read bars it should not have been able to see.

    Returns a human-readable description of the violation, or None if clean.

    Note that recursively-seeded indicators (EMA and friends) will also surface
    here, because their value at time t genuinely depends on how much history
    was loaded. That is a real reproducibility defect between backtest and live,
    not a false positive, so it is reported rather than tolerated.
    """
    if len(bars) < 10:
        return None

    ordered = sorted(bars, key=lambda b: (b.ts, b.symbol))
    cut = int(len(ordered) * split)
    if cut < 5 or cut >= len(ordered):
        return None

    prefix = ordered[:cut]
    boundary_ts = prefix[-1].ts

    full_signals = strategy_callable(ordered, hyperparams) or []
    prefix_signals = strategy_callable(prefix, hyperparams) or []

    full_in_window = _signal_fingerprint(
        [s for s in full_signals if s["ts"] <= boundary_ts]
    )
    prefix_in_window = _signal_fingerprint(
        [s for s in prefix_signals if s["ts"] <= boundary_ts]
    )

    if full_in_window == prefix_in_window:
        return None

    only_full = full_in_window - prefix_in_window
    only_prefix = prefix_in_window - full_in_window
    return (
        f"look-ahead detected at split={split}: {len(only_full)} signal(s) present "
        f"only with future bars loaded, {len(only_prefix)} present only without. "
        f"Signals at or before {boundary_ts.isoformat()} must not change when "
        f"later bars are withheld."
    )


def run_backtest(
    bars: list[Bar],
    strategy_callable: Callable[[list[Bar], dict[str, Any]], list[dict]],
    hyperparams: dict[str, Any],
    starting_cash: Decimal = Decimal("100000"),
    check_lookahead: bool = True,
    session_factory=None,
) -> BacktestResult:
    if not bars:
        return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), 0, notes="empty bars")

    signals = strategy_callable(bars, hyperparams) or []

    for sig in signals:
        if Decimal(str(sig["size_pct"])) > HARD_MAX_POSITION_PCT:
            return BacktestResult(
                Decimal("0"), Decimal("0"), Decimal("0"), 0,
                notes="strategy emitted size_pct above hard cap"
            )

    if check_lookahead:
        violation = detect_lookahead(bars, strategy_callable, hyperparams)
        if violation:
            return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), 0,
                                  notes=violation)

    # Bars from several symbols interleave on the timeline, so step through
    # distinct timestamps and keep a running mark price per symbol. Marking a
    # portfolio against only the current bar's symbol would value every other
    # open position at cost.
    bars_sorted = sorted(bars, key=lambda b: (b.ts, b.symbol))
    default_symbol = bars_sorted[0].symbol
    marks: dict[str, Decimal] = {}

    sim = Simulator(starting_cash=starting_cash, session_factory=session_factory)
    equity_curve: list[Decimal] = []
    trades = 0
    sig_idx = 0
    signals_sorted = sorted(signals, key=lambda s: s["ts"])

    i = 0
    n = len(bars_sorted)
    first_ts = bars_sorted[0].ts
    last_ts = bars_sorted[-1].ts

    while i < n:
        ts = bars_sorted[i].ts
        while i < n and bars_sorted[i].ts == ts:
            marks[bars_sorted[i].symbol] = bars_sorted[i].close
            i += 1

        while sig_idx < len(signals_sorted) and signals_sorted[sig_idx]["ts"] <= ts:
            sig = signals_sorted[sig_idx]
            sig_idx += 1
            symbol = sig.get("symbol") or default_symbol
            price = marks.get(symbol)
            if price is None or price <= 0:
                # No bar has priced this symbol yet; the signal is unfillable.
                continue
            equity = sim.equity(marks)
            notional = equity * Decimal(str(sig["size_pct"]))
            qty = notional / price
            sim.apply_fill(symbol, sig["side"], qty, price, ts)
            trades += 1

        equity_curve.append(sim.equity(marks))

    if trades == 0:
        return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), 0,
                              equity_curve=equity_curve, notes="no trades executed")

    arr = np.array([float(x) for x in equity_curve])
    if len(arr) < 2 or arr[0] == 0:
        return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), trades,
                              equity_curve=equity_curve, notes="insufficient equity samples")

    log_returns = np.diff(np.log(arr))

    annualization = _annualization_factor(first_ts, last_ts, len(equity_curve))

    mean_r = float(np.mean(log_returns)) if log_returns.size else 0.0
    std_r = float(np.std(log_returns, ddof=1)) if log_returns.size > 1 else 0.0
    sharpe = Decimal(str((mean_r / std_r) * annualization if std_r > 0 else 0.0)).quantize(Decimal("0.0001"))

    running_max = np.maximum.accumulate(arr)
    drawdowns = (running_max - arr) / running_max
    max_dd = Decimal(str(float(drawdowns.max()))).quantize(Decimal("0.0001"))

    t_stat = _newey_west_t(log_returns)

    passed = (
        sharpe >= SHARPE_THRESHOLD
        and max_dd < MAX_DD_THRESHOLD
        and t_stat >= T_STAT_THRESHOLD
        and trades >= MIN_TRADES
    )

    return BacktestResult(
        sharpe=sharpe, max_dd_pct=max_dd, t_stat=t_stat,
        trade_count=trades, equity_curve=equity_curve,
        passed=passed,
        notes="ok" if passed else "below threshold",
    )


def _annualization_factor(first_ts, last_ts, n_obs: int) -> float:
    """Scale per-observation Sharpe to annual, from the observed sampling rate.

    Deriving observations-per-year from the actual calendar span rather than
    assuming a 24-hour day is what makes this correct for both 24/7 crypto and
    session-bound markets: a 5-minute bar series yields ~105k observations per
    year on a continuous venue and ~20k on a US equity session, and the span
    reflects that without needing to know which venue produced the bars.
    """
    if n_obs < 2:
        return 1.0
    span = (last_ts - first_ts).total_seconds()
    if span <= 0:
        return 1.0
    obs_per_year = (n_obs - 1) * SECONDS_PER_YEAR / span
    return sqrt(obs_per_year)


def _newey_west_lag(n: int) -> int:
    """Standard automatic bandwidth, floor(4 * (n/100)^(2/9))."""
    if n < 2:
        return 0
    return max(1, int(4 * (n / 100.0) ** (2.0 / 9.0)))


def _newey_west_t(returns: np.ndarray, lag: int | None = None) -> Decimal:
    if lag is None:
        lag = _newey_west_lag(returns.size)
    if returns.size < lag + 2:
        return Decimal("0")
    n = returns.size
    mean = float(np.mean(returns))
    resid = returns - mean
    gamma = [float(np.sum(resid * resid)) / n]
    for k in range(1, lag + 1):
        gamma.append(float(np.sum(resid[k:] * resid[:-k])) / n)
    weights = [1.0 - k / (lag + 1) for k in range(lag + 1)]
    nw_var = gamma[0] + 2 * sum(w * g for w, g in zip(weights[1:], gamma[1:]))
    if nw_var <= 0:
        return Decimal("0")
    se = sqrt(nw_var / n)
    if se == 0:
        return Decimal("0")
    return Decimal(str(mean / se)).quantize(Decimal("0.0001"))
