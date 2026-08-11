from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import case, delete, select
from sqlalchemy.orm import Session

from app.models import Injury, RawTransaction

FINAL_COLUMNS = [
    "date",
    "season",
    "player_name",
    "team",
    "body_part",
    "injury_type",
    "notes",
    "preferred_source",
    "source_raw_transaction_id",
]

# pandas.read_csv converts these strings to missing values by default. Historical imports retain
# the source strings, so processing must reproduce that conversion before applying legacy rules.
LEGACY_CSV_NA_VALUES = frozenset(
    {
        "",
        "#N/A",
        "#N/A N/A",
        "#NA",
        "-1.#IND",
        "-1.#QNAN",
        "-NaN",
        "-nan",
        "1.#IND",
        "1.#QNAN",
        "<NA>",
        "N/A",
        "NA",
        "NULL",
        "NaN",
        "None",
        "n/a",
        "nan",
        "null",
    }
)


@dataclass(frozen=True)
class RebuildResult:
    raw_rows: int
    injury_rows: int


def _legacy_csv_value(value: str | None) -> str | None:
    if value is None or value in LEGACY_CSV_NA_VALUES:
        return None
    return value


def is_recovery_note(notes_text: object) -> bool:
    """Match the legacy pipeline's two recovery-note phrases."""

    if pd.isna(notes_text):
        return False

    notes_lower = str(notes_text).lower()
    recovery_patterns = [
        "recovering from surgery",
        "placed on il recovering from",
    ]
    return any(pattern in notes_lower for pattern in recovery_patterns)


