# NBA-versus-PST validation benchmark

## Scope

Benchmark version `nba-vs-pst-v1` compares frozen `nba-episodes-v3` episodes with the preserved
legacy PST `injuries` table. PST is external evidence, not ground truth. The benchmark is read-only:
it writes local files and does not update NBA, PST, or episode tables.

The full comparison period is 2019-10-22 through 2026-04-12. Although stored NBA reports begin on
2018-12-20, the 2018-19 archive is sparse. The job therefore derives the overlap as the intersection
of both tables' actual date ranges and the defensible continuous NBA boundary of 2019-10-22.
Optional requested dates can narrow, but cannot expand, that usable overlap.

## Matching

Player names from both sources use the same accent-insensitive alphanumeric key. For each player,
events are ordered by date and stable source ID. A dynamic-programming assignment produces
order-preserving, one-to-one matches within plus or minus seven days; no NBA episode or PST Injury
record can be reused.

The optimizer applies this deterministic priority:

1. maximize the number of matched pairs;
2. minimize total absolute date difference;
3. minimize explicit body-part disagreements;
4. minimize explicit injury-type disagreements;
5. minimize explicit laterality disagreements;
6. maximize normalized-reason similarity;
7. resolve an otherwise exact tie by stable source IDs.

Body-part agreement uses only the narrow synonym families `lower leg`/`shin` and
`forearm`/`arm`. Injury-type agreement is exact when both values exist. PST has no normalized
laterality column, so usable left/right/bilateral values are extracted conservatively from retained
PST notes. Reason similarity is `SequenceMatcher` similarity over lowercase alphanumeric-normalized
text.

Every selected match reports exact date, within-one-day, within-three-day, and within-seven-day
flags. A match is marked ambiguous when either record has more than one same-player candidate in
the seven-day window; the deterministic one-to-one assignment still selects exactly one record.

## Discrepancies and traceability

Matched rows can carry multiple review labels: `date_timing_discrepancy`, `body_disagreement`,
`injury_type_disagreement`, `laterality_disagreement`,
`ambiguous_multiple_candidate_match`, and `unresolved`. Unmatched rows are emitted as `nba_only` or
`pst_only` and also marked `unresolved`. These labels are review queues, not judgments about which
source is correct.

NBA rows retain the `nba_episode_id`, total episode lineage-link count, and the first supporting
condition, report-entry, and report IDs. The episode ID joins to every supporting condition through
`nba_injury_episode_conditions`. PST rows retain both `pst_injury_id` and the linked
`pst_raw_transaction_id` when present.

## Artifacts

The default output directory is `artifacts/validation/`. Each file is replaced atomically and the
summary is replaced last as the completion marker. If a run is interrupted, rerun the same command
to replace the complete set consistently.

- `nba_vs_pst_summary.json`
- `nba_vs_pst_matches.csv`
- `nba_vs_pst_nba_only.csv`
- `nba_vs_pst_pst_only.csv`
- `nba_vs_pst_discrepancies.csv`

The summary includes a digest over the deterministic artifact rows. Identical source tables,
arguments, and benchmark code produce identical artifact bytes and digest.

## Commands

Run the full comparison from the repository root:

```bash
.venv/bin/python -m app.jobs.benchmark_nba_vs_pst
```

For a bounded diagnostic, repeat `--player` as needed and use a separate output directory:

```bash
.venv/bin/python -m app.jobs.benchmark_nba_vs_pst \
  --player "Kira Lewis Jr." \
  --player "Nikola Topic" \
  --output-dir /private/tmp/nba-vs-pst-sample
```
