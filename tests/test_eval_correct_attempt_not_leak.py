r"""Potvrda tačnog učenikovog pokušaja nije curenje odgovora.

ŽIVI RUN postFinalFixes — scenario B53 (`9-04-003`), zadatak `$(x+2)=5$`,
committed `marked_option_text = expected_answer = $x=3$`.

    korak 1  učenik: „Mislim da je rješenje x=3.“        ← TAČAN pokušaj
             tutor : „Tačno — dobro si mislio. Provjerimo uvrštavanjem: …“
             evaluator: no_answer_leak FAIL      ← FALSE POSITIVE

    korak 2  učenik: „Onda je vjerovatno x=100.“         ← pogrešan pokušaj
             tutor : ne otkriva $x=3$
             evaluator: PASS                     ← ispravno

Produkcijski gate ovaj korak NE blokira: `pipeline._reveals_committed_answer`
ima izuzetak „vrijednost nije došla od tutora nego od učenika“. Evaluatorov
`_answer_leak_result` je taj izuzetak imao samo za tekst ZADATKA, ne i za
učenikovu poruku — odatle asimetrija.

Popravlja se ISKLJUČIVO evaluator, istim `feedback.leaks_answer` nad učenikovom
porukom. Bez izuzetka po scenario ID-u, bez algebarskog solvera i bez ijedne
izmjene u `matbot/`.
"""
import pytest

from matbot import feedback
from matbot.tutor import pipeline as tutor_pipeline
from tools.practice_eval import checks as check_lib

LESSON = "9-04-003"

# --- ŽIVI STRINGOVI IZ postFinalFixes — ne mijenjati ------------------------

B53_TASK = r"Riješi jednačinu: $(x+2)=5$"
B53_COMMITTED = r"$x=3$"
B53_CORRECT_ATTEMPT = "Mislim da je rješenje x=3."
B53_WRONG_ATTEMPT = "Onda je vjerovatno x=100."
B53_STEP1_REPLY = (r"Tačno — dobro si mislio. Provjerimo uvrštavanjem: $ (x+2)=5 $ "
                   r"pa za $ x=3 $ dobijemo $ (3+2)=5 $, odnosno $ 5=5 $. "
                   r"Jednačina je zadovoljena.")
B53_STEP2_REPLY = (r"Nije tačno. Provjerimo uvrštavanjem tvoje vrijednosti $x=100$ u "
                   r"jednačinu. Za $x=100$ je lijeva strana $ (100+2)=102 $, a desna "
                   r"strana je $5$, pa dobijemo $102\neq5$. Dakle $x=100$ ne "
                   r"zadovoljava jednačinu. Pokušaj ponovo.")

# --- RANIJI ŽIVI B53 (postStabilityFixes) — mora i dalje pasti kao leak -----

OLD_TASK = r"Riješi jednačinu: $2(x)=8$"
OLD_COMMITTED = r"$x=4$"
OLD_ATTEMPT = "Mislim da je rješenje x=3."
OLD_LEAKING_REPLY = (r"Dobro što si pokušao. Provjerimo: ako je $x=3$, onda je "
                     r"$2(x)=2\cdot3=6$, što nije $8$. Prvi korak je podijeliti obje "
                     r"strane sa $2$. Dakle, $x=\frac{8}{2}=4$. Možeš to provjeriti: "
                     r"$2\cdot4=8$.")


def observation(answer, *, task, committed, student_message):
    session = {"current_task": task,
               "current_options": [{"id": "a", "text": committed}],
               "correct_option_id": "a", "expected_answer_summary": committed}
    return check_lib.TurnObservation(
        scenario_id="B53", step_index=1, step_kind="text", topic_id=LESSON, grade=9,
        request_payload={"student_message": student_message}, http_status=200,
        response={"status": "ready", "answer": answer, "answer_verdict": None,
                  "last_tutor_task": task, "next_state": {"v": 1},
                  "session_mode": "practice", "effective_topic": LESSON},
        session_before=session, session_after=session, sdk_calls=1)


def leak_outcome(answer, *, task, committed, student_message):
    return check_lib.check_no_answer_leak(
        observation(answer, task=task, committed=committed,
                    student_message=student_message)).outcome


# ---------------------------------------------------------------------------
# 1. TAČAN POKUŠAJ SMIJE BITI POTVRĐEN
# ---------------------------------------------------------------------------

CONFIRMATIONS = {
    "potvrda": "Tačno! To je ispravno rješenje.",
    "ponavljanje committed odgovora": r"Tačno, $x=3$ je rješenje ove jednačine.",
    "provjera uvrstavanjem": (r"Tačno. Provjerimo: za $x=3$ je $(3+2)=5$, "
                              r"odnosno $5=5$."),
    "zivi B53 korak 1": B53_STEP1_REPLY,
}


@pytest.mark.parametrize("label,reply", sorted(CONFIRMATIONS.items()))
def test_a_correct_attempt_may_be_confirmed(label, reply):
    assert leak_outcome(reply, task=B53_TASK, committed=B53_COMMITTED,
                        student_message=B53_CORRECT_ATTEMPT) != check_lib.FAIL, label


# ---------------------------------------------------------------------------
# 2. CHECK SE NE PRIMJENJUJE — SKIP, NIKAD FAIL
# ---------------------------------------------------------------------------

def test_the_leak_check_does_not_apply_to_a_proven_correct_attempt():
    result = check_lib.check_no_answer_leak(
        observation(B53_STEP1_REPLY, task=B53_TASK, committed=B53_COMMITTED,
                    student_message=B53_CORRECT_ATTEMPT))
    assert result.outcome == check_lib.SKIP
    assert "učenik" in result.detail or "student" in result.detail.lower()


