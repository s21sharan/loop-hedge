# Kalshi weather integration — Cycle 1 design

**Date:** 2026-07-31
**Status:** Design approved via brainstorming session. Ready for implementation planning.
**Predecessor:** `docs/research/2026-07-28-followup-brokers-and-strategies.md`

---

## Context

The existing loop-hedge system trades crypto bar data on binance.us via a genesis
→ checker → maker → executor loop. Prior research (`2026-07-28-asset-class-expansion.md`)
identified Kalshi event contracts as the highest-leverage venue expansion because
they break the `N_eff ≈ 1/ρ` breadth ceiling that caps crypto strategies.

Cycle 1 wires Kalshi weather contracts (NYC and LAX daily-high, ~20 active
contracts at a time) into the existing architecture, alongside Open-Meteo
weather forecasts as the model input for weather strategies. **Live order
execution against Kalshi is deferred to cycle 3.** Cycle 1 stops when a
genesis-proposed weather strategy can be validated by the checker and would
emit signals to the DB.

Polymarket-US is deferred entirely to a second brainstorming cycle after
cycle 3 lands. Kalshi first is the sequential path; both venues at once was
explicitly rejected during scope decomposition.

## Non-goals for cycle 1

- Live Kalshi order routing (executor changes — cycle 3)
- Polymarket-US connector (separate cycle after Kalshi is proven)
- Options plumbing, computer-use, or any Tier-3 asset class from the earlier research
- Backfill of historical Kalshi candles beyond what live polling accumulates
- Multi-city expansion beyond NYC + LAX (deferred; trivial to widen once pipeline works)
- Full GFS ensemble via NOMADS/GRIB2 (Open-Meteo is the aggregator we use)

---

## Architecture

Two new sibling ingester services, two new tables, one Simulator refactor.
No new agent services. Existing 9-container docker compose graph becomes 11.

### New services

- **`kalshi-ingestor`** — polls Kalshi public API. Two schedules:
  - Every hour: sync `contract` table (list new NYC + LAX daily-high markets;
    update `settlement_value` when Kalshi posts it after resolution).
  - Every 5 minutes: for each active contract, poll `/markets/{ticker}/candles?resolution=5`
    and insert `Bar` rows.
  - On seeing `settlement_value` transition from null → 0 or 1, write one final
    `Bar` with `close = settlement_value` at `resolution_ts`.
- **`weather-ingestor`** — polls Open-Meteo every 6 hours for NYC and LAX.
  Writes `WeatherForecast` rows with `temp_mean_c` and `temp_std_c` derived
  from the ensemble spread.

Both are `python -m loophedge kalshi` and `python -m loophedge weather` in the CLI
dispatcher, matching the existing `ingest`, `execute`, `risk`, etc. pattern.

### Schema changes (alembic migration 004)

```
CREATE TABLE contract (
    symbol            VARCHAR(96) PRIMARY KEY,
    venue             VARCHAR(32) NOT NULL,
    open_ts           TIMESTAMPTZ NULL,
    close_ts          TIMESTAMPTZ NULL,
    resolution_ts     TIMESTAMPTZ NULL,
    settlement_value  NUMERIC(20,8) NULL,
    resolution_source VARCHAR(128) NULL,
    metadata          JSON NOT NULL DEFAULT '{}'::json
);

CREATE TABLE weather_forecast (
    id              SERIAL PRIMARY KEY,
    city            VARCHAR(8) NOT NULL,
    forecast_ts     TIMESTAMPTZ NOT NULL,
    valid_ts        TIMESTAMPTZ NOT NULL,
    temp_mean_c     NUMERIC(6,2) NOT NULL,
    temp_std_c      NUMERIC(6,2) NOT NULL,
    source          VARCHAR(32) NOT NULL,
    UNIQUE (city, forecast_ts, valid_ts, source)
);

-- backfill existing crypto symbols
INSERT INTO contract (symbol, venue) VALUES
    ('BTCUSDT', 'binance_us'),
    ('ETHUSDT', 'binance_us');
```

`bar.symbol` is already 96 chars wide (migration 003). No FK from `bar.symbol`
to `contract.symbol` because bars can arrive slightly before the hourly contract
sync — the ingester handles the race by skip-and-warn.

