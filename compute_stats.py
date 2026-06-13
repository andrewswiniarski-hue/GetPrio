"""Step 4: The stats layer.

For the current patch, per champion+role:
  1. Empirical-Bayes (beta-binomial) shrinkage of win rate
  2. Lower confidence bound (Wilson) as the ranking-safe estimate
  3. Pick-rate velocity = OLS slope of daily pick rate within the patch
  4. Pro-soloq signal = games on tracked pro accounts this patch
  5. Composite emergence score

Run after parse_matches:  python compute_stats.py --patch 25.11
Omit --patch to use the most recent patch in the warehouse.
"""
import argparse
import logging
import math

import numpy as np

import db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MIN_GAMES = 20          # ignore ultra-sparse champ/role cells entirely
PRIOR_STRENGTH = 400    # pseudo-games for shrinkage; tune via backtest
MIN_DURATION_S = 300    # exclude remakes/early surrenders from all stats

# Composite-score weights. Crude starting values; tune via backtest.py.
WR_EDGE_WEIGHT = 100    # per point of shrunken WR above the patch mean
VELOCITY_WEIGHT = 5000  # per unit of daily pick-rate slope
PRO_WEIGHT = 1.5        # per log1p(pro soloq game)

# Novelty: a pick is "novel" (genuinely emerging, vs. an already-known meta
# pick) if its share of all picks in the *prior* patch was below this. The
# coach-facing value is the novel list — it strips perennial picks a coach
# already plans around. Crude starting value; tune via backtest.
NOVELTY_PICKRATE = 0.005


def patch_sort_key(patch: str) -> tuple:
    """Numeric patch ordering so '16.9' < '16.11' (lexical sort breaks this)."""
    return tuple(int(x) for x in patch.split(".") if x.isdigit())


def prior_patch_of(cur, patch: str) -> str | None:
    """The patch immediately before `patch` that has daily stats, or None."""
    cur.execute("SELECT DISTINCT patch FROM champ_daily_stats WHERE patch <> ''")
    earlier = [p for (p,) in cur.fetchall()
               if patch_sort_key(p) < patch_sort_key(patch)]
    return max(earlier, key=patch_sort_key) if earlier else None


def baseline_pick_rates(cur, prior: str | None) -> dict:
    """(champ_id, pos) -> share of all picks in the prior patch. {} if none."""
    if not prior:
        return {}
    cur.execute(
        """
        WITH cell AS (
            SELECT champion_id, team_position, sum(games) AS g
            FROM champ_daily_stats WHERE patch = %s GROUP BY 1, 2
        )
        SELECT champion_id, team_position,
               g::float / sum(g) OVER ()
        FROM cell
        """,
        (prior,),
    )
    return {(cid, pos): pr for cid, pos, pr in cur.fetchall()}


def composite_score(shrunk_wr: float, prior_mean: float, velocity: float,
                    pro_n: int, wr_w: float = WR_EDGE_WEIGHT,
                    vel_w: float = VELOCITY_WEIGHT,
                    pro_w: float = PRO_WEIGHT) -> float:
    """Emergence score: above-average shrunken WR, scaled by how fast the
    pick is growing and whether pros are labbing it. Weights are injectable
    so the backtest can sweep them without forking the formula."""
    return (
        (shrunk_wr - prior_mean) * wr_w     # WR edge in points
        + max(velocity, 0.0) * vel_w        # growth term
        + math.log1p(pro_n) * pro_w         # pro-lab term
    )


def wilson_lcb(wins: int, games: int, z: float = 1.96) -> float:
    """Wilson score interval lower bound."""
    if games == 0:
        return 0.0
    p = wins / games
    denom = 1 + z * z / games
    centre = p + z * z / (2 * games)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * games)) / games)
    return (centre - margin) / denom


