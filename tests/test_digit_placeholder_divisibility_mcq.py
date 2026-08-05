r"""Djeljivosni oracle ne smije suditi zadatku s cifrom-mjestodržačem.

OBAVEZNI LIVE RELEASE GATE, commit baef3fd, scenario `harder_level2`
(`scratchpad/live_release_gate/baef3fd62491b59956ffc4d591ed8abbb9ac97bc.json`).

    lekcija      : 6-03-004 „Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25“
    nivoi        : previous=1 target=2 committed=1
    zadatak      : „Nađi cifru $x$ tako da je broj $3x5$ djeljiv sa 9.
                    Koja cifra $x$ to zadovoljava?“
    draft issues : no_correct_option
    reviewer     : correct
    final issues : marked_option_math_mismatch
    dijagnostika : reviewer_final_mcq_integrity_rejection: decision=correct
                   in_tutor_preflight=True unchanged=False marked_option_math_mismatch
    objavljeno   : ne · sesija nepromijenjena · 2 poziva

MATEMATIKA: $3x5$ je djeljiv sa 9 kad je $3+x+5=8+x$ djeljiv sa 9, dakle $x=1$.
Tačna opcija je CIFRA $1$ — a nijedna cifra od 0 do 9 nije djeljiva sa 9 osim 0
i 9 samih.

UZROK: `mcq_integrity.evaluate_divisibility_mcq` pretpostavlja da su OPCIJE oni
brojevi čija se djeljivost tvrdi. Ovdje broj čija se djeljivost tvrdi jeste
`3x5` — numerik s mjestodržačem — a opcije su kandidati za cifru `x`. Oracle za
taj oblik nije imao granicu primjenjivosti, pa je nad potpuno ispravnim paketom
vratio `no_correct_option`. Recenzent je tu lažnu primjedbu „popravio“ tako što
je među opcije uveo broj djeljiv sa 9 (živi artifact: `18`), čime je pokvario
ispravan paket i pao na `marked_option_math_mismatch`.

Oracle se NE slabi: za oblik koji stvarno podržava („Koji od brojeva je djeljiv
sa 25?“) sva četiri ishoda ostaju netaknuta. Mijenja se samo granica: kad se
tvrdnja odnosi na numerik s mjestodržačem, oracle ne može ništa dokazati i mora
preskočiti, a ne pogađati.
"""
import pytest

from matbot import mcq_integrity
from matbot.tutor import package_preflight
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import prompts as tutor_prompts
from tests.conftest import (make_reviewer_checks, make_reviewer_final, make_task_payload,
                            make_tutor_draft, queue_two_call)

LESSON = "6-03-004"
SESSION = "gate-1"

# --- DOSLOVNI GATE ZADATAK — ne mijenjati ----------------------------------
GATE_TASK = (r"Nađi cifru $x$ tako da je broj $3x5$ djeljiv sa 9. "
             r"Koja cifra $x$ to zadovoljava?")
GATE_DIGIT_OPTIONS = (r"$1$", r"$2$", r"$4$", r"$8$")   # tačna je $1$ (indeks 0)
GATE_BROKEN_OPTIONS = (r"$18$", r"$1$", r"$4$", r"$8$")  # recenzentova „ispravka“

SUPPORTED_TASK = "Koji od sljedećih brojeva je djeljiv sa 25?"
SUPPORTED_OPTIONS = (r"$75$", r"$68$", r"$31$", r"$44$")


# ---------------------------------------------------------------------------
# 1. ORACLE: MJESTODRŽAČ ZNAČI „NE MOGU DOKAZATI“
# ---------------------------------------------------------------------------

def test_the_gate_task_is_outside_the_oracle_scope():
    result = mcq_integrity.evaluate_divisibility_mcq(GATE_TASK, GATE_DIGIT_OPTIONS)
    assert result.applicable is False
    assert result.reason_code == ""