def extract_injury_info(notes_text: object) -> tuple[str | None, str | None]:
    """Classify notes using the legacy pipeline's ordered keyword rules."""

    if pd.isna(notes_text):
        return None, None

    notes_lower = str(notes_text).lower()
    body_part = None
    injury_type = None

    if is_recovery_note(notes_text):
        if "achilles" in notes_lower:
            body_part = "achilles"
        elif "acl" in notes_lower or "knee" in notes_lower:
            body_part = "knee"
        elif "calf" in notes_lower:
            body_part = "calf"
        injury_type = "recovery"
        return body_part, injury_type

    if "acl" in notes_lower or "anterior cruciate ligament" in notes_lower:
        body_part = "knee"
        injury_type = (
            "ACL tear"
            if any(word in notes_lower for word in ["torn", "tear", "ruptured", "reconstruct"])
            else "ACL injury"
        )
    elif "mcl" in notes_lower or "medial collateral ligament" in notes_lower:
        body_part = "knee"
        injury_type = (
            "MCL tear"
            if any(word in notes_lower for word in ["torn", "tear", "ruptured"])
            else "MCL injury"
        )
    elif "pcl" in notes_lower or "posterior cruciate ligament" in notes_lower:
        body_part = "knee"
        injury_type = (
            "PCL tear"
            if any(word in notes_lower for word in ["torn", "tear", "ruptured"])
            else "PCL injury"
        )
    elif "achilles" in notes_lower:
        body_part = "achilles"
        injury_type = (
            "tear"
            if any(word in notes_lower for word in ["torn", "tear", "ruptured"])
            else "achilles injury"
        )
    elif "calf" in notes_lower:
        body_part = "calf"
        if "strain" in notes_lower or "strained" in notes_lower:
            injury_type = "strain"
        elif "tear" in notes_lower or "torn" in notes_lower:
            injury_type = "tear"
        elif "sore" in notes_lower or "soreness" in notes_lower:
            injury_type = "soreness"
        elif "sprain" in notes_lower:
            injury_type = "sprain"
        else:
            injury_type = "injury"
    else:
        body_parts_map = {
            "knee": "knee",
            "ankle": "ankle",
            "foot": "foot",
            "hand": "hand",
            "wrist": "wrist",
            "shoulder": "shoulder",
            "elbow": "elbow",
            "hip": "hip",
            "back": "back",
            "hamstring": "hamstring",
            "quadriceps": "quadriceps",
            "quad": "quadriceps",
            "groin": "groin",
            "thumb": "thumb",
            "finger": "finger",
            "toe": "toe",
            "neck": "neck",
            "head": "head",
            "shin": "shin",
            "thigh": "thigh",
            "abductor": "abductor",
            "plantaris": "plantaris",
            "leg": "leg",
            "arm": "arm",
            "chest": "chest",
            "rib": "rib",
            "abdomen": "abdomen",
            "abdominal": "abdomen",
            "eye": "eye",
            "nose": "nose",
            "mouth": "mouth",
            "jaw": "jaw",
        }

        for key, value in body_parts_map.items():
            if key in notes_lower:
                body_part = value
                break

        if "plantar fasci" in notes_lower:
            injury_type = "plantar fasciitis"
        elif "hyperextend" in notes_lower:
            injury_type = "hyperextension"
        elif "turf toe" in notes_lower:
            injury_type = "turf toe"
        elif "stress reaction" in notes_lower or "stress fracture" in notes_lower:
            injury_type = "stress reaction"
        elif "sublux" in notes_lower:
            injury_type = "subluxation"
        elif "impingement" in notes_lower:
            injury_type = "impingement"
        elif "bursitis" in notes_lower:
            injury_type = "bursitis"
        elif "infection" in notes_lower or "infected" in notes_lower:
            injury_type = "infection"
        elif "tendinopathy" in notes_lower or "tendinosis" in notes_lower:
            injury_type = "tendinopathy"
        elif "synovitis" in notes_lower:
            injury_type = "synovitis"
        elif "blood clot" in notes_lower:
            injury_type = "blood clot"
        elif "corneal abrasion" in notes_lower or (
            "abrasion" in notes_lower and body_part == "eye"
        ):
            injury_type = "corneal abrasion"
        elif "shin split" in notes_lower:
            injury_type = "shin splints"
        elif "nerve" in notes_lower or "pinched" in notes_lower:
            injury_type = "nerve issue"
        elif "bulging disc" in notes_lower or ("disc" in notes_lower and body_part == "back"):
            injury_type = "disc injury"
        elif "loose" in notes_lower and ("cartilage" in notes_lower or "bodies" in notes_lower):
            injury_type = "loose bodies"
        elif "scar tissue" in notes_lower:
            injury_type = "scar tissue"
        elif "cyst" in notes_lower:
            injury_type = "cyst"
        elif "concussion" in notes_lower:
            injury_type = "concussion"
        elif "hernia" in notes_lower:
            injury_type = "hernia"
        elif "retina" in notes_lower:
            injury_type = "retinal injury"
        elif "dislocate" in notes_lower or "separated" in notes_lower:
            injury_type = "dislocation"
        elif "contusion" in notes_lower or "pointer" in notes_lower:
            injury_type = "contusion"
        elif "swelling" in notes_lower or "swollen" in notes_lower:
            injury_type = "swelling"
        elif "laceration" in notes_lower or "lacerated" in notes_lower:
            injury_type = "laceration"
        elif any(word in notes_lower for word in ["torn", "tear", "ruptured"]):
            injury_type = "tear"
        elif "strain" in notes_lower or "strained" in notes_lower:
            injury_type = "strain"
        elif "sprain" in notes_lower or "sprained" in notes_lower:
            injury_type = "sprain"
        elif "fracture" in notes_lower or "broken" in notes_lower:
            injury_type = "fracture"
        elif "surgery" in notes_lower:
            injury_type = "surgery"
        elif "bruise" in notes_lower or "bruised" in notes_lower:
            injury_type = "bruise"
        elif any(
            word in notes_lower for word in ["illness", "flu", "virus", "sick", "migraine", "covid"]
        ):
            body_part = "illness"
            injury_type = "illness"
        elif "sore" in notes_lower or "soreness" in notes_lower:
            injury_type = "soreness"
        elif "stiff" in notes_lower or "stiffness" in notes_lower:
            injury_type = "stiffness"
        elif any(
            word in notes_lower for word in ["inflammation", "tendinitis", "irritation", "inflamed"]
        ):
            injury_type = "inflammation"
        elif "tightness" in notes_lower:
            injury_type = "tightness"
        elif "spasm" in notes_lower:
            injury_type = "spasm"
        elif any(word in notes_lower for word in ["legal", "fined", "coach"]):
            injury_type = "non-injury"
        elif "rehab" in notes_lower:
            injury_type = "rehab"
        elif "rest" in notes_lower:
            injury_type = "rest"
        else:
            injury_type = "injury"

    return body_part, injury_type


def get_nba_season(value: date | pd.Timestamp) -> str:
    year = value.year
    month = value.month
    if month >= 10:
        return f"{year}-{str(year + 1)[-2:]}"
    return f"{year - 1}-{str(year)[-2:]}"


def _source_name(source_type: str) -> str:
    return "NBA_IL" if source_type == "il" else "NBA_Missed_Games"


def _score_record(row: pd.Series) -> int:
    score = 0
    if row["source"] == "NBA_IL":
        score += 1000
    if pd.notna(row["notes"]) and "placed on il" in str(row["notes"]).lower():
        score += 500
    score += int(row["notes_length"])
    return score


