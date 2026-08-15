# Historical legacy regression

Prompt 3 was validated with the eight historical CSVs covering 2010-10-03 through 2026-04-12.
The files contain 44,180 row occurrences. Idempotent raw ingestion stores 44,126 unique source
records: 28,345 IL transactions and 15,781 missed-game transactions.

## Full-data result

Running `legacy/process_injuries_pipeline.py` over the four concatenated IL files and four
concatenated missed-game files produced:

- 12,877 injuries
- 1,470 unique players
- date range 2010-10-03 through 2026-04-12

Rebuilding from PostgreSQL produced the same counts and date range. A multiset comparison across
date, season, player, team, body part, injury type, and notes found one record substitution:

| Result | Date | Player | Team | Body part | Injury type | Notes |
|---|---|---|---|---|---|---|
| Legacy only | 2018-03-27 | Dwight Powell | Mavericks | knee | injury | left knee injury (DTD) |
| SQL only | 2018-03-27 | Dwight Powell | Mavericks | knee | bursitis | bursitis in knee (DTD) |

Both source rows are from `missed_game`, have the same date and body part, and have equal note
lengths and deduplication scores. The legacy exact-dedup phase sorts all candidate rows only by
score with pandas' default unstable quicksort. Its later time-window phase keeps whichever tied
same-day knee row happens to appear first. The concatenated legacy input includes duplicate row
occurrences, while PostgreSQL intentionally retains one row per source identity. Removing those
unrelated duplicates changes quicksort's global tie ordering and therefore which Dwight Powell row
is retained.

No classification, scoring, or time-window rule was changed to mask this difference. The SQL
pipeline uses the same sort and deduplication logic on the canonical unique raw records. The
behavior is locked by a focused regression test. Defining a stable tie-breaker would be a future,
intentional processing-rule change requiring review.

Every SQL-backed injury produced in this regression run has a non-null foreign key to the selected
`RawTransaction`.
