# Loop-Engineered Mock Hedge Fund — Design Spec

**Date:** 2026-06-29
**Status:** Approved (pending user sign-off on this written spec)
**Author:** brainstormed with Claude Code

## 1. Goal

Build a self-improving autonomous quant trading system that trades crypto with mock currency, applies Roan Cherny–style "loop engineering" (the agent is not in the chat — the agent is in a loop that runs without us), and gets better over time via accumulated lessons, hyperparameter tuning, and (eventually) strategy genesis.

The system must:

1. Trade crypto continuously without a human prompting it.
2. Verify every signal through a separate "checker" agent before execution (maker/checker pattern).
3. Persist memory across restarts (state + lessons survive container reboots).
4. Self-improve along three layered axes: lessons learned, hyperparameter tuning, new strategy genesis.
5. Run safely under hard outer-bound risk constraints that no agent can rewrite.
6. Be testable end-to-end via historical bar replay before any change ships.

## 2. Scope decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Asset class | Crypto (Binance) | 24/7, free public data, deep liquidity |
| Self-improvement layers | Lessons + hyperparameter tuning + strategy genesis, staged | User wants all three; staged to de-risk genesis |
| Trading cadence | 1m–5m intraday bars, two-tier loop | LLM cannot reason every bar; deterministic fast loop + slower agent loop |
| Runtime | Docker on a VPS | 24/7, isolated containers act as "worktrees" |
| Mock execution | Self-sim ledger for backtests; Binance Spot Testnet for live paper | Backtests need determinism; live paper needs realism |
| Agent framework | Claude Agent SDK (Python) | Native Python integration, long-lived agents with tools |
| Risk model | Hard outer bounds in code; agent tunes within them | Safety is non-negotiable, tuning improves alpha |

## 3. Architecture

### 3.1 Container layout

Eight services wired via Redis pub/sub and a shared Postgres for structured state.

```
                    ┌──────────────────────────────────────────┐
                    │              Redis (pub/sub)             │
                    └──┬──────────┬──────────┬──────────┬──────┘
                       │          │          │          │
   ┌───────────────┐   │   ┌──────▼──────┐   │   ┌──────▼──────┐
   │ data-ingestor │──►│   │   maker     │   │   │  checker    │
   │  (no LLM)     │   │   │   agent     │──►│──►│   agent     │
   │  Binance WS   │   │   │ (Sonnet)    │   │   │  (Opus)     │
   └──────┬────────┘   │   └─────────────┘   │   └──────┬──────┘
          │            │                     │          │
          ▼            │                     │          ▼
   ┌───────────────┐   │   ┌─────────────┐   │   ┌─────────────┐
   │  Postgres     │◄──┴──►│  executor   │◄──┴──►│risk-monitor │
   │  (state)      │       │  (no LLM)   │       │  (no LLM)   │
   └───────┬───────┘       └─────────────┘       └─────────────┘
           ▲                                            │
           │       ┌──────────────────────┐             │
           └───────┤ strategy-genesis     │◄────────────┘
                   │  agent (Opus, slow)  │
                   └──────────┬───────────┘
                              │
                   ┌──────────▼───────────┐    ┌─────────────┐
                   │  skills/  volume     │    │  dashboard  │
                   │  (versioned .md)     │    │  (FastAPI)  │
                   └──────────────────────┘    └─────────────┘
```

### 3.2 Service responsibilities

- **data-ingestor** — Pulls Binance klines + websocket stream. Normalizes, dedupes, writes to Postgres `bars` table. Publishes `bar.closed` events to Redis. **No LLM.**
- **executor** — Subscribes to `signal.verified`. Places orders against the configured venue (`simulator` or `binance_testnet`). Enforces per-trade and per-strategy hard caps pre-trade. Updates `fills` and `positions`. **No LLM.**
- **maker-agent** — Triggers every N bars (default 15 minutes). Reads `skills/alpha_research.md` and active strategies, emits candidate signals to Redis. **Sonnet.**
- **checker-agent** — Triggers per `signal.candidate` event. Independently re-runs a walk-forward backtest using `skills/backtest_verification.md`. Never sees the maker's reasoning trace. Emits `signal.verified` or `signal.rejected`. **Opus** (different model architecture catches different errors).
- **strategy-genesis-agent** — Triggers hourly. Reads recent PnL + lessons, either tunes hyperparameters of active strategies or proposes new strategy code into `strategies/pending/`. Promotions to `strategies/active/` require checker sign-off plus 30 days of paper-trading. **Opus.**
- **risk-monitor** — Ticks every minute. Computes drawdown vs 30-day rolling high, position-size compliance, sector exposure. Publishes `circuit.broken` on breach and instructs executor to flatten everything. **No LLM.**
- **dashboard** — FastAPI + HTMX. Read-only UI: equity curve, active positions, pending signals, lessons feed, recent rejections, manual unlock control for the kill switch.
- **redis**, **postgres** — Infrastructure.

### 3.3 Two-tier loop

