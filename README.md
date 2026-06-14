# GetPrio

**Early-warning radar for champion draft priority.** GetPrio watches high-elo
ranked solo queue and flags emerging champion picks *before* they reach
professional drafts — combining pick-rate velocity, shrunken win-rate edge,
and whether pro players are already practicing the pick on their solo-queue
accounts.

> Soloq win rates alone don't predict pro viability. The signal is a pick
> that is **rising**, **winning more than its sample suggests**, and
> **showing up on pro players' practice accounts** — all at once.

## How it works

```
Riot API (League-V4, Match-V5)          Leaguepedia (Cargo API)
        │                                       │
        ▼                                       ▼
 1. fetch_ladder.py    Master+ ladder    fetch_pro_rosters.py   pro rosters +
 2. fetch_matches.py   raw match JSON    load_pro_accounts.py   soloq accounts
 3. parse_matches.py   facts tables      fetch_pro_picks.py     stage picks
        │                                       │               (ground truth)
        ▼                                       ▼
 4. compute_stats.py ──────────────► emergence_scores
                                     (daily, per champion/role/patch)
```

- **Warehouse**: Postgres. Raw match JSON lands immutably (`matches_raw`),
  parses into fact tables, aggregates to day grain (`champ_daily_stats`) —
  day granularity is what makes pick-rate *velocity* computable.
- **Emergence score** per champion/role: win-rate edge (empirical-Bayes
  shrunken) + within-patch pick-rate velocity (OLS slope over daily pick
  rates) + log-scaled count of games on tracked pro accounts.
- **Pro layer**: rosters and solo-queue account IDs auto-harvested from
  Leaguepedia, resolved and validated through Riot Account-V1.
- **Ground truth**: every champion pick in pro stage games, for backtesting
  detection lead time ("flagged N days before first pro appearance").
- **Scheduling**: a Windows Task Scheduler job runs the ingest chain daily.

## Sample output

```
Top emerging picks, patch 16.12:
  Rumble         TOP      g=123  wr=0.506  vel=+0.08581  pro=1  score=430.79
  Varus          BOTTOM   g=130  wr=0.504  vel=+0.08259  pro=0  score=413.31
  Yasuo          TOP      g=56   wr=0.489  vel=+0.07454  pro=0  score=371.63
  Kaisa          BOTTOM   g=194  wr=0.513  vel=+0.07118  pro=0  score=357.28
```

```sql
SELECT * FROM latest_emergence ORDER BY score DESC LIMIT 25;
```

## Setup

```bash
createdb lol_draft_tool
pip install -r requirements.txt

export RIOT_API_KEY="RGAPI-..."
export DATABASE_URL="postgresql://user:pass@localhost:5432/lol_draft_tool"
```

For unattended/scheduled runs, secrets can instead live in gitignored files
next to the scripts: `.riot_key` and `.database_url`.

## Run

```bash
python fetch_ladder.py                  # refresh Master+ player pool (applies schema.sql on first run)
python fetch_matches.py --limit 200     # bounded raw-match pull; resumes where it left off
python parse_matches.py                 # raw JSON -> fact tables
python compute_stats.py                 # rebuild aggregates + emergence scores

python fetch_pro_rosters.py             # regenerate pro account seed CSV from Leaguepedia
python load_pro_accounts.py pro_accounts_seed.csv
```

Daily automation: `register_daily_task.ps1` registers `daily_run.ps1` in
Windows Task Scheduler (ladder → matches → parse → stats, with logging and
a loud failure if the API key has expired).

Coach dashboard:

```bash
streamlit run dashboard.py    # novel-pick watchlist by role, per-pick briefing
```

A busy-coach view of `latest_emergence`: emerging picks ranked by score with
the key evidence visible up front (novel/rising badges, win rate + sample,
which pros are practicing it, stage status), and a one-click Briefing per pick
(pick-rate trend, pro game log, stage detail).

## Tests

```bash
python -m unittest discover -s tests -t .
```

Zero-dependency `unittest` suite over the load-bearing pure logic: champion
name normalization, esports↔gameVersion patch mapping, the emergence-score
math (composite score, Wilson LCB, pick velocity), and the SoloqueueIds
wikitext parser.

## Design notes

- **`matches_raw` is immutable.** New parsed fields are backfilled by
  re-running the parser over stored JSON — never by re-fetching.
- **Routing is centralized**: League-V4/Summoner-V4 use platform routing
  (kr, euw1, na1), Match-V5 uses regional routing (asia, europe, americas);
  the mapping lives in `config.py` only.
- **Rate limiting**: every Riot call goes through a token-bucket limiter
  honoring 429 Retry-After, with backoff on 5xx. Daily volume is bounded
  and batched.
- **Score weights are starting values.** The backtest harness (in progress)
  replays past patches against pro-play ground truth and tunes weights for
  detection lead time.

## Status

Running daily against KR Master+ (10k+ matches ingested); win rates
validated against public reference data; 49 LCK pros' accounts tracked and
joining live. In progress: backtest harness over pro-play ground truth.
Planned: multi-region ingest, matchup matrices, dashboard.

---

GetPrio is not endorsed by Riot Games and does not reflect the views or
opinions of Riot Games or anyone officially involved in producing or
managing Riot Games properties. Riot Games and all associated properties
are trademarks or registered trademarks of Riot Games, Inc.
