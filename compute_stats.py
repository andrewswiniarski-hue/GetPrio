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
    cur.execute("SELECT patch FROM matches ORDER BY game_creation DESC LIMIT 1")
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
            WHERE m.patch = %s
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
        WHERE m.patch = %s
        GROUP BY 1, 2, 3, 4, 6, dt.total_games
        """,
        (patch, patch),
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
            WHERE m.patch = %s
            GROUP BY 1, 2
            """,
            (patch,),
        )
        pro_games = {(cid, pos): n for cid, pos, n in cur.fetchall()}

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

            # Composite score: above-average shrunken WR, scaled by how fast
            # the pick is growing and whether pros are labbing it.
            # Weights are a starting point — tune against backtests.
            score = (
                (shrunk - prior_mean) * 100          # WR edge in points
                + max(velocity, 0) * 5000            # growth term
                + math.log1p(pro_n) * 1.5            # pro-lab term
            )

            cur.execute(
                """
                INSERT INTO emergence_scores
                    (patch, champion_id, champion_name, team_position, games,
                     raw_wr, shrunk_wr, wr_lcb, pick_rate, pick_velocity,
                     pro_soloq_games, score)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (patch, champ_id, champ_name, pos, games,
                 round(raw_wr, 4), round(shrunk, 4), round(lcb, 4),
                 round(pick_rate, 5), round(velocity, 5), pro_n,
                 round(score, 4)),
            )
            inserted += 1

        conn.commit()

        cur.execute(
            """
            SELECT champion_name, team_position, games, shrunk_wr,
                   pick_velocity, pro_soloq_games, score
            FROM latest_emergence
            ORDER BY score DESC
            LIMIT 15
            """
        )
        log.info("Top emerging picks, patch %s:", patch)
        for name, pos, g, wr, vel, pro_n, sc in cur.fetchall():
            log.info("  %-14s %-8s g=%-6d wr=%.3f vel=%+.5f pro=%-3d score=%.2f",
                     name, pos, g, wr, vel, pro_n, sc)

    log.info("Wrote %d emergence rows", inserted)
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", default=None)
    args = ap.parse_args()
    main(args.patch)
