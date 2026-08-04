"""Univerzalni troslojni kontroler pedagoške težine (matbot/difficulty_level.py)
— čista, deterministička jedinica bez ijedne zavisnosti na lekciju/porodicu.
"""
import re
from pathlib import Path

from matbot import difficulty_level

ROOT = Path(__file__).resolve().parent.parent


def test_fresh_level_plain_request_stays_unchanged():
    t = difficulty_level.transition(1, "")
    assert t.previous_level == 1
    assert t.target_level == 1
    assert t.request == "same"
    assert t.level_changed is False
    assert t.boundary_reason is None


def test_harder_increases_by_one():
    t = difficulty_level.transition(1, "harder")
    assert t.target_level == 2
    assert t.request == "harder"
    assert t.level_changed is True
    assert t.boundary_reason is None


def test_easier_decreases_by_one():
    t = difficulty_level.transition(2, "easier")
    assert t.target_level == 1
    assert t.request == "easier"
    assert t.level_changed is True
    assert t.boundary_reason is None


def test_harder_at_ceiling_stays_capped_and_reports_boundary():
    t = difficulty_level.transition(3, "harder")
    assert t.previous_level == 3
    assert t.target_level == 3
    assert t.level_changed is False
    assert t.boundary_reason == "at_maximum"


def test_easier_at_floor_stays_floored_and_reports_boundary():
    t = difficulty_level.transition(1, "easier")
    assert t.previous_level == 1
    assert t.target_level == 1
    assert t.level_changed is False
    assert t.boundary_reason == "at_minimum"


def test_full_transition_table():
    # (previous, request) -> (target, level_changed, boundary_reason)
    table = {
        (1, "harder"): (2, True, None),
        (2, "harder"): (3, True, None),
        (3, "harder"): (3, False, "at_maximum"),
        (3, "easier"): (2, True, None),
        (2, "easier"): (1, True, None),
        (1, "easier"): (1, False, "at_minimum"),
        (1, ""): (1, False, None),
        (2, ""): (2, False, None),
        (3, ""): (3, False, None),
    }
    for (previous, request), (target, changed, boundary) in table.items():
        t = difficulty_level.transition(previous, request)
        assert (t.target_level, t.level_changed, t.boundary_reason) == (target, changed, boundary), \
            (previous, request, t)


def test_request_is_case_and_whitespace_normalized():
    assert difficulty_level.transition(1, "  Harder ").target_level == 2
    assert difficulty_level.transition(2, "EASIER").target_level == 1


def test_unknown_request_text_is_treated_as_same():
    t = difficulty_level.transition(2, "bogus")
    assert t.request == "same"
    assert t.target_level == 2
    assert t.level_changed is False


def test_out_of_range_previous_level_is_clamped_defensively():
    assert difficulty_level.transition(0, "").previous_level == 1
    assert difficulty_level.transition(99, "").previous_level == 3


def test_level_to_label_exact_mapping():
    assert difficulty_level.LEVEL_TO_LABEL == {1: "easy", 2: "standard", 3: "hard"}


def test_prompt_block_contains_the_target_level_and_all_eight_dimensions():
    text = difficulty_level.prompt_block(3)
    assert "3" in text
    assert "napredna primjena" in text
    for dimension in difficulty_level._DIMENSIONS:
        assert dimension in text


def test_prompt_block_is_stable_and_deterministic():
    assert difficulty_level.prompt_block(2) == difficulty_level.prompt_block(2)


def test_prompt_block_clamps_out_of_range_levels():
    assert difficulty_level.prompt_block(0) == difficulty_level.prompt_block(1)
    assert difficulty_level.prompt_block(9) == difficulty_level.prompt_block(3)


def test_prompt_block_never_mentions_a_specific_lesson_family_or_form():
    # Univerzalnost: rubrik ne smije zavisiti od ijedne konkretne lekcije.
    for level in (1, 2, 3):
        text = difficulty_level.prompt_block(level).lower()
        for forbidden in ("razlomak", "trougao", "jednačin", "sistem", "geometrij"):
            assert forbidden not in text


def test_module_never_names_a_lesson_id():
    """Isti obrazac kao test_no_lesson_id_branching_was_introduced —
    modul ne smije poznavati nijedan kanonski ID lekcije."""
    topic_re = re.compile(r"\b\d-\d{2}-\d{3}\b")
    source = (ROOT / "matbot" / "difficulty_level.py").read_text(encoding="utf-8")
    offenders = [line.strip() for line in source.splitlines() if topic_re.search(line)]
    assert not offenders, offenders
