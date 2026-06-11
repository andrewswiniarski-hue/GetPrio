-- ============================================================
-- LoL SoloQ Meta-Detection Pipeline: Warehouse Schema
-- Postgres 14+
-- ============================================================

-- ------------------------------------------------------------
-- Reference: patches
-- Populated manually or from Data Dragon versions endpoint.
-- patch = first two components of gameVersion, e.g. '25.11'
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patches (
    patch           TEXT PRIMARY KEY,
    release_date    DATE,
    notes_url       TEXT
);

-- ------------------------------------------------------------
-- Ladder players we track (Master+ across platforms)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    puuid           TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,           -- na1, euw1, kr, ...
    summoner_id     TEXT,
    riot_id         TEXT,                    -- gameName#tagLine if resolved
    tier            TEXT,                    -- CHALLENGER / GRANDMASTER / MASTER
    league_points   INT,
    wins            INT,
    losses          INT,
    first_seen      TIMESTAMPTZ DEFAULT now(),
    last_seen       TIMESTAMPTZ DEFAULT now(),
    last_match_pull TIMESTAMPTZ              -- when we last fetched their match ids
);

CREATE INDEX IF NOT EXISTS idx_players_platform_tier ON players (platform, tier);
CREATE INDEX IF NOT EXISTS idx_players_last_pull ON players (last_match_pull NULLS FIRST);

-- ------------------------------------------------------------
-- Pro player soloq accounts (the pro-relevance layer).
-- One pro can map to many accounts across platforms.
-- Seed from a CSV; join to players/participants via puuid.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pro_accounts (
    puuid           TEXT PRIMARY KEY,
    pro_name        TEXT NOT NULL,           -- e.g. 'Faker'
    team            TEXT,                    -- e.g. 'T1'
    league          TEXT,                    -- LCK / LEC / LTA / LPL ...
    role            TEXT,                    -- TOP/JUNGLE/MID/BOT/SUPPORT
    platform        TEXT NOT NULL,
    riot_id         TEXT,
    active          BOOLEAN DEFAULT TRUE,
    added_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pro_accounts_pro ON pro_accounts (pro_name);

-- ------------------------------------------------------------
-- Raw match JSON landing zone (immutable; parse downstream).
-- Keeping raw JSONB means you can re-parse when you add fields.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matches_raw (
    match_id        TEXT PRIMARY KEY,        -- e.g. 'KR_7012345678'
    routing         TEXT NOT NULL,           -- americas / asia / europe / sea
    fetched_at      TIMESTAMPTZ DEFAULT now(),
    processed       BOOLEAN DEFAULT FALSE,
    payload         JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_raw_unprocessed
    ON matches_raw (processed) WHERE processed = FALSE;

-- ------------------------------------------------------------
-- Parsed match header (1 row per game)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matches (
    match_id        TEXT PRIMARY KEY REFERENCES matches_raw(match_id),
    platform        TEXT NOT NULL,
    queue_id        INT NOT NULL,            -- 420 = ranked solo
    game_version    TEXT NOT NULL,
    patch           TEXT NOT NULL,
    game_creation   TIMESTAMPTZ NOT NULL,
    game_duration_s INT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_patch ON matches (patch);
CREATE INDEX IF NOT EXISTS idx_matches_creation ON matches (game_creation);

-- ------------------------------------------------------------
-- Parsed participants (10 rows per game) — the core fact table
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS participants (
    match_id        TEXT NOT NULL REFERENCES matches(match_id),
    puuid           TEXT NOT NULL,
    champion_id     INT NOT NULL,
    champion_name   TEXT NOT NULL,
    team_position   TEXT,                    -- TOP/JUNGLE/MIDDLE/BOTTOM/UTILITY
    team_id         INT NOT NULL,            -- 100 blue / 200 red
    win             BOOLEAN NOT NULL,
    kills           INT,
    deaths          INT,
    assists         INT,
    gold_earned     INT,
    total_damage    INT,
    cs              INT,                     -- minions + neutral
    vision_score    INT,
    keystone_id     INT,                     -- primary rune
    summoner1_id    INT,
    summoner2_id    INT,
    item_ids        INT[],                   -- item0..item6
    opp_champion_id INT,                     -- lane opponent (same team_position, other team)
    PRIMARY KEY (match_id, puuid)
);

CREATE INDEX IF NOT EXISTS idx_participants_champ ON participants (champion_id, team_position);
CREATE INDEX IF NOT EXISTS idx_participants_puuid ON participants (puuid);

-- ------------------------------------------------------------
-- Daily champion/role/patch aggregates (incrementally rebuilt).
-- Day granularity is what enables pick-rate *velocity*.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS champ_daily_stats (
    day             DATE NOT NULL,
    patch           TEXT NOT NULL,
    platform        TEXT NOT NULL,
    champion_id     INT NOT NULL,
    champion_name   TEXT NOT NULL,
    team_position   TEXT NOT NULL,
    games           INT NOT NULL,
    wins            INT NOT NULL,
    total_games_day INT NOT NULL,            -- all games that day (denominator for pick rate)
    PRIMARY KEY (day, patch, platform, champion_id, team_position)
);

-- ------------------------------------------------------------
-- Matchup aggregates (sparse; shrink hard downstream)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matchup_stats (
    patch           TEXT NOT NULL,
    team_position   TEXT NOT NULL,
    champion_id     INT NOT NULL,
    opp_champion_id INT NOT NULL,
    games           INT NOT NULL,
    wins            INT NOT NULL,
    PRIMARY KEY (patch, team_position, champion_id, opp_champion_id)
);

-- ------------------------------------------------------------
-- Output: emergence scores written by compute_stats.py
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emergence_scores (
    run_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    patch           TEXT NOT NULL,
    champion_id     INT NOT NULL,
    champion_name   TEXT NOT NULL,
    team_position   TEXT NOT NULL,
    games           INT NOT NULL,
    raw_wr          NUMERIC(5,4),
    shrunk_wr       NUMERIC(5,4),            -- empirical-Bayes posterior mean
    wr_lcb          NUMERIC(5,4),            -- lower confidence bound (ranking metric)
    pick_rate       NUMERIC(6,5),
    pick_velocity   NUMERIC(8,5),            -- slope of daily pick rate within patch
    pro_soloq_games INT,                     -- games on tracked pro accounts this patch
    score           NUMERIC(8,4),            -- composite emergence score
    PRIMARY KEY (run_at, patch, champion_id, team_position)
);

-- Convenience view: latest emergence run
CREATE OR REPLACE VIEW latest_emergence AS
SELECT e.*
FROM emergence_scores e
WHERE e.run_at = (SELECT max(run_at) FROM emergence_scores);
