# Follow-up research: brokers, concrete strategies, non-financial alternatives

**Date:** 2026-07-28
**Status:** Companion to `2026-07-28-asset-class-expansion.md`. That doc ranks
*asset classes*; this doc answers the follow-up questions "what international
broker actually works" and "what concretely do I trade on the Tier-1 venues."
**Method:** Three parallel research agents, each with a narrow brief that
avoided re-deriving the earlier doc's statistical framing.

---

## 1. International equity brokers

**Winner: Interactive Brokers (IBKR) via `ib_async` + IB Gateway (or IBeam)
in Docker.** The only broker that combines all three of: (a) accepts US
residents, (b) real programmatic API, (c) actual international coverage
(150+ markets, 34 countries — LSE, Xetra, Euronext, TSE, HKEX, ASX, and more).
Paper account is real, mirrors production, and shares the API surface with
live. Fits the existing `Bar`-based OHLCV architecture cleanly.

**Everyone else is out:**
- Saxo, Trading212, DEGIRO: won't accept US residents or lack an official API
- TradeStation, Tradier, Alpaca (main): US-only, no international
- Lightyear, eToro: no automation API
- Alpaca Europe: exists but B2B/fintech-partner only, not for individual US devs

**Cost to be aware of on IBKR:** market data subscriptions are per-exchange
per-month, API-gated. Budget $30–50/mo if you enable several venues (LSE ~£4,
Xetra ~€5, HKEX ~HKD 130, TSE ~¥1100). Use IBKR **Pro**, not Lite — Lite
disables API order types.

**Gotchas the agent flagged, verify before building against them:**
- Some venues may require a *funded* live account before paper trading unlocks
  the market data — test with paper orders into each target venue before
  wiring strategies
- Client Portal session times out ~24h; TWS Gateway needs restart windows;
  some 2FA methods (mobile push) don't automate cleanly
- Multi-currency positions accrue and need explicit `FXCONV` orders
- Corporate actions arrive as separate messages you must handle
- Per-exchange trading holiday calendars

---

## 2. Concrete strategies within Tier-1 venues

The earlier asset-class doc named Kalshi / Manifold / Deribit as top venues
but didn't say what to trade. This does.

### Kalshi (US-legal, real cash)

- **Weather markets vs NWS/GFS/ECMWF ensemble.** Published academic result
  (Diebold-Mariano test): market beats a naive model overall, but on
  *selectively traded* buckets the model beats the market with t = −2.77,
  p = 0.006, n = 17,275. Edge lives in trade selection, not raw forecasting.
  Capacity capped at ~$5–20K/day across the surface — thin cities have
  5–10¢ spreads.
- **Favorite-longshot bias via maker rebate.** Whelan (GWU/UCD 2025–26)
  documents statistically-significant maker returns from posting limit orders
  on the favorite side of binaries. Requires quote-level cancel/replace but
  not co-located latency; fits the maker/checker split you have. Bias
  compressed 60–70% after election-volume MMs arrived, but remains in
  non-headline markets (sports props, non-election politics).
- **Skip:** CPI/NFP scalping — sub-second execution around 8:30 ET.

### Polymarket (data source only for US persons)

Direct Polymarket is geoblocked; VPN violates ToS. Polymarket US via QCX
(launched Dec 2025) is the only US-legal execution path and needs full KYC.
- **Practical use for you: fair-value anchor for Kalshi quoting.** 73% of
  cross-venue arb is captured by <100ms bots so direct arb is dead, but the
  price signal remains valuable as a Bayesian prior when quoting Kalshi.
- Long-tail overpricing (short low-p tokens on shallow books) is a real
  documented edge (arXiv 2604.24366) but capped at $1–5K/market.

### Deribit (crypto derivatives, offshore)

**Critical caveat: Deribit ToS excludes US persons.** Same jurisdiction
problem you already work around with `binance.us`. Do not open a Deribit
account from the US.

If that constraint is solved (non-US entity, non-US residency, whatever):
- **Perpetual funding-rate carry.** Long spot, short perp when funding
  positive. Baseline ~11% APR from 0.01%/8h funding; realistic mid-single-
  digit net after fees. $100K sits well below the $500K/coin market-impact
  threshold. 1h-bar cadence is enough — fits your architecture natively.
  Currently ~50% arbed; edge is now in *timing* (max carry only when
  funding annualizes >15–20%).