SQLAlchemy models use the generic `JSON` column type (maps to Postgres `jsonb`
via dialect, works on SQLite for tests). The migration SQL uses `JSON` rather
than `JSONB` for the same portability reason.

### Simulator cost function

Replace the hardcoded `SLIPPAGE_BPS = 5` and `fee_bps = 10` in
`src/loophedge/ledger/simulator.py:apply_fill` with a per-venue cost model.
Kalshi's fee is per-contract absolute, not per-notional bps, so the interface
returns a computed fee amount given `(qty, price)` rather than a rate:

```python
@dataclass
class CostModel:
    apply_slippage: Callable[[Decimal, str], Decimal]   # ref_price, side → fill_price
    fee: Callable[[Decimal, Decimal], Decimal]           # qty, fill_price → fee_absolute

def _crypto_slippage(ref, side):  return ref * (Decimal('1.0005') if side == 'long' else Decimal('0.9995'))
def _crypto_fee(qty, price):      return qty * price * Decimal('0.001')  # 10 bps of notional
def _kalshi_slippage(ref, side):  return ref                              # binary, no slippage
def _kalshi_fee(qty, price):      return qty * Decimal('0.07') * price * (Decimal('1') - price)

COST_MODELS: dict[str, CostModel] = {
    'binance_us': CostModel(_crypto_slippage, _crypto_fee),
    'kalshi':     CostModel(_kalshi_slippage, _kalshi_fee),
}
```

`apply_fill` looks up the venue via a per-session `symbol → venue` cache (one
query per symbol on first touch). Unknown venue falls back to `binance_us`
costs with a warning log.

Kalshi rounds fees up to the nearest cent per contract; the spec above uses
the mathematical formula and defers rounding to implementation (may need
`decimal.ROUND_CEILING` to match Kalshi exactly).

### Agent-loop integration

Genesis, checker, and maker gain two new tools via `agents/tools.py`:

- `query_kalshi_bars(symbol_pattern: str, timeframe: str, limit: int = 200)`
  — returns bars matching a symbol prefix (e.g. `KXHIGHNY-26AUG*`)
- `query_weather_forecast(city: str, valid_ts: str | None = None, limit: int = 20)`
  — returns recent forecasts for a city, optionally filtered to a valid date

Genesis playbook (`skills/strategy_genesis.md`) gets a new section:
*"Writing weather strategies"* — describes the `(bar, forecast)` join, Kalshi
price range (0.00–1.00), settlement mechanics, and the `forecasts` kwarg.

### Strategy signature evolution

Widen `generate_signals` from `(bars, hyperparams)` to
`(bars, hyperparams, *, forecasts=None)`. Backwards-compatible: existing crypto
strategies ignore the new kwarg. New Kalshi strategies use it. Backtest engine
and maker pass `forecasts` when `Contract.venue == 'kalshi'`, else omit.

## Data flow: one Kalshi contract lifecycle

1. Kalshi lists `KXHIGHNY-26AUG05-B82.5` at market open.
2. `kalshi-ingestor` hourly sync inserts a `contract` row with
   `venue='kalshi'`, `open_ts`, `resolution_ts`,
   `resolution_source='NWS-KNYC-max-2026-08-05'`,
   `metadata={city:'NYC', bucket_low:80, bucket_high:85}`.
3. Every 5 min, ingester polls Kalshi candles and inserts `Bar` rows with
   prices normalized to `0.00–1.00`.
4. `weather-ingestor` polls Open-Meteo every 6h for NYC. Writes forecast rows.
5. Every 4h, genesis calls `query_weather_forecast('NYC', valid_ts='2026-08-05')`
   and `query_kalshi_bars('KXHIGHNY-26AUG05*')`, proposes `nyc_high_zscore_v1`
   via `propose_strategy`.
6. Every 30 min, checker sweeps pending strategies. Loads bars + forecasts,
   runs `run_backtest` — cost function looks up `Contract.venue`, uses
   `_kalshi_fee`. If it clears thresholds (Sharpe 1.5, t 3.0, 100 trades,
   drawdown < kill-switch bound), promotes to active.
7. Every 15 min, maker tick loads active strategies. For a Kalshi strategy,
   fetches recent bars and relevant forecasts, calls
   `generate_signals(bars, hp, forecasts=fx)`, writes signal rows to DB.
   **Signals land in DB only — no live execution in cycle 1.**
