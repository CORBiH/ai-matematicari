"""Kurikularni opseg lekcije stiže do OBA poziva (živi FINAL40 FW-X03).

ŽIVI BLOKATOR IZDANJA. Kanonski FINAL40 na `4fa5fc0` objavio je, za lekciju
9. razreda OSNOVNE škole „Međusobni položaj prave i ravni“ (9-02-006), zadatak
s jednačinom ravni $2x-y+z=3$, parametarskim zapisom prave, normalnim vektorom
i skalarnim proizvodom. Matematika je bila tačna i nijedna kapija nije imala šta
prijaviti — `inside_lesson` pita EGZISTIRA li vještina lekcije u zadatku, a
zadatak jeste bio o međusobnom položaju prave i ravni.

KORIJEN UZROKA JE PODATAK, NE VALIDATOR. Lekcija nema semantički ugovor, pa je
jedino što su Tutor i Recenzent o njoj znali — NASLOV. Taj naslov doslovno
postoji na dva nivoa školovanja: sintetički (osnovna škola, na modelu kvadra) i
analitički (srednja škola / fakultet, u R3). Bez ijedne granice model je
izabrao analitički, i to je bio jedini izbor koji mu je iko ponudio.

ŠTA SE MJERI OVDJE: UGOVOR PODATAKA, ne rječnik. Namjerno NE postoji provjera
koja zabranjuje „skalarni proizvod“ ili „vektor“ — takva zabrana bi lažno
oborila 7. razred, gdje su vektori kurikularni (KS_2018-0083..0088). Provjerava
se da lekcija više nije „title-only“ i da obje strane dvopozivnog puta dobiju
isti kurikularni opseg.

DOKAZ POREKLA: opseg smije preformulisati samo ono što postojeće mapiranje već
traži — `KS_2018-0333` („na modelu kvadra odrediti međusobne položaje prave i
ravni“) i `RS_2014-0116` („da objasne međusobne odnose tačaka, pravih i ravnih
u prostoru“), obje `exact` mapirane na 9-02-006.
"""
import json
from pathlib import Path

import pytest

from matbot.topics import lesson_info
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor.prompts import (_lesson_block, build_reviewer_input,
                                  build_tutor_input)

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES_PATH = ROOT / "data" / "lesson_scope_overrides.json"

SCOPED_GRADE, SCOPED_LESSON = 9, "9-02-006"
# Kontrola iz ISTE oblasti: dokazuje da je promjena po lekciji, ne po oblasti.
CONTROL_GRADE, CONTROL_LESSON = 9, "9-02-005"

TITLE_ONLY_FALLBACK = "title-only lesson"

# Dokazi iz reference/curriculum/semantics/MATBOT_Faza2_Mapiranje.xlsx.
EVIDENCE_IDS = ("KS_2018-0333", "RS_2014-0116")


def _session():
    return {
        "current_task": "", "current_options": [], "expected_answer_summary": "",
        "difficulty": "easy", "difficulty_level": 1, "hint_level": 0,
        "recent_tasks": [], "recent_turns": [], "solution_summary": "",
    }


# ---------------------------------------------------------------------------
# 1. IZVOR I PROVENIJENCIJA
# ---------------------------------------------------------------------------

def test_override_source_is_version_controlled_and_cites_evidence():
    """Opseg bez kurikularnog dokaza je izmišljen kurikulum — zabranjeno."""
    payload = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    entry = payload["overrides"][SCOPED_LESSON]
    assert tuple(entry["evidence_ids"]) == EVIDENCE_IDS
    assert entry["lesson_scope"].strip()
    assert len(entry["objectives"]) >= 1


def test_only_one_lesson_is_scoped_by_this_patch():
    """Namjerno JEDNA lekcija: ostalih 70 izloženih je zaseban posao."""
    payload = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    assert list(payload["overrides"]) == [SCOPED_LESSON]


# ---------------------------------------------------------------------------
# 2. GENERISANI ARTEFAKT
# ---------------------------------------------------------------------------

