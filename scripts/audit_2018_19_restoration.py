"""Audit whether partial 2018-19 data can be safely restored to PublicInjuryEntry.

Read-only: connects to the local PostgreSQL database and runs the same canonical
projection query used by publish_public_entries.py, but scoped to the 2018-19
partial window (2018-12-20 through 2019-10-21).

Run with: .venv/bin/python scripts/audit_2018_19_restoration.py
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.db.session import build_engine, build_session_factory
from app.models.nba import (
    NBAInjuryCondition,
    NBAPlayer,
    NBAReport,
    NBAReportEntry,
    NBAScheduleGame,
    NBATeam,
    PublicInjuryEntry,
)

_AUDIT_START = date(2018, 12, 20)
_AUDIT_END = date(2019, 10, 21)  # inclusive; day before current cutoff
_CURRENT_CUTOFF = date(2019, 10, 22)


def _build_2018_19_query(session: Session):
    """Replicate _build_publish_query but filtered to the 2018-19 partial window.

    Returns Row tuples with positional access:
      [0]  NBAReportEntry (entity)
      [1]  NBAPlayer.canonical_name
      [2]  NBATeam.canonical_name
      [3]  NBAInjuryCondition.body_part
      [4]  NBAInjuryCondition.injury_type
      [5]  NBAReport.source_url
      [6]  NBAReport.report_date
      [7]  NBAReport.report_time
      [8]  schedule.season
      [9]  schedule.season_type
    """
    schedule = NBAScheduleGame.__table__
    return (
        session.query(
            NBAReportEntry,
            NBAPlayer.canonical_name,
            NBATeam.canonical_name,
            NBAInjuryCondition.body_part,
            NBAInjuryCondition.injury_type,
            NBAReport.source_url,
            NBAReport.report_date,
            NBAReport.report_time,
            schedule.c.season,
            schedule.c.season_type,
        )
        .join(NBAPlayer, NBAReportEntry.player_id == NBAPlayer.id)
        .outerjoin(NBATeam, NBAReportEntry.team_id == NBATeam.id)
        .join(NBAReport, NBAReportEntry.report_id == NBAReport.id)
        .join(
            NBAInjuryCondition,
            and_(
                NBAInjuryCondition.report_entry_id == NBAReportEntry.id,
                NBAInjuryCondition.condition_index == 1,
            ),
        )
        .outerjoin(
            schedule,
            and_(
                schedule.c.game_date == NBAReportEntry.game_date,
                func.replace(schedule.c.matchup, " ", "")
                == func.replace(NBAReportEntry.matchup, " ", ""),
            ),
        )
        .filter(
            NBAReportEntry.game_date >= _AUDIT_START,
            NBAReportEntry.game_date <= _AUDIT_END,
        )
        .order_by(NBAReportEntry.game_date, NBAReportEntry.row_number)
    )


def run_audit(session: Session) -> dict:
    """Execute the audit and return a structured report dict."""
    rows = _build_2018_19_query(session).all()

    report: dict = {}

    # ── Total candidate rows ──────────────────────────────────────────────
    report["total_candidate_rows"] = len(rows)

    if not rows:
        report["message"] = "No candidate rows found in the 2018-19 partial window."
        return report

    # Unpack positional columns for readability
    def _entry(r):
        return r[0]

    def _player_name(r):
        return r[1]

    def _team_name(r):
        return r[2]

    def _body_part(r):
        return r[3]

    def _injury_type(r):
        return r[4]

    def _source_url(r):
        return r[5]

    def _report_date(r):
        return r[6]

    def _report_time(r):
        return r[7]

    def _season(r):
        return r[8]

    def _season_type(r):
        return r[9]

    # ── Date range ────────────────────────────────────────────────────────
    dates = [_entry(r).game_date for r in rows]
    report["earliest_game_date"] = str(min(dates))
    report["latest_game_date"] = str(max(dates))

    # ── Counts by season_type ─────────────────────────────────────────────
    season_type_counter: Counter[str | None] = Counter()
    for r in rows:
        season_type_counter[_season_type(r)] += 1
    report["counts_by_season_type"] = dict(season_type_counter)

    # ── Counts by season ──────────────────────────────────────────────────
    season_counter: Counter[str | None] = Counter()
    for r in rows:
        season_counter[_season(r)] += 1
    report["counts_by_season"] = dict(season_counter)

    # ── Null season / null season_type ────────────────────────────────────
    report["rows_with_null_season"] = sum(1 for r in rows if _season(r) is None)
    report["rows_with_null_season_type"] = sum(1 for r in rows if _season_type(r) is None)

    # ── Distinct source URLs ──────────────────────────────────────────────
    source_urls = sorted({_source_url(r) for r in rows})
    report["distinct_source_url_count"] = len(source_urls)
    report["distinct_source_urls"] = source_urls

    # ── Duplicate (source_url, row_number) keys ───────────────────────────
    key_counts: Counter[tuple[str, int]] = Counter()
    for r in rows:
        entry = _entry(r)
        key_counts[(_source_url(r), entry.row_number)] += 1
    duplicates = {k: v for k, v in key_counts.items() if v > 1}
    report["duplicate_keys"] = {
        f"{url} #{rn}": cnt for (url, rn), cnt in sorted(duplicates.items())
    }

    # ── Conflict check with existing PublicInjuryEntry ────────────────────
    existing_keys = set(
        session.query(
            PublicInjuryEntry.source_url,
            PublicInjuryEntry.row_number,
        ).all()
    )
    new_keys = {(_source_url(r), _entry(r).row_number) for r in rows}
    conflicts = new_keys & existing_keys
    report["conflict_count"] = len(conflicts)
    if conflicts:
        sample = sorted(conflicts)[:20]
        report["conflict_sample"] = [
            {"source_url": url, "row_number": rn} for url, rn in sample
        ]
    else:
        report["conflict_sample"] = []

    # ── Cutoff change safety: gap analysis ────────────────────────────────
    gap_count = (
        session.query(func.count(NBAReportEntry.id))
        .filter(
            NBAReportEntry.game_date >= _AUDIT_START,
            NBAReportEntry.game_date < _CURRENT_CUTOFF,
        )
        .scalar()
    )
    report["entries_in_full_gap_window"] = gap_count

    # Count entries strictly between audit end and current cutoff (should be 0)
    off_season_count = (
        session.query(func.count(NBAReportEntry.id))
        .filter(
            NBAReportEntry.game_date > _AUDIT_END,
            NBAReportEntry.game_date < _CURRENT_CUTOFF,
        )
        .scalar()
    )
    report["off_season_gap_entries"] = off_season_count

    # ── Current PublicInjuryEntry count ───────────────────────────────────
    report["current_public_entry_count"] = (
        session.query(func.count(PublicInjuryEntry.id)).scalar()
    )

    # ── Sample rows (first 10) ────────────────────────────────────────────
    report["sample_rows"] = []
    for r in rows[:10]:
        entry = _entry(r)
        report["sample_rows"].append({
            "game_date": str(entry.game_date),
            "player": _player_name(r),
            "team": _team_name(r),
            "matchup": entry.matchup,
            "status": entry.status,
            "body_part": _body_part(r),
            "injury_type": _injury_type(r),
            "season": _season(r),
            "season_type": _season_type(r),
            "source_url": _source_url(r),
        })

    return report


def main():
    engine = build_engine()
    session_factory = build_session_factory(engine)
    with session_factory() as session:
        report = run_audit(session)

    print("=" * 72)
    print("  2018-19 PARTIAL SEASON RESTORATION AUDIT")
    print("=" * 72)
    print(f"  Audit window:    {_AUDIT_START}  through  {_AUDIT_END}")
    print(f"  Current cutoff:  {_CURRENT_CUTOFF}  (publish_public_entries.py)")
    print()

    total = report["total_candidate_rows"]
    print(f"  Total candidate rows:         {total}")
    if total == 0:
        print("  No data to restore.")
        return

    print(f"  Earliest game_date:           {report['earliest_game_date']}")
    print(f"  Latest game_date:             {report['latest_game_date']}")
    print()

    print("  Counts by season_type:")
    for st, cnt in sorted(report["counts_by_season_type"].items(), key=lambda x: str(x[0])):
        print(f"    {str(st):<20s} {cnt:>6d}")
    print()

    print("  Counts by season:")
    for s, cnt in sorted(report["counts_by_season"].items(), key=lambda x: str(x[0])):
        print(f"    {str(s):<20s} {cnt:>6d}")
    print()

    print(f"  Rows with null season:        {report['rows_with_null_season']}")
    print(f"  Rows with null season_type:   {report['rows_with_null_season_type']}")
    print()

    print(f"  Distinct source URLs:         {report['distinct_source_url_count']}")
    for url in report["distinct_source_urls"]:
        print(f"    {url}")
    print()

    dupes = report["duplicate_keys"]
    print(f"  Duplicate (source_url, row_number) keys: {len(dupes)}")
    for key, cnt in dupes.items():
        print(f"    {key}: {cnt} occurrences")
    print()

    print(f"  Conflicts with existing PublicInjuryEntry: {report['conflict_count']}")
    if report["conflict_sample"]:
        for c in report["conflict_sample"]:
            print(f"    {c['source_url']} #{c['row_number']}")
    print()

    print(f"  Current PublicInjuryEntry count:  {report['current_public_entry_count']}")
    print(
        f"  Entries in {_AUDIT_START}–{_AUDIT_END} window:   "
        f"{report['entries_in_full_gap_window']}"
    )
    print(f"  Off-season gap entries (2019-10 to 2019-10-21): {report['off_season_gap_entries']}")
    print()

    print("  Sample rows:")
    for s in report["sample_rows"]:
        print(f"    {s['game_date']}  {s['player']:<25s} {str(s['team']):<25s} "
              f"{s['matchup']:<12s} status={str(s['status']):<10s} "
              f"body_part={str(s['body_part']):<12s} "
              f"season={s['season']}  season_type={s['season_type']}")
    print()

    # ── Safety verdict ────────────────────────────────────────────────────
    print("=" * 72)
    print("  SAFETY ASSESSMENT")
    print("=" * 72)
    if report["conflict_count"] == 0:
        print("  [OK] No conflicts with existing PublicInjuryEntry rows.")
    else:
        print(f"  [WARN] {report['conflict_count']} keys already exist in PublicInjuryEntry.")
        print("         These would be overwritten (idempotent update) if the cutoff is changed.")

    if report["rows_with_null_season"] > 0:
        print(f"  [WARN] {report['rows_with_null_season']} rows have NULL season.")
    if report["rows_with_null_season_type"] > 0:
        print(f"  [WARN] {report['rows_with_null_season_type']} rows have NULL season_type.")

    no_schedule = report["counts_by_season_type"].get(None, 0)
    if no_schedule:
        print(f"  [INFO] {no_schedule} rows have no schedule match (NULL season_type).")
        print("         These will still publish but without season/season_type metadata.")

    if report["off_season_gap_entries"] > 0:
        print(f"  [WARN] {report['off_season_gap_entries']} entries exist in the off-season gap.")
        print("         Changing the cutoff would include unintended rows.")
    else:
        print("  [OK] No entries exist between end of 2018-19 and current cutoff.")

    print()
    print("  Changing _SEASON_CUTOFF to date(2018, 12, 20) would:")
    print(f"    - Add {total} rows to PublicInjuryEntry")
    print("    - Not change any 2019-20 onward output (same query, wider window)")
    if report["off_season_gap_entries"] == 0:
        print("    - Be SAFE: no entries exist between {_AUDIT_END} and {_CURRENT_CUTOFF}")
    print()


if __name__ == "__main__":
    main()
