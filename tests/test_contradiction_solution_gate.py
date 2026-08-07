"""Živi release gate 5ac723e, scenario grade9, lekcija 9-05-010 „Sistem bez
rješenja“ — kontradikcijsko rješenje je padalo kao aritmetička greška.

Tutorov zadatak (tekst JESTE sačuvan u artefaktu):

    Odredi koliko rješenja ima sljedeći sistem linearnih jednačina:
    $x+y=3$
    $x+y=5$

Rješenje takve lekcije MORA prikazati lažnu jednakost ($3=5$) da bi dokazalo
kontradikciju. `mathcheck` je taj segment tretirao kao `numeric_equality_mismatch`,
recenzent nije imao lijek koji zadržava smisao lekcije (nijedna ispravka ne može
ukloniti kontradikciju a ostati vjerna lekciji), pa je SVAKI pokušaj pao
zatvoreno: `reviewer_final_mcq_integrity_rejection`, ništa objavljeno, gate FAIL.

Ovdje se dokazuje:
  • paket s deklarisano-lažnom kontradikcijom nema preflight nalaza i OBJAVLJUJE se;
  • gola lažna jednakost i dalje nosi nalaz, s serverski izračunatim vrijednostima;
  • recenzent dobija lijek za `numeric_inconsistency` i može popraviti u istom pozivu;
  • Tutorov prompt STVARNO nosi uputstvo o kontradikcijskom zapisu.
"""
import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import package_preflight as preflight
from matbot.tutor.lesson_context import build
from matbot.tutor.pipeline import REVIEWER_FINAL_INTEGRITY_CODE, SAFE_ERROR_MESSAGE
from matbot.tutor.schema import (DifficultyEvidence, ReviewerChecks, ReviewerFinal,
                                 SignatureParameter, TaskPayload, TaskSignature,
                                 TutorDraft, TutorOption)
from tests.conftest import FakeLLM

SESSION = "contradiction"
GRADE, TOPIC = 9, "9-05-010"


@pytest.fixture(autouse=True)
def _model_route(monkeypatch):
    # Batch #3: 9-05-010 je postala deterministička, a ovaj modul ciljano
    # ispituje MODEL put (recenzent popravlja golu netačnu jednakost). Isti
    # mehanizam kao produkcijski rollback vraća lekciju na Tutor+Reviewer put.
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")

# Živi oblik zadatka iz artefakta (recenzentova verzija, bez „obrazloži“).
LIVE_TASK_TEXT = ("Odredi koliko rješenja ima sljedeći sistem linearnih "
                  "jednačina:\n\n$x+y=3$\n\n$x+y=5$")
OPTIONS = ("Sistem nema rješenja", "Sistem ima tačno jedno rješenje",
           "Sistem ima beskonačno mnogo rješenja", "Sistem ima tačno dva rješenja")

# Rješenje s IZRIČITO deklarisanom lažnom jednakošću — vjerno lekciji.
DECLARED_FALSE_SOLUTION = (
    "Lijeve strane obje jednačine su iste, $x+y$, a desne strane su različite. "
    "Kada bi sistem imao rješenje, slijedilo bi $3=5$, što nije tačno. "
    "Zato sistem nema rješenja.")

# Gola lažna jednakost — bez markera server je ne razlikuje od greške u računu.
BARE_FALSE_SOLUTION = (
    "Lijeve strane obje jednačine su iste. Iz njih dobijamo $3=5$. "
    "Zato sistem nema rješenja ni za jedan par brojeva iks i ipsilon.")


