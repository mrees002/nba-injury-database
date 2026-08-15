import pytest

from app.nba.classification import CLASSIFICATION_VERSION, classify_conditions, classify_reason


def test_classifier_has_an_explicit_methodology_version():
    assert CLASSIFICATION_VERSION == "nba-reason-v7"


def test_preserves_laterality_and_nba_specific_terms():
    acl = classify_reason("Injury/Illness - Left Knee; ACL Reconstruction", "Injury/Illness")
    assert (acl.body_part, acl.laterality, acl.injury_type) == ("knee", "left", "surgery")

    achilles = classify_reason("Injury/Illness - Right Achilles; Rupture", "Injury/Illness")
    assert (achilles.body_part, achilles.laterality, achilles.injury_type) == (
        "achilles",
        "right",
        "tear",
    )


def test_non_injury_reasons_are_not_episode_candidates():
    assert not classify_reason("G League - Two-Way", "G League").is_injury
    assert not classify_reason("Rest").is_injury
    assert not classify_reason("Injury/Illness - Rest", "Injury/Illness").is_injury
    assert not classify_reason("Injury/Illness - G League", "Injury/Illness").is_injury
    assert not classify_reason("Injury/Illness - Personal reasons", "Injury/Illness").is_injury
    assert not classify_reason("Injury/Illness - Personal", "Injury/Illness").is_injury
    assert not classify_reason("Injury/Illness - N/A; Family Reasons", "Injury/Illness").is_injury
    assert not classify_reason("Ineligible To Play").is_injury
    assert not classify_reason("Trade Pending - -", "Trade Pending").is_injury
    assert not classify_reason("ALL PLAYERS AVAILABLE").is_injury
    assert classify_reason("Injury/Illness - N/A; Illness", "Injury/Illness").is_injury


def test_unclassified_injury_reason_is_preserved():
    result = classify_reason("Injury/Illness - N/A; Unusual condition", "Injury/Illness")
    assert result.is_injury
    assert result.body_part is None
    assert result.injury_type is None
    assert "unusual condition" in result.normalized_reason


def test_body_part_terms_do_not_match_inside_unrelated_words():
    whiplash = classify_reason("Injury/Illness - Neck; Whiplash", "Injury/Illness")
    discomfort = classify_reason("Injury/Illness - General discomfort", "Injury/Illness")
    tear = classify_reason("Injury/Illness - Muscle tear", "Injury/Illness")
    assert whiplash.body_part == "neck"
    assert discomfort.body_part is None
    assert tear.body_part is None


def test_non_injury_terms_do_not_match_inside_unrelated_words():
    restorative = classify_reason(
        "Injury/Illness - Left Knee; Restorative Procedure", "Injury/Illness"
    )
    personalized = classify_reason(
        "Injury/Illness - Personalized Medical Assessment", "Injury/Illness"
    )
    assert restorative.is_injury
    assert restorative.injury_type == "surgery"
    assert personalized.is_injury


