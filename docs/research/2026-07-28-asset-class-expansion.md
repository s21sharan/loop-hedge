# Beyond Crypto: Asset-Class Expansion Research

**Date:** 2026-07-28
**Method:** Five parallel research agents (asset classes, hedge fund landscape, agent
networks, alternative markets, iteration statistics), grounded against this codebase.
**Status:** Research findings. No code changes made.

---

## Research quality caveat — read this first

This environment's network policy denies outbound CONNECT to arbitrary hosts. `WebSearch`
works (it routes through the Anthropic API); `WebFetch` and `curl` to third-party domains
returned 403 at the gateway for every host tried — `databento.com`, `gamma-api.polymarket.com`,
`api.elections.kalshi.com`, `arxiv.org`, and others.

**Consequence:** every external claim below comes from search-result summaries, not from
reading primary sources. Vendor pricing, API rate limits, retention windows, and sandbox
terms are **leads to verify, not confirmed facts**. Statistical results derived from first
principles (§1, §2) were computed locally and are independent of this limitation.

---

## 0. Executive summary

Three findings, in order of how much they should change what you do.

**1. The current configuration cannot be profitable at 1–5m holding periods, under any
signal.** This is arithmetic, not pessimism, and it is independent of which asset class
you add. See §1.

**2. Bar frequency contributes exactly zero statistical learning.** `t ≈ SR·√Y` is
invariant to sampling rate — sampling 1m instead of 1d multiplies observations by 1,440
and divides per-bar Sharpe by √1440, cancelling exactly. Iteration velocity is governed by
calendar time, signal quality, and *independent* breadth. Ticking faster does not learn
faster. See §2.

**3. The highest-leverage expansion is not a conventional asset class.** It is
cleanly-labeled, fast-resolving event contracts, because they are the only instrument type
that breaks the `1/ρ` effective-breadth ceiling that caps crypto at `N_eff ≈ 3.4` no matter
how many pairs you add. Two independent research agents converged on Kalshi from different
briefs. See §3.

---

## 1. The binding constraint: the cost floor

### 1.1 The arithmetic

For a signal with information coefficient `IC` against forward returns over holding period
`h`, taking a full-size directional position, expected gross P&L per bet under joint
normality is `0.798 · IC · σ_h`. With round-trip cost `c`, breakeven is:

```
IC* = 1.253 · c / σ_h
```

This repo's simulator (`src/loophedge/ledger/simulator.py`) configures
`SLIPPAGE_BPS = 5` and `fee_bps = 10` — **15 bp per side, 30 bp round trip**. That cost
model is *realistic* (Binance spot taker is 10 bp); the problem is the holding period.

At BTC's ~50% annualized vol, 24/7:

| Holding | σ_h | Round-trip cost as % of σ_h | Breakeven IC |
|---|---|---|---|
| 1m | 6.9 bp | 435% | **5.45** |
| 5m | 15.4 bp | 195% | **2.44** |
| 15m | 26.7 bp | 112% | **1.41** |
| 1h | 53.4 bp | 56% | 0.70 |
| 4h | 106.8 bp | 28% | 0.35 |
| 1d | 261.7 bp | 11% | **0.14** |
| 1w | 692.4 bp | 4% | 0.05 |

**An IC is a correlation. It cannot exceed 1.0.** The 1m, 5m, and 15m rows require an IC
above 1.0, so they have negative expectancy against *any* signal, including a perfect one.
The regime is closed, not merely difficult.

Realistic ICs for systematic strategies are 0.02–0.05. At IC = 0.05, breakeven requires a
holding period of **~8 days**. At IC = 0.03, ~23 days.

### 1.2 Why this matters more than the asset-class question

`.env.example` sets `BAR_TIMEFRAME=5m`. The design spec commits to "1m–5m intraday bars."
Under the configured cost model every strategy at that horizon loses money for reasons
unrelated to signal quality. The checker then writes those losses to `LESSONS.md`, and the
maker conditions on them next tick.

**The loop as specified would learn the fee schedule and record it as market structure.**

Fortunately `skills/strategies/active/` is currently empty, so nothing is running yet. The
default can be changed before any strategy is written against it.

### 1.3 Best-case comparison

Even at Binance perpetual-futures *maker* fees (~4 bp round trip, ignoring adverse
selection), 1m breakeven IC is 0.73 and 5m is 0.33 — both implausible. The optimum for
retail-cost execution is **~1 day taker, ~4 hours maker**. That is 300–1,400× slower than
the current design.