8. When `resolution_ts` passes, kalshi-ingestor polls settlement and writes
   a final `Bar` with `close=0` or `close=1`. Any open simulator position
   on that symbol hits that bar and closes at settlement value.

## Error handling

One rule: **an ingester or agent tick must never crash the container.**

- **HTTP failures** (5xx, timeout, DNS): retry with exponential backoff up to
  3 attempts, then log and skip this cycle. Loop continues on next scheduled tick.
  Existing `restart: on-failure:10` compose policy still catches process death.
- **Duplicate ingestion**: every INSERT uses `ON CONFLICT DO NOTHING` against
  the unique keys defined in the schema section.
- **Bar for unknown symbol** (contract hourly sync hasn't caught up): skip bar,
  warn once, retry on next tick. Contract sync catches it within the hour;
  the next 5-min bar cycle succeeds.
- **Cost function lookup fallback**: unknown venue defaults to `binance_us`
  costs and logs a warning. Post-migration, all live symbols have a contract row.
- **Missing weather forecast** (strategy runs before weather-ingestor caught
  up): pass `forecasts=None` or empty list. Strategy sees no forecast data,
  emits no signals. Not an error.
- **Kalshi settlement timing**: if `settlement_value` isn't posted by
  `resolution_ts`, hourly sync will pick it up when Kalshi posts it. Positions
  on that symbol remain open at whatever the last live bar's close was until
  settlement is written.
- **Strategy raises during `generate_signals`**: already wrapped per-strategy
  in `maker.tick()` and in `run_backtest`. Inherited by Kalshi strategies.

## Testing

Unit tests, new files:

- `tests/test_kalshi_ingestor.py` — mock httpx responses; assert `Bar` and
  `Contract` writes; assert idempotency on re-run; assert `settlement_value`
  update path and settlement-bar generation
- `tests/test_weather_ingestor.py` — mock Open-Meteo response; assert
  `WeatherForecast` writes with correct dedup on `(city, forecast_ts, valid_ts, source)`
- `tests/test_simulator_cost_polymorphism.py` — `apply_fill` uses `_kalshi_fee`
  for `venue='kalshi'`; uses flat-bps for `venue='binance_us'`; unknown venue
  falls back with warning
- `tests/test_contract_settlement.py` — insert `contract` with future
  `resolution_ts`, bars leading up to it, settlement bar with `close=1`;
  assert simulator closes position at 1.0 with correct realized PnL
- `tests/test_kalshi_backtest_end_to_end.py` — hand-written weather strategy
  over synthetic bars + forecasts; assert PnL, Sharpe, trade count, and cost
  match hand-computed expected values
- `tests/test_e2e_kalshi_agent_loop.py` — analogue of `test_e2e_agent_loop.py`:
  seed contract + bars + forecasts, stub Anthropic client, run
  genesis → checker path, assert a pending strategy is created and validated
- `tests/test_migrations.py` — `alembic upgrade head` from empty, then
  `downgrade -1` and `upgrade +1`; no data loss

Cassette tests (placeholders that require `ANTHROPIC_LIVE_RECORD=1`):

- `tests/test_kalshi_genesis.py`
- `tests/test_kalshi_checker.py`

Existing 92-test suite stays green.

## Rollout

- Migration 004 applies cleanly on top of the current droplet DB
  (idempotent since `IF NOT EXISTS`-style, and no data mutation on existing tables)
- Both new services deploy via `docker compose up -d --force-recreate`
- No `.env` changes required — Kalshi public API and Open-Meteo need no keys
- After deploy, `docker compose logs -f kalshi-ingestor weather-ingestor` should
  show data flowing within the first tick of each

## Open questions deferred to implementation

- Exact Kalshi ticker format for NYC/LAX daily-high (need to check current API response)
- Open-Meteo endpoint parameters for ensemble spread (documented at
  https://open-meteo.com/en/docs but exact field names TBD at implementation)
- Whether Kalshi's `/markets/{ticker}/candles` returns exactly the fields we
  expect (open/high/low/close/volume) or needs adaptation

These are all "look up the API spec while writing the ingester" items, not
design questions.
