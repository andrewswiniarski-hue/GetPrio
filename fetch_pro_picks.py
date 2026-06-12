"""Load pro-play champion picks into pro_picks (backtest ground truth).

Flow:  Tournaments (official primary, league/year)
       -> ScoreboardGames  (game id, date, patch per game)
       -> ScoreboardPlayers (champion + role per player per game)
       -> pro_picks (one row per pick, idempotent upsert)

Patch numbering: Leaguepedia records the official patch ("26.11") while the
warehouse uses gameVersion numbering ("16.11"). The majors diverged in 2025
(official 25.x == gameVersion 15.x), so majors >= 25 are mapped down by 10.

Run:  python fetch_pro_picks.py [--year 2026] [--leagues LCK LEC]
Reruns only add/refresh rows; safe to run daily after stage days.
"""
import argparse
import datetime as dt
import logging
import re

import db
from champions import champ_key
from fetch_pro_rosters import (LEAGUES, cargo_query, make_session,
                               season_tournaments)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CARGO_PAGE = 500

ROLE_TO_POSITION = {
    "top": "TOP",
    "jungle": "JUNGLE",
    "jng": "JUNGLE",
    "mid": "MIDDLE",
    "middle": "MIDDLE",
    "bot": "BOTTOM",
    "adc": "BOTTOM",
    "support": "UTILITY",
    "sup": "UTILITY",
}


def warehouse_patch(esports_patch: str) -> str | None:
    m = re.match(r"^\s*(\d+)\.(\d+)\s*$", esports_patch or "")
    if not m:
        return None
    major, minor = int(m.group(1)), int(m.group(2))
    if major >= 25:  # official year-based numbering started at 25.x (2025)
        major -= 10
    return f"{major}.{minor}"


def tournament_games(session, page: str) -> dict[str, dict]:
    """GameId -> {date, patch} for one tournament."""
    rows = cargo_query(
        session,
        tables="ScoreboardGames",
        fields="ScoreboardGames.GameId,ScoreboardGames.DateTime_UTC,"
               "ScoreboardGames.Patch",
        where=f'ScoreboardGames.OverviewPage="{page}"',
    )
    games: dict[str, dict] = {}
    for row in rows:
        game_id = row.get("GameId")
        # Cargo returns underscored fields with spaces in JSON keys.
        stamp = (row.get("DateTime UTC") or "").strip()
        if not game_id or not stamp:
            continue
        games[game_id] = {
            "date": stamp.split(" ")[0],
            "patch": warehouse_patch(row.get("Patch") or ""),
        }
    return games


def tournament_picks(session, page: str) -> list[dict]:
    """All ScoreboardPlayers rows for one tournament (offset-paginated)."""
    rows: list[dict] = []
    offset = 0
    while True:
        batch = cargo_query(
            session,
            tables="ScoreboardPlayers",
            fields="ScoreboardPlayers.GameId,ScoreboardPlayers.Link,"
                   "ScoreboardPlayers.Champion,ScoreboardPlayers.Role,"
                   "ScoreboardPlayers.Team",
            where=f'ScoreboardPlayers.OverviewPage="{page}"',
            offset=offset,
        )
        rows.extend(batch)
        if len(batch) < CARGO_PAGE:
            return rows
        offset += len(batch)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES),
                    choices=list(LEAGUES))
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    args = ap.parse_args()

    session = make_session()
    conn = db.get_conn()
    db.apply_schema(conn)

    total = unmatched_games = 0
    with conn.cursor() as cur:
        for league in args.leagues:
            league_value, _ = LEAGUES[league]
            tournaments = season_tournaments(session, league_value, args.year)
            if not tournaments:
                tournaments = season_tournaments(session, league_value,
                                                 args.year - 1)
            picks_in_league = 0
            for t in tournaments:
                games = tournament_games(session, t["OverviewPage"])
                if not games:
                    continue  # unplayed/future tournament
                for row in tournament_picks(session, t["OverviewPage"]):
                    game = games.get(row.get("GameId") or "")
                    champ = (row.get("Champion") or "").strip()
                    if not game or not champ:
                        unmatched_games += not game
                        continue
                    cur.execute(
                        """
                        INSERT INTO pro_picks (game_id, league, game_date,
                            patch, champ_key, team_position, player, team)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (game_id, champ_key) DO UPDATE SET
                            game_date     = EXCLUDED.game_date,
                            patch         = EXCLUDED.patch,
                            team_position = EXCLUDED.team_position,
                            player        = EXCLUDED.player,
                            team          = EXCLUDED.team
                        """,
                        (row["GameId"], league, game["date"], game["patch"],
                         champ_key(champ),
                         ROLE_TO_POSITION.get(
                             (row.get("Role") or "").strip().lower()),
                         row.get("Link"), row.get("Team")),
                    )
                    picks_in_league += 1
                log.info("%s / %s: %d games", league, t["Name"], len(games))
            total += picks_in_league
            log.info("%s: %d picks loaded", league, picks_in_league)
    conn.commit()
    conn.close()
    if unmatched_games:
        log.warning("%d player rows had no matching game header",
                    unmatched_games)
    log.info("Done: %d picks upserted", total)


if __name__ == "__main__":
    main()
