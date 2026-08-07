r"""Semantički ekvivalentno curenje odgovora — živi run postStabilityFixes, B53.

Gate iz prethodnog commita hvatao je SAMO doslovni `$x=4$`. Novi run je pokazao
da model isti odgovor izgovori kroz račun, pa su i produkcija i evaluator dali
false negative:

    aktivni zadatak : „Riješi jednačinu: $2(x)=8$“
    committed       : $x=4$   (ujedno i označena opcija)

    korak 1 („Mislim da je rješenje x=3.“):
        „… Dakle, $x=\frac{8}{2}=4$. Možeš to provjeriti: $2\cdot4=8$.“
    korak 2 („Onda je vjerovatno x=100.“):
        „… Dakle $x=\frac{8}{2}=4$. Provjera: $2(4)=8$, što je tačno.“

DVA nezavisna uzroka, oba dokazana nad produkcijskim stringovima:

  1. sloj doslovnog poklapanja traži niz „x=4“, a između `x=` i `4` stoji
     `\frac{8}{2}` — pa niza nema;
  2. sloj otkrivajuće fraze poredi s kandidatom „x=4“, pa gola vrijednost „4“
     iza fraze („rješenje je 4“, „dobiješ 4“) nikad ne poklapa.

Evaluator je dijelio isti detektor, pa i isti promašaj — to je bila namjerna
odluka („ne pravi drugi paralelni detektor“) i ovdje se ne mijenja: popravlja se
JEDAN detektor, a evaluator ostaje nezavisan dokaz da gate radi.
"""
import pytest

from matbot import feedback
from matbot.tutor import pipeline as tutor_pipeline
from tests.conftest import make_task_payload, make_tutor_draft, queue_two_call
from tools.practice_eval import checks as check_lib

LESSON = "9-04-003"
TASK = r"Riješi jednačinu: $2(x)=8$"
ANSWER = r"$x=4$"

# Doslovni produkcijski odgovori — ne mijenjati, oni su dokaz.
B53_STEP1 = (r"Dobro što si pokušao. Provjerimo: ako je $x=3$, onda je "
             r"$2(x)=2\cdot3=6$, što nije $8$. Zato $x=3$ nije tačno. Prvi korak "
             r"rješavanja je podijeliti obje strane jednačine sa $2$ (jer je lijeva "
             r"strana $2$ puta $x$). Dakle, $x=\frac{8}{2}=4$. Možeš to provjeriti: "
             r"$2\cdot4=8$.")
B53_STEP2 = (r"Nije tačno. Ako je $x=100$, onda je $2(100)=200$, a to nije $8$. Prvi "
             r"korak pravilnog rješavanja je podijeliti obje strane jednačine sa $2$ "
             r"(jer je lijeva strana $2$ puta $x$). Dakle $x=\frac{8}{2}=4$. "
             r"Provjera: $2(4)=8$, što je tačno.")

MUST_BE_LEAK = {
    "lanac racuna": r"Dakle, $x=\frac{8}{2}=4$.",
    "provjera supstitucijom": r"Provjera: $2(4)=8$",
    "doslovno rjesenje": r"Tačno rješenje je $x=4$",
    "proza s frazom": "Rješenje je 4",
    "proza dobijes": "Podijeli 8 sa 2 i dobiješ 4",
    "razmaci u mathu": r"Dakle $ x = \frac{8}{2} = 4 $.",
    "B53 korak 1": B53_STEP1,
    "B53 korak 2": B53_STEP2,
}

