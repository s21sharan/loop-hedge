# Strategy Genesis Playbook

## Goal
Propose a new Python strategy file in `strategies/pending/` whose `generate_signals(bars, hyperparams) -> list[dict]` produces candidate signals on minute bars.

## Required exports
Each strategy file must export:
- `NAME: str` — unique strategy id.
- `DEFAULT_HYPERPARAMS: dict` — initial values.
- `generate_signals(bars, hyperparams) -> list[dict]` — see interface.

## Bar interface (CRITICAL)

`bars` is a list of SQLAlchemy `Bar` ORM objects with **attribute access, not dict access**.
`query_bars` returns dicts for readability, but the runtime (backtest + live) passes real objects.
Always use `bar.close`, never `bar.get("close")`.

Attributes on each bar:
- `bar.symbol: str` — e.g. `"BTCUSDT"`
- `bar.timeframe: str` — e.g. `"5m"`
- `bar.ts: datetime` — UTC-aware timestamp of the bar's open
- `bar.open: Decimal`
- `bar.high: Decimal`
- `bar.low: Decimal`
- `bar.close: Decimal`
- `bar.volume: Decimal`

OHLCV fields are `decimal.Decimal`. Convert with `float(bar.close)` before doing arithmetic
with the `statistics` or `numpy` modules.

## Signal shape
Each signal returned must be a dict:
```python
{"symbol": str, "side": "buy" | "sell", "size_pct": float, "ts": datetime}
```
`ts` should be the bar's `ts` at which the signal fires (use `bar.ts`, not a string).

## Constraints
- Must respect `risk_rules.md` (size cap, leverage, no shorting on first version).
- Must use only deterministic technical indicators (no external API calls).
- `size_pct` must be in `[0.005, 0.05]` (0.5% – 5% of equity).
- Never look ahead: at bar index `i`, only use `bars[:i]` (or `bars[:i+1]` if you commit to
  the current close), never anything past `i`.

## Minimal skeleton
```python
from statistics import mean, pstdev

NAME = "example_meanrev_v1"
DEFAULT_HYPERPARAMS = {"symbol": "BTCUSDT", "lookback": 30, "size_pct": 0.02}

def generate_signals(bars, hyperparams):
    hp = {**DEFAULT_HYPERPARAMS, **(hyperparams or {})}
    lb = int(hp["lookback"])
    if len(bars) < lb + 1:
        return []
    signals = []
    for i in range(lb, len(bars)):
        window = [float(b.close) for b in bars[i - lb:i]]
        m = mean(window)
        sd = pstdev(window)
        if sd == 0:
            continue
        z = (float(bars[i].close) - m) / sd
        if z <= -1.8:
            signals.append({
                "symbol": bars[i].symbol,
                "side": "buy",
                "size_pct": hp["size_pct"],
                "ts": bars[i].ts,
            })
    return signals
```