- **Cash-and-carry basis trade.** Spot vs quarterly future. Convergence is
  arithmetic, not predictive. Front-month basis was 20%+ Nov 2024, ~10%
  May 2025; still positive-carry through 2026. $100K trivially fits.
- **Skip:** 0DTE gamma scalping and vol-surface trades — need dealer-flow
  data and sub-minute hedging, architecturally wrong for this system.

---

## 3. Non-financial alternatives

Only two categories survived the "actually worth building" filter. Most
alt-investment hype is trash for a solo automated operator.

- **Prediction market arb + LLM-driven news → prices (Kalshi + Polymarket US).**
  14 of the top-20 profitable Polymarket wallets are already bots. Legal for
  US persons on both sides post-Dec-2025. Events map cleanly to your `Bar`
  model, LLM ingests news to update priors, checker vetoes on liquidity, maker
  posts orders. **This is the closest fit to your existing loop-hedge
  architecture of anything the three agents surfaced.** ~2–3 weeks connector
  work. Realistic 10–30% annualized on small stakes, decaying as more LLM
  agents pile in.
- **DFS Pick'em optimizer (PrizePicks, Underdog).** These platforms set
  *proprietary* projections rather than sportsbook-derived lines, so
  model-driven mispricings survive longer than sportsbook arbs. Legal in
  most US states. Realistic 5–15% ROI at low volume. Semi-hostile API
  (scraping); ~3–4 weeks build.

**Real edges but painful:**
- 13F/insider/congressional replication (Pelosi disclosures: 827% cumulative
  over 10 years vs SPX ~3×) — slow-signal equity sleeve on IBKR/Alpaca; ~1 week
  build; treat as buy-and-hold rebalance, not intraday.
- 0DTE SPX credit spreads — needs options approval, $25K PDT, options plumbing
  you don't have.

**Traps (avoid):**
- Sportsbook arb (DK/FD/MGM) — books limit winning accounts to $5 max within
  ~20 bets
- Pinnacle API — closed July 2025
- Betfair Exchange — new Expert Fee (20% >£25K profit, 40% >£100K) kills
  systematic strategies at scale
- Nansen/Arkham copy-trade — research terminals, not routers
- Music/RE/art tokenization (Royal, Lofty, Masterworks) — buy-and-hold yield
  plays, no bot edge; Masterworks in business-model stress
- Sneakers/CSGO skins/domains — margins 10–25%, platform fees 15–20%; manual
  labor dressed up as arb
- Tokenized T-bills — 25–70bps over TreasuryDirect; cash management, not alpha

---

## 4. Synthesis: which to actually build next

Ordered by fit with what you already have and legal accessibility as a
US-resident solo dev:

**1. Kalshi weather + Polymarket-US integration.** Both agents 2 and 3
converged on this from independent briefs. Legal, fits the `Bar`+LLM
architecture, 3 weeks of connector work, symbol columns already widened for
it. Start with weather (deterministic 24h resolution, dozens of independent
symbols per day, cleanest research problem).

**2. IBKR paper for international equities *only if* you specifically want
non-US market exposure.** More engineering than Kalshi (session management,
market calendars, corporate actions, currency conversion) but the only
serious path if the goal is genuinely international.

**3. Deribit funding-carry desk — do NOT build this from the US.** Best
edge-per-engineering-hour on paper (arithmetic edge, right capacity, right
cadence) but the US-jurisdiction blocker is a hard stop. If you have or can
establish non-US residency, this is a strong pick.

**4. 13F/congressional replication** as a *background* slow-frequency sleeve
that runs alongside whatever you build first. ~1 week; no interference with
the fast loop.

**Skip:** everything else surfaced across the three reports.

---

## 5. What this doc does not cover

The earlier `2026-07-28-asset-class-expansion.md` §7 "The honest ceiling" and
§4 "What the field is actually doing" apply unchanged: published LLM
decision-making has negative results (Alpha Arena, PolyBench), your current
5m crypto config is arithmetically unprofitable regardless of asset class,
and the framing that survives scrutiny is "research platform for measuring
pipeline honesty" rather than "money-making system."

None of the venues surfaced here escape those caveats. What this doc adds is
narrower: given you're going to build something anyway, these are the
concrete paths worth engineering.
