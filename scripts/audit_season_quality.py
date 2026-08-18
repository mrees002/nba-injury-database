#!/usr/bin/env python3
"""Read-only season-quality audit for the official NBA injury-report dataset.

Runs against the live PostgreSQL database using SELECT-only queries.
Prints a per-season detail section and a compact comparison table.
No writes, no network requests, no pipeline side-effects.
"""

from __future__ import annotations

import sys
from datetime import date
from typing import Any

from sqlalchemy import create_engine, text

from app.config import get_settings

# --------------------------------------------------------------------------- #
# Season date ranges – mirrors NBA_SEASONS in app/api.py
# --------------------------------------------------------------------------- #
SEASONS: dict[str, tuple[date, date]] = {
    "2018-19": (date(2018, 10, 16), date(2019, 6, 13)),
    "2019-20": (date(2019, 10, 22), date(2020, 10, 11)),
    "2020-21": (date(2020, 12, 22), date(2021, 7, 20)),
    "2021-22": (date(2021, 10, 19), date(2022, 6, 26)),
    "2022-23": (date(2022, 10, 18), date(2023, 6, 20)),
    "2023-24": (date(2023, 10, 24), date(2024, 6, 23)),
    "2024-25": (date(2024, 10, 22), date(2025, 6, 22)),
    "2025-26": (date(2025, 10, 21), date(2026, 6, 13)),
}

STATUS_VALUES = ["Out", "Doubtful", "Questionable", "Probable", "Available"]
ENTRY_TYPES = ["player", "not_submitted", "all_available"]

# --------------------------------------------------------------------------- #
# SQL helpers
# --------------------------------------------------------------------------- #

SUMMARY_SQL = text("""
WITH season_entries AS (
    SELECT
        e.id,
        e.game_date,
        e.matchup,
        e.status,
        e.entry_type,
        r.report_date
    FROM nba_report_entries e
    JOIN nba_reports r ON r.id = e.report_id
    WHERE e.game_date BETWEEN :start_date AND :end_date
),
-- Substantive = entry_type = 'player' and has a non-blank status
substantive AS (
    SELECT * FROM season_entries
    WHERE entry_type = 'player'
      AND status IS NOT NULL
      AND TRIM(status) != ''
),
-- Counts by status (substantive only)
status_counts AS (
    SELECT status, COUNT(*) AS cnt
    FROM substantive
    GROUP BY status
),
-- Blank/null status count (substantive rows with no status)
blank_status AS (
    SELECT COUNT(*) AS cnt
    FROM substantive
    WHERE status IS NULL OR TRIM(status) = ''
),
-- Counts by entry_type
entry_type_counts AS (
    SELECT entry_type, COUNT(*) AS cnt
    FROM season_entries
    GROUP BY entry_type
),
-- Report-level aggregation
report_agg AS (
    SELECT
        r.id AS report_id,
        r.report_date,
        COUNT(e.id) AS entry_count,
        COUNT(e.id) FILTER (WHERE e.entry_type = 'player' AND e.status IS NOT NULL AND TRIM(e.status) != '') AS substantive_count,
        COUNT(DISTINCT CASE WHEN e.entry_type = 'player' AND e.status IS NOT NULL AND TRIM(e.status) != '' THEN (e.game_date, e.matchup) END) AS games_represented
    FROM nba_reports r
    LEFT JOIN nba_report_entries e ON e.report_id = r.id
    WHERE r.report_date BETWEEN :start_date AND :end_date
    GROUP BY r.id, r.report_date
)
SELECT
    -- Substantive counts
    (SELECT COUNT(*) FROM substantive) AS substantive_entry_count,
    -- Distinct (game_date, matchup) pairs in substantive entries
    (SELECT COUNT(DISTINCT (game_date, matchup)) FROM substantive) AS distinct_games,
    -- Distinct report dates in substantive entries
    (SELECT COUNT(DISTINCT report_date) FROM substantive) AS distinct_report_dates,
    -- Distinct players
    (SELECT COUNT(DISTINCT player_id) FROM nba_report_entries e
     WHERE e.game_date BETWEEN :start_date AND :end_date
       AND e.entry_type = 'player' AND e.status IS NOT NULL AND TRIM(e.status) != '') AS distinct_players,
    -- Distinct teams
    (SELECT COUNT(DISTINCT team_id) FROM nba_report_entries e
     WHERE e.game_date BETWEEN :start_date AND :end_date
       AND e.entry_type = 'player' AND e.status IS NOT NULL AND TRIM(e.status) != '') AS distinct_teams,
    -- Earliest / latest game_date in substantive entries
    (SELECT MIN(game_date) FROM substantive) AS earliest_game_date,
    (SELECT MAX(game_date) FROM substantive) AS latest_game_date,
    -- Status counts
    (SELECT cnt FROM status_counts WHERE status = 'Out') AS count_out,
    (SELECT cnt FROM status_counts WHERE status = 'Doubtful') AS count_doubtful,
    (SELECT cnt FROM status_counts WHERE status = 'Questionable') AS count_questionable,
    (SELECT cnt FROM status_counts WHERE status = 'Probable') AS count_probable,
    (SELECT cnt FROM status_counts WHERE status = 'Available') AS count_available,
    -- Blank/null status
    (SELECT cnt FROM blank_status) AS count_blank_status,
    -- Entry type counts
    (SELECT cnt FROM entry_type_counts WHERE entry_type = 'player') AS count_player,
    (SELECT cnt FROM entry_type_counts WHERE entry_type = 'not_submitted') AS count_not_submitted,
    (SELECT cnt FROM entry_type_counts WHERE entry_type = 'all_available') AS count_all_available,
    -- Reports
    (SELECT COUNT(*) FROM nba_reports r
     WHERE r.report_date BETWEEN :start_date AND :end_date) AS nba_report_count,
    -- Reports with zero surviving entries
    (SELECT COUNT(*) FROM report_agg WHERE entry_count = 0) AS reports_zero_entries,
    -- Distinct source report dates
    (SELECT COUNT(DISTINCT report_date) FROM nba_reports r
     WHERE r.report_date BETWEEN :start_date AND :end_date) AS distinct_source_report_dates,
    -- Avg substantive entries per represented game
    (SELECT ROUND(
        CASE WHEN SUM(games_represented) > 0
             THEN SUM(substantive_count)::numeric / SUM(games_represented)
             ELSE NULL END, 2)
     FROM report_agg) AS avg_entries_per_game,
    -- Avg represented games per substantive report date
    (SELECT ROUND(
        CASE WHEN COUNT(*) > 0
             THEN SUM(games_represented)::numeric / COUNT(*)
             ELSE NULL END, 2)
     FROM report_agg
     WHERE substantive_count > 0) AS avg_games_per_report
""")

# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def run() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)

    results: list[dict[str, Any]] = []

    for season_label, (start_date, end_date) in SEASONS.items():
        with engine.connect() as conn:
            row = conn.execute(
                SUMMARY_SQL,
                {"start_date": start_date, "end_date": end_date},
            ).mappings().fetchone()
        results.append({"season": season_label, **dict(row)})

    engine.dispose()

    # ------------------------------------------------------------------ #
    # Per-season detail
    # ------------------------------------------------------------------ #
    for r in results:
        total_status = sum(
            r.get(f"count_{s.lower()}", 0) or 0 for s in STATUS_VALUES
        )
        blank = r["count_blank_status"] or 0
        pct_blank = (blank / (total_status + blank) * 100) if (total_status + blank) else 0

        print(f"\n{'=' * 72}")
        print(f"  Season {r['season']}")
        print(f"{'=' * 72}")
        print(f"  Substantive entries (player+status):  {r['substantive_entry_count']}")
        print(f"  Distinct (game_date, matchup) pairs:  {r['distinct_games']}")
        print(f"  Distinct report dates represented:    {r['distinct_report_dates']}")
        print(f"  Distinct players:                    {r['distinct_players']}")
        print(f"  Distinct teams:                      {r['distinct_teams']}")
        print(f"  Earliest game_date:                  {r['earliest_game_date']}")
        print(f"  Latest game_date:                    {r['latest_game_date']}")
        print()
        print("  Status breakdown (substantive entries):")
        for s in STATUS_VALUES:
            cnt = r.get(f"count_{s.lower()}", 0) or 0
            print(f"    {s:<16} {cnt:>8}")
        print(f"    {'Blank/NULL':<16} {blank:>8}  ({pct_blank:.1f}%)")
        print()
        print("  Entry type counts (all entries in season window):")
        for et in ENTRY_TYPES:
            cnt = r.get(f"count_{et}", 0) or 0
            print(f"    {et:<20} {cnt:>8}")
        print()
        print(f"  NBAReport rows:                      {r['nba_report_count']}")
        print(f"  Reports with zero surviving entries: {r['reports_zero_entries']}")
        print(f"  Distinct source report dates:        {r['distinct_source_report_dates']}")
        print(f"  Avg substantive entries per game:     {r['avg_entries_per_game']}")
        print(f"  Avg games per substantive report:    {r['avg_games_per_report']}")

    # ------------------------------------------------------------------ #
    # Compact comparison table
    # ------------------------------------------------------------------ #
    print(f"\n{'=' * 120}")
    print("  COMPACT COMPARISON TABLE")
    print(f"{'=' * 120}")

    headers = [
        "Season", "Entries", "Games", "RptDates", "Players", "Teams",
        "Out", "Doubtful", "Ques", "Prob", "Avail", "Blank%",
        "Player", "NotSub", "AllAvail", "Reports", "ZeroEnt",
        "SrcDates", "Ent/Game", "Games/Rpt",
    ]
    fmt = "{:<10} {:>8} {:>7} {:>8} {:>8} {:>6} {:>7} {:>8} {:>6} {:>6} {:>7} {:>7} {:>8} {:>8} {:>9} {:>8} {:>8} {:>9} {:>9} {:>10}"
    print(fmt.format(*headers))
    print("-" * 120)

    for r in results:
        total_status = sum(r.get(f"count_{s.lower()}", 0) or 0 for s in STATUS_VALUES)
        blank = r["count_blank_status"] or 0
        pct_blank = f"{(blank / (total_status + blank) * 100):.1f}" if (total_status + blank) else "—"

        def _v(key: str) -> str:
            v = r.get(key)
            return str(v) if v is not None else "—"

        print(fmt.format(
            r["season"],
            _v("substantive_entry_count"),
            _v("distinct_games"),
            _v("distinct_report_dates"),
            _v("distinct_players"),
            _v("distinct_teams"),
            _v("count_out"),
            _v("count_doubtful"),
            _v("count_questionable"),
            _v("count_probable"),
            _v("count_available"),
            pct_blank,
            _v("count_player"),
            _v("count_not_submitted"),
            _v("count_all_available"),
            _v("nba_report_count"),
            _v("reports_zero_entries"),
            _v("distinct_source_report_dates"),
            _v("avg_entries_per_game"),
            _v("avg_games_per_report"),
        ))

    # ------------------------------------------------------------------ #
    # Structural-break flags
    # ------------------------------------------------------------------ #
    print(f"\n{'=' * 120}")
    print("  STRUCTURAL-BREAK FLAGS")
    print(f"{'=' * 120}")

    entry_counts = [r["substantive_entry_count"] or 0 for r in results]
    game_counts = [r["distinct_games"] or 0 for r in results]
    report_counts = [r["nba_report_count"] or 0 for r in results]
    blank_pcts = []
    for r in results:
        ts = sum(r.get(f"count_{s.lower()}", 0) or 0 for s in STATUS_VALUES)
        bl = r["count_blank_status"] or 0
        blank_pcts.append((bl / (ts + bl) * 100) if (ts + bl) else 0)

    median_entries = sorted(entry_counts)[len(entry_counts) // 2]
    median_games = sorted(game_counts)[len(game_counts) // 2]
    median_reports = sorted(report_counts)[len(report_counts) // 2]

    for i, r in enumerate(results):
        flags: list[str] = []
        ec = entry_counts[i]
        gc = game_counts[i]
        rc = report_counts[i]
        bp = blank_pcts[i]

        if median_entries > 0 and ec < median_entries * 0.3:
            flags.append(f"entries={ec} is <30% of median ({median_entries})")
        if median_games > 0 and gc < median_games * 0.3:
            flags.append(f"games={gc} is <30% of median ({median_games})")
        if median_reports > 0 and rc < median_reports * 0.3:
            flags.append(f"reports={rc} is <30% of median ({median_reports})")
        if bp > 10:
            flags.append(f"blank_status={bp:.1f}% > 10%")
        if r["reports_zero_entries"] and r["reports_zero_entries"] > 0:
            flags.append(f"{r['reports_zero_entries']} reports with zero entries")
        if r["count_not_submitted"] and r["count_not_submitted"] > 0:
            flags.append(f"{r['count_not_submitted']} not_submitted rows")

        if flags:
            print(f"  {r['season']}:")
            for f in flags:
                print(f"    - {f}")
        else:
            print(f"  {r['season']}: (no flags)")

    print()


if __name__ == "__main__":
    run()
