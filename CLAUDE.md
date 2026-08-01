# loop-hedge — session context

## Live deployment (DigitalOcean)

- **Public IP:** 159.223.99.2 (US region — Binance geo-blocks; ingester uses `api.binance.us`)
- **Private IP:** 10.116.0.2 (DO VPC)
- **User:** `loophedge` (root SSH is disabled; use `sudo` when needed)
- **Repo path on droplet:** `~/loop-hedge` (i.e. `/home/loophedge/loop-hedge`)
- **SSH key:** `~/.ssh/digitaloceanvps` on the developer's laptop

### SSH

Always pass `-i` and `-o IdentitiesOnly=yes` — SSH will offer other keys first and hit the per-IP rate limiter otherwise, which locks you out for ~10 min.

```bash
ssh -i ~/.ssh/digitaloceanvps -o IdentitiesOnly=yes loophedge@159.223.99.2
```

Or set up `~/.ssh/config` on your laptop once:
```
Host loophedge
  HostName 159.223.99.2
  User loophedge
  IdentityFile ~/.ssh/digitaloceanvps
  IdentitiesOnly yes
```
Then `ssh loophedge` works.

**If SSH stops accepting your key:** you probably hit fail2ban / rate limit. Wait 10 min, or use DO panel → droplet → **Recovery Console** (needs root temp password from a Reset Root Password email). Do NOT use "Launch Droplet Console" — it's SSH-based and won't work when SSH is broken.

### Dashboard

Bound to `127.0.0.1:8000` on the droplet — reach it via SSH tunnel from your laptop:
```bash
ssh -i ~/.ssh/digitaloceanvps -o IdentitiesOnly=yes -L 8000:localhost:8000 loophedge@159.223.99.2
# then open http://localhost:8000
```

## Common droplet commands

Run from `~/loop-hedge` on the droplet:

```bash
# service status
docker compose ps

# tail service logs
docker compose logs -f --tail=50 strategy-genesis-agent checker-agent maker-agent

# grep agent token usage
docker compose logs --tail=200 strategy-genesis-agent 2>&1 | grep agent-usage

# postgres shell
docker compose exec postgres psql -U loophedge -d loophedge

# strategies summary
docker compose exec -T postgres psql -U loophedge -d loophedge -c "SELECT status, COUNT(*) FROM strategies GROUP BY status;"

# bars sanity
docker compose exec -T postgres psql -U loophedge -d loophedge -c "SELECT symbol, COUNT(*), MAX(ts) FROM bars GROUP BY symbol;"

# run alembic migrations (override entrypoint because CLI expects service name)
docker compose run --rm --entrypoint alembic data-ingestor upgrade head

# rebuild after code change
git pull && docker compose --profile build-only build loophedge-base && docker compose up -d --force-recreate

# restart just the agents
docker compose up -d --force-recreate strategy-genesis-agent checker-agent maker-agent
```

## Secrets

Live in `~/loop-hedge/.env` on the droplet — never in git. Notable keys:
- `POSTGRES_PASSWORD` — generated at first boot with `openssl rand -base64 32`
- `ANTHROPIC_API_KEY` — for the agent services
- `BINANCE_API_BASE=https://api.binance.us` — Binance geo-blocks the DO US region
- `LIVE_VENUE=simulator` (default; `binance_testnet` requires keys)

## Known gotchas

- **Container filesystem ownership.** Containers run as root; host is `loophedge`. `SkillsRepo` handles this via `git config --global --add safe.directory`, but ad-hoc git operations from the host may see permission errors on files the container wrote. Fix: `sudo chown -R loophedge:loophedge skills/` if it happens.
- **Bind-mounted `skills/`.** Contains a nested `.git` repo — treat as separate from the main repo. Never `git add skills/.git`.
- **`skills/strategies/active/`** — files here may drift from DB `status='active'` if a git stash was ever done. If DB says active but `ls` says empty, the files are in a stash; either `git stash pop` or SQL-retire the phantoms.
- **Genesis proposes every 4h**, checker sweeps every 30 min, maker ticks every 15 min. Maker only calls the LLM when at least one strategy is active.
- **Kalshi/Polymarket work is in progress.** See `docs/research/2026-07-28-*.md` for the roadmap. Cycle 1 (Contract lifecycle + Kalshi ingester + Open-Meteo + backtest + agent-loop integration) is being designed but not yet implemented.

## Timeframe migration (1h bars) — as of 2026-08-01

**Change:** Migrated from 5m to 1h bars to achieve profitable strategy discovery.

**Rationale:** At 5m with 30bp round-trip costs, strategies need IC ≥ 2.44 to break even (impossible; IC is a correlation, max 1.0). At 1h, breakeven IC drops to 0.70 (achievable with realistic signal quality). See `docs/research/2026-07-28-asset-class-expansion.md` §1 for the math.

**What changed on the VPS:**
- `.env`: `BAR_TIMEFRAME=5m` → `BAR_TIMEFRAME=1h`
- Backfilled 8,760 bars (365 days) of 1h OHLCV from Binance `api.binance.us`
- Data-ingestor, genesis, checker restarted

**To re-run the backfill** (if bars are lost):
```bash
docker compose run --rm --entrypoint python data-ingestor scripts/backfill_historical_bars.py \
  --symbol BTCUSDT --timeframe 1h --days 365 --base-url https://api.binance.us
```

**What to expect:** Genesis's next proposals (every 4h from restart) will be 1h strategies. Existing 5m strategies in `pending` will be re-evaluated by checker on 1h data and likely still fail (they were optimized for micro-scale moves). New strategies will have room to work.

## Where to look for context

- `docs/research/2026-07-28-asset-class-expansion.md` — 623-line research doc: statistical framing, promotion-gate math, why 1-5m crypto is arithmetically unprofitable at retail cost, ranked venue recommendations
- `docs/research/2026-07-28-followup-brokers-and-strategies.md` — followup: IBKR for international equities, Kalshi weather concrete strategies, non-financial alternatives assessed honestly
- `docs/superpowers/specs/` — design specs for feature builds
- `docs/superpowers/plans/` — implementation plans

## Testing

```bash
.venv/bin/pytest tests/ -q
```

92 tests pass as of this writing. Two skipped (`test_checker.py`, `test_genesis.py`) require `ANTHROPIC_LIVE_RECORD=1` to record VCR cassettes against the real API.
