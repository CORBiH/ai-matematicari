"""KAPIJA PARNOSTI: 528 lekcija bez ugovora mora se ponašati kao prije Faze A.

Uvođenje univerzalnog motora smjelo je promijeniti ponašanje ISKLJUČIVO za šest
pilot lekcija. Za sve ostale — `legacy_uncontracted`, `needs_review`,
`legacy_pinned` — routiranje porodica, redoslijed, prva porodica i ponašanje
teže/lakše moraju ostati identični.

Baseline je zamrznut u `tests/fixtures/legacy_routing_baseline.json`, generisan
NEZAVISNOM reimplementacijom istorijskog algoritma
(`scripts/freeze_legacy_routing.py`) — ne pozivom koda koji se testira.
"""
import json
from pathlib import Path

import pytest

from matbot import task_families as tf
from matbot.contracts import registry
from matbot.topics import lesson_info

ROOT = Path(__file__).resolve().parent.parent
BASELINE = json.loads(
    (ROOT / "tests" / "fixtures" / "legacy_routing_baseline.json").read_text(encoding="utf-8")
)
LESSONS = BASELINE["lessons"]
ENABLED = set(BASELINE["excluded_enabled_contracts"])


def _current(row):
    info = lesson_info(row["grade"], row["topic_id"])
    assert info is not None, row["topic_id"]
    return tf.applicable_families(
        row["grade"], info["oblast"], info["title"], lesson_id=row["topic_id"])


# --- 1-2: baseline postoji i pokriva tačno lekcije bez ugovora --------------

def test_1_frozen_baseline_covers_every_non_pilot_lesson():
    topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    all_ids = {
        lesson["id"]
        for grade in topics["grades"].values()
        for lesson in grade["lessons"]
    }
    baseline_ids = {row["topic_id"] for row in LESSONS}
    assert len(all_ids) == 536
    assert baseline_ids == all_ids - ENABLED
    assert len(baseline_ids) == 530


def test_2_all_528_legacy_routes_match_the_baseline():
    mismatches = []
    for row in LESSONS:
        current = _current(row)
        if current != row["families"]:
            mismatches.append({
                "topic_id": row["topic_id"],
                "expected": row["families"],
                "actual": current,
            })
    assert not mismatches, (
        f"{len(mismatches)} lekcija promijenilo legacy routiranje: {mismatches[:5]}"
    )


def test_4_family_order_matches_exactly():
    """Ne samo skup — REDOSLIJED, jer o njemu zavisi i prvi zadatak i rotacija."""
    reordered = [
        row["topic_id"] for row in LESSONS
        if _current(row) != row["families"]
        and sorted(_current(row)) == sorted(row["families"])
    ]
    assert not reordered, reordered[:10]


# --- 7: nijedna porodica koju legacy još treba nije preuranjeno obrisana -----

def test_7_no_family_required_by_legacy_was_removed():
    required = {family for row in LESSONS for family in row["families"]}
    missing = sorted(required - set(tf.FAMILY_DESCRIPTIONS))
    assert not missing, (
        f"Porodice koje nemigrirane lekcije još koriste, a obrisane su: {missing}"
    )


@pytest.mark.parametrize("family,consumers", [
    ("fraction_add_subtract_equal", ["6-04-009"]),
    ("fraction_add_subtract_unlike", ["6-04-010"]),
    ("fraction_multiplication", ["6-04-011"]),
    ("fraction_division", ["6-04-012"]),
    ("fraction_expression", ["6-04-014"]),
])
def test_7_deleted_family_audit_matches_its_real_consumers(family, consumers):
    """Revizija pet porodica koje su nakratko bile obrisane.

    Četiri su imale isključivo pilot potrošače; `fraction_expression` je imala
    NEMIGRIRANOG potrošača (6-04-014) i njeno brisanje je bila stvarna
    regresija. Sve su vraćene da bi legacy ostao doslovno isti."""
    from matbot.legacy import practice_routing

    actual = sorted(
        topic_id
        for topic_id, families in practice_routing.GRADE6_FRACTION_FAMILIES_BY_TOPIC.items()
        if family in families
    )
    assert actual == consumers
    assert family in tf.FAMILY_DESCRIPTIONS


def test_7_only_fraction_expression_had_a_non_pilot_consumer():
    from matbot.legacy import practice_routing

    deleted = {"fraction_add_subtract_equal", "fraction_add_subtract_unlike",
               "fraction_multiplication", "fraction_division", "fraction_expression"}
    non_pilot = {
        family
        for topic_id, families in practice_routing.GRADE6_FRACTION_FAMILIES_BY_TOPIC.items()
        if topic_id not in ENABLED
        for family in families
        if family in deleted
    }
    assert non_pilot == {"fraction_expression"}


# --- 8-10: pilot lekcije i fail-closed --------------------------------------

def test_8_pilot_lessons_do_not_appear_in_the_legacy_baseline():
    baseline_ids = {row["topic_id"] for row in LESSONS}
    for topic_id in ENABLED:
        assert topic_id not in baseline_ids
        assert registry.state_for_topic(topic_id) == registry.STATE_ENGINE


# --- 13: privremeni ID-jevi lekcija su ograničeni na legacy granicu ----------

def test_13_topic_ids_live_only_in_the_legacy_boundary_or_fixtures():
    import re

    pattern = re.compile(r"\b\d-\d{2}-\d{3}\b")
    offenders = []
    for path in (ROOT / "matbot").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith("matbot/legacy/"):
            continue                      # dozvoljena, označena granica
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(relative)
    assert not offenders, f"ID lekcije izvan legacy granice: {offenders}"