def latest_patch(cur) -> str:
    # Aborted games can carry an empty gameVersion (and duration 0);
    # never let them drive patch selection.
    cur.execute(
        """
        SELECT patch FROM matches
        WHERE patch <> '' AND game_duration_s >= %s
        ORDER BY game_creation DESC LIMIT 1
        """,
        (MIN_DURATION_S,),
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit("No parsed matches in warehouse")
    return row[0]


def rebuild_daily_stats(cur, patch: str) -> None:
    """Rebuild champ_daily_stats for one patch from participants."""
    cur.execute("DELETE FROM champ_daily_stats WHERE patch = %s", (patch,))
    cur.execute(
        """
        WITH day_totals AS (
            SELECT date(m.game_creation) AS day, m.platform,
                   count(DISTINCT m.match_id) AS total_games
            FROM matches m
            WHERE m.patch = %s AND m.game_duration_s >= %s
            GROUP BY 1, 2
        )
        INSERT INTO champ_daily_stats
            (day, patch, platform, champion_id, champion_name,
             team_position, games, wins, total_games_day)
        SELECT date(m.game_creation), m.patch, m.platform,
               p.champion_id, max(p.champion_name),
               coalesce(p.team_position, 'UNKNOWN'),
               count(*), count(*) FILTER (WHERE p.win),
               dt.total_games
        FROM participants p
        JOIN matches m USING (match_id)
        JOIN day_totals dt
          ON dt.day = date(m.game_creation) AND dt.platform = m.platform
        WHERE m.patch = %s AND m.game_duration_s >= %s
        GROUP BY 1, 2, 3, 4, 6, dt.total_games
        """,
        (patch, MIN_DURATION_S, patch, MIN_DURATION_S),
    )


def pick_velocity(days: list[int], rates: list[float]) -> float:
    """OLS slope of pick rate vs day index. Needs >= 3 days."""
    if len(days) < 3:
        return 0.0
    x, y = np.array(days, dtype=float), np.array(rates, dtype=float)
    x -= x.mean()
    denom = (x ** 2).sum()
    return float((x * y).sum() / denom) if denom else 0.0


def main(patch: str | None) -> None:
    conn = db.get_conn()
    with conn.cursor() as cur:
        patch = patch or latest_patch(cur)
        log.info("Computing stats for patch %s", patch)

        rebuild_daily_stats(cur, patch)
        conn.commit()

        # ---- pull champ/role aggregates with daily series ----
        cur.execute(
            """
            SELECT champion_id, max(champion_name), team_position,
                   sum(games)::int, sum(wins)::int,
                   array_agg(day ORDER BY day),
                   array_agg(games::float / nullif(total_games_day,0)
                             ORDER BY day)
            FROM champ_daily_stats
            WHERE patch = %s
            GROUP BY champion_id, team_position
            """,
            (patch,),
        )
        rows = cur.fetchall()

        # global prior mean = overall win rate (~0.5 by construction in soloq,
        # but compute it anyway per patch)
        total_games = sum(r[3] for r in rows) or 1
        total_wins = sum(r[4] for r in rows)
        prior_mean = total_wins / total_games

        # patch-wide total games per day for pick rate denominator already
        # baked into the daily series.

        # ---- pro soloq games per champ this patch ----
        cur.execute(
            """
            SELECT p.champion_id, coalesce(p.team_position,'UNKNOWN'), count(*)
            FROM participants p
            JOIN matches m USING (match_id)
            JOIN pro_accounts pa ON pa.puuid = p.puuid AND pa.active
            WHERE m.patch = %s AND m.game_duration_s >= %s
            GROUP BY 1, 2
            """,
            (patch, MIN_DURATION_S),
        )
        pro_games = {(cid, pos): n for cid, pos, n in cur.fetchall()}

        # ---- novelty baseline: pick share in the prior patch ----
        prior = prior_patch_of(cur, patch)
        baseline = baseline_pick_rates(cur, prior)
        if prior:
            log.info("Novelty baseline: prior patch %s (%d known cells)",
                     prior, len(baseline))
        else:
            log.info("Novelty baseline: none (no prior patch in warehouse); "
                     "novel flag will be NULL")

        inserted = 0
        for (champ_id, champ_name, pos, games, wins,
             day_arr, rate_arr) in rows:
            if games < MIN_GAMES or pos == "UNKNOWN":
                continue

            raw_wr = wins / games
            shrunk = ((wins + PRIOR_STRENGTH * prior_mean)
                      / (games + PRIOR_STRENGTH))
            lcb = wilson_lcb(wins, games)
            pick_rate = games / total_games

            day_idx = [(d - day_arr[0]).days for d in day_arr]
            velocity = pick_velocity(day_idx, [r or 0.0 for r in rate_arr])

            pro_n = pro_games.get((champ_id, pos), 0)

            score = composite_score(shrunk, prior_mean, velocity, pro_n)

            base_pr = baseline.get((champ_id, pos), 0.0)
            # None when we have no prior patch to judge against.
            novel = None if not prior else base_pr < NOVELTY_PICKRATE

            cur.execute(
                """
                INSERT INTO emergence_scores
                    (patch, champion_id, champion_name, team_position, games,
                     raw_wr, shrunk_wr, wr_lcb, pick_rate, pick_velocity,
                     pro_soloq_games, score, novel, baseline_pick_rate)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (patch, champ_id, champ_name, pos, games,
                 round(raw_wr, 4), round(shrunk, 4), round(lcb, 4),
                 round(pick_rate, 5), round(velocity, 5), pro_n,
                 round(score, 4), novel, round(base_pr, 5)),
            )
            inserted += 1

        conn.commit()

        def top_list(label, novel_only):
            where = "WHERE novel" if novel_only else ""
            cur.execute(
                f"""
                SELECT champion_name, team_position, games, shrunk_wr,
                       pick_velocity, pro_soloq_games, score, novel
                FROM latest_emergence {where}
                ORDER BY score DESC LIMIT 15
                """
            )
            log.info(label)
            for name, pos, g, wr, vel, pro_n, sc, nov in cur.fetchall():
                flag = "NEW" if nov else ("   " if nov is False else " ? ")
                log.info("  [%s] %-14s %-8s g=%-6d wr=%.3f vel=%+.5f "
                         "pro=%-3d score=%.2f",
                         flag, name, pos, g, wr, vel, pro_n, sc)

        top_list(f"Top emerging picks, patch {patch}:", novel_only=False)
        if prior:
            top_list(f"Top NOVEL emerging picks, patch {patch} (vs {prior}) "
                     f"— the coach view:", novel_only=True)

    log.info("Wrote %d emergence rows", inserted)
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", default=None)
    args = ap.parse_args()
    main(args.patch)
