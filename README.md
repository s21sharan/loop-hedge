# loop-hedge

A loop-engineered mock hedge fund. See `docs/superpowers/specs/2026-06-29-loop-hedge-design.md`.

## Quick start (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Phases

- Phase 0–1: deterministic core (this branch). No LLM.
- Phase 2+: agent layer.
