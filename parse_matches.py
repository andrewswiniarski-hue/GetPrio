"""Step 3: Parse unprocessed raw match JSON into the matches and
participants tables, including lane-opponent resolution.

Run after fetch_matches:  python parse_matches.py
"""
import logging
from datetime import datetime, timezone

import db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH = 500


def patch_from_version(game_version: str) -> str:
    """'25.11.123.456' -> '25.11'"""
    parts = game_version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else game_version


def parse_one(cur, match_id: str, payload: dict) -> None:
    info = payload.get("info", {})
    if not info:
        return

    game_version = info.get("gameVersion", "0.0")
    patch = patch_from_version(game_version)
    platform = info.get("platformId", "").lower()
    creation_ms = info.get("gameCreation", 0)
    creation = datetime.fromtimestamp(creation_ms / 1000, tz=timezone.utc)

    cur.execute(
        """
        INSERT INTO matches (match_id, platform, queue_id, game_version,
                             patch, game_creation, game_duration_s)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (match_id) DO NOTHING
        """,
        (match_id, platform, info.get("queueId"), game_version,
         patch, creation, info.get("gameDuration")),
    )

    parts = info.get("participants", [])

    # lane opponent: same teamPosition, opposite team
    opp_by_pos_team: dict[tuple[str, int], int] = {}
    for p in parts:
        pos, team = p.get("teamPosition") or "", p.get("teamId")
        if pos:
            opp_by_pos_team[(pos, team)] = p.get("championId")

    for p in parts:
        pos = p.get("teamPosition") or ""
        team = p.get("teamId")
        opp_team = 200 if team == 100 else 100
        opp_champ = opp_by_pos_team.get((pos, opp_team)) if pos else None

        items = [p.get(f"item{i}", 0) for i in range(7)]
        cs = (p.get("totalMinionsKilled", 0) or 0) + \
             (p.get("neutralMinionsKilled", 0) or 0)
        keystone = None
        styles = (p.get("perks") or {}).get("styles") or []
        if styles and styles[0].get("selections"):
            keystone = styles[0]["selections"][0].get("perk")

        cur.execute(
            """
            INSERT INTO participants (
                match_id, puuid, champion_id, champion_name, team_position,
                team_id, win, kills, deaths, assists, gold_earned,
                total_damage, cs, vision_score, keystone_id,
                summoner1_id, summoner2_id, item_ids, opp_champion_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (match_id, puuid) DO NOTHING
            """,
            (match_id, p.get("puuid"), p.get("championId"),
             p.get("championName"), pos or None, team, p.get("win"),
             p.get("kills"), p.get("deaths"), p.get("assists"),
             p.get("goldEarned"),
             p.get("totalDamageDealtToChampions"), cs,
             p.get("visionScore"), keystone,
             p.get("summoner1Id"), p.get("summoner2Id"),
             items, opp_champ),
        )


def main() -> None:
    conn = db.get_conn()
    total = 0
    while True:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT match_id, payload FROM matches_raw
                WHERE processed = FALSE
                LIMIT %s
                """,
                (BATCH,),
            )
            rows = cur.fetchall()
            if not rows:
                break
            for match_id, payload in rows:
                try:
                    parse_one(cur, match_id, payload)
                except Exception:
                    log.exception("Failed parsing %s; marking processed", match_id)
                cur.execute(
                    "UPDATE matches_raw SET processed = TRUE WHERE match_id = %s",
                    (match_id,),
                )
            conn.commit()
            total += len(rows)
            log.info("Parsed %d matches (running total %d)", len(rows), total)
    conn.close()


if __name__ == "__main__":
    main()
