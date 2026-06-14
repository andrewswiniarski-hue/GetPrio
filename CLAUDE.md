# CLAUDE.md — GetPrio (LoL SoloQ Meta-Detection Pipeline)

## What this project is
**GetPrio**: an analytics pipeline that detects emerging champion picks in
high-elo soloq *before* they appear in professional play — a proactive
drafting edge for a pro LoL team. Core thesis: soloq win rates alone don't predict pro viability;
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
python fetch_pro_rosters.py            # regenerate pro_accounts_seed.csv from Leaguepedia (slow: rate-limit backoffs)
python load_pro_accounts.py pro_accounts_seed.csv   # resolve + load; rerun after regen or roster changes
python fetch_pro_picks.py              # refresh pro-play ground truth (pro_picks) from Leaguepedia scoreboards
python compute_stats.py [--patch 25.11]

streamlit run dashboard.py             # coach dashboard over latest_emergence (reads .database_url if env unset)
python backtest.py                     # replay as-of rankings vs pro_picks (recall + lead time, novel-only view)
python -m unittest discover -s tests -t .   # pure-logic test suite (zero deps; champ_key, patch maps, scoring, roster parsing)
```

Scheduled ingest: `daily_run.ps1` runs the 4-step chain daily via Task
Scheduler ("LoLDraftTool Daily Ingest", 07:00, registered by
`register_daily_task.ps1`). Secrets load from gitignored `.riot_key` and
`.database_url`; an expired dev key fails the pre-flight loudly (one missed
day, not a wedged scheduler). Logs in `logs/` (last 14 kept). The task runs
on battery (the laptop is usually unplugged at 07:00). Each run writes a
heartbeat: `logs/last_run.json` (result + freshness) and, on failure, a
`logs/LAST_RUN_FAILED.txt` marker cleared on the next success — so a failed
or green-but-stale run is visible without reading the log.

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
- **Patch = first two components of gameVersion** ("16.11.700.100" → "16.11").
  Aborted games can have an *empty* gameVersion and game_duration_s = 0 with no
  participants; the remake filter handles them.
- Secrets only via env vars. Never commit keys. For long-running loops the dev
  key can live in the gitignored `.riot_key` file (read into the env at launch).

## Current state
- ✅ Running against the real Riot API (KR): 10k+ matches / 100k participants
  ingested; real payloads parse clean (edge cases are all sub-300s games)
- ✅ Remake filter: `compute_stats.MIN_DURATION_S = 300` excludes remakes and
  aborted games from `champ_daily_stats`, pro-games counts, and patch selection
- ✅ Win rates validated vs Lolalytics KR Master+ patch 16.11: 7/9 champs within
  sampling error. Caveat: player sample is Challenger-heavy (fetch order), so a
  ~1pt systematic offset vs Master-dominated public data is expected
- ✅ `load_pro_accounts.py` resolves blank puuids from riot_id via Account-V1;
  join verified end-to-end (seeded account's games light up `pro_soloq_games`)
- ✅ `fetch_pro_rosters.py` auto-builds the seed CSV from Leaguepedia's Cargo
  API (LCK+LEC rosters, soloq IDs incl. default-tag guesses for legacy names).
  Loaded: 66 kr accounts / 49 pros; 696 pro games visible in the warehouse.
  Hand-edits to the CSV are clobbered by regeneration — re-add manual rows or
  load them from a second CSV. euw1/na1 rows skip until those platforms return
- ✅ Backtest ground truth loaded: `fetch_pro_picks.py` → `pro_picks` (5,870
  picks / 587 LCK+LEC stage games of 2026, all champion keys join the
  warehouse via `champions.champ_key`). Esports patch numbering maps to
  gameVersion: official "26.11" == warehouse "16.11" (majors ≥25 minus 10).
  Leaguepedia auth: bot creds in gitignored `.leaguepedia_creds` bypass the
  harsh anonymous IP limits (anonymous penalties can last hours)
- ✅ `backtest.py` runs as-of replay vs ground truth. First daily ingest ran
  unattended (2026-06-13); warehouse holds one complete patch (16.11, 14 days)
  plus live 16.12. Backtest v1 is recall+lead only and overstates the signal
  (see roadmap #3); the daily scheduler is now accumulating the patches needed
  to make tuning meaningful
- ⚠️ `config.PLATFORMS` temporarily kr-only; restore euw1/na1 once a production
  key replaces the dev key (dev key expires every 24h)
- ✅ Coach dashboard v1 (`dashboard.py` + `dashboard_data.py`, Streamlit):
  novel-only watchlist by role, glanceable cards (badges, WR+sample, pro names
  inline, stage status), one-click Briefing (pick-rate curve, pro game log,
  stage detail). Data layer is streamlit-free so it's testable. Verified
  rendering live against 16.12. `dashboard_data.connect()` reads the URL
  directly (config.py captures DATABASE_URL too early for `streamlit run`)
- ✅ Pipeline hardening: scheduler runs on battery; daily heartbeat
  (`logs/last_run.json` + `LAST_RUN_FAILED.txt` marker) + post-run freshness
  guard; 30-test zero-dep `unittest` suite over the load-bearing pure logic
  (`champ_key`, `warehouse_patch`/`patch_sort_key`, `composite_score`/
  `wilson_lcb`/`pick_velocity`, `parse_soloqueue_ids`)
- ❌ Matchup matrices unbuilt; backtest precision side + weight tuning pending
  more accumulated patches

## Known tuning knobs (set crudely, tune via backtest)
- `compute_stats.PRIOR_STRENGTH = 400` (shrinkage pseudo-games)
- `compute_stats.MIN_GAMES = 20`
- Composite score weights: WR-edge ×100, velocity ×5000, log1p(pro_games) ×1.5.
  The *correct* weights come from backtesting: replay past patches, optimize for
  lead time on picks that later appeared in pro play (ground truth: Oracle's
  Elixir match data, free CSV downloads).

## Roadmap (priority order)
1. ~~Pro-account seed CSV~~ done via `fetch_pro_rosters.py`; optional polish:
   hand-fill the ~33 players Leaguepedia has no usable soloq IDs for
2. Production API key; then re-enable euw1/na1 in `config.PLATFORMS`
3. Backtest harness: `backtest.py` replays as-of daily rankings vs `pro_picks`
   (recall + lead time), with a novel-only view. Novelty baseline DONE
   (`NOVELTY_PICKRATE`, prior-patch pick share) — live `novel` flag on the
   emergence report works now; backtest novel-only metrics activate once a
   patch has both a prior-patch soloq baseline and pro_picks. Remaining: a
   precision side (do flagged picks actually reach stage?), then weight
   tuning — both need a few more accumulated patches to be meaningful
4. Matchup/synergy matrices (table `matchup_stats` already exists, unused);
   best surfaced inside the dashboard's per-pick Briefing, champion-centric
   (not a full matrix), shrunk hard with sample sizes shown
5. ~~Streamlit dashboard over `latest_emergence`~~ v1 DONE (`dashboard.py`);
   polish: per-pick matchup drill-down, "we flag N days early" credibility
   banner once the backtest validates
6. ~~Scheduling~~ done (`daily_run.ps1` via Task Scheduler)

## Working style for Claude Code sessions
- One verifiable objective per session; run the pipeline to prove changes work
- Prefer small diffs over rewrites; the module boundaries are intentional
- When touching SQL, test against the live DB, not by inspection
- Rate-limit budget is precious on a dev key — use --limit aggressively while testing
