"""Step 1: Refresh the Master+ player pool for each tracked platform.

Run daily:  python fetch_ladder.py
"""
import logging

import config
import db
from riot_api import RiotClient

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TIER_NAME = {
    "challengerleagues": "CHALLENGER",
    "grandmasterleagues": "GRANDMASTER",
    "masterleagues": "MASTER",
}


def main() -> None:
    client = RiotClient()
    conn = db.get_conn()
    db.apply_schema(conn)

    with conn.cursor() as cur:
        for platform in config.PLATFORMS:
            for tier_ep in config.APEX_TIERS:
                league = client.apex_league(platform, tier_ep)
                if not league:
                    log.warning("No league data for %s/%s", platform, tier_ep)
                    continue
                entries = league.get("entries", [])
                for entry in entries:
                    db.upsert_player(cur, entry, platform, TIER_NAME[tier_ep])
                conn.commit()
                log.info("%s %s: upserted %d players",
                         platform, TIER_NAME[tier_ep], len(entries))
    conn.close()


if __name__ == "__main__":
    main()
