# Alpha Research Playbook (Maker Agent)

## Goal
Generate candidate trade signals from active strategies in `strategies/active/`, filtered against current lessons learned.

## Rules
- Position size must be between 0.5% and 5% of equity.
- Skip any signal whose strategy violates `risk_rules.md`.
- Read `LESSONS.md` before emitting; if any lesson is relevant to the current condition, apply it.

## Lessons learned
(Auto-appended by the checker on every rejection.)
