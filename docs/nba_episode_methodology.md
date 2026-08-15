# NBA InjuryEpisode methodology

## Version and scope

The current methodology is `nba-episodes-v3`. It constructs NBA-native injury episodes
offline from classified official-NBA injury conditions. It does not use PST records, alter raw
NBA reports or report entries, change classification, or infer `InjuryEpisode` rows from an
external benchmark.

Each episode retains all supporting observations through
`nba_injury_episode_conditions`. A lineage link resolves to the classified condition, its raw
report entry, and the stored NBA report.

## Episode identity and chronology

Player identity is mandatory. Explicit team, body part, and laterality conflicts are hard split
boundaries. Different named structures on the same body part, such as ACL and meniscus, are also
hard boundaries. Bilateral, left, right, and unknown laterality remain distinct values; unknown
laterality is not silently treated as unilateral.

Observations are processed deterministically by player, report publication date and time, game
date, report-entry ID, condition index, and condition ID. Publication chronology controls gap
matching because the reports are the actual observation sequence. Game dates remain the episode
start and last-observed dates.

Conditions classified separately from the same source row cannot merge with one another. This
preserves compound injuries such as ACL plus MCL as separate episodes while retaining their
shared source-row lineage.

## Continuation rules

- An exact normalized reason can continue an open episode across a publication gap of at most
  14 days.
- The same body part and injury type can continue across at most seven days.
- Conservative compatibility groups allow short wording evolution among symptoms, sprain/strain,
  bruise/contusion, concussion/headache, surgery/recovery, and structurally related surgery or
  rehabilitation descriptions.
- Generic injury wording is weak evidence and is limited to a three-day gap.
- Narrow refinements allow generic Achilles/tendon wording to continue an explicitly identified
  tendon tear and allow ACL, MCL, or PCL injury wording to refine to the corresponding tear.
- Initially unknown laterality or type may be filled only by a later compatible observation that
  states the value explicitly. Existing explicit values are not overwritten by conflicts.

Clearly incompatible same-body injury types do not merge merely because they share anatomy.
Named-structure conflicts remain separate even when both conditions have a generic type such as
`tear`.

## Return, disappearance, and recurrence

An explicit `Available` observation closes an episode for later game dates. A later report version
on the same game date can supersede an earlier `Available` status, avoiding a false close caused
by intraday report revisions. A later injury after a completed availability transition starts a
new episode.

Absence from a report is not treated as recovery. Short disappearances can continue when positive
identity evidence remains; long gaps split even an exact reason. Offseason and archive gaps do not
create inferred recovery dates. Repeated episodes with the same player/team/body/laterality
identity are possible, but they should not be interpreted as clinically confirmed recurrence
without supporting report language.

## Final full-archive audit

The finalized full rebuild on 2026-08-13 produced:

- 18,914 episodes covering 1,057 players.
- 716,305 episode-condition links and 716,305 unique linked injury conditions.
- Zero orphan injury conditions, zero episodes without lineage, and zero cross-team episodes.
- 564 single-observation episodes (2.982%); median 15 observations per episode and maximum 1,591.
- 11,294 episodes explicitly closed by an `Available` observation.
- Laterality: 6,916 left, 6,972 right, 173 bilateral, and 4,853 unknown.
- 140 episodes longer than 90 days; maximum observed duration 241 days.

Duration distribution in calendar days:

| Duration | Episodes |
| --- | ---: |
| Same day | 9,646 |
| 1–3 days | 3,600 |
| 4–7 days | 2,260 |
| 8–14 days | 1,494 |
| 15–30 days | 1,111 |
| 31–90 days | 663 |
| Over 90 days | 140 |

The median duration is 0 days, the 75th percentile is 5 days, the 90th percentile is 15 days,
the 99th percentile is 76.87 days, and the maximum is 241 days. Long-duration review included
credible surgery, fracture, Achilles, and rehabilitation sequences such as Nikola Topic, Isaiah
Jackson, Danilo Gallinari, Dario Saric, and Vlatko Cancar. Short-duration review included
single-report contusions, soreness, illness, and availability observations. Kira Lewis Jr.'s
right-knee ACL/MCL source rows retained two classified conditions in two distinct episodes.

Two consecutive complete rebuilds produced the same semantic digest:
`4058cea37eeb2a224db293d212d1a158aec2fda856e79ec82a56a213fd43b6df`.

## Known limitations

- Episode start is the first observed NBA report/game date, not a medically established onset.
- Episode end is populated only from explicit availability; disappearance alone does not establish
  recovery, so many episodes have no clinical recovery date.
- Multiple daily NBA report versions inflate observation counts but preserve the source snapshots.
- Team changes intentionally split episodes, even when an injury may clinically continue after a
  transaction.
- Conservative wording and gap rules can fragment an injury when descriptions change abruptly,
  while ambiguous same-anatomy wording can occasionally join observations that cannot be proven
  clinically identical.
- Unknown laterality remains unknown unless a compatible later observation explicitly supplies it.
- Recurrence group counts describe repeated data identities, not diagnosed clinical recurrence.
- The raw archive retains two documented bounded acquisition gaps from 2023-07-13; no episode rule
  attempts to infer observations for missing source reports.

These limitations are understood and should remain visible when episodes are compared with an
external benchmark or exposed publicly.

## Rebuild and audit

From the repository root:

```bash
.venv/bin/python -m app.jobs.rebuild_nba_episodes
.venv/bin/python -m app.jobs.audit_nba_episodes
```

The rebuild is deterministic, transactional, safe to rerun, requires no network access, and
replaces only the derived episode and episode-condition lineage tables.