---

## 2. What actually maximizes iteration

The stated goal was to "maximize iteration." The statistics say iteration velocity is not
what most people think it is.

### 2.1 Frequency buys nothing

For true annualized Sharpe `SR_a` sampled `n` times per year over `Y` years:

```
t = ŜR_p · √T ≈ (SR_a/√n) · √(nY) = SR_a · √Y
```

The `n` cancels. Years required to distinguish a strategy from zero:

| Requirement | SR=0.5 | SR=1.0 | SR=1.5 | SR=2.0 |
|---|---|---|---|---|
| t = 2.80 (95%, 80% power) | 31.4 | **7.9** | 3.5 | 2.0 |
| t = 3.00 (Harvey–Liu hurdle) | 36.0 | **9.0** | 4.0 | 2.2 |

A Sharpe-1.0 strategy needs **~8 years of live data** to confirm at 80% power, whether you
sample it every minute or every day. "How many trades do I need?" is malformed; the
invariant is calendar time × SR².

### 2.2 The trial budget is finite and small

Bailey–Borwein–López de Prado–Zhu's Minimum Backtest Length: the expected maximum Sharpe
from `N` worthless strategies on `Y` years of data is
`E[max ŜR] ≈ (1/√Y)·[(1−γ)Φ⁻¹(1−1/N) + γΦ⁻¹(1−1/(Ne))]`, γ = 0.5772.

Inverted — **lifetime trial budget given available history:**

| History | Max trials for a credible SR=1.0 | for SR=1.5 |
|---|---|---|
| 3 years | 14 | 121 |
| 5 years | 45 | 1,423 |
| **8 years (crypto perps)** | **243** | **51,135** |

A genesis agent producing one strategy per 15-minute tick (96/day) exhausts the SR-1.0
budget in **2.5 days**. Past that, the best backtest you find is fully explained by noise.

Two consequences:

- **Historical data is a non-renewable statistical endowment.** Replay gives you ~8 years
  of evidence once. Running the same 8 years 10,000 times does not create 80,000 years of
  evidence; it creates a Sharpe-2.23 mirage (that is the *expected* best result from 10,000
  worthless strategies on 3 years of data).
- **Raising the promotion bar from SR 1.0 to 1.5 multiplies the trial budget 210×**
  (243 → 51,135) at zero cost. This is the cheapest available improvement.

### 2.3 Breadth has a hard ceiling, and crypto's is low

For `N` instruments with average pairwise correlation ρ: `N_eff = N/(1+(N−1)ρ) → 1/ρ`.

| Universe | 2 symbols | 200 symbols | Learning-speed gain |
|---|---|---|---|
| Raw crypto, directional (ρ≈0.75) | 1.14 | 1.33 | **1.2×** |
| Crypto, BTC-beta-neutral (ρ≈0.29) | 1.55 | 3.41 | 2.2× |
| Equity stat-arb residuals (ρ≈0.05) | 1.90 | 18.26 | 9.6× |
| Independent binary events (ρ≈0) | 2.00 | 200.00 | **100×** |

Going from 2 to 200 crypto pairs *directionally* buys ~1.2× — you have bought 200 copies of
Bitcoin. Symbols 21–200 are worth almost nothing. Beta-neutralizing first is worth ~1.9×
and removes the dominant variance term that contains no alpha.

**Only structurally independent bets break the ceiling.**

### 2.4 Why event contracts are a different class of feedback

A crypto trade outcome is a near-worthless learning signal. KL divergence per trade between
"this works" and "coin flip":

| Setting | P(win) | Trades to a decisive (~20:1) update |
|---|---|---|
| Daily bets, true SR=1.0 | 0.5209 | **3,442** |
| 5-min bets, true SR=1.0 | 0.5012 | **990,735** |

Versus detecting forecast skill via Brier score on binary contracts:

| Brier improvement | Resolved contracts needed |
|---|---|
| 0.10 | **36** |
| 0.05 | **144** |
| 0.03 | 400 |

**36–400 resolved contracts versus 3,442 trades** — a 10–100× improvement in learning
velocity, achievable in weeks rather than years. Event contracts give clean attributable
labels (no exit-timing confound), bounded resolution time, structural independence, and a
proper scoring rule that measures calibration directly rather than through a noisy P&L lens.