def test_official_nba_anatomy_and_procedure_vocabulary():
    cases = {
        "Injury/Illness - Left Cuneiform; Fracture": ("foot", "left", "fracture"),
        "Injury/Illness - Right Syndesmosis; Injury w/ Fibula Fracture": (
            "ankle",
            "right",
            "fracture",
        ),
        "Injury/Illness - Left 4th Metacarpal; Fracture": ("hand", "left", "fracture"),
        "Injury/Illness - Right Knee; Arthroscopy": ("knee", "right", "surgery"),
        "Injury/Illness - Right Foot; Plantar Fasciitis": (
            "foot",
            "right",
            "plantar fasciitis",
        ),
        "Injury/Illness - Left Hip; Bone Edema": ("hip", "left", "swelling"),
        "Injury/Illness - Left Plantar Fascia; Fasciitis": (
            "foot",
            "left",
            "plantar fasciitis",
        ),
        "Injury/Illness - Right Ulnar Styloid; Fracture": ("wrist", "right", "fracture"),
        "Injury/Illness - Right Retrocalcaneal; Bursitis": ("foot", "right", "bursitis"),
        "Injury/Illness - Left Thumb; Sprain": ("thumb", "left", "sprain"),
        "Injury/Illness - Left Navicular; Fracture": ("foot", "left", "fracture"),
        "Injury/Illness - Rectus Abdominis; Strain": ("abdomen", None, "strain"),
        "Injury/Illness - Left UCL; Injury": ("elbow", "left", "injury"),
        "Injury/Illness - Left AC Joint; Sprain": ("shoulder", "left", "sprain"),
        "Injury/Illness - Left Scapholunate; Ligament Surgery": (
            "wrist",
            "left",
            "surgery",
        ),
        "Injury/Illness - Left Pubic Bone; Stress Fracture": (
            "pelvis",
            "left",
            "fracture",
        ),
        "Injury/Illness - Left Rotator Cuff; Strain": ("shoulder", "left", "strain"),
        "Injury/Illness - Right Peroneal; Soreness": ("lower leg", "right", "soreness"),
        "Injury/Illness - Right Midfoot; Sprain": ("foot", "right", "sprain"),
        "Injury/Illness - Left Scaphoid; Fracture": ("wrist", "left", "fracture"),
        "Injury/Illness - Right Iliac Wing; Fracture": ("pelvis", "right", "fracture"),
        "Injury/Illness - Right Medial Femoral Condyle; Fracture": (
            "knee",
            "right",
            "fracture",
        ),
        "Injury/Illness - Left Ribcage; Contusion": ("rib", "left", "contusion"),
        "Injury/Illness - Right Olecranon; Bursitis": ("elbow", "right", "bursitis"),
        "Injury/Illness - Sternum; Contusion": ("chest", None, "contusion"),
        "Injury/Illness - Right Orbital Bone; Fracture": ("face", "right", "fracture"),
        "Injury/Illness - Left Posterior Tibialis Tendon; Surgery Rehabilitation": (
            "posterior tibialis",
            "left",
            "recovery",
        ),
        "Injury/Illness - Left Superior Tib/fib; Sprain": ("lower leg", "left", "sprain"),
        "Injury/Illness - Left Pinky; Fracture": ("finger", "left", "fracture"),
        "Injury/Illness - Right Trunk; Contusion": ("torso", "right", "contusion"),
        "Injury/Illness - Right Upper Trap; Strain": ("trapezius", "right", "strain"),
        "Injury/Illness - Anterior Triangle; Contusion": ("neck", None, "contusion"),
        "Injury/Illness - Bilateral Shins; Soreness": (
            "lower leg",
            "bilateral",
            "soreness",
        ),
        "Injury/Illness - Right Pectoralis; Strain": ("chest", "right", "strain"),
        "Injury/Illness - Right Midback; Spasms": ("back", "right", "spasm"),
        "Injury/Illness - Right Anke; Sprain": ("ankle", "right", "sprain"),
        "Injury/Illness - Left Achillies; Tendinopathy": (
            "achilles",
            "left",
            "tendinopathy",
        ),
        "Injury/Illness - Gluteus Maximus; Contusion": ("hip", None, "contusion"),
        "Injury/Illness - Left AC; Sprain, Grade 1": ("shoulder", "left", "sprain"),
        "Injury/Illness - Load management - bilateral knees": (
            "knee",
            "bilateral",
            "injury management",
        ),
        "Injury/Illness - Overall Body; Soreness": (
            "whole body",
            None,
            "soreness",
        ),
    }
    for raw_reason, expected in cases.items():
        result = classify_reason(raw_reason, "Injury/Illness")
        assert (result.body_part, result.laterality, result.injury_type) == expected


def test_independently_stated_simultaneous_conditions_are_split():
    conditions = classify_conditions(
        "Injury/Illness - Left Hip; Strain, Right Hip; Soreness", "Injury/Illness"
    )
    assert [
        (condition.body_part, condition.laterality, condition.injury_type)
        for condition in conditions
    ] == [("hip", "left", "strain"), ("hip", "right", "soreness")]

    slash_conditions = classify_conditions(
        "Injury/Illness - Right quadriceps soreness/Left hip contusion",
        "Injury/Illness",
    )
    assert [(condition.body_part, condition.laterality) for condition in slash_conditions] == [
        ("quadriceps", "right"),
        ("hip", "left"),
    ]