def test_a_correct_digit_package_is_no_longer_called_no_correct_option():
    """Doslovni živi uzrok: ispravan paket je dobijao lažni `no_correct_option`."""
    failure, _ = mcq_integrity.mathematical_publication_failure(
        GATE_TASK, GATE_DIGIT_OPTIONS, 0)
    assert failure == ""


@pytest.mark.parametrize("marked", [0, 1, 2, 3])
def test_no_marked_index_of_a_placeholder_task_is_judged(marked):
    failure, _ = mcq_integrity.mathematical_publication_failure(
        GATE_TASK, GATE_DIGIT_OPTIONS, marked)
    assert failure == ""


PLACEHOLDER_SHAPES = {
    "cifra u sredini": r"Za koju cifru $x$ je broj $3x5$ djeljiv sa 9?",
    "cifra na kraju": r"Nađi cifru $a$ tako da je $47a$ djeljiv sa 3.",
    "nadvučeni zapis": r"Za koju cifru $x$ je $\overline{2x8}$ djeljiv sa 4?",
    "slovo na početku": r"Koja cifra $b$ čini broj $b24$ djeljivim sa 6?",
}


@pytest.mark.parametrize("label,question", sorted(PLACEHOLDER_SHAPES.items()))
def test_every_placeholder_numeral_leaves_the_oracle_scope(label, question):
    assert mcq_integrity.evaluate_divisibility_mcq(
        question, (r"$1$", r"$2$", r"$4$", r"$8$")).applicable is False, label


# ---------------------------------------------------------------------------
# 2. ORACLE SE NE SLABI ZA OBLIK KOJI STVARNO PODRŽAVA
# ---------------------------------------------------------------------------

SUPPORTED_VERDICTS = {
    "tačna oznaka": (SUPPORTED_OPTIONS, 0, ""),
    "kriva oznaka": (SUPPORTED_OPTIONS, 1, "marked_option_math_mismatch"),
    "nijedna tačna": ((r"$74$", r"$68$", r"$31$", r"$44$"), 0, "no_correct_option"),
    "dvije tačne": ((r"$75$", r"$50$", r"$31$", r"$44$"), 0, "multiple_correct_options"),
}


@pytest.mark.parametrize("label", sorted(SUPPORTED_VERDICTS))
def test_the_supported_shape_keeps_every_verdict(label):
    options, marked, expected = SUPPORTED_VERDICTS[label]
    failure, result = mcq_integrity.mathematical_publication_failure(
        SUPPORTED_TASK, options, marked)
    assert result.applicable is True, label
    assert failure == expected, label


def test_a_plain_number_question_without_letters_is_still_judged():
    """Kontrola granice: pitanje bez ijednog slova uz cifru ostaje u dosegu."""
    assert mcq_integrity.evaluate_divisibility_mcq(
        "Koji broj je djeljiv sa 3 i sa 5?", (r"$15$", r"$16$")).applicable is True


# ---------------------------------------------------------------------------
# 3. PREFLIGHT: ISTA GRANICA, I ISTI IZVJEŠTAJ REVIEWERU
# ---------------------------------------------------------------------------

def _task(text=GATE_TASK, options=GATE_DIGIT_OPTIONS, marked=0, expected=None,
          **updates):
    task = make_task_payload(text=text, options=options, correct_option_index=marked,
                             expected=expected if expected is not None else options[marked])
    return task.model_copy(update=updates) if updates else task


def test_preflight_no_longer_reports_the_false_issue():
    assert package_preflight.collect_package_issues(_task()) == ()


def test_preflight_still_reports_a_real_mismatch_with_option_ids():
    issues = package_preflight.collect_package_issues(
        _task(text=SUPPORTED_TASK, options=SUPPORTED_OPTIONS, marked=1))
    codes = [issue.code for issue in issues]
    assert "marked_option_math_mismatch" in codes


def test_the_reviewer_message_names_the_issue_code():
    issues = package_preflight.collect_package_issues(
        _task(text=SUPPORTED_TASK, options=SUPPORTED_OPTIONS, marked=1))
    message = package_preflight.format_for_reviewer(issues)
    assert "marked_option_math_mismatch" in message


