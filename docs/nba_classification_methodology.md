# NBA reason-classification methodology

Assessment date: 2026-08-12

Classifier version: `nba-reason-v7`

## Decision

### CLASSIFICATION: PASS WITH DOCUMENTED LIMITATIONS

Version 7 is sufficiently stable and conservative to serve as the derived condition input to a
future InjuryEpisode methodology. It does not force incomplete source text into an unsupported
anatomy or diagnosis. The remaining nulls and ambiguous compound phrases are quantified below and
remain linked to their unchanged raw observations.

This decision certifies classification only. It does not certify episode construction,
deduplication, recurrence logic, games missed, PST parity, or any product layer.

## Scope and source preservation

PostgreSQL was treated as authoritative. The audit evaluated every raw reason in the completed
official-NBA archive; it made no network request and did not parse or download a PDF.

| Raw input measure | Value |
| --- | ---: |
| NBA report candidates | 12,114 |
| stored reports | 12,112 |
| raw report entries | 1,127,319 |
| entries with a non-null raw reason | 1,127,319 |
| distinct raw reasons | 6,039 |
| player entries eligible for derived conditions | 1,007,541 |
| typed `not_submitted` observations | 119,754 |
| typed `all_available` observations | 24 |

`not_submitted` and `all_available` are team-level source observations, not players, and therefore
do not receive `NBAInjuryCondition` rows. Every player entry receives at least one condition.

The reclassification job changed only `nba_injury_conditions`. Before and after the full rebuild,
the raw archive remained at 12,114 candidates, 12,112 reports, and 1,127,319 entries. A database
fingerprint over every `nba_report_entries` column was identical before and after. The PST tables
were not read or changed by the job, and `NBAInjuryEpisode` remained empty.

## Complete raw-vocabulary audit

The long tail is material: 3,576 of the 6,039 distinct spellings occur fewer than ten times, while
135 recurring reasons account for 802,362 observations.

| Raw-reason frequency | Distinct reasons | Observations |
| --- | ---: | ---: |
| once | 556 | 556 |
| 2-9 | 2,259 | 10,368 |
| 10-99 | 2,328 | 72,490 |
| 100-999 | 761 | 241,543 |
| 1,000 or more | 135 | 802,362 |

### Source categories and statuses

| Source reason category | Entries |
| --- | ---: |
| `Injury/Illness` | 681,467 |
| `G League` | 248,614 |
| null | 191,447 |
| `G League Team` | 2,572 |
| `NOT YET SUBMITTED` | 1,321 |
| `G League - Two-Way` | 747 |
| `G League - On Assignment` | 327 |
| `Not With Team` | 252 |
| `Personal Reasons` | 181 |
| `Rest` | 175 |
| `League Suspension` | 79 |
| `Coach's Decision` | 55 |
| `Trade Pending` | 39 |
| `Injury/Illness -` | 28 |
| `Team Suspension` | 11 |
| `Injury/Illness - -` | 3 |
| `-` | 1 |

The player-status vocabulary is `Out` (744,224), `Questionable` (134,581), `Available`
(60,294), `Probable` (47,671), and `Doubtful` (20,771). The 119,778 null statuses belong to the
119,754 `not_submitted` and 24 `all_available` team observations.

### Recurring patterns

The most frequent medical reasons are left ankle sprain (35,403), right ankle sprain (33,824),
health-and-safety protocols (21,501), left hamstring strain (11,009), right calf strain (10,866),
right hamstring strain (9,514), right knee soreness (9,335), left knee soreness (9,088), left knee
surgery (8,572), and left calf strain (7,938). The most frequent non-medical reasons are G League
two-way (189,367), G League assignment (58,172), not with team (12,714), personal reasons (9,238),
the source placeholder `-` (5,918), and rest (4,929). `NOT YET SUBMITTED` occurs 119,600 times as
the complete raw reason plus 154 additional source variants.

The vocabulary contains explicit left, right, and bilateral anatomy; injury types and symptoms;
procedures; rehabilitation/reconditioning; illnesses; source protocols; equipment such as masks
and braces; and non-medical availability reasons. Slash, comma, semicolon, parentheses, and
laterality-led `and` clauses are all observed in potentially compound reasons.

## Taxonomy and classification rules

`is_injury` means that the source observation is medically relevant to injury, illness, recovery,
or an associated medical restriction. It is not a clinical confirmation and it does not imply that
the player missed a game. Raw text always remains the authoritative description.

### Anatomy

The observed normalized anatomy vocabulary is:

`abdomen`, `achilles`, `ankle`, `arm`, `back`, `calf`, `chest`, `ear`, `elbow`, `eye`, `face`,
`finger`, `foot`, `forearm`, `groin`, `hamstring`, `hand`, `head`, `hip`, `illness`, `knee`, `leg`,
`lower leg`, `mouth`, `neck`, `nose`, `pelvis`, `posterior tibialis`, `quadriceps`, `respiratory
system`, `rib`, `sciatic nerve`, `shoulder`, `skin`, `stomach`, `tailbone`, `thigh`, `thumb`,
`throat`, `toe`, `torso`, `trapezius`, `whole body`, and `wrist`.

Specific source terms are normalized only where the anatomical relationship is direct. Examples
include ACL/MCL/PCL/meniscus to knee; syndesmosis to ankle; metatarsal/navicular/midfoot to foot;
scaphoid to wrist; sacroiliac/iliac wing to pelvis; olecranon to elbow; and orbital to face. The
specific raw term is preserved in `raw_reason` and `raw_row_text` even when the derived label is
broader.

### Condition type

The primary observed normalized types are `sprain`, `strain`, `soreness`, `surgery`, `illness`,
`recovery`, `contusion`, `fracture`, `injury management`, `tear`, `inflammation`, `tightness`,
`tendinopathy`, `bruise`, `spasm`, `stress reaction`, `concussion`, `impingement`, and `pain`.
Additional source-supported labels include ligament-specific ACL/MCL/PCL injury or tear,
dislocation, subluxation, blood clot, bursitis, protective equipment, tendon injury, laceration,
infection, disc injury, immobilization, nerve issue, sciatica, chondromalacia, asthma, migraine,
headache, dysfunction, instability, and other directly observed diagnoses.

An ACL/MCL/PCL reconstruction or repair is `surgery`; a stated tear or rupture is a tear; a bare
ligament name remains a generic ligament injury. Recovery and reconditioning text is classified as
`recovery` even when the phrase also says surgical. This avoids turning a recovery phase into a new
procedure. A bare anatomy such as `Right Hamstring` receives anatomy and laterality but no inferred
strain. A bare `Meniscus` does not become a tear.

### Laterality

Laterality is a separate field with exactly `left`, `right`, `bilateral`, or null. Explicit
`bilateral`, `both`, `left and right`, and `right and left` map to `bilateral`; otherwise an explicit
left or right token is retained. No side is inferred from team, player, anatomy, or neighboring
rows.

### Matching boundaries

Anatomy and explicit non-injury terms use alphanumeric token/phrase boundaries, not unconstrained
substring tests. This prevents matches such as `hip` in `whiplash`, `disc` in `discomfort`, `ear`
in `tear`, `rest` in `restorative`, or `personal` in `personalized`. Longer and more specific
anatomy precedes broader anatomy where terms overlap. Recurrent source misspellings are normalized
only after they were observed in the archive and added as regression examples.

### Explicit non-injury handling

The following actual archive concepts are explicitly non-injury: G League team/two-way/assignment,
rest, personal or family reasons, not with team, coach decision, league or team suspension, trade
pending, ineligible to play, contract expiration, not yet submitted, and all players available.
Source category and bounded raw text are both used because historical category columns sometimes
say `Injury/Illness` for an explicitly non-medical reason.

The final archive contains 295,447 non-injury conditions. Every condition in an explicit
non-injury source category is non-injury. The `Injury/Illness` source category contains 434
non-injury conditions; these are explicit non-medical source reasons or the separately retained
non-medical clause of a mixed row. A medical clause and G League/rest/personal/trade clause in one
row are kept as separate conditions when the medical clause has both explicit anatomy and type.

### Compound conditions

Version 7 emits more than one condition only when the clauses are structurally separable and every
medical clause independently resolves to anatomy and type. It recognizes comma or slash clauses,
and `and` only when the next clause starts with explicit laterality. A semicolon, ampersand, or
parenthesis starts a separate non-injury clause only when followed by a recognized non-injury
concept. Lexical slashes in `N/A`, `Injury/Illness`, and `tib/fib` are protected from splitting.

Medical-only clauses must resolve to distinct anatomy/laterality or anatomy/type identities.
Protective equipment and immobilization remain attached to the related medical condition rather
than becoming a second diagnosis. Shared explicit laterality is propagated only across a validated
split. Ambiguous regional phrases such as `Foot/Ankle; Soreness`, descriptive commas such as
`Fractured Third Finger, Left Hand`, and illness synonyms remain one condition.

All emitted conditions share the original `report_entry_id` and have stable one-based
`condition_index` values. The raw reason is never rewritten.

## Version change and archive-wide result