def test_mixed_medical_and_non_injury_clauses_remain_distinguishable():
    slash = classify_conditions(
        "Injury/Illness - Left Groin; Soreness / G League Assignment",
        "Injury/Illness",
    )
    semicolon = classify_conditions(
        "Injury/Illness - Left Knee; Soreness; Rest",
        "Injury/Illness",
    )

    assert [(item.body_part, item.injury_type, item.is_injury) for item in slash] == [
        ("groin", "soreness", True),
        (None, None, False),
    ]
    assert [(item.body_part, item.injury_type, item.is_injury) for item in semicolon] == [
        ("knee", "soreness", True),
        (None, None, False),
    ]


def test_mixed_non_injury_qualifier_remains_one_clause():
    conditions = classify_conditions(
        "Injury/Illness - Right Foot; inflammation & not with team, self-isolating",
        "Injury/Illness",
    )
    assert [(item.body_part, item.injury_type, item.is_injury) for item in conditions] == [
        ("foot", "inflammation", True),
        (None, None, False),
    ]


def test_lexical_slashes_are_not_treated_as_condition_delimiters():
    n_a = classify_conditions(
        "Injury/Illness - N/A; Illness (G League - two way)", "Injury/Illness"
    )
    embedded_header = classify_conditions(
        "Recovery - Splint; Left Knee Surgery Injury/Illness - Right Thumb; "
        "Surgery Recovery - Brace",
        None,
    )

    assert [(item.body_part, item.injury_type, item.is_injury) for item in n_a] == [
        ("illness", "illness", True),
        (None, None, False),
    ]
    assert len(embedded_header) == 1


def test_ambiguous_conjunction_is_not_split_without_independent_anatomy():
    conditions = classify_conditions(
        "Injury/Illness - Ankle soreness - right and left", "Injury/Illness"
    )
    assert len(conditions) == 1
    assert conditions[0].laterality == "bilateral"

    descriptive_comma = classify_conditions(
        "Injury/Illness - Fractured Third Finger, Left Hand", "Injury/Illness"
    )
    regional_slash = classify_conditions(
        "Injury/Illness - Right Foot/Ankle; Sprain", "Injury/Illness"
    )
    illness_synonyms = classify_conditions("Injury/Illness - Illness/Infection", "Injury/Illness")
    protective_mask = classify_conditions(
        "Injury/Illness - Nasal; Fracture / Face Mask", "Injury/Illness"
    )
    assert len(descriptive_comma) == 1
    assert len(regional_slash) == 1
    assert len(illness_synonyms) == 1
    assert len(protective_mask) == 1
    assert protective_mask[0].injury_type == "fracture"


def test_shared_laterality_is_propagated_across_split_ligament_conditions():
    conditions = classify_conditions(
        "Injury/Illness - Right Knee ACL/MCL; Sprain", "Injury/Illness"
    )
    assert [(item.body_part, item.laterality, item.injury_type) for item in conditions] == [
        ("knee", "right", "ACL injury"),
        ("knee", "right", "MCL injury"),
    ]


