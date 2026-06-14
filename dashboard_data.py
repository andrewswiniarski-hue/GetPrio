"""Data layer for the GetPrio coach dashboard.

Pure functions returning plain Python/dicts so they can be unit-tested without
Streamlit. The dashboard caches these; nothing here imports streamlit.
"""
import os

import psycopg2

from champions import champ_key

# Riot team_position -> the role label a coach actually uses.
POS_SHORT = {"TOP": "TOP", "JUNGLE": "JG", "MIDDLE": "MID",
             "BOTTOM": "BOT", "UTILITY": "SUP"}
SHORT_TO_POS = {v: k for k, v in POS_SHORT.items()}
ROLE_ORDER = ["TOP", "JG", "MID", "BOT", "SUP"]


def load_database_url() -> str:
    """Env first, then the gitignored .database_url (as the runner does)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".database_url")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                url = f.read().strip()
            os.environ["DATABASE_URL"] = url
    return url or ""


def connect():
    """Connect using the loaded URL directly (config.py captures DATABASE_URL
    at import time, which is too early when launched via `streamlit run`)."""
    url = load_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL not set and .database_url not found")
    return psycopg2.connect(url)


def current_patch_and_freshness(cur):
    cur.execute("SELECT patch, max(run_at) FROM latest_emergence GROUP BY 1")
    row = cur.fetchone()
    patch = row[0] if row else None
    cur.execute("SELECT max(day) FROM champ_daily_stats WHERE patch = %s",
                (patch,))
    last_day = cur.fetchone()[0]
    return patch, last_day


def watchlist(cur):
    """The emergence report (latest run) as a list of dicts, score desc."""
    cur.execute(
        """
        SELECT champion_id, champion_name, team_position, games,
               shrunk_wr, wr_lcb, pick_rate, pick_velocity, pro_soloq_games,
               score, novel, baseline_pick_rate
        FROM latest_emergence
        ORDER BY score DESC
        """
    )
    cols = ["champion_id", "champion_name", "team_position", "games",
            "shrunk_wr", "wr_lcb", "pick_rate", "pick_velocity",
            "pro_soloq_games", "score", "novel", "baseline_pick_rate"]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d["role"] = POS_SHORT.get(d["team_position"], d["team_position"])
        d["champ_key"] = champ_key(d["champion_name"])
        for k in ("shrunk_wr", "wr_lcb", "pick_rate", "pick_velocity",
                  "score", "baseline_pick_rate"):
            d[k] = float(d[k]) if d[k] is not None else None
        out.append(d)
    return out


def pro_logs(cur, patch):
    """(champ_id, pos) -> [{pro, team, date, win}] sorted recent-first."""
    cur.execute(
        """
        SELECT p.champion_id, coalesce(p.team_position, 'UNKNOWN'),
               pa.pro_name, pa.team, date(m.game_creation), p.win
        FROM participants p
        JOIN matches m USING (match_id)
        JOIN pro_accounts pa ON pa.puuid = p.puuid AND pa.active
        WHERE m.patch = %s AND m.game_duration_s >= 300
        ORDER BY date(m.game_creation) DESC
        """,
        (patch,),
    )
    out = {}
    for cid, pos, pro, team, day, win in cur.fetchall():
        out.setdefault((cid, pos), []).append(
            {"pro": pro, "team": team, "date": day, "win": win})
    return out


def pickrate_curves(cur, patch):
    """(champ_id, pos) -> [(day, pick_rate)] within the patch."""
    cur.execute(
        """
        SELECT champion_id, team_position, day,
               games::float / nullif(total_games_day, 0)
        FROM champ_daily_stats
        WHERE patch = %s
        ORDER BY day
        """,
        (patch,),
    )
    out = {}
    for cid, pos, day, pr in cur.fetchall():
        out.setdefault((cid, pos), []).append((day, pr or 0.0))
    return out


def stage_status(cur):
    """champ_key/pos -> {first, last, games, leagues} across all pro_picks."""
    cur.execute(
        """
        SELECT champ_key, team_position, min(game_date), max(game_date),
               count(*), array_agg(DISTINCT league)
        FROM pro_picks
        WHERE team_position IS NOT NULL
        GROUP BY 1, 2
        """
    )
    out = {}
    for k, pos, first, last, n, leagues in cur.fetchall():
        out[(k, pos)] = {"first": first, "last": last, "games": n,
                         "leagues": sorted(leagues)}
    return out
