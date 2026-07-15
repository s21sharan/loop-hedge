# Risk Rules

## Hard caps (enforced in code, not here)
- Per-trade size: ≤ 5% of equity.
- Per-strategy allocation: ≤ 25% of equity.
- Portfolio drawdown kill switch: 15% from 30-day rolling high.

## Soft heuristics
- Avoid same-side stacking on a single symbol within 1 hour.
- Reduce sizing by 50% during the first 24 hours of a new strategy's live life.