def turn(message="Daj mi zadatak."):
    return {
        "session_id": SESSION, "grade": GRADE, "selected_topic": TOPIC,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def evidence():
    return DifficultyEvidence(
        reasoning_steps=1, condition_count=1, operation_count=1,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


def task(context, *, solution, signature="one"):
    return TaskPayload(
        selected_lesson_id=context.topic_id, selected_lesson_title=context.title,
        target_difficulty_level=1, text=LIVE_TASK_TEXT,
        task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(OPTIONS)],
        correct_option_index=0, correct_option_id="a",
        expected_answer=OPTIONS[0], solution=solution,
        difficulty="easy", difficulty_evidence=evidence(),
        task_signature=TaskSignature(
            task_family="linear_system_solution_count",
            operation_or_relation="classify",
            normalized_parameters=[SignatureParameter(name="case", value=signature)],
            required_conditions=["same_left_sides"], relevant_objects=["system"],
            answer_type="multiple_choice"))


def checks():
    return ReviewerChecks(
        math_correct=True, marked_option_correct=True, inside_lesson=True,
        intent_handled=True, difficulty_direction_correct=True,
        response_addresses_student=True, task_solvable_and_unambiguous=True,
        mathjax_valid=True, language_age_appropriate=True,
        independently_solved=True, independent_answer="nema rješenja",
        task_package_consistent=True, difficulty_evidence_valid=True,
        task_signature_consistent=True)


def queue(fake, draft_task, *, decision="approve", final_task=...):
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=draft_task)
    final_task = draft_task if final_task is ... else final_task
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision=decision, checks=checks(),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=final_task.difficulty_evidence))
    return draft


# ---------------------------------------------------------------------------
# 1) PREFLIGHT NALAZI
# ---------------------------------------------------------------------------

def test_declared_false_contradiction_has_no_preflight_issues():
    context = build(GRADE, TOPIC)
    issues = preflight.collect_package_issues(
        task(context, solution=DECLARED_FALSE_SOLUTION))
    assert issues == ()


def test_bare_false_equality_still_carries_the_numeric_issue():
    context = build(GRADE, TOPIC)
    issues = preflight.collect_package_issues(
        task(context, solution=BARE_FALSE_SOLUTION))
    numeric = [i for i in issues if i.code == "numeric_inconsistency"]
    assert len(numeric) == 1
    # Serverski izračunate vrijednosti u dijagnostici — nikad izraz iz sadržaja.
    assert "server evaluated 3 vs 5" in numeric[0].detail
    assert "solution" in numeric[0].detail
    described = preflight.describe_issues(issues)
    assert "Lijeve strane" not in described


def test_reviewer_block_carries_the_numeric_remedy():
    context = build(GRADE, TOPIC)
    issues = preflight.collect_package_issues(
        task(context, solution=BARE_FALSE_SOLUTION))
    block = preflight.format_for_reviewer(issues)
    assert "For `numeric_inconsistency`" in block
    assert "što nije tačno" in block


# ---------------------------------------------------------------------------
# 2) CIJELI DVOPOZIVNI PUT
# ---------------------------------------------------------------------------

@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def test_faithful_no_solution_package_is_published(universal):
    """TAČAN živi scenario poslije popravke: paket se objavljuje iz prvog puta."""
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, solution=DECLARED_FALSE_SOLUTION))

    response = run_practice_turn(store, fake, turn())
    session = store.peek(SESSION)

    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert session is not None
    assert session["expected_answer_summary"] == OPTIONS[0]
    # Tutorov prompt STVARNO nosi uputstvo o kontradikcijskom zapisu.
    instructions, _ = fake.tutor_calls[0]
    assert "što nije tačno" in instructions


def test_reviewer_repairs_a_bare_false_equality_in_the_same_call(universal):
    """Recenzent doda marker lažnosti — paket se objavljuje, bez trećeg poziva."""
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    repaired = task(context, solution=DECLARED_FALSE_SOLUTION, signature="repaired")
    queue(fake, task(context, solution=BARE_FALSE_SOLUTION),
          decision="correct", final_task=repaired)

    response = run_practice_turn(store, fake, turn())

    assert response["status"] == "ready"
    assert fake.call_count == 2
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert "numeric_inconsistency" in reviewer_input
    assert "server evaluated 3 vs 5" in reviewer_input


def test_unrepaired_bare_false_equality_still_fails_closed(universal, caplog):
    """Recenzent vrati isti nalaz → odbijeno prije mutacije sesije, kao dosad."""
    context, store, fake = build(GRADE, TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, solution=BARE_FALSE_SOLUTION), decision="approve")

    response = run_practice_turn(store, fake, turn())

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2
    assert REVIEWER_FINAL_INTEGRITY_CODE in caplog.text
    assert "unchanged=True" in caplog.text