def test_generated_record_carries_scope_and_objectives():
    lesson = lesson_info(SCOPED_GRADE, SCOPED_LESSON)
    assert lesson["lesson_scope"] != ""
    assert len(lesson["objectives"]) >= 1


def test_control_lesson_record_stays_unscoped():
    lesson = lesson_info(CONTROL_GRADE, CONTROL_LESSON)
    assert lesson["lesson_scope"] == ""
    assert lesson["objectives"] == []


def test_scope_describes_the_synthetic_treatment_the_curriculum_asks_for():
    """Pozitivna tvrdnja, ne zabrana: opseg mora imenovati model tijela i
    tri slučaja položaja — upravo ono što KS_2018-0333 traži."""
    scope = lesson_info(SCOPED_GRADE, SCOPED_LESSON)["lesson_scope"].lower()
    assert "kvadra" in scope
    for case in ("ležati u ravni", "paralelna", "sjeći"):
        assert case in scope, case


# ---------------------------------------------------------------------------
# 3. PROPAGACIJA U OBA POZIVA — jezgro popravke
# ---------------------------------------------------------------------------

def test_lesson_block_replaces_the_title_only_fallback():
    context = lesson_context_module.build(SCOPED_GRADE, SCOPED_LESSON)
    block = _lesson_block(context)
    assert "lesson scope/objectives:" in block
    assert TITLE_ONLY_FALLBACK not in block


def test_control_lesson_block_still_uses_the_title_only_fallback():
    """Bez ovoga bi test prolazio i da je fallback globalno uklonjen."""
    context = lesson_context_module.build(CONTROL_GRADE, CONTROL_LESSON)
    block = _lesson_block(context)
    assert TITLE_ONLY_FALLBACK in block
    assert "lesson scope/objectives:" not in block


@pytest.mark.parametrize("builder", ["tutor", "reviewer"])
def test_both_calls_receive_the_same_scope(builder):
    """Tutor i Recenzent moraju dobiti ISTI opseg — inače jedan sudi po
    granici koju drugi nije vidio."""
    context = lesson_context_module.build(SCOPED_GRADE, SCOPED_LESSON)
    scope = lesson_info(SCOPED_GRADE, SCOPED_LESSON)["lesson_scope"]
    if builder == "tutor":
        text = build_tutor_input(context, _session(), "Daj mi zadatak.")
    else:
        text = build_reviewer_input(context, _session(), "Daj mi zadatak.",
                                    draft_json="{}")
    assert scope in text
    assert TITLE_ONLY_FALLBACK not in text


def test_scope_reaches_both_calls_identically():
    context = lesson_context_module.build(SCOPED_GRADE, SCOPED_LESSON)
    block = _lesson_block(context)
    tutor = build_tutor_input(context, _session(), "Daj mi zadatak.")
    reviewer = build_reviewer_input(context, _session(), "Daj mi zadatak.",
                                    draft_json="{}")
    assert block in tutor and block in reviewer


# ---------------------------------------------------------------------------
# 4. ŠTA OVA POPRAVKA NAMJERNO NE RADI
# ---------------------------------------------------------------------------

def test_no_forbidden_term_list_was_introduced():
    """Granica je kurikularni NIVO, ne spisak zabranjenih riječi.

    Vektori su kurikularni u 7. razredu (KS_2018-0083..0088), pa bi zabrana
    riječi oborila legitimne lekcije. Opseg zato ne nabraja zabranjene pojmove
    — on opisuje traženi tretman."""
    entry = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    scope = entry["overrides"][SCOPED_LESSON]["lesson_scope"].lower()
    for banned_word_attempt in ("skalarni proizvod", "vektor", "parametarsk",
                                "jednačina ravni"):
        assert banned_word_attempt not in scope, banned_word_attempt


def test_other_exposed_lessons_are_deliberately_untouched():
    """Hard granica ovog zakrpa — ostatak klastera ostaje title-only."""
    for lesson_id in ("9-02-013", "9-02-014", "9-02-017", "9-03-016"):
        lesson = lesson_info(9, lesson_id)
        assert lesson is not None, lesson_id
        assert lesson["lesson_scope"] == "", lesson_id
        assert lesson["objectives"] == [], lesson_id
