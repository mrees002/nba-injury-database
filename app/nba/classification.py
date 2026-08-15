from __future__ import annotations

import re
from dataclasses import replace

from app.nba.types import ClassifiedReason

CLASSIFICATION_VERSION = "nba-reason-v7"

_NON_INJURY = (
    "g league",
    "rest",
    "personal",
    "family reasons",
    "not with team",
    "coach's decision",
    "coach’s decision",
    "coaches decision",
    "suspension",
    "not yet submitted",
    "all players available",
    "contract expired",
    "trade pending",
    "ineligible to play",
)
_NON_INJURY_CATEGORIES = {
    "-",
    "coach's decision",
    "g league",
    "g league - on assignment",
    "g league - two-way",
    "g league team",
    "league suspension",
    "not with team",
    "not yet submitted",
    "personal reasons",
    "rest",
    "team suspension",
    "trade pending",
}
_NON_INJURY_CLAUSE_START = (
    r"(?:g[ -]?league(?:\s*[- ]?(?:on assignment|two[ -]?way|2[ -]?way|assignment))?"
    r"|rest|personal(?: reasons?)?|family reasons?|not with team|coach(?:es|'s|’s)? decision"
    r"|(?:league|team) suspension|trade pending|ineligible to play|contract expired)"
)
_RECOVERY_TERMS = (
    "return to competition",
    "return to play",
    "reconditioning",
    "re-conditioning",
    "re- conditioning",
    "conditioning",
    "recovery",
    "rehab",
    "rehabilitation",
)
_PROCEDURE_TERMS = (
    "arthroscopy",
    "scope",
    "procedure",
    "repair",
    "reconstruction",
    "meniscectomy",
    "appendectomy",
    "excision",
)
_BODY_PARTS = (
    (
        (
            "acl",
            "anterior cruciate ligament",
            "mcl",
            "pcl",
            "meniscus",
            "meniscal",
            "meniscectomy",
            "patellofemoral",
            "femoral condyle",
            "popliteus",
        ),
        "knee",
    ),
    (("achilles", "achillies"), "achilles"),
    (("posterior tibialis",), "posterior tibialis"),
    (("hamstring",), "hamstring"),
    (("quadricep", "quadriceps", "quad"), "quadriceps"),
    (("trapezius", "upper trap"), "trapezius"),
    (("groin", "adductor", "inguinal", "sports hernia"), "groin"),
    (("syndesmosis", "ankle", "anke"), "ankle"),
    (("knee", "knees", "patella", "patellar"), "knee"),
    (
        (
            "cuneiform",
            "cuboid",
            "metatarsal",
            "navicular",
            "lisfranc",
            "retrocalcaneal",
            "heel",
            "foot",
            "midfoot",
            "plantar",
        ),
        "foot",
    ),
    (("mtp", "mp joint", "hallucis", "toe", "toes"), "toe"),
    (
        (
            "pelvis",
            "pelvic",
            "pubic bone",
            "sacrum",
            "sacral",
            "sacroiliac",
            "si",
            "iliac wing",
            "ilium",
            "ischial",
        ),
        "pelvis",
    ),
    (("sciatic nerve", "sciatica"), "sciatic nerve"),
    (("hip", "glut", "glute", "gluteal", "gluteus"), "hip"),
    (("back", "midback", "lumbar", "thoracic", "thoratic", "disc", "facet"), "back"),
    (("acromioclavicular", "ac joint", "ac", "rotator cuff", "shoulder"), "shoulder"),
    (
        (
            "ulnar styloid",
            "scapholunate",
            "perilunate",
            "scaphoid",
            "distal radius",
            "flexor carpi ulnaris",
            "wrist",
        ),
        "wrist",
    ),
    (("metacarpal", "hand"), "hand"),
    (("thumb",), "thumb"),
    (("finger", "fingers", "pinkie", "pinky"), "finger"),
    (("ucl", "olecranon", "elbow"), "elbow"),
    (("calf", "soleus", "plantaris"), "calf"),
    (
        (
            "lower leg",
            "shin",
            "shins",
            "tib",
            "tibia",
            "tibial",
            "fib",
            "fibula",
            "fibular",
            "peroneal",
        ),
        "lower leg",
    ),
    (("leg",), "leg"),
    (("thigh",), "thigh"),
    (("neck", "cervical", "anterior triangle"), "neck"),
    (("concussion", "head"), "head"),
    (("migraine", "headache"), "head"),
    (("zygomatic", "orbital", "oribital", "facial", "face", "jaw", "lip", "chin"), "face"),
    (("dental", "tooth", "oral", "mouth"), "mouth"),
    (("throat", "pharyngitis"), "throat"),
    (("forearm",), "forearm"),
    (("biceps", "arm"), "arm"),
    (
        (
            "lung",
            "respiratory",
            "asthma",
            "bronchitis",
            "pneumothorax",
            "sinus",
            "sinusitis",
            "congestion",
        ),
        "respiratory system",
    ),
    (("ribcage", "costochondral", "rib", "ribs"), "rib"),
    (("sternum", "chest", "pectoral", "pectoralis"), "chest"),
    (("trunk",), "torso"),
    (("overall body",), "whole body"),
    (
        ("abdomen", "abdominal", "oblique", "core", "rectus abdominis", "appendectomy"),
        "abdomen",
    ),
    (("stomach", "g/i", "gastrointestinal", "gastroenteritis"), "stomach"),
    (("tailbone", "tail bone", "coccyx"), "tailbone"),
    (("nasal", "nose"), "nose"),
    (("retina", "retinal", "corneal", "cornea", "eye"), "eye"),
    (("ear",), "ear"),
    (("skin",), "skin"),
    (
        (
            "illness",
            "ilness",
            "illlness",
            "flu",
            "virus",
            "covid",
            "health and safety",
            "infection",
            "bronchitis",
            "gastroenteritis",
            "gastrointestinal",
            "g/i symptoms",
            "mononucleosis",
            "tonsillitis",
            "strep throat",
            "sinusitis",
            "pharyngitis",
            "congestion",
            "sinus",
            "food poisoning",
            "common cold",
            "cold",
            "sick",
            "under the weather",
        ),
        "illness",
    ),
)


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _contains_body_term(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


def _contains_explicit_non_injury(text: str) -> bool:
    return any(_contains_body_term(text, term) for term in _NON_INJURY)


def _is_explicit_non_injury(text: str, reason_category: str | None) -> bool:
    category = " ".join((reason_category or "").lower().split())
    return category in _NON_INJURY_CATEGORIES or _contains_explicit_non_injury(text)


def _is_recovery(text: str) -> bool:
    return _contains(text, _RECOVERY_TERMS)


def _is_procedure(text: str) -> bool:
    return "surgery" in text or "surgical" in text or _contains(text, _PROCEDURE_TERMS)


def _split_reason_parts(detail: str) -> list[str]:
    protected_slash = "\ue000"
    protected = re.sub(
        r"\b(?:n\s*/\s*a|injury\s*/\s*illness|tib\s*/\s*fib)\b",
        lambda match: match.group().replace("/", protected_slash),
        detail,
        flags=re.IGNORECASE,
    )
    non_injury_boundary = re.search(
        rf"\s*(?:,|/|[;&]|\()\s*(?=(?:on\s+)?{_NON_INJURY_CLAUSE_START}\b)",
        protected,
        flags=re.IGNORECASE,
    )
    if non_injury_boundary:
        medical_text = protected[: non_injury_boundary.start()]
        non_injury_text = protected[non_injury_boundary.end() :]
    else:
        medical_text = protected
        non_injury_text = None

    medical_separator = r"\s*(?:,|/)\s*|\s+and\s+(?=(?:left|right|bilateral)\b)"
    parts = re.split(medical_separator, medical_text, flags=re.IGNORECASE)
    if non_injury_text is not None:
        parts.append(non_injury_text)
    return [
        part.replace(protected_slash, "/").strip(" -;()") for part in parts if part.strip(" -;()")
    ]


def classify_reason(raw_reason: str | None, reason_category: str | None = None) -> ClassifiedReason:
    source = " ".join((raw_reason or "").split())
    lowered = source.lower().replace("n/a", "unspecified")
    detail = re.sub(r"^injury/illness\s*-\s*", "", lowered)
    normalized = re.sub(r"[^a-z0-9]+", " ", detail).strip()
    category = (reason_category or "").lower()
    is_non_injury = _is_explicit_non_injury(detail, reason_category)
    laterality = None
    if re.search(r"\bbilateral\b|\bleft and right\b|\bright and left\b|\bboth\b", detail):
        laterality = "bilateral"
    elif re.search(r"\bleft\b", detail):
        laterality = "left"
    elif re.search(r"\bright\b", detail):
        laterality = "right"

    body_part = None
    for terms, label in _BODY_PARTS:
        if any(_contains_body_term(detail, term) for term in terms):
            body_part = label
            break

    injury_type = None
    if "acl" in detail or "anterior cruciate ligament" in detail:
        if _is_recovery(detail):
            injury_type = "recovery"
        elif _is_procedure(detail):
            injury_type = "surgery"
        else:
            injury_type = "ACL tear" if _contains(detail, ("tear", "rupture")) else "ACL injury"
    elif "mcl" in detail or "medial collateral ligament" in detail:
        if _is_recovery(detail):
            injury_type = "recovery"
        elif _is_procedure(detail):
            injury_type = "surgery"
        else:
            injury_type = "MCL tear" if _contains(detail, ("tear", "rupture")) else "MCL injury"
    elif "pcl" in detail or "posterior cruciate ligament" in detail:
        if _is_recovery(detail):
            injury_type = "recovery"
        elif _is_procedure(detail):
            injury_type = "surgery"
        else:
            injury_type = "PCL tear" if _contains(detail, ("tear", "rupture")) else "PCL injury"
    elif "concussion" in detail:
        injury_type = "concussion"
    elif "whiplash" in detail:
        injury_type = "whiplash"
    elif "pneumothorax" in detail:
        injury_type = "pneumothorax"
    elif "neuropathy" in detail:
        injury_type = "neuropathy"
    elif "migraine" in detail:
        injury_type = "migraine"
    elif "headache" in detail:
        injury_type = "headache"
    elif "turf toe" in detail:
        injury_type = "turf toe"
    elif "sciatica" in detail:
        injury_type = "sciatica"
    elif "asthma" in detail:
        injury_type = "asthma"
    elif "chondromalacia" in detail:
        injury_type = "chondromalacia"
    elif _contains(detail, ("disc bulge", "disc protrusion", "annular fissure")):
        injury_type = "disc injury"
    elif _contains(detail, ("radiculopathy", "stinger", "neuroma")):
        injury_type = "nerve issue"
    elif _contains(detail, ("fracture", "fractuce", "broken")):
        injury_type = "fracture"
    elif _contains(detail, ("rupture", "tear", "torn")):
        injury_type = "tear"
    elif _contains(detail, ("sprain", "sprained")):
        injury_type = "sprain"
    elif _contains(detail, ("strain", "strained")):
        injury_type = "strain"
    elif _is_recovery(detail):
        injury_type = "recovery"
    elif _is_procedure(detail):
        injury_type = "surgery"
        if "appendectomy" in detail and body_part is None:
            body_part = "abdomen"
    elif _contains(detail, ("stress reaction", "stress response", "bone stress")):
        injury_type = "stress reaction"
    elif _contains(detail, ("hyperextension", "hyperextended")):
        injury_type = "hyperextension"
    elif "sublux" in detail:
        injury_type = "subluxation"
    elif _contains(detail, ("dislocation", "dislocated", "displacement")):
        injury_type = "dislocation"
    elif "separation" in detail:
        injury_type = "separation"
    elif _contains(detail, ("contusion", "contuson", "contused", "pointer")):
        injury_type = "contusion"
    elif "abrasion" in detail:
        injury_type = "abrasion"
    elif _contains(detail, ("laceration", "lacerated", "stitches", "sutures")):
        injury_type = "laceration"
    elif _contains(detail, ("bone bruise", "bruise", "bruised")):
        injury_type = "bruise"
    elif _contains(detail, ("pain", "discomfort", "ache")):
        injury_type = "pain"
    elif _contains(detail, ("soreness", "sore")):
        injury_type = "soreness"
    elif _contains(
        detail,
        (
            "inflammation",
            "imflammation",
            "tendinitis",
            "tendonitis",
            "irritation",
            "synovitis",
            "iritis",
            "periostitis",
        ),
    ):
        injury_type = "inflammation"
    elif _contains(detail, ("tendinopathy", "tendonopathy", "tendinosis")):
        injury_type = "tendinopathy"
    elif "fasciitis" in detail:
        injury_type = "plantar fasciitis"
    elif "tenosynovitis" in detail:
        injury_type = "tenosynovitis"
    elif "sesamoiditis" in detail:
        injury_type = "sesamoiditis"
    elif "capsulitis" in detail:
        injury_type = "capsulitis"
    elif "bursitis" in detail:
        injury_type = "bursitis"
    elif _contains(detail, ("edema", "swelling", "swollen")):
        injury_type = "swelling"
    elif "effusion" in detail:
        injury_type = "swelling"
    elif "tightness" in detail:
        injury_type = "tightness"
    elif "tension" in detail:
        injury_type = "tightness"
    elif _contains(
        detail, ("infection", "strep throat", "tonsillitis", "sinusitis", "pharyngitis")
    ):
        injury_type = "infection"
    elif "allergic" in detail and "reaction" in detail:
        injury_type = "allergic reaction"
    elif _contains(
        detail,
        (
            "illness",
            "ilness",
            "illlness",
            "flu",
            "influenza",
            "infuenza",
            "virus",
            "covid",
            "health and safety",
            "bronchitis",
            "gastroenteritis",
            "gastrointestinal",
            "g/i symptoms",
            "mononucleosis",
            "food poisoning",
            "common cold",
            "cold",
            "sick",
            "under the weather",
            "congestion",
        ),
    ):
        if body_part is None:
            body_part = "illness"
        injury_type = "illness"
    elif "management" in detail:
        injury_type = "injury management"
    elif "impingement" in detail:
        injury_type = "impingement"
    elif "stiffness" in detail:
        injury_type = "stiffness"
    elif "spasm" in detail:
        injury_type = "spasm"
    elif _contains(detail, ("neuritis", "neuropraxia")):
        injury_type = "nerve issue"
    elif "medical condition" in detail:
        injury_type = "medical condition"
    elif "medical assessment" in detail:
        injury_type = "medical condition"
    elif body_part == "respiratory system" and "condition" in detail:
        injury_type = "medical condition"
    elif _contains(detail, ("deep vein thrombosis", "blood clot", "dvt")):
        injury_type = "blood clot"
    elif _contains(detail, ("hernia",)):
        injury_type = "hernia"
    elif "dehydration" in detail:
        injury_type = "dehydration"
    elif "dysfunction" in detail or "dysfucntion" in detail:
        injury_type = "dysfunction"
    elif "instability" in detail:
        injury_type = "instability"
    elif "arthritis" in detail:
        injury_type = "arthritis"
    elif (
        "facemask" in detail
        or "face mask" in detail
        or detail.strip() == "mask"
        or (body_part == "face" and _contains_body_term(detail, "mask"))
    ):
        injury_type = "protective equipment"
    elif _contains(detail, ("splint", "brace", "cast")):
        injury_type = "immobilization"
    elif "lesion" in detail:
        injury_type = "lesion"
    elif detail.strip() == "tos" or "; tos" in detail:
        injury_type = "thoracic outlet syndrome"
    elif "syndrome" in detail:
        injury_type = "syndrome"
    elif "mallet" in detail:
        injury_type = "tendon injury"
    elif "tendon" in detail:
        injury_type = "tendon injury"
    elif _contains(detail, ("injury management", "injury maintenance", "maintenance")):
        injury_type = "injury management"
    elif "dental work" in detail:
        injury_type = "dental procedure"
    elif "injury" in detail:
        injury_type = "injury"

    is_injury = not is_non_injury and (
        "injury/illness" in category
        or "injury/illness" in lowered
        or body_part is not None
        or injury_type is not None
    )
    return ClassifiedReason(
        body_part=body_part,
        laterality=laterality,
        injury_type=injury_type,
        normalized_reason=normalized,
        is_injury=is_injury,
        classification_version=CLASSIFICATION_VERSION,
    )


def classify_conditions(
    raw_reason: str | None, reason_category: str | None = None
) -> tuple[ClassifiedReason, ...]:
    """Classify independently stated simultaneous conditions without altering source text.

    A comma or slash can delimit two conditions in official reports. ``and`` is treated as a
    delimiter only when the next clause starts with explicit laterality. We split only when every
    clause is independently injury-classifiable and the clauses resolve to distinct
    body-part/laterality pairs. Otherwise the original reason remains one condition.
    """

    original = classify_reason(raw_reason, reason_category)
    source = " ".join((raw_reason or "").split())
    detail = re.sub(r"^injury/illness\s*-\s*", "", source, flags=re.IGNORECASE)
    parts = _split_reason_parts(detail)
    if len(parts) < 2:
        return (original,)
    classified = tuple(classify_reason(part, reason_category) for part in parts)
    injury_parts = [item for item in classified if item.is_injury]
    non_injury_parts = [
        (part, item) for part, item in zip(parts, classified, strict=True) if not item.is_injury
    ]
    if injury_parts and non_injury_parts:
        if all(item.body_part and item.injury_type for item in injury_parts) and all(
            _is_explicit_non_injury(part.lower(), None) for part, _ in non_injury_parts
        ):
            return classified
        return (original,)
    if not all(item.is_injury and item.body_part and item.injury_type for item in classified):
        return (original,)
    if any(item.injury_type in {"protective equipment", "immobilization"} for item in classified):
        return (original,)
    body_parts = {item.body_part for item in classified}
    explicit_lateralities = {item.laterality for item in classified if item.laterality}
    injury_types = {item.injury_type for item in classified}
    if not explicit_lateralities and injury_types <= {"illness", "infection"}:
        return (original,)
    ligament_types = {"ACL injury", "ACL tear", "MCL injury", "MCL tear", "PCL injury", "PCL tear"}
    if (
        len(body_parts) == 1
        and len(explicit_lateralities) < 2
        and not all(item.injury_type in ligament_types for item in classified)
    ):
        return (original,)
    identities = {(item.body_part, item.laterality) for item in classified}
    injury_identities = {(item.body_part, item.injury_type) for item in classified}
    if len(identities) < 2 and len(injury_identities) < 2:
        return (original,)
    if len(explicit_lateralities) == 1:
        shared_laterality = next(iter(explicit_lateralities))
        classified = tuple(
            item if item.laterality else replace(item, laterality=shared_laterality)
            for item in classified
        )
    return classified
