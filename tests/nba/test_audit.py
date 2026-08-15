from app.nba.audit import _potentially_ambiguous_unsplit


def test_potential_ambiguity_flags_unsplit_multi_anatomy_for_review():
    assert _potentially_ambiguous_unsplit(
        "Injury/Illness - Right Foot/Ankle; Sprain", "Injury/Illness"
    )
    assert not _potentially_ambiguous_unsplit(
        "Injury/Illness - Left Hip; Strain, Right Hip; Soreness", "Injury/Illness"
    )
    assert not _potentially_ambiguous_unsplit(
        "Injury/Illness - Right Ankle; Sprain", "Injury/Illness"
    )
