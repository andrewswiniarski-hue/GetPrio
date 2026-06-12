"""Load/refresh pro soloq accounts from a seed CSV.

CSV columns: puuid,pro_name,team,league,role,platform,riot_id
Leave puuid blank and it is resolved from riot_id ("gameName#tagLine")
via Account-V1, using the platform's regional routing from config.PLATFORMS.

Run:  python load_pro_accounts.py pro_accounts_seed.csv
"""
import csv
import logging
import sys

import config
import db
from riot_api import RiotClient

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def resolve_puuid(client: RiotClient, platform: str, riot_id: str) -> str | None:
    routing = config.PLATFORMS.get(platform)
    if not routing:
        log.warning("Platform %r not in config.PLATFORMS; enable it there "
                    "to resolve %s", platform, riot_id)
        return None
    if "#" not in riot_id:
        log.warning("riot_id %r is not gameName#tagLine; skipping", riot_id)
        return None
    name, tag = riot_id.split("#", 1)
    account = client.account_by_riot_id(routing, name, tag)
    if not account:
        log.warning("Account-V1 found no account for %r on %s", riot_id, routing)
        return None
    return account.get("puuid")


def main(path: str) -> None:
    conn = db.get_conn()
    client = None
    n = skipped = 0
    with conn.cursor() as cur, open(path, newline="",
                                    encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row = {k: (v or "").strip() for k, v in row.items()}
            if not row.get("puuid"):
                client = client or RiotClient()
                row["puuid"] = resolve_puuid(
                    client, row.get("platform", ""), row.get("riot_id", ""))
                if not row["puuid"]:
                    skipped += 1
                    continue
            cur.execute(
                """
                INSERT INTO pro_accounts
                    (puuid, pro_name, team, league, role, platform, riot_id)
                VALUES (%(puuid)s, %(pro_name)s, %(team)s, %(league)s,
                        %(role)s, %(platform)s, %(riot_id)s)
                ON CONFLICT (puuid) DO UPDATE SET
                    pro_name = EXCLUDED.pro_name,
                    team     = EXCLUDED.team,
                    league   = EXCLUDED.league,
                    role     = EXCLUDED.role,
                    riot_id  = EXCLUDED.riot_id,
                    active   = TRUE
                """,
                row,
            )
            n += 1
    conn.commit()
    conn.close()
    log.info("Upserted %d pro accounts (%d skipped/unresolved)", n, skipped)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python load_pro_accounts.py pro_accounts_seed.csv")
    main(sys.argv[1])
