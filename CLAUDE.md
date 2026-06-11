# CLAUDE.md — LoL SoloQ Meta-Detection Pipeline

## What this project is
An analytics pipeline that detects emerging champion picks in high-elo soloq
*before* they appear in professional play — a proactive drafting edge for a
pro LoL team. Core thesis: soloq win rates alone don't predict pro viability;
the signal is **pick-rate velocity + shrunken win-rate edge + pro players
labbing the pick on their soloq accounts**.

## Architecture (4-step batch pipeline)
1. `fetch_ladder.py` — League-V4 apex tiers (Master+) per platform → `players`
2. `fetch_matches.py` — Match-V5 ids + raw JSON per player → `matches_raw` (immutable JSONB landing zone)
3. `parse_matches.py` — raw JSON → `matches` + `participants` (incl. lane-opponent resolution)
4. `compute_stats.py` — rebuilds `champ_daily_stats`, writes `emergence_scores`

Query the current report: `SELECT * FROM latest_emergence ORDER BY score DESC;`

## Commands
```bash
pip install -r requirements.txt
export RIOT_API_KEY="RGAPI-..."
export DATABASE_URL="postgresql://localhost:5432/lolmeta"

python fetch_ladder.py                 # daily; applies schema.sql on first run
python fetch_matches.py --limit 200    # frequent; bounded per run, resumes via players.last_match_pull
python parse_matches.py                # after each fetch batch
python load_pro_accounts.py seed.csv   # when pro account list changes
python compute_stats.py [--patch 25.11]
```

## Critical conventions — do not break these
- **Routing split**: League-V4/Summoner-V4 use *platform* routing (kr, euw1, na1);
  Match-V5 uses *regional* routing (asia, europe, americas). The mapping lives in
  `config.PLATFORMS`. Never hardcode hosts elsewhere.
- **`matches_raw` is immutable.** Never UPDATE payloads. To add a parsed field:
  add the column, extend `parse_matches.parse_one`, then
  `UPDATE matches_raw SET processed = FALSE` and re-run the parser. Never re-fetch
  from the API for data we already have.
- **Day-level aggregates are load-bearing.** `champ_daily_stats` exists so
  pick-rate *velocity* (OLS slope within a patch) can be computed. Don't collapse
  it to patch-level totals.
- **All API calls go through `riot_api.RiotClient`** (token-bucket limiter +
  429 Retry-After + 5xx backoff). Never call requests directly.
- **Patch = first two components of gameVersion** ("25.11.700.100" → "25.11").
- Secrets only via env vars. Never commit keys.

## Current state
- ✅ Schema validated against live Postgres 16
- ✅ End-to-end smoke-tested with synthetic data (300 matches → parse → stats;
  lane opponents resolved, pro-account join verified)
- ❌ Never run against the real Riot API — expect payload edge cases
- ❌ No remake filter: games with gameDuration < ~300s (remakes/early surrenders)
  currently pollute stats. Fix in parse or filter in aggregation. **High priority.**
- ❌ No tests beyond the smoke test; no scheduler; no dashboard

## Known tuning knobs (set crudely, tune via backtest)
- `compute_stats.PRIOR_STRENGTH = 400` (shrinkage pseudo-games)
- `compute_stats.MIN_GAMES = 20`
- Composite score weights: WR-edge ×100, velocity ×5000, log1p(pro_games) ×1.5.
  The *correct* weights come from backtesting: replay past patches, optimize for
  lead time on picks that later appeared in pro play (ground truth: Oracle's
  Elixir match data, free CSV downloads).

## Roadmap (priority order)
1. Run against real API (KR, small limits), fix payload edge cases, add remake filter
2. Validate win rates vs. Lolalytics/U.GG Master+ for ~10 champs (±1–2pts = pass)
3. Pro-account seed CSV (start ~50 LCK/LEC accounts) + verify pro_soloq_games signal
4. Backtest harness over previous patches; tune score weights for lead time
5. Matchup/synergy matrices (table `matchup_stats` already exists, unused)
6. Streamlit dashboard over `latest_emergence`
7. Scheduling (cron is fine; Airflow only if this grows)

## Working style for Claude Code sessions
- One verifiable objective per session; run the pipeline to prove changes work
- Prefer small diffs over rewrites; the module boundaries are intentional
- When touching SQL, test against the live DB, not by inspection
- Rate-limit budget is precious on a dev key — use --limit aggressively while testing
