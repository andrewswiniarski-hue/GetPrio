"""Step 2: For each tracked player, pull recent ranked-solo match IDs,
skip ones we already have, fetch full match JSON for the rest.

Run continuously or on a frequent cron:  python fetch_matches.py --limit 500
The --limit flag caps how many players are processed per run so a single
invocation stays bounded; players are processed least-recently-pulled first.
"""
import argparse
import logging
import time

import config
import db
from riot_api import RiotClient

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def players_to_pull(cur, limit: int):
    cur.execute(
        """
        SELECT puuid, platform
        FROM players
        ORDER BY last_match_pull ASC NULLS FIRST
        LIMIT %s
        """,
        (limit,),
    )
    return cur.fetchall()


def main(player_limit: int) -> None:
    client = RiotClient()
    conn = db.get_conn()

    start_time = int(time.time()) - config.MATCH_LOOKBACK_DAYS * 86400
    new_matches = 0

    with conn.cursor() as cur:
        for puuid, platform in players_to_pull(cur, player_limit):
            routing = config.PLATFORMS.get(platform)
            if not routing:
                continue

            match_ids = client.match_ids_by_puuid(
                routing, puuid,
                queue=config.QUEUE_RANKED_SOLO,
                count=config.MATCH_IDS_PER_PULL,
                start_time=start_time,
            )

            already = db.known_match_ids(cur, match_ids)
            todo = [m for m in match_ids if m not in already]

            for match_id in todo:
                payload = client.match_detail(routing, match_id)
                if payload is None:
                    continue
                if db.insert_raw_match(cur, match_id, routing, payload):
                    new_matches += 1

            cur.execute(
                "UPDATE players SET last_match_pull = now() WHERE puuid = %s",
                (puuid,),
            )
            conn.commit()

    log.info("Inserted %d new raw matches", new_matches)
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200,
                    help="max players to process this run")
    args = ap.parse_args()
    main(args.limit)
