from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from math import sqrt
from typing import Any

import numpy as np

from loophedge.ledger.simulator import Simulator
from loophedge.models import Bar
from loophedge.risk.caps import HARD_MAX_POSITION_PCT


SHARPE_THRESHOLD = Decimal("1.0")
MAX_DD_THRESHOLD = Decimal("0.12")
T_STAT_THRESHOLD = Decimal("1.5")
MIN_TRADES = 30


@dataclass
class BacktestResult:
    sharpe: Decimal
    max_dd_pct: Decimal
    t_stat: Decimal
    trade_count: int
    equity_curve: list[Decimal] = field(default_factory=list)
    passed: bool = False
    notes: str = ""


def run_backtest(
    bars: list[Bar],
    strategy_callable: Callable[[list[Bar], dict[str, Any]], list[dict]],
    hyperparams: dict[str, Any],
    starting_cash: Decimal = Decimal("100000"),
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

    sim = Simulator(starting_cash=starting_cash)
    equity_curve: list[Decimal] = []
    trades = 0
    sig_idx = 0
    signals_sorted = sorted(signals, key=lambda s: s["ts"])

    for bar in bars:
        while sig_idx < len(signals_sorted) and signals_sorted[sig_idx]["ts"] <= bar.ts:
            sig = signals_sorted[sig_idx]
            equity = sim.equity({s: bar.close for s in [bar.symbol]})
            notional = equity * Decimal(str(sig["size_pct"]))
            qty = notional / bar.close
            sim.apply_fill(bar.symbol, sig["side"], qty, bar.close, bar.ts)
            trades += 1
            sig_idx += 1
        equity_curve.append(sim.equity({bar.symbol: bar.close}))

    if trades == 0:
        return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), 0,
                              equity_curve=equity_curve, notes="no trades executed")

    arr = np.array([float(x) for x in equity_curve])
    if len(arr) < 2 or arr[0] == 0:
        return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), trades,
                              equity_curve=equity_curve, notes="insufficient equity samples")

    log_returns = np.diff(np.log(arr))

    bar_minutes = max(1, int((bars[1].ts - bars[0].ts).total_seconds() // 60)) if len(bars) > 1 else 5
    bars_per_day = 1440 // bar_minutes
    annualization = sqrt(252 * bars_per_day)

    mean_r = float(np.mean(log_returns)) if log_returns.size else 0.0
    std_r = float(np.std(log_returns, ddof=1)) if log_returns.size > 1 else 0.0
    sharpe = Decimal(str((mean_r / std_r) * annualization if std_r > 0 else 0.0)).quantize(Decimal("0.0001"))

    running_max = np.maximum.accumulate(arr)
    drawdowns = (running_max - arr) / running_max
    max_dd = Decimal(str(float(drawdowns.max()))).quantize(Decimal("0.0001"))

    t_stat = _newey_west_t(log_returns, lag=5)

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


def _newey_west_t(returns: np.ndarray, lag: int = 5) -> Decimal:
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
