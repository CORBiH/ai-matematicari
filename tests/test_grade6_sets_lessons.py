"""NOVE LEKCIJE SKUPOVA (6. razred) — kurikulum, ugovor i opseg.

EDIN-FEEDBACK: oblast „Skupovi i skupovne operacije“ imala je jedanaest
lekcija koje uvode POJEDINAČNE radnje (unija, presjek, razlika, komplement,
Dekartov proizvod), ali nijednu koja ih SPAJA i nijednu tekstualnu. Dodate su
dvije:

    6-01-012  Zadaci s više skupovnih operacija
    6-01-013  Tekstualni zadaci sa skupovima

Obje su dodate KANONSKI: red u Excelu (izvor istine) → `scripts/
build_topics_json.py` → `data/topics.json`, i dodjela u
`data/lesson_semantic_assignments.json` → `scripts/build_lesson_semantics.py`
→ kompajlirani ugovor. Nijedan ID lekcije nije ušao u Python.
"""
import json
from pathlib import Path

import pytest

from matbot import topics
from matbot.semantics import contracts as semantic_contracts

ROOT = Path(__file__).resolve().parent.parent
MULTI, TEXTUAL = "6-01-012", "6-01-013"
OBLAST = "Skupovi i skupovne operacije"
GRADE = 6


def _lessons():
    payload = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    return payload["grades"][str(GRADE)]["lessons"]


# ---------------------------------------------------------------------------
# A) KURIKULUM
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson_id,title", [
    (MULTI, "Zadaci s više skupovnih operacija"),
    (TEXTUAL, "Tekstualni zadaci sa skupovima"),
])
def test_lesson_exists_exactly_once_with_the_canonical_title(lesson_id, title):
    rows = [row for row in _lessons() if row["id"] == lesson_id]
    assert len(rows) == 1, rows
    assert rows[0]["title"] == title
    assert rows[0]["oblast"] == OBLAST


def test_new_lessons_follow_the_existing_sets_lessons_in_order():
    order = [row["id"] for row in _lessons() if row["id"].startswith("6-01-")]
    assert order[-3:] == ["6-01-011", MULTI, TEXTUAL], order


def test_every_grade6_lesson_id_stays_unique():
    ids = [row["id"] for row in _lessons()]
    assert len(ids) == len(set(ids))


def test_lesson_info_resolves_both_lessons():
    for lesson_id in (MULTI, TEXTUAL):
        info = topics.lesson_info(GRADE, lesson_id)
        assert info is not None, lesson_id
        assert info["oblast"] == OBLAST
        assert info["title"]


def test_topics_response_exposes_both_lessons_to_the_frontend():
    response = topics.topics_response(GRADE)
    grouped = response["grouped"]
    assert OBLAST in grouped
    ids = [row["topic"] for row in grouped[OBLAST]]
    assert MULTI in ids and TEXTUAL in ids
    names = {row["topic"]: row["display_name"] for row in grouped[OBLAST]}
    assert names[MULTI] == "Zadaci s više skupovnih operacija"
    assert names[TEXTUAL] == "Tekstualni zadaci sa skupovima"
    assert OBLAST in response["oblast_order"]


# ---------------------------------------------------------------------------
# B) SEMANTIČKI UGOVOR
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson_id,concept", [
    (MULTI, "multi_operation"),
    (TEXTUAL, "set_word_problem"),
])
def test_semantic_contract_resolves_and_is_blocking(lesson_id, concept):
    contract = semantic_contracts.contract_for(lesson_id)
    assert contract is not None, lesson_id
    assert contract.family_id == "finite_set_direct"
    assert contract.blocking
    assert tuple(contract.parameters["concepts"]) == (concept,)


def test_contract_pins_each_lesson_to_its_own_visible_skill():
    """Lekcija ne smije naslijediti opseg cijele oblasti."""
    multi = semantic_contracts.contract_for(MULTI)
    textual = semantic_contracts.contract_for(TEXTUAL)
    assert multi.parameters["concepts"] != textual.parameters["concepts"]
    for contract in (multi, textual):
        prompt = " ".join(contract.prompt_lines)
        assert "glavna vidljiva radnja mora biti" in prompt
        assert "server odbija paket prije objave" in prompt


def test_multi_operation_prompt_demands_more_than_one_operation():
    contract = semantic_contracts.contract_for(MULTI)
    prompt = " ".join(contract.prompt_lines).lower()
    assert "više skupovnih operacija" in prompt
    assert "jednom zadatku" in prompt
    # Recenzentova invarijanta traži BAR DVIJE radnje — jedna nije ova lekcija.
    note = contract.reviewer_note.lower()
    assert "bar dvije" in note


def test_textual_prompt_demands_a_story_not_a_bare_symbolic_expression():
    contract = semantic_contracts.contract_for(TEXTUAL)
    prompt = " ".join(contract.prompt_lines).lower()
    assert "tekstualni zadatak" in prompt
    note = contract.reviewer_note.lower()
    assert "tekstualnu situaciju" in note
    assert "goli simbolički zapis" in note


def test_difficulty_grows_without_leaving_the_taught_material():
    """Teže = više radnji i zagrade, nikad novo gradivo."""
    multi = semantic_contracts.contract_for(MULTI)
    bounds = {str(k): v.lower() for k, v in multi.level_bounds.items()}
    assert "dvije operacije" in bounds["1"]
    assert "zagrad" in bounds["2"]
    assert "tri operacije" in bounds["3"]
    for lesson_id in (MULTI, TEXTUAL):
        note = semantic_contracts.contract_for(lesson_id)._scope_note \
            if hasattr(semantic_contracts.contract_for(lesson_id), "_scope_note") else ""
        del note  # opseg živi u izvoru dodjele; ovdje se provjerava ponašanje


@pytest.mark.parametrize("forbidden", ["vjerovatnoć", "uključivanja-isključivanja"])
def test_out_of_scope_material_is_named_in_the_assignment_source(forbidden):
    """Zabrane su zapisane u KANONSKOM izvoru, ne u Pythonu."""
    source = json.loads((ROOT / "data" / "lesson_semantic_assignments.json")
                        .read_text(encoding="utf-8"))
    rows = {row["lesson_id"]: row for row in source["assignments"]}
    notes = " ".join(rows[lesson_id].get("_scope_note", "")
                     for lesson_id in (MULTI, TEXTUAL)).lower()
    assert forbidden in notes


# ---------------------------------------------------------------------------
# C) ARHITEKTURA
# ---------------------------------------------------------------------------

def test_no_lesson_id_of_the_new_lessons_leaks_into_python():
    """Dodavanje lekcije mijenja PODATKE, ne Python (CLAUDE.md)."""
    for path in (ROOT / "matbot").rglob("*.py"):
        source = path.read_text(encoding="utf-8", errors="replace")
        assert MULTI not in source, path
        assert TEXTUAL not in source, path