Cost per unit of risk is competitive: Kalshi taker fee is `0.07·p·(1−p)`, which against
payoff sd `√(p(1−p))` is 3.5% at p=0.50 — comparable to holding BTC for a full day (4.2%),
but with a clean label attached.

### 2.5 The LESSONS.md problem

Given §2.4, the current design actively degrades the maker. Writing every losing trade to
`LESSONS.md` applies a 0.0001%-of-decisive Bayesian update as if it were conclusive — and an
LLM reads text as instruction, not as a likelihood contribution. Over a few hundred ticks
this accumulates a document of confident, mutually inconsistent rules derived from coin
flips, and the maker follows them.

**It is a superstition accumulator as specified.** Fixes:

- Never write an *alpha* lesson from a single trade. Aggregate: "over the last 200 trades in
  condition X, hit rate 46% (n=200, t=−1.4)" — include n and t so the model can weight it.
- Separate three lesson types: **mechanical** ("the Bar object uses attribute access"),
  **risk/constraint** ("position exceeded cap"), and **alpha**. The first two are learnable
  from n=1 and are the genuine value of the loop. Only alpha lessons need large n.
- Expire and prune. An append-only file read in full every tick becomes a contradictory
  10,000-line prompt.
- Every checker rejection is a consumed trial and must increment N.

---

## 3. Ranked recommendations

### Tier 1 — build these

**1. Kalshi demo environment (event contracts).** Independently recommended by two agents
with different briefs. Meets every architectural requirement better than any conventional
asset class:

- Public market data, no API key required; native OHLC + volume + open interest candlesticks
  at 1/60/1440-minute granularity
- Real demo environment (`demo-api.kalshi.co`) with the full order lifecycle, no KYC, no
  funding, no expiry
- 24/7 — zero agent-loop starvation, same operating profile as crypto
- Prices bounded 1–99 cents: no splits, no dividends, no currency, no roll adjustment
- Daily weather series resolve in ~24h off a deterministic NWS observation — dozens of
  independent, unambiguously-labeled outcomes per day
- Liquidity is real: $31B notional in June 2026

Start with recurring daily series (weather, the 15-minute BTC series), which behave as
stable symbols with repeating history and therefore fit the existing `Bar` model.

**2. Manifold Markets as a zero-risk staging integration.** Play money, no KYC, bots
explicitly welcomed, ~500 req/min, free. Validates the settlement-lifecycle refactor before
touching a regulated venue. Caveat: CPMM not CLOB, so order-book intuitions don't transfer,
and play-money calibration is poor.

**3. Crypto derivatives testnets (Deribit, Binance Futures, Bybit).** The highest
ROI-per-engineering-hour item found, and nobody asks about it because it doesn't feel like a
new asset class. Free, permanent, 24/7 testnets with full APIs give the genesis agent
leverage, term structure, funding rates, and real options mechanics — three new hypothesis
families — without inheriting market calendars, corporate actions, roll schedules, or
exchange data licensing. Funding-rate series drops into the existing `Bar` table at
`timeframe='8h'` with zero schema change.

Caveat: testnet order books are thin and fills are unrepresentative. Treat testnet P&L as a
*mechanism* test, validate strategies against real historical data offline.

### Tier 2 — good, more work

**4. US equities + ETFs via Alpaca.** The cleanest conventional add, at $0. Free tier serves
*full SIP consolidated historical bars* provided the query's `end` is ≥15 minutes old —
structurally irrelevant for a system whose agent loop runs every 15m–1h. Paper accounts need
only an email, start at $100k, persist indefinitely. 24/5 trading via Blue Ocean ATS gives
~120 tradable hours/week.

Real value here is **cross-sectional breadth**: 5,000+ liquid tickers enable relative-value,
pairs, and dispersion strategies that don't exist in a 10-symbol crypto universe — and
equity stat-arb residuals have ρ≈0.05 versus crypto's 0.29, a 5× better breadth ceiling.

Two caveats: don't use the free websocket as your bar source (IEX-only, ~3.2% of volume,
gapped on anything but mega-caps — poll REST instead); and **verify the 15-minute-lag SIP
claim with a real API key** before designing around it, as it could not be confirmed directly.

