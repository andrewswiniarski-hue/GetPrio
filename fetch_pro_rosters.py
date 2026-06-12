"""Build the pro-account seed CSV from Leaguepedia's Cargo API.

Flow:  Tournaments (official, primary, current year)
       -> TournamentRosters (later tournaments win on team/role)
       -> Players.SoloqueueIds (wikitext, region-tagged)
       -> pro_accounts_seed.csv with blank puuids.

This script only harvests; resolution and loading stay in load_pro_accounts.py:

    python fetch_pro_rosters.py -o pro_accounts_seed.csv
    python load_pro_accounts.py pro_accounts_seed.csv

Leaguepedia is a volunteer wiki: SoloqueueIds can be stale or tagless.
Players whose entries yield no usable riot_id are logged at the end for
manual follow-up. Account-V1 (in the loader) is the validator of record.
"""
import argparse
import csv
import datetime as dt
import logging
import os
import re
import time

import requests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

API_URL = "https://lol.fandom.com/api.php"
# MediaWiki etiquette: identify the client. Anonymous rate limits are strict;
# keep CARGO_DELAY_S conservative.
USER_AGENT = "LoL-Draft-Tool/0.1 (soloq meta pipeline; pro roster seeding)"
CARGO_DELAY_S = 4.0
CARGO_MAX_RETRIES = 6
PLAYER_CHUNK = 20  # players per Cargo IN-query (keeps URLs short)

# League label -> (Tournaments.League value on Leaguepedia, home platform)
LEAGUES = {
    "LCK": ("LoL Champions Korea", "kr"),
    "LEC": ("LoL EMEA Championship", "euw1"),
}

# Region markers used in SoloqueueIds wikitext -> our platform ids.
# Markers we can't ingest (CN has no public API; minor regions are off-scope)
# simply suspend collection until the next recognized marker.
REGION_TO_PLATFORM = {
    "KR": "kr",
    "EUW": "euw1",
    "NA": "na1",
}

PLAYER_ROLES = {
    "top": "TOP",
    "jungle": "JUNGLE",
    "mid": "MID",
    "bot": "BOT",
    "support": "SUPPORT",
}

# Many SoloqueueIds entries are legacy summoner names that predate Riot IDs.
# The 2023 migration gave existing accounts a default regional tagline, and
# pros mostly kept it, so tagless names are worth guessing with the default
# tag. Account-V1 in the loader is the validator: bad guesses 404 and skip.
DEFAULT_TAGS = {
    "kr": "KR1",
    "euw1": "EUW",
    "na1": "NA1",
}
# Legacy summoner names: 3-16 chars, unicode letters/digits/spaces.
LEGACY_NAME_RE = re.compile(r"[^\W_][\w ]{1,14}[^\W_]")

_last_call = 0.0

# Optional bot-password login (Special:BotPasswords). Logged-in sessions get
# far friendlier API limits than anonymous IPs. One line in the gitignored
# file:  YourUsername@botname:generated-password
CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".leaguepedia_creds")


