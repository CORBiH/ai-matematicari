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
    assert len(all_ids) == 534
    assert baseline_ids == all_ids - ENABLED
    assert len(baseline_ids) == 528


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


# --- 3-6: prva porodica, redoslijed, teže, lakše ----------------------------

def test_3_initial_family_matches_for_every_lesson():
    wrong = [
        row["topic_id"] for row in LESSONS
        if tf.select_family(_current(row)) != row["first_family"]
    ]
    assert not wrong, wrong[:10]


def test_4_family_order_matches_exactly():
    """Ne samo skup — REDOSLIJED, jer o njemu zavisi i prvi zadatak i rotacija."""
    reordered = [
        row["topic_id"] for row in LESSONS
        if _current(row) != row["families"]
        and sorted(_current(row)) == sorted(row["families"])
    ]
    assert not reordered, reordered[:10]


def test_5_harder_behaviour_matches():
    wrong = []
    for row in LESSONS:
        families = _current(row)
        harder = tf.select_family(
            families, current_family=families[-1], difficulty_request="harder")
        if harder != row["harder_family"]:
            wrong.append((row["topic_id"], row["harder_family"], harder))
    assert not wrong, wrong[:10]


def test_6_easier_behaviour_matches():
    wrong = []
    for row in LESSONS:
        families = _current(row)
        easier = tf.select_family(
            families, current_family=families[-1], difficulty_request="easier")
        if easier != row["easier_family"]:
            wrong.append((row["topic_id"], row["easier_family"], easier))
    assert not wrong, wrong[:10]


# --- 7: nijedna porodica koju legacy još treba nije preuranjeno obrisana -----

def test_7_no_family_required_by_legacy_was_removed():
    required = {family for row in LESSONS for family in row["families"]}
    missing = sorted(required - set(tf.FAMILY_DESCRIPTIONS))
    assert not missing, (
        f"Porodice koje nemigrirane lekcije još koriste, a obrisane su: {missing}"
    )


def test_7_every_legacy_family_still_has_a_contract():
    from matbot.task_family_validation import CONTRACTS

    required = {family for row in LESSONS for family in row["families"]}
    assert not sorted(required - set(CONTRACTS))


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


def test_9_enabled_contract_failure_never_invokes_legacy(monkeypatch):
    """Detaljan scenarij živi u test_contract_architecture_gate.py::test_g6_*;
    ovdje se drži granica: pad ugovora ne smije pozvati legacy routing."""
    from matbot.contracts import verifiers

    calls = []
    original = tf.applicable_families
    monkeypatch.setattr(
        tf, "applicable_families",
        lambda *a, **kw: calls.append(a) or original(*a, **kw))
    monkeypatch.setattr(
        verifiers, "verify_exact_rational",
        lambda *a, **kw: verifiers.VerifierResult(True, False, "forced"))

    from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
    from matbot.session_store import SessionStore
    from tests.conftest import FakeLLM

    # Oboren verifikator obara SAMOPROVJERU serverskog generatora, pa priprema
    # kostura padne PRIJE jedinog poziva — FakeLLM ne treba nijedan odgovor.
    store, fake = SessionStore(), FakeLLM()
    response = run_practice_turn(store, fake, {
        "session_id": "parity-fail", "grade": 6, "selected_topic": "6-04-009",
        "selected_oblast": "", "student_message": "Daj zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    })
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert calls == [], "legacy routing je pozvan za lekciju s uključenim ugovorom"


def test_10_unsupported_contract_never_invokes_legacy(monkeypatch):
    from matbot.contracts import schema

    contract = registry.contract_for("6-04-009")
    unsupported = schema.replace(contract, status="unsupported")

    calls = []
    original = tf.applicable_families
    monkeypatch.setattr(
        tf, "applicable_families",
        lambda *a, **kw: calls.append(a) or original(*a, **kw))

    from matbot.practice import PRACTICE_UNAVAILABLE_MESSAGE, run_practice_turn
    from matbot.session_store import SessionStore
    from tests.conftest import FakeLLM

    store, fake = SessionStore(), FakeLLM()
    with registry.override_contracts({"6-04-009": unsupported}):
        response = run_practice_turn(store, fake, {
            "session_id": "parity-unsupported", "grade": 6,
            "selected_topic": "6-04-009", "selected_oblast": "",
            "student_message": "Daj zadatak.", "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "student_question", "selected_option_id": "",
            "client_turn_id": "",
        })
    assert response["answer"] == PRACTICE_UNAVAILABLE_MESSAGE
    assert fake.call_count == 0, "nedostupna lekcija ne smije trošiti AI poziv"
    assert calls == []


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
