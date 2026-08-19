-- lean_production_bootstrap.sql
--
-- Standalone schema for a lean Supabase production database.
-- Creates ONLY the tables required by the public API and the
-- incremental update job.  No archive, discovery, parsing, or
-- episode tables are created.
--
-- After applying, stamp alembic_version to the current archive
-- head so that local/archive Alembic tooling stays in sync:
--
--   INSERT INTO alembic_version (version_num)
--   VALUES ('0007_public_injury_entries');
--
-- Usage (psql):
--   psql "$DATABASE_URL" -f scripts/lean_production_bootstrap.sql
--
-- Usage (Python):
--   See scripts/bootstrap_lean_production.py

BEGIN;

-- ----------------------------------------------------------------
-- 1. nba_players  (referenced by public_injury_entries)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nba_players (
    id              BIGINT       NOT NULL,
    canonical_name  TEXT         NOT NULL,
    name_key        TEXT         NOT NULL,
    official_id     VARCHAR(64),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT pk_nba_players             PRIMARY KEY (id),
    CONSTRAINT uq_nba_players_name_key    UNIQUE (name_key),
    CONSTRAINT uq_nba_players_official_id UNIQUE (official_id)
);

-- ----------------------------------------------------------------
-- 2. nba_teams  (referenced by public_injury_entries)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nba_teams (
    id              BIGINT       NOT NULL,
    canonical_name  TEXT         NOT NULL,
    abbreviation    VARCHAR(3),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT pk_nba_teams                 PRIMARY KEY (id),
    CONSTRAINT uq_nba_teams_canonical_name  UNIQUE (canonical_name),
    CONSTRAINT uq_nba_teams_abbreviation    UNIQUE (abbreviation)
);

-- ----------------------------------------------------------------
-- 3. update_runs  (pipeline bookkeeping)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS update_runs (
    id                     BIGINT       NOT NULL,
    started_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at            TIMESTAMPTZ,
    requested_start_date   DATE,
    requested_end_date     DATE,
    rows_fetched           INTEGER      NOT NULL DEFAULT 0,
    rows_inserted          INTEGER      NOT NULL DEFAULT 0,
    rows_processed         INTEGER      NOT NULL DEFAULT 0,
    status                 VARCHAR(32)  NOT NULL,
    error_details          TEXT,

    CONSTRAINT pk_update_runs PRIMARY KEY (id)
);

-- ----------------------------------------------------------------
-- 4. nba_schedule_games  (season/season_type lookup)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nba_schedule_games (
    id           BIGINT       NOT NULL,
    season       TEXT         NOT NULL,
    game_date    DATE         NOT NULL,
    season_type  VARCHAR(32)  NOT NULL,
    away_team    TEXT         NOT NULL,
    home_team    TEXT         NOT NULL,
    matchup      VARCHAR(16)  NOT NULL,
    source       TEXT,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT pk_nba_schedule_games PRIMARY KEY (id),
    CONSTRAINT uq_nba_schedule_season_date_matchup
        UNIQUE (season, game_date, matchup)
);

CREATE INDEX IF NOT EXISTS ix_nba_schedule_season    ON nba_schedule_games (season);
CREATE INDEX IF NOT EXISTS ix_nba_schedule_game_date ON nba_schedule_games (game_date);

-- ----------------------------------------------------------------
-- 5. public_injury_entries  (public-facing table)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public_injury_entries (
    id                   BIGINT       NOT NULL,
    source_url           TEXT         NOT NULL,
    source_report_date   DATE         NOT NULL,
    source_report_time   TIME         NOT NULL,
    row_number           INTEGER      NOT NULL,
    game_date            DATE         NOT NULL,
    game_time            TIME,
    matchup              VARCHAR(16)  NOT NULL,
    player_id            BIGINT       REFERENCES nba_players(id),
    player_name          TEXT         NOT NULL,
    team_id              BIGINT       REFERENCES nba_teams(id),
    team_name            TEXT         NOT NULL,
    status               VARCHAR(64),
    raw_reason           TEXT,
    reason_category      TEXT,
    body_part            TEXT,
    injury_type          TEXT,
    season               TEXT,
    season_type          VARCHAR(32),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT pk_public_injury_entries PRIMARY KEY (id),
    CONSTRAINT uq_public_injury_entries_url_row
        UNIQUE (source_url, row_number)
);

CREATE INDEX IF NOT EXISTS ix_public_injury_entries_game_date   ON public_injury_entries (game_date);
CREATE INDEX IF NOT EXISTS ix_public_injury_entries_player_id   ON public_injury_entries (player_id);
CREATE INDEX IF NOT EXISTS ix_public_injury_entries_team_id     ON public_injury_entries (team_id);
CREATE INDEX IF NOT EXISTS ix_public_injury_entries_season      ON public_injury_entries (season);
CREATE INDEX IF NOT EXISTS ix_public_injury_entries_season_type ON public_injury_entries (season_type);

-- ----------------------------------------------------------------
-- 6. alembic_version  (migration tracking)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alembic_version (
    version_num  VARCHAR(32)  NOT NULL,

    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Stamp to current archive head so local Alembic tooling recognises
-- the production database as up-to-date.
INSERT INTO alembic_version (version_num)
VALUES ('0007_public_injury_entries')
ON CONFLICT DO NOTHING;

COMMIT;
