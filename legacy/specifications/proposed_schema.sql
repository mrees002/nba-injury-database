-- Historical proposed logical schema retained for the PST benchmark.
-- Codex should implement this through SQLAlchemy models + Alembic migrations.

CREATE TABLE raw_transactions (
    id BIGSERIAL PRIMARY KEY,
    source_type VARCHAR(32) NOT NULL CHECK (source_type IN ('il', 'missed_game')),
    transaction_date DATE NOT NULL,
    team TEXT,
    acquired TEXT,
    relinquished TEXT,
    notes TEXT,
    source_url TEXT,
    source_row_key TEXT NOT NULL,
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, source_row_key)
);

CREATE INDEX ix_raw_transactions_date
    ON raw_transactions (transaction_date);

CREATE INDEX ix_raw_transactions_relinquished
    ON raw_transactions (relinquished);

CREATE TABLE update_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    requested_start_date DATE,
    requested_end_date DATE,
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_processed INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL,
    error_details TEXT
);

CREATE TABLE injuries (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    season VARCHAR(7) NOT NULL,
    player_name TEXT NOT NULL,
    team TEXT,
    body_part TEXT,
    injury_type TEXT NOT NULL,
    notes TEXT,
    preferred_source VARCHAR(32),
    source_raw_transaction_id BIGINT REFERENCES raw_transactions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_injuries_date ON injuries (date);
CREATE INDEX ix_injuries_player_name ON injuries (player_name);
CREATE INDEX ix_injuries_team ON injuries (team);
CREATE INDEX ix_injuries_season ON injuries (season);
CREATE INDEX ix_injuries_body_part ON injuries (body_part);
CREATE INDEX ix_injuries_injury_type ON injuries (injury_type);
