"""Load/refresh pro soloq accounts from a seed CSV.

CSV columns: puuid,pro_name,team,league,role,platform,riot_id
Build the seed list from sites that track pro soloq accounts, then resolve
each Riot ID to a puuid once (Account-V1) and keep the puuid here.

Run:  python load_pro_accounts.py pro_accounts_seed.csv
"""
import csv
import logging
import sys

import db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main(path: str) -> None:
    conn = db.get_conn()
    n = 0
    with conn.cursor() as cur, open(path, newline="") as f:
        for row in csv.DictReader(f):
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
    log.info("Upserted %d pro accounts", n)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python load_pro_accounts.py pro_accounts_seed.csv")
    main(sys.argv[1])