def test_the_reviewer_message_names_the_affected_option_ids():
    """Nalaz nad opcijama mora imenovati POGOĐENE opcije, ne samo kod."""
    duplicated = _task(text=SUPPORTED_TASK,
                       options=(r"$\frac{1}{2}$", r"$\frac{2}{4}$", r"$\frac{3}{4}$",
                                r"$\frac{1}{4}$"),
                       marked=0, expected=r"$\frac{1}{2}$")
    message = package_preflight.format_for_reviewer(
        package_preflight.collect_package_issues(duplicated))
    assert "option IDs" in message
    assert "a" in message and "b" in message


# ---------------------------------------------------------------------------
# 4. CIJELI TURN — GATE SCENARIO harder_level2
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _levels_on(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(message="Daj mi zadatak.", client_turn_id="gate-t1"):
    return {
        "session_id": SESSION, "grade": 6, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": client_turn_id,
    }


def _draft(task):
    return make_tutor_draft(intent="generate_task", new_task=task)


def test_the_gate_package_now_publishes(store, fake_llm):
    """Ispravan paket iz gate lekcije prolazi — bez trećeg poziva."""
    task = _task()
    queue_two_call(fake_llm, draft=_draft(task),
                   reviewer=make_reviewer_final(decision="approve", final=_draft(task)))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn())

    assert response["status"] == "ready"
    assert "3x5" in response["answer"]
    assert fake_llm.call_count == 2
    assert store.peek(SESSION)["current_task"]


def test_the_reviewers_broken_correction_is_still_rejected(store, fake_llm):
    """Živi ishod: uvođenje $18$ pomiče označenu opciju — mora pasti zatvoreno."""
    broken = _task(text=SUPPORTED_TASK, options=SUPPORTED_OPTIONS, marked=1,
                   expected=SUPPORTED_OPTIONS[1])
    queue_two_call(fake_llm, draft=_draft(_task(text=SUPPORTED_TASK,
                                                options=SUPPORTED_OPTIONS, marked=0)),
                   reviewer=make_reviewer_final(decision="correct", final=_draft(broken)))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn())

    assert response.get("status") is None
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2                 # nikad treći poziv
    assert store.peek(SESSION) is None              # sesija nepromijenjena


def test_a_correction_that_moves_the_problem_to_another_pair_is_rejected(store, fake_llm):
    """Recenzent ne smije premjestiti isti nalaz na drugi par opcija."""
    broken = _task(text=SUPPORTED_TASK,
                   options=(r"$75$", r"$50$", r"$31$", r"$44$"), marked=0,
                   expected=r"$75$")
    moved = _task(text=SUPPORTED_TASK,
                  options=(r"$31$", r"$44$", r"$75$", r"$100$"), marked=2,
                  expected=r"$75$")
    queue_two_call(fake_llm, draft=_draft(broken),
                   reviewer=make_reviewer_final(decision="correct", final=_draft(moved)))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn())

    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2
    assert store.peek(SESSION) is None


def test_a_decision_with_a_false_mandatory_check_is_still_rejected(store, fake_llm):
    task = _task()
    queue_two_call(fake_llm, draft=_draft(task),
                   reviewer=make_reviewer_final(
                       decision="correct", final=_draft(task),
                       checks=make_reviewer_checks(marked_option_correct=False)))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn())
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None


# ---------------------------------------------------------------------------
# 5. PROMPT ZADRŽAVA POSTOJEĆE ZAHTJEVE
# ---------------------------------------------------------------------------

def test_the_reviewer_prompt_still_demands_a_complete_resolution():
    from matbot.tutor import lesson_context as lesson_context_module

    text = tutor_prompts.build_reviewer_instructions(
        lesson_context_module.build(6, LESSON)).lower()
    assert "every reported issue" in text
    assert "must not introduce a new defect" in text