def test_observed_official_medical_vocabulary_is_classified_defensibly():
    cases = {
        "Injury/Illness - Right Lung; Pneumothorax": (
            "respiratory system",
            "right",
            "pneumothorax",
        ),
        "Injury/Illness - Upper Respiratory Infection": (
            "respiratory system",
            None,
            "infection",
        ),
        "Injury/Illness - Stomach; Gastroenteritis": ("stomach", None, "illness"),
        "Injury/Illness - N/A; Migraine": ("head", None, "migraine"),
        "Injury/Illness - Neuropathy": (None, None, "neuropathy"),
        "Injury/Illness - Lip; Laceration": ("face", None, "laceration"),
        "Injury/Illness - Appendectomy": ("abdomen", None, "surgery"),
        "Injury/Illness - DVT, right arm": ("arm", "right", "blood clot"),
        "Injury/Illness - Right General; Inguinal Hernia": ("groin", "right", "hernia"),
        "Injury/Illness - Medical assessment": (None, None, "medical condition"),
        "Injury/Illness - Allergic; Reaction": (None, None, "allergic reaction"),
        "Injury/Illness - Sinusitis": ("respiratory system", None, "infection"),
        "Injury/Illness - N/A; Pharyngitis": ("throat", None, "infection"),
        "Injury/Illness - Sinus; Congestion": ("respiratory system", None, "illness"),
        "Injury/Illness - Right corneal; Abrasion": ("eye", "right", "abrasion"),
        "Injury/Illness - Neck; Whiplash": ("neck", None, "whiplash"),
        "Injury/Illness - Right Thumb; UCL Reconstruction": ("thumb", "right", "surgery"),
        "Injury/Illness - Right Knee; Effusion": ("knee", "right", "swelling"),
        "Injury/Illness - Left Knee; Meniscectomy": ("knee", "left", "surgery"),
        "Injury/Illness - Left Foot; Turf Toe": ("foot", "left", "turf toe"),
        "Injury/Illness - Left Thigh; Contused": ("thigh", "left", "contusion"),
        "Injury/Illness - Right Sciatica; -": (
            "sciatic nerve",
            "right",
            "sciatica",
        ),
        "Injury/Illness - Left Sacroiliac Joint; Dysfunction": (
            "pelvis",
            "left",
            "dysfunction",
        ),
        "Injury/Illness - Asthma; Symptoms": (
            "respiratory system",
            None,
            "asthma",
        ),
        "Injury/Illness - Right Knee; Chondromalacia": (
            "knee",
            "right",
            "chondromalacia",
        ),
        "Injury/Illness - Back; Lumbar Spine Stress Response": (
            "back",
            None,
            "stress reaction",
        ),
        "Injury/Illness - Lumbar; Disc Bulge": ("back", None, "disc injury"),
        "Injury/Illness - Right Hip; Synovitis": ("hip", "right", "inflammation"),
        "Injury/Illness - Left Lumbar; Radiculopathy": (
            "back",
            "left",
            "nerve issue",
        ),
        "Injury/Illness - Right Shoulder; AC Separation": (
            "shoulder",
            "right",
            "separation",
        ),
        "Injury/Illness - Left Cervical; Stinger": ("neck", "left", "nerve issue"),
        "Injury/Illness - Left Eye; Iritis": ("eye", "left", "inflammation"),
        "Injury/Illness - Right Knee; Patellar Tendonitis": (
            "knee",
            "right",
            "inflammation",
        ),
        "Injury/Illness - N/a; Facemask": (None, None, "protective equipment"),
        "Injury/Illness - Face; Mask": ("face", None, "protective equipment"),
        "Injury/Illness - Right Thumb; Splint": ("thumb", "right", "immobilization"),
        "Injury/Illness - Right Index Finger; Lacerated": (
            "finger",
            "right",
            "laceration",
        ),
        "Injury/Illness - Right Calf; Contuson": ("calf", "right", "contusion"),
        "Injury/Illness - N/a; Headache": ("head", None, "headache"),
        "Injury/Illness - N/a; Post Appendectomy Surgery Recovery": (
            "abdomen",
            None,
            "recovery",
        ),
        "Injury/Illness - N/A; Illlness": ("illness", None, "illness"),
        "Injury/Illness - N/a; G/I symptoms": ("stomach", None, "illness"),
        "Return to Competition": (None, None, "recovery"),
    }
    for raw_reason, expected in cases.items():
        result = classify_reason(raw_reason, "Injury/Illness")
        assert (result.body_part, result.laterality, result.injury_type) == expected


@pytest.mark.parametrize(
    ("raw_reason", "expected"),
    [
        ("Injury/Illness - Left Ankle; Sprain", ("ankle", "left", "sprain")),
        ("Injury/Illness - Right Hamstring; Strain", ("hamstring", "right", "strain")),
        ("Injury/Illness - Left Groin; Soreness", ("groin", "left", "soreness")),
        ("Injury/Illness - Concussion", ("head", None, "concussion")),
        ("Injury/Illness - N/A; Influenza", ("illness", None, "illness")),
        ("Injury/Illness - Right Achilles; Tendinopathy", ("achilles", "right", "tendinopathy")),
        ("Injury/Illness - Right Knee; Meniscus Tear", ("knee", "right", "tear")),
        ("Injury/Illness - Left Knee; MCL Tear", ("knee", "left", "MCL tear")),
        ("Injury/Illness - Right Knee; ACL Tear", ("knee", "right", "ACL tear")),
        ("Injury/Illness - Right Knee; ACL Reconstruction", ("knee", "right", "surgery")),
    ],
)
def test_core_taxonomy_examples(raw_reason, expected):
    result = classify_reason(raw_reason, "Injury/Illness")
    assert (result.body_part, result.laterality, result.injury_type) == expected
