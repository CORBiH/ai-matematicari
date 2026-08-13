"""PRIMARNA VJEŠTINA LEKCIJE — semantička vjernost, ne samo pripadnost sesije.

ŽIVI NALAZ: lekcija „Skup racionalnih brojeva…“ (pojmovna lekcija o jednom
skupu brojeva) dobijala je zadatke čija je STVARNA vještina sabiranje
razlomaka. Sesija je cijelo vrijeme bila na ispravnoj lekciji, pa je zatečena
mjera („nema zanosa lekcije“) pokazivala nulu — a zadatak ipak nije ispitivao
ono što lekcija predaje.

DVA ODVOJENA INVARIJANTA OD SADA:
  • integritet sesije   — sesija ostaje vezana za izabranu lekciju;
  • semantička vjernost — PRIMARNA vještina zadatka pripada toj lekciji.

Uzrok nije bio nedostatak podataka nego instalacija: 152 od 184 model-podržanih
lekcija dobijalo je u promptu SAMO naslov, dok je kanonsko mapiranje NPP ishoda
na lekcije postojalo neiskorišteno. Ovaj fajl zaključava da taj signal STVARNO
stiže do modela i da se nikad ne izmišlja.
"""
import json
from pathlib import Path

import pytest

from matbot import lesson_objectives
from matbot.tutor import lesson_context
from matbot.tutor import prompts as tutor_prompts

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "data" / "lesson_objectives.compiled.json"


def _topics():
    return json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# A) ARTEFAKT JE GENERISAN, KANONSKI I NEDUPLIRAN
# ---------------------------------------------------------------------------

def test_artifact_exists_and_declares_its_generator():
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    readme = " ".join(payload["_readme"])
    assert "scripts/build_lesson_objectives.py" in readme
    assert "MATBOT_Faza2_Mapiranje.xlsx" in readme
    assert payload["lessons"]


def test_artifact_never_duplicates_the_curriculum_index():
    """Artefakt NE nosi drugi registar lekcija.

    Kanonski naslov i oblast žive isključivo u `topics.json`; ovdje se čuvaju
    samo ISHODI. (Kurikularna proza smije spomenuti pojam koji se slučajno
    poklapa s naslovom neke lekcije — to je sadržaj ishoda, ne registar.)"""
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    for lesson_id, row in payload["lessons"].items():
        assert "title" not in row, lesson_id
        assert "oblast" not in row, lesson_id
        assert set(row) <= {"grade", "primary_skills", "supporting_concepts",
                            "neighbour_exclusions", "evidence_ids"}, lesson_id


def test_every_compiled_lesson_id_exists_in_the_curriculum():
    known = {lesson["id"] for grade in _topics()["grades"].values()
             for lesson in grade["lessons"]}
    compiled = set(json.loads(ARTIFACT.read_text(encoding="utf-8"))["lessons"])
    assert compiled <= known


def test_coverage_is_substantial_and_reported():
    total, with_primary = lesson_objectives.coverage()
    assert total >= 200, total
    assert with_primary >= 180, with_primary


# ---------------------------------------------------------------------------
# B) SIGNAL STVARNO STIŽE DO MODELA
# ---------------------------------------------------------------------------

def _lesson_with_objectives():
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))["lessons"]
    for lesson_id, row in payload.items():
        if row.get("primary_skills"):
            return lesson_id, row
    raise AssertionError("nema nijedne lekcije s primarnom vještinom")


def test_context_carries_the_primary_skill():
    lesson_id, row = _lesson_with_objectives()
    context = lesson_context.build(row["grade"], lesson_id)
    assert context is not None
    assert tuple(context.objectives) == tuple(row["primary_skills"])


def test_prompt_states_the_primary_skill_as_binding():
    lesson_id, row = _lesson_with_objectives()
    context = lesson_context.build(row["grade"], lesson_id)
    block = tutor_prompts._lesson_block(context)
    assert "PRIMARNA VJEŠTINA OVE LEKCIJE" in block
    assert row["primary_skills"][0][:40] in block
    assert "NISU dovoljni" in block


def test_supporting_concepts_are_marked_as_tools_not_targets():
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))["lessons"]
    lesson_id = next((k for k, v in payload.items() if v.get("supporting_concepts")), None)
    assert lesson_id, "očekivana bar jedna lekcija s pomoćnim pojmovima"
    context = lesson_context.build(payload[lesson_id]["grade"], lesson_id)
    block = tutor_prompts._lesson_block(context)
    assert "NE smiju postati ono što se ispituje" in block


def test_neighbour_skills_are_explicitly_forbidden_as_targets():
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))["lessons"]
    lesson_id = next((k for k, v in payload.items() if v.get("neighbour_exclusions")), None)
    assert lesson_id, "očekivana bar jedna lekcija sa susjednim zabranama"
    context = lesson_context.build(payload[lesson_id]["grade"], lesson_id)
    block = tutor_prompts._lesson_block(context)
    assert "SUSJEDNE VJEŠTINE — nikad cilj ovog zadatka" in block


# ---------------------------------------------------------------------------
# C) NIŠTA SE NE IZMIŠLJA I NIŠTA SE NE GAZI
# ---------------------------------------------------------------------------

def test_hand_written_scope_always_wins_over_the_compiled_evidence():
    """Ručno autorski opseg (topics.json) ima prednost — dopuna ga ne gazi."""
    overrides = json.loads((ROOT / "data" / "lesson_scope_overrides.json")
                           .read_text(encoding="utf-8")).get("overrides", {})
    lesson_id = next(iter(overrides))
    grade = int(lesson_id.split("-")[0])
    context = lesson_context.build(grade, lesson_id)
    authored = overrides[lesson_id].get("objectives") or []
    if authored:
        assert tuple(context.objectives) == tuple(authored)
    assert context.lesson_scope                      # opisni opseg ostaje


def test_lesson_without_evidence_behaves_exactly_as_before():
    known = set(json.loads(ARTIFACT.read_text(encoding="utf-8"))["lessons"])
    for grade, payload in _topics()["grades"].items():
        for lesson in payload["lessons"]:
            if lesson["id"] in known:
                continue
            context = lesson_context.build(int(grade), lesson["id"])
            if context is None:
                continue
            assert context.objectives == () or context.lesson_scope
            block = tutor_prompts._lesson_block(context)
            assert "PRIMARNA VJEŠTINA OVE LEKCIJE" not in block
            return


def test_reader_is_inert_for_unknown_lessons():
    assert lesson_objectives.primary_skills("") == ()
    assert lesson_objectives.primary_skills("9-99-999") == ()
    assert lesson_objectives.supporting_concepts(None) == ()
    assert lesson_objectives.neighbour_exclusions("9-99-999") == ()


def test_compiled_text_obeys_project_terminology():
    """Ishodi ulaze u prompt, pa moraju poštovati projektnu terminologiju."""
    from matbot.terminology import contains_forbidden_term

    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))["lessons"]
    for lesson_id, row in payload.items():
        for bucket in ("primary_skills", "supporting_concepts", "neighbour_exclusions"):
            for statement in row.get(bucket, ()):
                assert not contains_forbidden_term(statement), (lesson_id, statement)


def test_no_lesson_id_conditionals_were_introduced():
    """Rješenje je PODATAK + jedan kompajler, ne grana po lekciji."""
    source = (ROOT / "matbot" / "lesson_objectives.py").read_text(encoding="utf-8")
    known = {lesson["id"] for grade in _topics()["grades"].values()
             for lesson in grade["lessons"]}
    assert not [lesson_id for lesson_id in known if lesson_id in source]