def make_session() -> requests.Session:
    """Session with UA set, logged in if .leaguepedia_creds exists."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    if not os.path.exists(CREDS_FILE):
        return session
    with open(CREDS_FILE, encoding="utf-8") as f:
        raw = f.read().strip()
    if ":" not in raw:
        log.warning(".leaguepedia_creds malformed (want user@bot:password); "
                    "continuing anonymously")
        return session
    name, password = raw.split(":", 1)
    try:
        tok = session.get(API_URL, params={
            "action": "query", "meta": "tokens", "type": "login",
            "format": "json"}, timeout=30).json()
        resp = session.post(API_URL, data={
            "action": "login", "lgname": name, "lgpassword": password,
            "lgtoken": tok["query"]["tokens"]["logintoken"],
            "format": "json"}, timeout=30).json()
        result = resp.get("login", {}).get("result")
        if result == "Success":
            log.info("Leaguepedia: logged in as %s", name)
        else:
            log.warning("Leaguepedia login failed (%s); continuing "
                        "anonymously", result)
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("Leaguepedia login error (%s); continuing anonymously",
                    type(e).__name__)
    return session


def cargo_query(session: requests.Session, **params) -> list[dict]:
    """One Cargo query with polite pacing + retry on ratelimit/5xx/HTML."""
    global _last_call
    params = {"action": "cargoquery", "format": "json", "limit": 500, **params}
    for attempt in range(CARGO_MAX_RETRIES):
        wait = CARGO_DELAY_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            resp = session.get(API_URL, params=params, timeout=30)
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            # Fandom's cache layer intermittently returns 503 HTML pages.
            backoff = 10 * (attempt + 1)
            log.warning("Cargo request failed (%s); retrying in %ss",
                        type(e).__name__, backoff)
            time.sleep(backoff)
            continue
        if "error" in data:
            code = data["error"].get("code", "")
            if code == "ratelimited":
                backoff = 30 * (attempt + 1)
                log.warning("Leaguepedia rate limit; sleeping %ss", backoff)
                time.sleep(backoff)
                continue
            raise RuntimeError(f"Cargo error {code}: "
                               f"{data['error'].get('info', '')}")
        rows = [r["title"] for r in data.get("cargoquery", [])]
        if len(rows) >= params["limit"]:
            log.warning("Cargo query hit limit=%d; results may be truncated",
                        params["limit"])
        return rows
    raise RuntimeError("Exceeded Cargo API retries")


def season_tournaments(session: requests.Session, league_value: str,
                       year: int) -> list[dict]:
    """Official primary tournaments for a league-year, oldest first."""
    rows = cargo_query(
        session,
        tables="Tournaments",
        fields="Tournaments.OverviewPage,Tournaments.Name,"
               "Tournaments.DateStart",
        where=(f'Tournaments.League="{league_value}" '
               f"AND Tournaments.Year={year} "
               f"AND Tournaments.TournamentLevel=\"Primary\" "
               f"AND Tournaments.IsOfficial=1"),
    )
    # Unscheduled splits have an empty DateStart; sort them last so any
    # pre-announced rosters they carry take precedence as the freshest.
    return sorted(rows, key=lambda r: r.get("DateStart") or "9999-99-99")


def season_rosters(session: requests.Session,
                   tournaments: list[dict]) -> dict[str, dict]:
    """Merge rosters across a season: player page -> {team, role}.

    Iterates tournaments oldest-first so mid-season moves end up with the
    latest team/role. Coaches/streamers are dropped via PLAYER_ROLES.
    """
    pages = [t["OverviewPage"] for t in tournaments]
    quoted = ",".join(f'"{p}"' for p in pages if '"' not in p)
    rows = cargo_query(
        session,
        tables="TournamentRosters",
        fields="TournamentRosters.OverviewPage,TournamentRosters.Team,"
               "TournamentRosters.RosterLinks,TournamentRosters.Roles",
        where=f"TournamentRosters.OverviewPage IN ({quoted})",
    )
    by_tournament: dict[str, list[dict]] = {}
    for row in rows:
        by_tournament.setdefault(row.get("OverviewPage", ""), []).append(row)

    players: dict[str, dict] = {}
    for t in tournaments:
        for row in by_tournament.get(t["OverviewPage"], []):
            raw_links = (row.get("RosterLinks") or "").strip()
            raw_roles = (row.get("Roles") or "").strip()
            if not raw_links or not raw_roles:
                continue  # unannounced/TBD roster (future tournaments)
            links = raw_links.split(";;")
            roles = raw_roles.split(";;")
            if len(links) != len(roles):
                log.warning("Roster length mismatch for %s @ %s; zipping",
                            row.get("Team"), t["OverviewPage"])
            for page, role in zip(links, roles):
                page, role = page.strip(), role.strip().lower()
                if page and role in PLAYER_ROLES:
                    players[page] = {"team": row.get("Team", ""),
                                     "role": PLAYER_ROLES[role]}
    return players


def fetch_soloqueue_ids(session: requests.Session,
                        pages: list[str]) -> dict[str, dict]:
    """Player page -> {id, soloq} via chunked Players-table lookups."""
    out: dict[str, dict] = {}
    clean = [p for p in pages if '"' not in p]
    for skipped in set(pages) - set(clean):
        log.warning("Skipping player page with quotes: %r", skipped)
    # MediaWiki page titles are first-letter-uppercase, but RosterLinks can
    # carry the display form ("deokdam"); query the ucfirst variant too and
    # map results back to the roster's spelling.
    variant_to_page: dict[str, str] = {}
    for p in clean:
        variant_to_page.setdefault(p, p)
        variant_to_page.setdefault(p[:1].upper() + p[1:], p)
    variants = sorted(variant_to_page)
    for i in range(0, len(variants), PLAYER_CHUNK):
        chunk = variants[i:i + PLAYER_CHUNK]
        quoted = ",".join(f'"{p}"' for p in chunk)
        rows = cargo_query(
            session,
            tables="Players",
            fields="Players.OverviewPage,Players.ID,Players.SoloqueueIds",
            where=f"Players.OverviewPage IN ({quoted})",
        )
        for row in rows:
            page = variant_to_page.get(row["OverviewPage"],
                                       row["OverviewPage"])
            out[page] = {
                "id": row.get("ID") or row["OverviewPage"],
                "soloq": row.get("SoloqueueIds") or "",
            }
    return out


def parse_soloqueue_ids(text: str, home_platform: str) -> list[tuple[str, str]]:
    """Parse SoloqueueIds wikitext into [(platform, riot_id)].

    Format in the wild:  '''KR:''' Hide on bush#KR1<br>'''EUW:''' name#tag
    Untagged leading entries belong to the league's home region. Tagless
    legacy names get the region's post-migration default tag (DEFAULT_TAGS);
    Account-V1 in the loader weeds out the guesses that no longer exist.
    """
    text = re.sub(r"<br\s*/?>", "\n", text).replace("'''", "")
    platform: str | None = home_platform
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        marker = re.match(r"^([A-Z]{2,4})\s*:\s*(.*)$", line)
        if marker:
            platform = REGION_TO_PLATFORM.get(marker.group(1))
            line = marker.group(2)
        # Riot game names cannot contain commas; old-style entries are
        # comma-separated lists.
        for cand in line.split(","):
            cand = re.sub(r"\s*#\s*", "#", cand.strip())
            if not platform:
                continue
            if "#" not in cand and LEGACY_NAME_RE.fullmatch(cand):
                cand = f"{cand}#{DEFAULT_TAGS[platform]}"
            if re.match(r"^[^#]+#\w{2,7}$", cand):
                pair = (platform, cand)
                if pair not in found:
                    found.append(pair)
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default="pro_accounts_seed.csv")
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES),
                    choices=list(LEAGUES))
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    args = ap.parse_args()

    session = make_session()

    rows: list[dict] = []
    seen_accounts: dict[tuple[str, str], str] = {}
    unresolved: list[str] = []
    for league in args.leagues:
        league_value, home_platform = LEAGUES[league]
        tournaments = season_tournaments(session, league_value, args.year)
        if not tournaments:
            log.warning("No %d tournaments for %s; falling back to %d",
                        args.year, league, args.year - 1)
            tournaments = season_tournaments(session, league_value,
                                             args.year - 1)
        log.info("%s: %d tournaments (%s)", league, len(tournaments),
                 ", ".join(t["Name"] for t in tournaments))
        roster = season_rosters(session, tournaments)
        log.info("%s: %d rostered players", league, len(roster))
        details = fetch_soloqueue_ids(session, sorted(roster))
        for page, meta in roster.items():
            detail = details.get(page)
            if not detail:
                unresolved.append(f"{page} ({league} {meta['team']}): "
                                  "no Players entry on Leaguepedia")
                continue
            accounts = parse_soloqueue_ids(detail["soloq"], home_platform)
            if not accounts:
                unresolved.append(f"{detail['id']} ({league} {meta['team']}):"
                                  " no taggable soloq ids on Leaguepedia")
                continue
            for platform, riot_id in accounts:
                key = (platform, riot_id.lower())
                if key in seen_accounts:
                    log.warning("Duplicate account %s/%s for %s "
                                "(already mapped to %s); keeping first",
                                platform, riot_id, detail["id"],
                                seen_accounts[key])
                    continue
                seen_accounts[key] = detail["id"]
                rows.append({
                    "puuid": "",
                    "pro_name": detail["id"],
                    "team": meta["team"],
                    "league": league,
                    "role": meta["role"],
                    "platform": platform,
                    "riot_id": riot_id,
                })

    rows.sort(key=lambda r: (r["league"], r["team"], r["pro_name"],
                             r["platform"]))
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "puuid", "pro_name", "team", "league", "role", "platform",
            "riot_id"])
        writer.writeheader()
        writer.writerows(rows)

    by_platform: dict[str, int] = {}
    for r in rows:
        by_platform[r["platform"]] = by_platform.get(r["platform"], 0) + 1
    log.info("Wrote %d accounts to %s (%s)", len(rows), args.out,
             ", ".join(f"{k}={v}" for k, v in sorted(by_platform.items())))
    if unresolved:
        log.warning("%d players need manual follow-up:", len(unresolved))
        for line in sorted(unresolved):
            log.warning("  %s", line)


if __name__ == "__main__":
    main()
