"""Thin DB helpers shared by the ingestion scripts."""
import logging

import psycopg2
import psycopg2.extras

import config

log = logging.getLogger(__name__)


def get_conn():
    return psycopg2.connect(config.DATABASE_URL)


def apply_schema(conn, path: str = "schema.sql") -> None:
    with open(path) as f, conn.cursor() as cur:
        cur.execute(f.read())
    conn.commit()
    log.info("Schema applied")


def upsert_player(cur, entry: dict, platform: str, tier: str) -> None:
    """League-V4 entries include puuid on current API versions."""
    puuid = entry.get("puuid")
    if not puuid:
        return  # resolve via summoner-v4 in fetch_ladder if needed
    cur.execute(
        """
        INSERT INTO players (puuid, platform, summoner_id, tier,
                             league_points, wins, losses, last_seen)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (puuid) DO UPDATE SET
            tier = EXCLUDED.tier,
            league_points = EXCLUDED.league_points,
            wins = EXCLUDED.wins,
            losses = EXCLUDED.losses,
            last_seen = now()
        """,
        (puuid, platform, entry.get("summonerId"), tier,
         entry.get("leaguePoints"), entry.get("wins"), entry.get("losses")),
    )


def insert_raw_match(cur, match_id: str, routing: str, payload: dict) -> bool:
    """Returns True if inserted (i.e. match was new)."""
    cur.execute(
        """
        INSERT INTO matches_raw (match_id, routing, payload)
        VALUES (%s, %s, %s)
        ON CONFLICT (match_id) DO NOTHING
        """,
        (match_id, routing, psycopg2.extras.Json(payload)),
    )
    return cur.rowcount == 1


def known_match_ids(cur, match_ids: list[str]) -> set[str]:
    if not match_ids:
        return set()
    cur.execute(
        "SELECT match_id FROM matches_raw WHERE match_id = ANY(%s)",
        (match_ids,),
    )
    return {r[0] for r in cur.fetchall()}