MUST_STAY_ALLOWED = {
    "provjera samo ucenikovog x=3": (r"Provjerimo: ako je $x=3$, onda je $2\cdot3=6$, "
                                     r"što nije $8$."),
    "provjera samo ucenikovog x=100": (r"Ako je $x=100$, onda je $2(100)=200$, a to "
                                       r"nije $8$."),
    "samo konstatacija netacnosti": "Nije tačno, pokušaj ponovo.",
    "operacija bez racunanja": (r"Podijeli obje strane jednačine sa $2$. Nemoj još "
                                r"računati rezultat."),
    "broj koraka u prozi": r"Korak 1: podijeli obje strane sa $2$.",
    "broj iz zadatka koji nije odgovor": r"Lijeva strana je $2$ puta $x$, desna je $8$.",
}


# ---------------------------------------------------------------------------
# 1. DETEKTOR
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,text", sorted(MUST_BE_LEAK.items()))
def test_equivalent_disclosure_is_detected(label, text):
    assert feedback.leaks_answer(text, ANSWER, ANSWER, task_text=TASK), label


@pytest.mark.parametrize("label,text", sorted(MUST_STAY_ALLOWED.items()))
def test_useful_feedback_is_never_flagged(label, text):
    assert not feedback.leaks_answer(text, ANSWER, ANSWER, task_text=TASK), label


def test_a_number_that_belongs_to_the_task_cannot_prove_a_leak():
    """Vrijednost koja stoji u samom zadatku nije dokaz curenja."""
    assert not feedback.leaks_answer(r"Pogledaj $8$ na desnoj strani.",
                                     r"$x=8$", r"$x=8$", task_text=TASK)


def test_a_non_numeric_committed_answer_keeps_the_literal_layers():
    """Razlomak nije gola vrijednost — novi sloj se preskače, stari rade."""
    marked = r"$\frac{3}{5}$"
    assert feedback.leaks_answer(r"Tačan odgovor je $\frac{3}{5}$.", marked, marked)
    assert not feedback.leaks_answer("Uporedi oba razlomka.", marked, marked)


def test_legacy_callers_without_task_text_still_work():
    """`task_text` je opcioni dodatak — legacy Practice put se ne mijenja."""
    assert feedback.leaks_answer(r"Tačno rješenje je $x=4$", ANSWER, ANSWER)
    assert not feedback.leaks_answer("Nije tačno.", ANSWER, ANSWER)


# ---------------------------------------------------------------------------
# 2. PRODUKCIJSKI GATE KROZ STVARNI PIPELINE
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _levels_on(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(message, session_id="b53", client_turn_id="b53-t1"):
    return {
        "session_id": session_id, "grade": 9, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": client_turn_id,
    }


def _seed(store, fake_llm):
    task = make_task_payload(text=TASK, options=(r"$x=4$", r"$x=3$", r"$x=8$", r"$x=2$"),
                             correct_option_index=0, expected=ANSWER)
    queue_two_call(fake_llm, draft=make_tutor_draft(intent="generate_task", new_task=task))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn("Daj mi zadatak."))
    assert response.get("status") == "ready", response


@pytest.mark.parametrize("message,reply", [
    ("Mislim da je rješenje x=3.", B53_STEP1),
    ("Onda je vjerovatno x=100.", B53_STEP2),
])
def test_production_gate_blocks_the_live_b53_reply(store, fake_llm, message, reply):
    _seed(store, fake_llm)
    calls_before = fake_llm.call_count
    queue_two_call(fake_llm, draft=make_tutor_draft(
        intent="answer_attempt", reply=reply, new_task=None, grading="incorrect"))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn(message, client_turn_id="b53-t2"))

    assert response["answer"] == tutor_pipeline.LEAK_BLOCKED_REPLY
    assert not feedback.leaks_answer(response["answer"], ANSWER, ANSWER, task_text=TASK)
    assert fake_llm.call_count - calls_before == 1        # bez dodatnog poziva modela
    session = store.peek("b53")
    assert session["task_completed"] is False             # zadatak ostaje otvoren
    assert session["correct_option_id"]                   # označena opcija netaknuta
    assert response.get("revealed_correct_option_id") is None
    assert response["answer_verdict"] is None             # verdict nepromijenjen


