# Reference Data

This directory holds manually curated or externally sourced reference datasets that are
**not** produced by the injury-report ingestion pipeline.  They are read by isolated
audit scripts only.

## nba_schedule_games.csv

Normalized game schedule used by the coverage audit workflow.

### Columns

| Column | Description |
|---|---|
| season | NBA season label (e.g. `2024-25`) |
| game_date | ISO date (`YYYY-MM-DD`) |
| season_type | One of `preseason`, `regular`, `play_in`, `playoffs` |
| away_team | Canonical team name of the away team |
| home_team | Canonical team name of the home team |
| matchup | `AWAY_ABBR@HOME_ABBR` using official abbreviations |

### Building the reference file

```bash
python scripts/build_schedule_reference.py path/to/source1.csv [path/to/source2.json ...]
```

Input CSV/JSON files must include at minimum `game_date`, `away_team`, `home_team`.
Provide `season_type` in each row or via `--default-season-type`.

### Running the coverage audit

```bash
python scripts/audit_schedule_coverage.py
```

Compares `nba_schedule_games.csv` against canonical `NBAReportEntry` records in the
database. Requires a running PostgreSQL instance with the schema migrated.

Both scripts are **read-only** with respect to the database.