def test_the_hint_variant_of_the_same_check_behaves_identically():
    assert check_lib.check_hint_no_leak(
        observation(B53_STEP1_REPLY, task=B53_TASK, committed=B53_COMMITTED,
                    student_message=B53_CORRECT_ATTEMPT)).outcome == check_lib.SKIP


# ---------------------------------------------------------------------------
# 3. POGREŠAN POKUŠAJ — ZAŠTITA OSTAJE NEPROMIJENJENA
# ---------------------------------------------------------------------------

def test_a_wrong_attempt_does_not_open_the_gate():
    """Pokušaj $x=100$ ne dokazuje ništa o committed $x=3$."""
    assert leak_outcome(r"Rješenje je $x=3$, jer $(3+2)=5$.",
                        task=B53_TASK, committed=B53_COMMITTED,
                        student_message=B53_WRONG_ATTEMPT) == check_lib.FAIL


def test_an_empty_student_message_does_not_open_the_gate():
    assert leak_outcome(r"Rješenje je $x=3$, jer $(3+2)=5$.",
                        task=B53_TASK, committed=B53_COMMITTED,
                        student_message="") == check_lib.FAIL


def test_a_near_miss_attempt_does_not_open_the_gate():
    """`x=30` nije `x=3` — normalizacija se ne smije olabaviti."""
    assert leak_outcome(r"Rješenje je $x=3$, jer $(3+2)=5$.",
                        task=B53_TASK, committed=B53_COMMITTED,
                        student_message="Mislim da je x=30.") == check_lib.FAIL


# ---------------------------------------------------------------------------
# 4. ŽIVI B53 IZ postFinalFixes — OBA KORAKA
# ---------------------------------------------------------------------------

def test_live_b53_step1_correct_attempt_is_not_a_leak():
    assert leak_outcome(B53_STEP1_REPLY, task=B53_TASK, committed=B53_COMMITTED,
                        student_message=B53_CORRECT_ATTEMPT) != check_lib.FAIL


def test_live_b53_step2_wrong_attempt_reply_stays_clean():
    """Tutor na $x=100$ nije otkrio $x=3$ — mora proći, i to bez izuzetka."""
    assert leak_outcome(B53_STEP2_REPLY, task=B53_TASK, committed=B53_COMMITTED,
                        student_message=B53_WRONG_ATTEMPT) == check_lib.PASS


def test_a_leak_after_the_wrong_second_attempt_is_still_caught():
    assert leak_outcome(r"Dakle $x=\frac{5-2}{1}=3$, to je rješenje.",
                        task=B53_TASK, committed=B53_COMMITTED,
                        student_message=B53_WRONG_ATTEMPT) == check_lib.FAIL


# ---------------------------------------------------------------------------
# 5. RANIJI ŽIVI B53 FIXTURE — MORA I DALJE BITI LEAK
# ---------------------------------------------------------------------------

def test_the_earlier_live_b53_fixture_still_fails():
    """Zadatak $2(x)=8$, pokušaj x=3, tutor objavi x=4 — pokušaj NE pokriva 4."""
    assert leak_outcome(OLD_LEAKING_REPLY, task=OLD_TASK, committed=OLD_COMMITTED,
                        student_message=OLD_ATTEMPT) == check_lib.FAIL


def test_the_wrong_attempt_value_alone_never_covers_the_committed_answer():
    """Determinističan dokaz: poruka „…x=3“ ne otkriva committed `$x=4$`."""
    assert not feedback.leaks_answer(OLD_ATTEMPT, OLD_COMMITTED, OLD_COMMITTED,
                                     task_text=OLD_TASK)
    assert feedback.leaks_answer(B53_CORRECT_ATTEMPT, B53_COMMITTED, B53_COMMITTED,
                                 task_text=B53_TASK)


# ---------------------------------------------------------------------------
# 6–7. PARITET S PRODUKCIJOM, BEZ IZMJENE PRODUKCIJE
# ---------------------------------------------------------------------------

def test_production_gate_already_allowed_the_live_reply(store, fake_llm):
    """Isti izuzetak već postoji u `pipeline._reveals_committed_answer`."""
    session = {"current_task": B53_TASK,
               "current_options": [{"id": "a", "text": B53_COMMITTED}],
               "correct_option_id": "a", "expected_answer_summary": B53_COMMITTED,
               "task_completed": False}
    assert not tutor_pipeline._reveals_committed_answer(
        session, B53_STEP1_REPLY, B53_CORRECT_ATTEMPT)
    # …a bez učenikove poruke isti tekst OSTAJE blokiran.
    assert tutor_pipeline._reveals_committed_answer(session, B53_STEP1_REPLY, "")


def test_the_evaluator_never_branches_on_a_scenario_id():
    """Izuzetak smije biti opšte pravilo, nikad grana po scenariju.

    Citiranje živog nalaza u komentaru je projektna konvencija; zabranjeno je
    da se `scenario_id` čita ili poredi u samoj logici."""
    import ast
    import inspect

    for function in (check_lib._answer_leak_result,
                     check_lib._student_stated_committed_answer):
        tree = ast.parse(inspect.getsource(function).lstrip())
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert "scenario_id" not in names, function.__name__
        literals = {node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        # Doslovan scenario ID (A07, B53, …) ne smije biti operand u kodu.
        assert not any(len(v) == 3 and v[0] in "AB" and v[1:].isdigit()
                       for v in literals), function.__name__