- **Fast loop (deterministic, every 1–5m):** `data-ingestor → executor → risk-monitor`. Zero LLM calls; this is the hot path and must never block.
- **Slow loop (agent-driven, every 15m–1h):** `maker → checker → strategy-genesis → skills update`. All reasoning + self-improvement lives here.

## 4. Roan's six pieces, mapped

| Roan's piece | Our implementation |
|---|---|
| **Automation** | APScheduler inside each agent container — cron-like, no external scheduler. Fast loop fires on `bar.closed` Redis events; slow loop fires on time schedules. |
| **Skills** | `skills/` Docker volume of versioned `.md` files. Every change git-committed inside the volume for full audit trail. |
| **State files** | Postgres for structured state (positions, fills, PnL, equity curve). `state/` volume for unstructured memory (`STATE.md`, `LESSONS.md`, traces, pending signals). |
| **Verifier** | The `checker-agent` container. Different model (Opus vs maker's Sonnet), different skill file, sees only the signal payload — never the maker's reasoning. |
| **Worktrees** | Each container is the isolation unit. Strategies under test live in `strategies/pending/`; promoted strategies live in `strategies/active/`. Genesis agent can only write to `pending/`. |
| **Connectors** | Tools registered with the Agent SDK: `query_postgres`, `read_bars`, `run_backtest`, `place_order` (executor only), `read_skill`, `write_lesson`, `propose_strategy`. |

## 5. Agent loop behavior

### 5.1 Maker — one tick (default every 15 min)

1. Read `skills/alpha_research.md` (rules + lessons learned).
2. Query Postgres for last N bars across the active universe, current positions, available cash.
3. Iterate active strategies in `strategies/active/`, compute signals.
4. Filter against current lessons (e.g. "skip momentum on FOMC days").
5. Publish each candidate as `signal.candidate` (strategy id, symbol, side, size, reasoning).
6. Append a short trace to `state/maker_trace_<ts>.md`.

### 5.2 Checker — one tick (per candidate)

1. Receive `signal.candidate` payload. Does **not** see maker reasoning.
2. Read `skills/backtest_verification.md`.
3. Independently run walk-forward backtest of the proposed strategy on out-of-sample data; compute Sharpe, max drawdown, Newey-West t-stat.
4. Verdict = `approve` / `reject` / `needs_revision` with reasons.
5. Publish `signal.verified` or `signal.rejected`. Every rejection appended to `state/LESSONS.md` for the maker to read next tick — this is the feedback loop that makes the system self-improving.

### 5.3 Strategy-genesis — one tick (hourly)

1. Read `state/LESSONS.md` and recent PnL by strategy.
2. Decide: tune existing strategy hyperparameters, or propose new strategy code.
3. Tuning path: walk parameter grid, write updated `strategies/active/<name>.py` only after checker validates new params beat old ones on out-of-sample data.
4. Genesis path: write `strategies/pending/<new_name>.py`. Requires 30 days paper trading + checker sign-off before promotion.
5. Bounded by `risk_rules.md`: never propose >5% position sizing, never leverage >1x.

## 6. Data model

### 6.1 Postgres schema (narrow — only data that needs SQL)

```sql
bars(symbol, timeframe, ts, open, high, low, close, volume, PRIMARY KEY(symbol, timeframe, ts))

signals(
  id UUID PK,
  ts_created TIMESTAMPTZ,
  strategy_id TEXT,
  symbol TEXT,
  side TEXT,            -- 'long' | 'short' | 'flat'
  size_pct NUMERIC,     -- % of equity
  status TEXT,          -- 'candidate' | 'approved' | 'rejected' | 'executed' | 'killed'
  maker_payload JSONB,
  checker_verdict JSONB,
  rejection_reason TEXT
)

fills(id, signal_id FK, ts, symbol, side, qty, price, fees, venue)
positions(symbol PK, qty, avg_entry, unrealized_pnl, updated_at)
equity_snapshots(ts PK, cash, equity, drawdown_pct)

strategies(
  id PK, name, status,    -- 'active' | 'pending' | 'retired'
  source_path TEXT,
  hyperparams JSONB,
  created_at, promoted_at, retired_at,
  promoted_reason TEXT, retired_reason TEXT
)

backtests(
  id, ts, strategy_id, period_start, period_end,
  sharpe NUMERIC, max_dd_pct NUMERIC, t_stat NUMERIC, trade_count INT,
  passed BOOL, notes TEXT
)

risk_events(id, ts, kind, payload JSONB, actions_taken JSONB)
```

### 6.2 File-backed state (`state/` volume)

```
state/
├── STATE.md            ← portfolio summary, last tick timestamp, equity
├── LESSONS.md          ← append-only journal
├── maker_trace_<ts>.md ← rolling 24h of maker reasoning
├── checker_trace_<ts>.md
├── pending_signals/    ← signals awaiting checker
└── archive/            ← old traces, monthly rollups
```

Rationale: Postgres for things you query (PnL over time). Files for things agents read as context (lessons, traces). Agents are good at reading markdown, bad at SQL on every tick.

### 6.3 Skill volume (`skills/` — the brain)

```
skills/
├── .git/                          ← every change committed
├── alpha_research.md              ← maker's playbook
├── backtest_verification.md       ← checker's checklist
├── strategy_genesis.md            ← genesis agent's playbook
├── risk_rules.md                  ← hard caps + soft heuristics
└── strategies/
    ├── active/
    │   ├── momentum_btc.py
    │   └── mean_reversion_eth.py
    ├── pending/
    └── retired/
```

Every write to `skills/*.md` triggers `git commit -m "<agent>: <reason>"` via a post-write hook. Git blame ties every lesson back to the incident that produced it.

## 7. Error handling

Any failure in the fast loop defaults to *no trade*, never *yolo trade*.

| Failure | Behavior |
|---|---|
| Data ingestor crashes / Binance stalls > 30s | Executor sees stale bar timestamp, refuses to act. Maker pauses. Alert. |
| Maker agent times out / malformed JSON | Tick dropped. Logged to `STATE.md`. Retried next interval. |
| Checker disagrees with itself across runs | Signal rejected. Logged as `inconsistent_verdict`. Genesis reviews next cycle. |
| Executor can't reach Binance testnet | Auto-fallback to self-sim ledger. Surface mode mismatch on dashboard. |
| Postgres unreachable | Whole fast loop pauses. Risk monitor's last action wins. |
| Agent SDK rate-limited | Exponential backoff, log to `STATE.md`. Genesis has lowest priority — sleeps first. |

## 8. Risk circuit breakers

Three tiers, none LLM-decided, all enforced in code:

1. **Per-trade cap (immutable):** position size never exceeds 5% of equity. Enforced both in `risk_rules.md` and re-checked by the executor pre-fill.
2. **Per-strategy cap (immutable):** any single strategy ≤25% of equity across all open positions.
3. **Portfolio kill switch (immutable):** drawdown >15% from rolling 30-day high → `circuit.broken` published, executor flattens everything, maker + genesis pause 24h, requires manual unlock via dashboard.

The agent-tunable bounds are *below* these ceilings. Genesis may dial down position sizes; never up past the hard ceiling. Ceiling constants live in Python code, not skills — the agent has no write path to them.

## 9. Testing strategy (TDD)

| Layer | Test type | What we verify |
|---|---|---|
| Strategies (`strategies/active/*.py`) | Unit tests with deterministic fixtures | Signal correctness given known bar series. Each strategy ships with `test_<name>.py`. |
| Self-sim ledger | Unit tests | Fills at expected prices, slippage applied, fees deducted, positions correct. |
| Risk monitor | Unit tests | Kill switch fires at exactly the threshold. Caps enforced. |
| Executor | Integration tests with mocked Binance testnet | Order placement, retry on transient errors, fallback to self-sim on outage. |
| Maker/checker agents | Contract tests | Given fixed bar fixtures + frozen skill files, output is in expected schema. (LLM output non-deterministic, so test shape and constraints, not exact content.) |
| Full system | End-to-end replay test | Feed 30 days of historical bars through the live pipeline; assert no risk breaches, no malformed signals, lessons file grows. |
| Skill changes | Property tests | After every skill commit, replay last 7 days of fixtures; ensure no new strategy violates risk_rules.md. |

The end-to-end replay test is the load-bearing one — it is how we know the system *as a whole* still behaves before deploying any change.

## 10. Build phases

- **Phase 0 — Scaffolding (1–2 days):** repo layout, docker-compose, Postgres schema, Redis, dashboard skeleton. No agents yet.
- **Phase 1 — Deterministic core (2–3 days):** data-ingestor, executor, risk-monitor, self-sim ledger. End-to-end test: hardcoded buy/sell signals flow through to PnL. **No LLM yet.**
- **Phase 2 — Maker + Checker only (3–4 days):** wire in Claude Agent SDK. Single hardcoded momentum strategy. Verify maker emits, checker filters, executor fills. Lessons append on rejections.
- **Phase 3 — Hyperparameter tuning (2 days):** genesis agent, but tuning only. Verify it can improve a strategy's Sharpe without breaking risk rules.
- **Phase 4 — Strategy genesis (3–5 days):** genesis agent proposes new strategy code; runs through `pending/` → `active/` promotion gate.
- **Phase 5 — Binance testnet integration (1–2 days):** flip executor venue from self-sim to testnet. Run paper-live.

Phases 1 → 2 → 3 ship in order; Phase 4 only after 1–3 are battle-tested.

## 11. Out of scope (for v1)

- Multi-asset (equities, prediction markets) — crypto only.
- Multiple exchanges — Binance only.
- Real money — mock currency only.
- Mobile / push notifications — dashboard is browser-only.
- Multi-tenant — single user, single fund.
- Distributed compute across multiple VPSs — single host.

## 12. Open questions

None at this time. All blocking decisions resolved during brainstorming. The first surprise during Phase 1 implementation should bounce back to this spec for revision.