def test_both_consecutive_wrong_attempts_are_blocked(store, fake_llm):
    _seed(store, fake_llm)
    for index, (message, reply) in enumerate((("Mislim da je rješenje x=3.", B53_STEP1),
                                              ("Onda je vjerovatno x=100.", B53_STEP2))):
        queue_two_call(fake_llm, draft=make_tutor_draft(
            intent="answer_attempt", reply=reply, new_task=None, grading="incorrect"))
        response = tutor_pipeline.run_turn(store, fake_llm,
                                           _turn(message, client_turn_id=f"b53-s{index}"))
        assert response["answer"] == tutor_pipeline.LEAK_BLOCKED_REPLY


def test_a_step_hint_without_the_result_still_passes_through(store, fake_llm):
    """B53 korak 3 iz produkcije — bio je ispravan i mora ostati doslovan."""
    safe = (r"Dobro — znaš da treba osloboditi zagrade. Prvi konkretan korak: podijeli "
            r"obje strane jednačine sa $2$, jer je $x$ pomnožen sa $2$ u izrazu "
            r"$2(x)=8$. Ne radi račun sada, samo primijeni tu operaciju.")
    _seed(store, fake_llm)
    queue_two_call(fake_llm, draft=make_tutor_draft(
        intent="hint_request", reply=safe, new_task=None, hint=safe))
    response = tutor_pipeline.run_turn(store, fake_llm,
                                       _turn("Ne znam dalje.", client_turn_id="b53-h1"))
    assert safe in response["answer"]


def test_explicit_full_solution_request_may_still_reveal(store, fake_llm):
    _seed(store, fake_llm)
    worked = r"Podijelimo obje strane sa $2$: $x=\frac{8}{2}=4$."
    queue_two_call(fake_llm, draft=make_tutor_draft(
        intent="full_solution_request", reply=worked, new_task=None,
        worked_solution=worked))
    response = tutor_pipeline.run_turn(store, fake_llm,
                                       _turn("Uradi ga ti.", client_turn_id="b53-f1"))
    assert worked in response["answer"]
    assert response.get("revealed_correct_option_id")


# ---------------------------------------------------------------------------
# 3. EVALUATOR — NEZAVISAN DOKAZ DA GATE RADI
# ---------------------------------------------------------------------------

def _observation(answer):
    session = {"current_task": TASK,
               "current_options": [{"id": "a", "text": ANSWER}],
               "correct_option_id": "a", "expected_answer_summary": ANSWER}
    return check_lib.TurnObservation(
        scenario_id="B53", step_index=1, step_kind="text", topic_id=LESSON, grade=9,
        request_payload={"student_message": "Mislim da je rješenje x=3."},
        http_status=200,
        response={"status": "ready", "answer": answer, "answer_verdict": None,
                  "last_tutor_task": TASK, "next_state": {"v": 1},
                  "session_mode": "practice", "effective_topic": LESSON},
        session_before=session, session_after=session, sdk_calls=1)


@pytest.mark.parametrize("reply", [B53_STEP1, B53_STEP2])
def test_evaluator_fails_the_raw_live_reply(reply):
    """PRIJE produkcijskog shapinga evaluator MORA prijaviti curenje."""
    assert check_lib.check_no_answer_leak(_observation(reply)).outcome == check_lib.FAIL


def test_evaluator_passes_the_safe_replacement():
    """POSLIJE sigurne zamjene isti check prolazi — bez izuzetka za B53."""
    result = check_lib.check_no_answer_leak(
        _observation(tutor_pipeline.LEAK_BLOCKED_REPLY))
    assert result.outcome == check_lib.PASS


def test_evaluator_still_allows_checking_only_the_student_attempt():
    safe = r"Provjerimo: ako je $x=3$, onda je $2\cdot3=6$, što nije $8$."
    assert check_lib.check_no_answer_leak(_observation(safe)).outcome == check_lib.PASS


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