`nba-reason-v7` supersedes `nba-reason-v6`. The material semantic changes are boundary-safe
non-injury recognition; preservation of the medical half of mixed medical/non-medical rows;
protected lexical slashes and tighter compound splitting; procedure-versus-recovery precedence;
no inferred ACL tear from reconstruction; and archive-supported anatomy, diagnosis, spelling, and
equipment vocabulary.

| Measure | v6 baseline | v7 final |
| --- | ---: | ---: |
| derived conditions | 1,011,772 | 1,011,752 |
| injury conditions | 716,331 | 716,305 |
| explicit non-injury conditions | 295,441 | 295,447 |
| injury conditions with anatomy | 97.409% | 98.909% |
| injury conditions with type | 98.623% | 99.384% |
| injury conditions with laterality | 83.466% | 83.455% |
| entries with multiple conditions | 4,103 | 4,150 |
| injury observations with neither anatomy nor type | 958 | 26 |

Version 7 stores 708,489 injury conditions with anatomy, 711,891 with a condition type, and
597,794 with explicit laterality. The laterality distribution is left 297,074, right 296,263, and
bilateral 4,457. The lower laterality percentage is expected for illness, recovery, general-body,
and other source phrases that state no side.

The 4,150 compound entries produce 4,211 additional conditions: 4,089 entries have two conditions
and 61 have three. The full rebuild produced 1,011,752 conditions, all on `nba-reason-v7`, with no
missing normalized reason or broken source-entry relationship.

## Residual nulls and ambiguity

Null is intentional when the source does not state enough information.

| Most common anatomy-null reason | Observations | Interpretation |
| --- | ---: | --- |
| `Return to Competition Reconditioning` | 5,656 | recovery phase; no anatomy stated |
| `N/a; Surgical Recovery` | 1,178 | recovery phase; no anatomy stated |
| `N/a; Facemask` | 93 | equipment; no anatomy stated |
| `Not Available; Medical Condition` | 78 | medical condition; no anatomy stated |
| `Return to Competition` | 77 | recovery phase; no anatomy stated |
| `N/a; Surgical recovery` | 59 | capitalization variant; no anatomy stated |
| `N/A; Sprain (Mask)` | 32 | type stated; anatomy absent |
| `Neuropathy` | 26 | condition stated; anatomy absent |

There are 7,816 anatomy-null injury observations. Most are correctly typed recovery, equipment, or
general medical concepts. There are 4,414 type-null injury observations, led by anatomy-only or
incomplete phrases: right hamstring (319), right shoulder (137), right hip flexor (109), left knee
(86), left ankle (84), left knee capitalization variant (77), left hamstring (76), bare meniscus
(75), left shoulder (60), and left sciatic nerve (59).

Only 26 observations have neither anatomy nor type:

| Preserved raw reason | Observations |
| --- | ---: |
| `Right Medial Femoral` | 9 |
| `Left Fourth` | 7 |
| `Left Lateral` | 6 |
| `Not Available;` | 1 |
| `N/a; -` | 1 |
| `Probable` | 1 |
| blank quotation marks | 1 |

These are incomplete or placeholder source assertions. Guessing their missing anatomy or diagnosis
would be less defensible than retaining nulls.

The conservative ambiguity audit flags 2,626 unsplit entries. Frequent examples are nasal
fracture/face mask (333, intentionally one medical condition), knee sprain/ACL tear (212),
knee/elbow sprains (148), knee/back soreness (132), and foot/ankle soreness (131). Some represent
one diagnosis spanning neighboring anatomy; others may encode multiple conditions but lack enough
clause-level structure to assign types safely. They remain one condition with the complete raw text.

Known methodological limitations are therefore:

- lexical rules cannot recover omitted diagnoses or anatomy;
- a medically categorized unknown remains an injury condition unless it is explicitly non-medical;
- coupled anatomy with one shared type is not expanded into inferred diagnoses;
- normalized anatomy is intentionally coarser than some raw source terms;
- status and condition text describe a report observation, not a clinical diagnosis or game
  participation event.

## Reproducibility

From the repository root, with PostgreSQL running:

```bash
.venv/bin/python -m app.jobs.reclassify_nba_conditions
.venv/bin/python -m app.jobs.audit_nba_classification
```

The reclassification job reads stored `NBAReportEntry.raw_reason` and `reason_category`, updates
only derived `NBAInjuryCondition` rows, and contains no NBA acquisition path. It commits batches of
5,000 entries and selects only rows not already on the current version, so it is resumable and
idempotent. It refuses to run when any `NBAInjuryEpisode` exists, preventing silent invalidation of
a downstream layer.

The completed full run selected 1,011,772 v6 rows and wrote 1,011,752 v7 conditions. An immediate
second run returned `selected=0, updated=0`.