**5. Polymarket as a data source only.** Best free prediction-market backtest corpus
(`/prices-history` at 1-minute fidelity, plus subgraphs within The Graph's free tier). **Not
a venue** — US persons are locked out of the liquid offshore book, and Polymarket US requires
full KYC/SSN.

### Tier 3 — free data, paper-only

GridStatus (ERCOT day-ahead vs real-time LMP spreads — free, hourly, years deep), EVE Online
market history (free daily OHLCV, drops into `Bar` unchanged, genuine text→price channel via
patch notes), Metaculus API (offline calibration scoring against a strong human baseline),
GPU spot pricing (start logging now; CME and ICE compute futures land later in 2026).

### Skip

- **Options (conventional).** Breaks the bar abstraction outright. An option is a
  `(underlying, expiry, strike, right)` tuple, not a symbol; most 1-minute bars have zero
  volume; and the meaningful state is implied vol and greeks, which `Bar` cannot hold. A
  maker agent looking at option OHLCV without IV will "discover" that option prices decay
  over time. This is a second data model, not an adapter — weeks of work. Get options
  mechanics from Deribit testnet instead.
- **International equities.** Four holiday calendars, multiple currencies contaminating the
  drawdown metric, no free paper venue with a clean API.
- **Futures (CME).** Viable exactly one way — IBKR paper + Databento (~$180–200/mo) — plus
  continuous-contract construction, which is where most futures backtests silently lie.
  Defer until the equities loop demonstrably produces strategies the checker approves.
- **FX.** Best free data story of any class (OANDA + Dukascopy), but: FX "volume" is a
  broker tick count, not volume, so every volume-derived feature is noise; and US CFTC rules
  (mandatory FIFO, no hedging) will silently rewrite agent positions, causing the agent to
  observe P&L that contradicts its own position model.
- **MEV, LP/impermanent loss, on-chain private credit, tokenized treasuries, carbon, freight,
  trading cards, music royalties, cat bonds.** Each fails on at least two of: no API, no
  history, no venue, no sandbox, feedback loop in months, or ToS prohibits automation.

---

## 4. What the field is actually doing

### 4.1 The evidence on LLM-generated trading signals is negative, not merely thin

Two independent 2026 benchmarks, different venues, same result:

- **Alpha Arena** (nof1.ai) — frontier LLMs trading crypto perps with real money under
  identical prompts, data, and execution. Season 1: Qwen3-Max +22.3%, GPT-5 −62.7%,
  Gemini 2.5 Pro −56.7%. Across all seasons: 43 models, 2,527 trades, **42.6% of
  model-seasons profitable.** The +22% to −63% spread under identical conditions is the
  tell — that is variance, not skill.
- **PolyBench** — 7 frontier models, 38,666 Polymarket markets, realistic order-book
  execution. **Five of seven lost money while expressing uniformly high confidence.**

Reinforcing results: **FINSABER** (KDD 2026) widened LLM trading evaluation to 20 years and
100+ symbols and found reported advantages **vanish entirely** — narrow universes and short
windows manufacture apparent alpha. A reproducibility study of TradingAgents found ±2.8–4.2%
seed variance on a 3-month task, with neither configuration beating buy-and-hold. A survey of
19 LLM-trading studies found **only 2 disclose an extractable time-consistent data split and
0 reach full reproducibility.**

**The single most actionable consequence:** confidence carries no information about
correctness. Log stated confidence against realized outcome as a first-class metric and have
the checker treat maker confidence as approximately uninformative. Both the alternative-markets
and hedge-fund agents arrived at this independently.

### 4.2 What does work

Every institutional deployment with a real track record splits the same way: **LLMs write and
audit code; deterministic statistical models generate signals.**

- **Bridgewater AIA Labs** — >$4.5bn, the best-resourced AI-decision fund in existence,
  returned **+11.9% in 2025 against an HFRI composite of +12.6%**. Guardrails cut error rates
  from 8% to 1.6%, implying unguarded LLM output was wrong about one time in twelve.
- **AQR** — ML powers ~20% of signals in the flagship fund. That is statistical learning, not
  LLMs; the LLMs sit in the research loop.
- **D.E. Shaw DocLab**, Bridgewater PAT — document parsing and research acceleration.
- The 2016-era "AI will run the fund" cohort is entirely dead, pivoted, or silent (Aidyia
  dissolved 2023; Sentient broke up). The survivors with long track records — Voleon
  (~$23.4bn, 19 years), Numerai — use classical ML, not LLM decision-making.

**Your genesis agent (LLM writes strategy code) is the well-supported use case. Your maker
proposing signals directly is the one with published negative results.**

### 4.3 Contamination — a compounding problem

There is now a coherent 2025–26 literature showing published LLM trading alpha is
substantially memorization:

- **Look-Ahead-Bench** measures alpha decay across temporally distinct regimes and finds
  significant look-ahead bias in standard LLMs.
- An *Economics Letters* paper found GPT-4o recalls exact S&P 500 closing prices with **<1%
  error inside its training window**, and is significantly worse after cutoff.
- **KTD-Fin** masks tickers and dates, then applies Barra-style attribution: agent returns
  under leakage control are "largely explained by passive market and style exposure, with
  limited evidence of persistent stock-selection alpha."

For this system specifically: **an LLM checker backtesting 2024 crypto has that period's
price action and post-hoc commentary in its training corpus**, biasing it toward approving
winners it already knows. Mitigations, in priority order:

1. The checker must not *be* the evaluator. Backtesting runs in deterministic code the LLM
   cannot see the answers to; the LLM writes and audits the evaluation.
2. Reserve a hard post-cutoff window as sacred out-of-sample.
3. Anonymize the tape — strip symbols, absolute price levels, and dates from anything the LLM
   sees during evaluation. Feed normalized returns. Prompt-level "don't use your memory"
   instructions are documented to fail; this must be a data-layer mitigation.
4. Diagnose by measuring Sharpe decay across the cutoff boundary. Large pre/post gap =
   memorization, not alpha.

**You have two different models, which affords a nearly-free contamination detector**: where
models with different training cutoffs agree suspiciously well in-window and diverge
out-of-window, that is memorization.

### 4.4 Agent networks: what's real

- **Freqtrade** (52.7k★, pushed daily) is the most useful project to mine — see §5.
- **Nautilus Trader** — the same strategy code runs identically in backtest and live because
  both share one deterministic event core. That concept is worth stealing even without the
  library.
- **Microsoft RD-Agent(Q)** — the closest analogue to the genesis agent. Steal its
  Specification→Synthesis→Implementation decomposition, its Co-STEER knowledge base of past
  implementation failures (a structured `LESSONS.md`), and its explicit factor-count penalty.
- **QuantConnect Lean's `Insight` object** — a signal is not "buy BTC," it is
  `(symbol, direction, magnitude, confidence, valid_until, source_model)`. Worth copying into
  `schemas.py`; it makes signals decayable, composable, and attributable.
- **backtrader is dead** (last commit 2023-04-19) despite still being widely recommended.

**Multi-agent debate does not replicate.** Compute-normalized studies find independent
multi-agent setups cost ~58% token overhead and centralized ones ~285%, and that a single
agent given the same total budget matches or beats the team. Worse, introducing a *weaker*
agent into debate with a stronger one degrades the stronger one's output.

**This architecture is on the right side of that** — the checker is Opus against a Sonnet
maker, and its authority comes from running a deterministic backtest rather than from
arguing. That is the one multi-agent pattern the literature supports.

**The on-chain agent-token sector is empirically dead.** A peer-reviewed analysis of 11 Solana
agent treasuries across 925k holders: token holders lost **$191.7M** while treasuries held
$30M in paper gains; market-cap-to-AUM ratios exceeded 10,000× (established DeFi is <1×);
tokens down 93% from ATH. Giza sunset its flagship agent March 2026. Nothing to borrow except
Almanak's Safe+Zodiac non-custodial permission model.

**Two free external scoreboards exist** and address the gap every self-evaluating system has:
**Numerai Signals** (rigorously out-of-sample, stake-weighted) and **Recall Network**
paper-trading competitions (mock money — a native fit). Both cost nothing but an adapter.

---

## 5. Required changes to this codebase

### 5.1 Bugs and gaps found by direct code inspection

| # | Issue | Location | Impact |
|---|---|---|---|
| 1 | **Multi-symbol backtests mismark equity.** `sim.equity({bar.symbol: bar.close})` passes only the current bar's symbol; the simulator falls back to `avg_entry` for anything missing, so every other open position shows zero unrealized PnL. | `backtest/engine.py:64`, `ledger/simulator.py:77` | Any portfolio/cross-sectional strategy backtests wrong. Blocks the highest-leverage lever (breadth). |
| 2 | **Sharpe annualization assumes a 24h day and 252-day year.** | `backtest/engine.py:78-79` | Understates crypto Sharpe ~17% (should be 365 days); would inflate equity Sharpe ~1.9× (claims 288 5-min bars/day when an RTH session has 78). |
| 3 | **No causality enforcement.** `strategy_callable(bars, hyperparams)` receives the *entire* bar list up front and is trusted to return correctly-timestamped signals. | `backtest/engine.py:40` | A genesis agent optimizing for pass rate can use future bars. The checker would approve it. **Highest-priority fix.** |
| 4 | **Promotion gates far too loose.** `SHARPE_THRESHOLD=1.0`, `T_STAT_THRESHOLD=1.5`, `MIN_TRADES=30`. | `backtest/engine.py:14-17` | t≥1.5 is α≈6.7%; the gate admits ~1 in 15 worthless strategies. 30 trades provides essentially no protection. At 10 candidates/day this promotes ~244 junk strategies/year. |
| 5 | **`Bar.symbol` is `String(20)`.** | `models.py:18` | A Kalshi ticker (`KXHIGHNY-26JUL28-B82.5`) is 22 chars; Polymarket CLOB token IDs are ~77. Overflows on day one for any event venue. |
| 6 | **No instrument lifecycle anywhere in the schema.** No expiry, settlement value, or resolution timestamp. | `models.py` | Every Tier-1 recommendation resolves to a terminal value. Prerequisite refactor, not an add-on. |
| 7 | **Flat-bps cost model.** | `simulator.py:45` | Fine for crypto; wrong shape for equities (per-share), futures (per-contract + multiplier), and event contracts. No instrument metadata table (tick size, lot size, multiplier). |
| 8 | **Executor hardcodes `venue="simulator"`** when writing fills despite `binance_testnet` being configured. | `executor.py:56,174` | The testnet path may not be wired end-to-end. |

### 5.2 The evaluation harness that should replace the current gate

**Infrastructure prerequisites:**

- **Trials registry (mandatory).** Append-only:
  `(trial_id, ts, strategy_family, hypothesis_text, hyperparams, data_window, IS_sharpe, OOS_sharpe, pnl_vector_hash)`.
  Without it, N is unknown and the Deflated Sharpe Ratio is uncomputable. Every parameter
  variation, re-run, and checker rejection counts. Monotone, un-resettable.
- **Effective-N clustering.** Cluster trial P&L vectors nightly, cut at ρ=0.8, use cluster
  count as `N_eff` — 50 lookback variants of one idea are ~1 trial, not 50.
- **Sequestered holdout.** Most recent 18 months in a store the agents cannot read. Opened
  once per strategy family by a separate process. Failure retires the family permanently.
- **Null-alpha canary in CI.** Generate GBM series matched to real crypto vol with *zero*
  predictability by construction, run the full genesis→checker→promotion pipeline, and
  measure the promotion rate. If it exceeds nominal α, the gates are broken and you know by
  how much. **This empirically measures your own false-discovery rate and costs about a day
  to build.**

**Tier 0 — mechanical auto-reject (no agent judgment):**

| Check | Threshold |
|---|---|
| Look-ahead: shift all signals +1 bar | Sharpe must drop ≥50%; if unchanged, reject |
| Cost sanity | Round-trip cost < 25% of holding-period σ |
| Minimum holding period | ≥4h (taker) or ≥1h (maker-only) |
| Independent bets | ≥100 non-overlapping |
| History coverage | ≥2 years and ≥3 distinct vol regimes |
| Concentration | No single day >10% of P&L; no month >25% |

**Tier 1 — statistical:**

| Metric | Threshold |
|---|---|
| **Deflated Sharpe Ratio** (actual `N_eff`, actual trial-Sharpe variance, actual γ₃/γ₄) | **≥0.95** — replaces raw Sharpe entirely |
| **PBO via CSCV**, S=16 | **≤0.10** |
| Newey–West t, `L=⌊4(T/100)^(2/9)⌋`, net of 1× cost | **≥3.0** (not 1.5) |
| NW t at **3× cost** | ≥1.5 |
| CPCV path Sharpe, 5th percentile | >0 |
| Daily P&L ρ₁ | Report; if >0.1 use `η(q)` not `√q` (prevents 10–36% inflation) |

**Tier 2 — economics:** cost drag <30% of gross; capacity <1% of median bar volume; max DD
<20% (12% is too tight — it rejects good strategies for noise); positive Sharpe in ≥3 of 4
vol quartiles; **|ρ| <0.5 against the existing promoted book** (otherwise it adds no breadth).

**Tier 3 — holdout:** DSR ≥0.95 on the sequestered 18 months, computed once. Fail = permanent
retirement of the family.

**Tier 4 — paper probation, correctly scoped.** This is where the current design has its
deepest conceptual error. Paper trading **cannot** validate alpha:

| Probation | P(worthless strategy shows positive P&L) | P(shows ŜR>1.0) |
|---|---|---|
| 30 days | 50.0% | **38.7%** |
| 90 days | 50.0% | 31.0% |
| 365 days | 50.0% | 15.9% |

The spec's "30 days paper trading" gate passes **38.7% of worthless strategies**.
Distinguishing true SR=1.0 from zero at t=2 requires **4 years**.

**Therefore paper probation must be an *implementation* gate, not a statistical one.** Scope
it to quantities that converge fast: realized slippage vs modeled (within 1.5×, ~50 fills),
fill rate (>80% of modeled), realized turnover (within 20%), realized daily-return vol
(within 30%, ~30 days), realized ρ to existing book (within 0.2, ~60 days), zero unhandled
exceptions. Second moments converge quickly, so 30–60 days genuinely validates the *risk*
model. It never validates the *return* model. Say so in the code and the docs.

### 5.3 Patterns to port

1. **Freqtrade's `lookahead-analysis` and `recursive-analysis`** — the former re-runs a
   strategy on progressively truncated data asserting historical signals don't change; the
   latter varies loaded history length asserting indicator values at time *t* are identical.
   This is the mandatory gate missing between the genesis agent and `strategies/pending/`.
   GPL-3.0 — read the approach, don't vendor it. **Highest ROI item in all five reports.**
2. **Freqtrade's protections-as-objects** — `StoplossGuard`, `MaxDrawdown`, `CooldownPeriod`
   as declarative, composable, *backtestable* objects that run identically in backtest and
   live. `risk/caps.py` should adopt this.
3. **Nautilus's backtest/live path unification** — add a conformance test replaying a
   historical window through both `backtest/engine.py` and `services/executor.py`, asserting
   identical fills. If they can diverge, the checker's veto verifies a fiction.
4. **XAlpha's tri-alignment check** — verify hypothesis, code, and financial plausibility
   agree before any backtest. Catches the most common LLM failure (code implementing
   something subtly different from the stated idea) for near-zero cost.
5. **TrustTrade's agreement weighting** — replace the binary veto with a continuous agreement
   score that *sizes* the position. Extracts more information from the same two agents.
6. **Coordination Breakeven Spread** — log the counterfactual return of unvetoed maker
   signals and compute whether the checker earns its LLM cost plus the trades it prevented.
   Review monthly; be willing to conclude it doesn't.
7. **Hard invariants in the ledger, not in prompts** — max concentration, gross exposure,
   order notional, orders-per-interval asserted in `simulator.py` where no prompt can argue
   past them. Violation = halt, not clamp-and-continue.

---

## 6. Recommended build sequence

**Phase 0 — stop the bleeding (do before anything else).**
Change the default holding period to 4h–1d. Put `breakeven_IC = 1.253 × cost / σ_h` in the
genesis skill file and require every proposal to state its holding period and claimed IC.
Fix the annualization (#2) and the multi-symbol equity marking (#1). Nothing else matters
until expectancy can be positive.

**Phase 1 — make the loop honest.**
Build the trials registry, the null-alpha canary, and the look-ahead gate. Replace the
four-line promotion gate with DSR ≥0.95 and PBO ≤0.10. Restructure `LESSONS.md` into typed,
sample-sized, expiring records. Add hard invariants to the ledger.

**Phase 2 — add the instrument lifecycle.**
`Contract` table with `(ticker, series, open_ts, close_ts, resolution_ts, settlement_value,
resolution_source)`, settlement handling in the simulator, and widen `Bar.symbol`. This is
the prerequisite for every Tier-1 recommendation.

**Phase 3 — expand, in this order.**
Manifold (free, zero legal surface, validates the lifecycle refactor) → Kalshi demo (weather
series first: deterministic settlement, ~24h resolution) → crypto derivatives testnets
(funding rates into the existing `Bar` table is ~a day's work and serves as the numerical
null hypothesis LLM strategies must beat) → Alpaca equities (build the market-calendar
service and corporate-action handling here).

**Phase 4 — get externally scored.**
Numerai Signals and/or Recall Network. Add permanent baselines to the dashboard: buy-and-hold,
equal-weight, and **random-trades-at-matched-turnover**. The random control is the one
everyone skips and the most informative — if you can't beat it net of fees, you have
discovered that you are paying fees.

---

## 7. The honest ceiling

Stated plainly, because the alternative is expensive:

- **Profitable systematic crypto trading at 1–5m with retail costs and no latency edge is
  arithmetically impossible.** Not hard — impossible.
- **No amount of agent iteration accelerates statistical learning.** The market delivers one
  day of information per day. Ticking 35,040 times a year against 8 years of history is not
  learning 35,040× faster; it is spending the trial budget 35,040× faster.
- **A validated Sharpe-1.0 claim is not obtainable here within any relevant timeframe** — 7.9
  years at 80% power for a single pre-registered hypothesis, more after deflation.
- **Expected decay even for genuine discoveries is severe** — live-vs-backtest studies find
  median 73% deterioration; a ten-year backtest has an expected replication ratio of ~30%.
  Falck/Rej/Thesmar find signal *complexity* predicts decay, so an agent writing elaborate
  multi-indicator strategies is optimizing directly for the characteristic most associated
  with failure.
- **Mock capital removes the two forces that discipline real trading** — the cost of being
  wrong, and market impact. Conclusions about drawdown tolerance and sizing are unvalidated.

**Where the value genuinely is:**

1. **As an engineering artifact** — multi-agent orchestration, state management, code
   generation with probation gates, adversarial internal review. This value is fully real and
   does not depend on the strategies making money.
2. **As a research platform, if you invert the objective.** The interesting question is not
   "can LLM agents find alpha?" (the answer is essentially no). It is **"can an autonomous
   research loop be made statistically honest?"** Build the null-alpha canary, measure your
   pipeline's actual false-discovery rate on synthetic noise, then measure how much each gate
   reduces it. That is a real, publishable experiment almost nobody has run on an LLM-driven
   system.
3. **As a forecasting/calibration engine on event contracts** — clean labels, fast
   resolution, proper scoring, a public benchmark. "Our agent is better calibrated than the
   market on inflation contracts, Brier skill +0.03, n=400, t=2.0" is a real, defensible,
   falsifiable claim achievable in weeks.

**Where the self-deception lives:** treating tick rate as learning rate; treating
`LESSONS.md` length as accumulated knowledge; treating checker approval as validation (it
uses the same data as the maker — it is not independent); treating 30-day paper P&L as
evidence; treating a Sharpe-2 backtest as a discovery (with 3 years and 10,000 trials, that
is the *expected* noise result).

Replace the dashboard's "cycles completed" with **cumulative t² accrued** and **trials
consumed / trial budget**.

---

## 8. Primary sources

Statistical: Lo (2002) *The Statistics of Sharpe Ratios*; Bailey & López de Prado, *The
Deflated Sharpe Ratio* (SSRN 2460551); Bailey, Borwein, López de Prado & Zhu, *Pseudo-
Mathematics and Financial Charlatanism* (Notices AMS 61(5)) and *The Probability of Backtest
Overfitting* (JCF 20(4)); Harvey, Liu & Zhu (2016), RFS 29(1); Falck, Rej & Thesmar, *Why and
how systematic strategies decay* (arXiv 2105.01380); Grinold, *The Fundamental Law of Active
Management*.

LLM trading evidence: FINSABER (arXiv 2505.07078); Look-Ahead-Bench (arXiv 2601.13770);
KTD-Fin (arXiv 2605.28359); PolyBench (arXiv 2604.14199); *Execution Assumptions and
Reproducibility* (arXiv 2606.08285); *Paper Agents, Paper Gains* (arXiv 2605.29174);
TrustTrade (arXiv 2603.22567); AutoRedTrader (arXiv 2605.09185); TradeTrap (arXiv 2512.02261);
RD-Agent(Q); QuantaAlpha (arXiv 2602.07085); Alpha Arena (nof1.ai).

Tooling: Freqtrade lookahead/recursive analysis and protections; Nautilus Trader; QuantConnect
Lean Algorithm Framework; Microsoft Qlib + RD-Agent.
