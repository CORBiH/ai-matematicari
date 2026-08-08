"""Kanonska normalizacija identiteta lekcije — regresija završne kapije.

ŽIVI NALAZ (završna kapija na ebdccf0, scenario grade8, lekcija 8-09-004
„Množenje vektora brojem i primjene“): i Tutor i Recenzent su u polju
`selected_lesson_id` vratili sastavljeni zapis „Naslov (ID)“ — vrlo
vjerovatno eho ulaznog reda „- lekcija: Naslov (ID)“ — pa je stroga
invarijanta identiteta ispravno odbila objavu potpuno tačnog zadatka.

Popravka je NAMJERNO USKA normalizacija reprezentacije, ne fuzzy matching:
prihvataju se TAČNO dva zapisa — goli kanonski ID, i kanonski naslov iza
kojeg u zagradi stoji kanonski ID (oba moraju NEZAVISNO odgovarati
server-vlasničkom LessonContext-u; zapis se tada interno svodi na goli ID).
Sve ostalo i dalje pada na postojećoj invarijanti
`selected_lesson_id == context.topic_id`, koja se NE uklanja i NE zaobilazi.
"""
import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.pipeline import (UnifiedOutputError, canonicalize_task_lesson_id,
                                   validate_task_package)
from tests.conftest import FakeLLM, make_reviewer_final, make_task_payload, make_tutor_draft

GRADE, TOPIC = 8, "8-09-004"
TITLE = "Množenje vektora brojem i primjene"


@pytest.fixture()
def context():
    ctx = build(GRADE, TOPIC)
    assert ctx is not None and ctx.title == TITLE
    return ctx


def _task(lesson_id, title=TITLE):
    task = make_task_payload()
    return task.model_copy(update={"selected_lesson_id": lesson_id,
                                   "selected_lesson_title": title})


# ---------------------------------------------------------------------------
# 1–2: prihvaćeni zapisi
# ---------------------------------------------------------------------------

def test_bare_canonical_id_is_accepted(context):
    validated = validate_task_package(_task(TOPIC), context)
    assert validated.selected_lesson_id == TOPIC


def test_canonical_title_with_canonical_id_normalizes_to_bare_id(context):
    validated = validate_task_package(_task(f"{TITLE} ({TOPIC})"), context)
    assert validated.selected_lesson_id == TOPIC
    assert validated.selected_lesson_title == TITLE


def test_exact_live_gate_artifact_representation_normalizes(context):
    # Doslovni zapis iz artefakta pale kapije.
    live = "Množenje vektora brojem i primjene (8-09-004)"
    assert canonicalize_task_lesson_id(_task(live), context).selected_lesson_id == TOPIC


# ---------------------------------------------------------------------------
# 3–8, 10: odbijeni zapisi — invarijanta ostaje fail-closed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("malformed", [
    f"Pogrešan naslov ({TOPIC})",              # 3: tačan ID, pogrešan naslov
    f"{TITLE} (8-09-005)",                     # 4: tačan naslov, pogrešan ID
    f"Lekcija {TOPIC}",                        # 5: ID u prozi
    f"{TOPIC} dodatni tekst",                  # 5: ID pa proza
    f"{TOPIC} / 8-09-005",                     # 6: više ID-jeva
    TITLE,                                     # 7: samo naslov
    f"{TITLE} ({TOPIC}",                       # 8: neispravna zagrada
    f"{TITLE} {TOPIC})",                       # 8: neispravna zagrada
    f"{TITLE}({TOPIC})",                       # 8: bez razmaka prije zagrade
    "8-09",                                    # djelimičan ID
    "9-09-004",                                # ID iz drugog razreda
    f"{TITLE.upper()} ({TOPIC})",              # mutacija velikih slova
    f"Sabiranje vektora (8-09-003)",           # 10: druga stvarna lekcija, oba polja
    "8-09-003",                                # 10: druga stvarna lekcija, goli ID
])
def test_every_other_representation_is_rejected(context, malformed):
    with pytest.raises(UnifiedOutputError):
        validate_task_package(_task(malformed), context)


def test_normalization_never_touches_a_non_matching_id(context):
    # canonicalize je čista reprezentacijska funkcija: sve osim TAČNOG
    # sastavljenog zapisa vraća nepromijenjeno (odbijanje radi invarijanta).
    for raw in (f"Lekcija {TOPIC}", TITLE, "8-09-003"):
        assert canonicalize_task_lesson_id(_task(raw), context).selected_lesson_id == raw


# ---------------------------------------------------------------------------
# 9 i 11: cijeli model-put kroz run_practice_turn
# ---------------------------------------------------------------------------

def _turn(message="Daj mi zadatak."):
    return {
        "session_id": "identity-norm", "grade": GRADE, "selected_topic": TOPIC,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


@pytest.fixture(autouse=True)
def _universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def test_malformed_but_safe_representation_publishes_after_normalization():
    # 9: Tutor i Recenzent vraćaju isti sigurni sastavljeni zapis — paket se
    # normalizuje i objavljuje kad su svi ostali validatori zadovoljni.
    store, fake = SessionStore(), FakeLLM()
    draft = make_tutor_draft(new_task=_task(f"{TITLE} ({TOPIC})"))
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=make_tutor_draft(
        new_task=_task(f"{TITLE} ({TOPIC})"))))
    response = run_practice_turn(store, fake, _turn())
    assert response.get("status") == "ready", response.get("answer")
    session = store.peek("identity-norm")
    assert session["current_task"]
    assert fake.call_count == 2


def test_reviewer_cannot_switch_identity_to_another_valid_lesson():
    # 11: recenzentov `final` s TUĐOM (postojećom) lekcijom pada na invarijanti
    # PRIJE mutacije sesije.
    store, fake = SessionStore(), FakeLLM()
    draft = make_tutor_draft(new_task=_task(TOPIC))
    fake.queue(draft)
    fake.queue(make_reviewer_final(decision="correct", final=make_tutor_draft(
        new_task=_task("8-09-003", title="Sabiranje vektora"))))
    response = run_practice_turn(store, fake, _turn())
    assert response.get("status") != "ready"
    session = store.peek("identity-norm")
    assert session is None or not session.get("current_task")


# ---------------------------------------------------------------------------
# Izlazni ugovor: prompt izričito traži goli kanonski ID
# ---------------------------------------------------------------------------

def test_tutor_prompt_requires_bare_canonical_lesson_id(context):
    instructions = tutor_prompts.build_tutor_instructions(context)
    assert "SAMO goli kanonski ID lekcije" in instructions
    assert 'nikad "Naslov (ID)"' in instructions


def test_reviewer_prompt_requires_bare_canonical_lesson_id(context):
    instructions = tutor_prompts.build_reviewer_instructions(context)
    assert "SAMO goli kanonski ID lekcije" in instructions
    assert 'nikad "Naslov (ID)"' in instructions
