"""Central config. Everything sensitive comes from environment variables.

Set these before running:
    export RIOT_API_KEY="RGAPI-..."
    export DATABASE_URL="postgresql://user:pass@localhost:5432/lolmeta"
"""
import os

RIOT_API_KEY = os.environ.get("RIOT_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/lolmeta")

# Platforms to track and their regional routing for Match-V5.
# Start with the three highest-signal regions; add more later.
PLATFORMS = {
    "kr":   "asia",
    "euw1": "europe",
    "na1":  "americas",
}

# League-V4 apex tiers to ingest
APEX_TIERS = ["challengerleagues", "grandmasterleagues", "masterleagues"]

QUEUE_RANKED_SOLO = 420

# How many recent match IDs to request per player per pull (max 100)
MATCH_IDS_PER_PULL = 30

# Only fetch matches newer than this many days (keeps backfill bounded)
MATCH_LOOKBACK_DAYS = 14

# Rate limits. A dev key is 20 req/s and 100 req/2min; production keys are
# much higher. The client also respects 429 Retry-After headers, so these
# are just a polite ceiling.
REQUESTS_PER_SECOND = float(os.environ.get("RIOT_RPS", "15"))
REQUESTS_PER_TWO_MIN = int(os.environ.get("RIOT_RP2M", "90"))
