# Strategy Genesis Playbook

## Goal
Propose a new Python strategy file in `strategies/pending/` whose `generate_signals(bars, hyperparams) -> list[dict]` produces candidate signals on minute bars.

## Required exports
Each strategy file must export:
- `NAME: str` — unique strategy id.
- `DEFAULT_HYPERPARAMS: dict` — initial values.
- `generate_signals(bars, hyperparams) -> list[dict]` — see interface.

## Constraints
- Must respect `risk_rules.md` (size cap, leverage, no shorting on first version).
- Must use only deterministic technical indicators (no external API calls).
