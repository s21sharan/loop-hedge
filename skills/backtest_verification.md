# Backtest Verification Playbook (Checker Agent)

## Goal
Independently validate every signal candidate via walk-forward backtest before approving.

## Approval criteria
- Sharpe ratio ≥ 1.0 over the test window.
- Max drawdown < 12%.
- Newey-West t-statistic ≥ 1.5.
- ≥ 30 trades in the backtest period.

## Output format
Return `{ "verdict": "approve" | "reject" | "needs_revision", "reason": "..." }`.