def _deduplicate_by_body_part_and_time(frame: pd.DataFrame) -> pd.DataFrame:
    keep_indices: list[int] = []

    for player in frame["player_name"].unique():
        player_frame = frame[frame["player_name"] == player]

        for body_part in player_frame["body_part"].unique():
            body_part_frame = player_frame[player_frame["body_part"] == body_part]
            body_part_frame = body_part_frame.sort_values("date")
            body_part_indices = body_part_frame.index.tolist()
            if not body_part_indices:
                continue

            first_injury_type = body_part_frame.loc[body_part_indices[0], "injury_type"]
            if first_injury_type in ["ACL tear", "MCL tear", "PCL tear", "tear"] and body_part in [
                "knee",
                "achilles",
            ]:
                base_window = 365
            elif first_injury_type == "surgery":
                base_window = 180
            elif first_injury_type in ["achilles injury", "fracture"]:
                base_window = 90
            else:
                base_window = 30

            keep_indices.append(body_part_indices[0])
            last_kept_date = body_part_frame.loc[body_part_indices[0], "date"]
            last_kept_type = first_injury_type

            for index in body_part_indices[1:]:
                current_date = body_part_frame.loc[index, "date"]
                current_type = body_part_frame.loc[index, "injury_type"]
                days_diff = (current_date - last_kept_date).days

                is_related = False
                if (
                    last_kept_type in ["tear", "ACL tear", "MCL tear", "PCL tear"]
                    and current_type == "surgery"
                    and days_diff <= 180
                ):
                    is_related = True
                elif (
                    last_kept_type == "tear"
                    and body_part == "achilles"
                    and current_type == "achilles injury"
                    and days_diff <= 750
                ):
                    is_related = True
                elif (
                    last_kept_type == "achilles injury"
                    and current_type == "achilles injury"
                    and days_diff <= 180
                ):
                    is_related = True
                elif last_kept_type == "surgery" and current_type == "surgery" and days_diff <= 365:
                    is_related = True

                if is_related:
                    continue
                if days_diff > base_window:
                    keep_indices.append(index)
                    last_kept_date = current_date
                    last_kept_type = current_type
                    if current_type in [
                        "ACL tear",
                        "MCL tear",
                        "PCL tear",
                        "tear",
                    ] and body_part in ["knee", "achilles"]:
                        base_window = 365
                    elif current_type == "surgery":
                        base_window = 180
                    elif current_type in ["achilles injury", "fracture"]:
                        base_window = 90
                    else:
                        base_window = 30

    return frame.loc[keep_indices].sort_values("date").reset_index(drop=True)


def process_raw_transactions(raw_transactions: Sequence[RawTransaction]) -> pd.DataFrame:
    """Apply the legacy extraction and deduplication pipeline to raw database rows."""

    candidates: list[dict[str, Any]] = []
    for raw in raw_transactions:
        acquired = _legacy_csv_value(raw.acquired)
        relinquished = _legacy_csv_value(raw.relinquished)
        if pd.notna(acquired) and pd.isna(relinquished):
            continue
        if pd.isna(relinquished):
            continue

        notes = _legacy_csv_value(raw.notes)
        body_part, injury_type = extract_injury_info(notes)
        candidates.append(
            {
                "date": raw.transaction_date,
                "player_name": str(relinquished).strip().lstrip("•").strip(),
                "team": _legacy_csv_value(raw.team),
                "body_part": body_part,
                "injury_type": injury_type,
                "notes": notes,
                "source": _source_name(raw.source_type),
                "preferred_source": raw.source_type,
                "source_raw_transaction_id": raw.id,
                "notes_length": len(str(notes)) if pd.notna(notes) else 0,
            }
        )

    if not candidates:
        return pd.DataFrame(columns=FINAL_COLUMNS)

    merged = pd.DataFrame(candidates)
    merged["date"] = pd.to_datetime(merged["date"])
    merged["dedup_score"] = merged.apply(_score_record, axis=1)
    exact_dedup = merged.sort_values("dedup_score", ascending=False).drop_duplicates(
        subset=["date", "player_name", "team", "body_part", "injury_type"],
        keep="first",
    )
    without_recovery = exact_dedup[exact_dedup["injury_type"] != "recovery"].copy()
    deduplicated = _deduplicate_by_body_part_and_time(without_recovery)
    deduplicated["season"] = deduplicated["date"].apply(get_nba_season)
    return deduplicated[FINAL_COLUMNS].copy()


def _nullable(value: object) -> object | None:
    return None if pd.isna(value) else value


def rebuild_injuries(session: Session) -> RebuildResult:
    """Atomically replace normalized injuries from all stored raw transactions."""

    source_order = case((RawTransaction.source_type == "il", 0), else_=1)
    raw_transactions = list(
        session.scalars(select(RawTransaction).order_by(source_order, RawTransaction.id))
    )
    frame = process_raw_transactions(raw_transactions)

    try:
        session.execute(delete(Injury))
        session.add_all(
            [
                Injury(
                    date=row.date.date(),
                    season=row.season,
                    player_name=row.player_name,
                    team=_nullable(row.team),
                    body_part=_nullable(row.body_part),
                    injury_type=row.injury_type,
                    notes=_nullable(row.notes),
                    preferred_source=row.preferred_source,
                    source_raw_transaction_id=int(row.source_raw_transaction_id),
                )
                for row in frame.itertuples(index=False)
            ]
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return RebuildResult(raw_rows=len(raw_transactions), injury_rows=len(frame))
