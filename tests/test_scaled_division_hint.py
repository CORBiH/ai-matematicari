r"""Skaliranje djeljenika i djelioca mora biti nedvosmisleno zapisano.

ŽIVI RUN postStabilityFixes — scenario B45, lekcija „Dijeljenje decimalnih
brojeva“, zadatak `Izračunaj: $7,5:5$`.

Treći hint je htio pomnožiti oba člana sa 10 i napisao:

    „Izvedi račun $7,5\cdot 10:5\cdot 10 = 75:50$.“

Objava je odbijena:

    numeric_equality_mismatch: '7,5\cdot 10:5\cdot 10' (150) != '75:50' (1.5)

VALIDATOR JE U PRAVU i ne mijenja se. Po standardnom prioritetu operacija
`a·b:c·d` je `((a·b):c)·d`, dakle `((7,5·10):5)·10 = 150`. Model je mislio
`(7,5·10):(5·10) = 75:50 = 1,5`, ali to nije napisao.

Popravka je zato u promptu, ne u validatoru: kad se skaliraju oba člana,
zapis mora nositi zagrade ili razlomačku crtu. Oba ispravna oblika već prolaze
kroz postojeći `mathcheck` bez ijedne izmjene.
"""
import pytest

from matbot.mathcheck import find_numeric_inconsistencies
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import prompts as tutor_prompts

LESSON = "6-05-010"

# Doslovan izraz iz produkcije — ne mijenjati, on je dokaz.
LIVE_AMBIGUOUS = r"Izvedi račun $7,5\cdot 10:5\cdot 10 = 75:50$."

UNAMBIGUOUS = {
    "zagrade": r"Izvedi račun $(7,5\cdot 10):(5\cdot 10) = 75:50$.",
    "razlomak": r"Izvedi račun $\frac{7,5\cdot 10}{5\cdot 10} = \frac{75}{50}$.",
    "bez skaliranja": r"Izvedi račun $75:50$.",
    "puni lanac sa zagradama": r"$(7,5\cdot 10):(5\cdot 10) = 75:50 = 1,5$",
}


def test_the_live_ambiguous_expression_is_still_rejected():
    """Validator se NE slabi — 7,5·10:5·10 zaista jeste 150."""
    issues = find_numeric_inconsistencies(LIVE_AMBIGUOUS)
    assert issues
    assert "numeric_equality_mismatch" in issues[0]


@pytest.mark.parametrize("label,text", sorted(UNAMBIGUOUS.items()))
def test_an_unambiguous_scaling_passes_the_numeric_validator(label, text):
    assert find_numeric_inconsistencies(text) == [], label


def test_the_intended_value_is_preserved_by_the_correct_notation():
    """Namjeravana vrijednost 1,5 mora biti dokaziva iz zapisa sa zagradama."""
    assert find_numeric_inconsistencies(
        r"$(7,5\cdot 10):(5\cdot 10) = 75:50 = 1,5$") == []
    # …a pogrešna vrijednost i dalje pada.
    assert find_numeric_inconsistencies(
        r"$(7,5\cdot 10):(5\cdot 10) = 75:50 = 150$")


# --- PROMPT --------------------------------------------------------------

def _instructions():
    context = lesson_context_module.build(6, LESSON)
    return (tutor_prompts.build_tutor_instructions(context),
            tutor_prompts.build_reviewer_instructions(context))


def test_both_prompts_require_parentheses_or_a_fraction_when_scaling():
    for text in _instructions():
        lowered = text.lower()
        assert "scale both the dividend and the divisor" in lowered
        assert "parentheses" in lowered


# --- STANJE SESIJE NA ODBIJENOJ OBJAVI ------------------------------------

def test_hint_level_does_not_move_when_publication_is_rejected(store, fake_llm, monkeypatch):
    """B45: treći hint je odbijen, a `hint_level` je ostao na 2."""
    from matbot.tutor import pipeline as tutor_pipeline
    from tests.conftest import make_task_payload, make_tutor_draft, queue_two_call

    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    turn = {
        "session_id": "sd-1", "grade": 6, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "sd-t1",
    }
    task = make_task_payload(text=r"Izračunaj: $7,5:5$",
                             options=(r"$1,5$", r"$15$", r"$0,15$", r"$150$"),
                             correct_option_index=0, expected=r"$1,5$")
    queue_two_call(fake_llm, draft=make_tutor_draft(intent="generate_task", new_task=task))
    assert tutor_pipeline.run_turn(store, fake_llm, turn)["status"] == "ready"

    before = store.peek("sd-1")["hint_level"]
    queue_two_call(fake_llm, draft=make_tutor_draft(
        intent="hint_request", reply=LIVE_AMBIGUOUS, new_task=None, hint=LIVE_AMBIGUOUS))
    response = tutor_pipeline.run_turn(store, fake_llm,
                                       dict(turn, student_message="Ne znam.",
                                            client_turn_id="sd-t2"))

    assert response.get("status") is None
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert store.peek("sd-1")["hint_level"] == before        # nivo hinta se ne pomjera
    assert fake_llm.call_count == 3                          # 2 + 1, bez trećeg na turnu


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija: ovi testovi ispituju MODEL-strategiju (Tutor +
# Recenzent) i na lekcijama koje produkcija sada rutira deterministički
# (blocking ugovor + potpun generator). Izričito isključenje je ISTI mehanizam
# koji služi i kao produkcijski rollback (MATBOT_DETERMINISTIC_PRACTICE=
# disabled) — model-put time ostaje trajno testiran, bajt za bajt kakav je bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
