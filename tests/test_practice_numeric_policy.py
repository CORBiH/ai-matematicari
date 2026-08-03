"""Family-aware Practice numeric-consistency policy (Phase 2 fix).

Requirement resolved: incorrect distractors must NEVER be truth-checked
(they are intentionally wrong by multiple-choice design), while the
server-marked CORRECT option and expected_answer must ALWAYS be checked
regardless of family. The QUESTION text follows a per-family policy:
"check" (default) or "allow_intentional_mismatch" for the 10 families whose
question may present a deliberately false object under examination.
"""
import json

import pytest

from matbot import task_families as tf
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.task_family_validation import CONTRACTS, question_numeric_policy
from tests.conftest import FakeLLM, make_options, make_output, make_task, make_task_for_family

_MISMATCH_FAMILIES = {
    "detect_student_error", "detect_formula_error", "recognize_correct_statement",
    "verify_solution", "verify_ordered_pair", "choose_correct_formula",
    "identify_next_step", "choose_method", "determine_number_of_solutions",
    "identify_equivalent_system",
}


@pytest.fixture
def force_family(monkeypatch):
    """Server bira porodicu deterministički iz lekcije (matbot/task_families.py)
    — za testove koji provjeravaju SPECIFIČNU porodicu neveznu za izabranu
    lekciju, prisiljavamo select_family() da vrati traženu porodicu, isto kao
    kad bi je stvarna lekcija prirodno dodijelila."""
    def _apply(family_id):
        monkeypatch.setattr(tf, "select_family", lambda *a, **kw: family_id)
    return _apply


# Lekcija iz oblasti Razlomci BEZ ugovora → nepromijenjen legacy put, tj. tačno
# ono što ovaj fajl i testira (politika po PORODICI). Pilot lekcije s ugovorom
# (6-04-005/006/009/010/011/012) ovdje se namjerno ne koriste: one više ne idu
# kroz porodice nego kroz univerzalni motor (matbot/contracts/).
LEGACY_FRACTION_TOPIC = "6-04-007"


def _payload(msg="Daj zadatak.", **kw):
    base = {"session_id": "sess-numpol", "grade": 6, "selected_topic": LEGACY_FRACTION_TOPIC,
            "selected_oblast": "", "student_message": msg, "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "", "selected_option_id": "", "client_turn_id": ""}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Global invariants: policy field exists and matches spec exactly
# ---------------------------------------------------------------------------

def test_all_families_have_an_explicit_question_numeric_policy():
    for family_id, contract in CONTRACTS.items():
        assert contract.question_numeric_policy in ("check", "allow_intentional_mismatch"), family_id


def test_exact_ten_families_allow_intentional_mismatch():
    actual = {fid for fid, c in CONTRACTS.items() if c.question_numeric_policy == "allow_intentional_mismatch"}
    assert actual == _MISMATCH_FAMILIES


def test_all_other_families_default_to_check():
    for family_id in CONTRACTS:
        if family_id not in _MISMATCH_FAMILIES:
            assert question_numeric_policy(family_id) == "check", family_id


def test_unknown_family_defaults_to_check():
    assert question_numeric_policy("nepostojeca_porodica") == "check"


# ---------------------------------------------------------------------------
# 1-4. Incorrect distractors never truth-checked
# ---------------------------------------------------------------------------

def test_valid_task_with_false_equality_distractors_is_accepted(force_family):
    """Tri od četiri opcije sadrže NAMJERNO pogrešnu jednakost — zadatak i
    dalje mora biti prihvaćen jer su to distraktori, ne tačan odgovor."""
    force_family("fraction_operation")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Izračunaj $\\frac{2}{7}+\\frac{3}{7}$.",
        expected="$\\frac{5}{7}$",
        options=make_options("$\\frac{5}{7}$",           # correct
                             "$3\\cdot16/2=48$",          # false distractor
                             "$\\sqrt{100}=20$",          # false distractor
                             "$\\frac{75}{3}=15$"),       # false distractor
        correct_option_index=0,
        task_family="fraction_operation")))
    r = run_practice_turn(store, fake, _payload())
    assert r["status"] == "ready"
    assert r["answer"] != SAFE_ERROR_MESSAGE


def test_false_distractors_remain_present_and_unchanged_in_browser_options(force_family):
    force_family("fraction_operation")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Izračunaj $\\frac{2}{7}+\\frac{3}{7}$.",
        expected="$\\frac{5}{7}$",
        options=make_options("$\\frac{5}{7}$", "$3\\cdot16/2=48$",
                             "$\\sqrt{100}=20$", "$\\frac{75}{3}=15$"),
        correct_option_index=0,
        task_family="fraction_operation")))
    r = run_practice_turn(store, fake, _payload())
    texts = {o["text"] for o in r["next_state"]["task"]["options"]}
    assert texts == {"$\\frac{5}{7}$", "$3\\cdot16/2=48$", "$\\sqrt{100}=20$", "$\\frac{75}{3}=15$"}


def test_correct_option_identity_survives_sanitation_and_shuffle(force_family):
    force_family("fraction_operation")
    for _ in range(10):
        store, fake = SessionStore(), FakeLLM()
        fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
            text="Izračunaj $\\frac{2}{7}+\\frac{3}{7}$.",
            expected="$\\frac{5}{7}$",
            options=make_options("$\\frac{5}{7}$", "$3\\cdot16/2=48$",
                                 "$\\sqrt{100}=20$", "$\\frac{75}{3}=15$"),
            correct_option_index=0,
            task_family="fraction_operation")))
        run_practice_turn(store, fake, _payload())
        sess = store.peek("sess-numpol")
        correct_text = next(o["text"] for o in sess["current_options"]
                            if o["id"] == sess["correct_option_id"])
        assert correct_text == "$\\frac{5}{7}$"


def test_expected_answer_and_correct_option_id_do_not_leak(force_family):
    force_family("fraction_operation")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Izračunaj $\\frac{2}{7}+\\frac{3}{7}$.",
        expected="TAJNO-5-7",
        options=make_options("$\\frac{5}{7}$", "$3\\cdot16/2=48$",
                             "$\\sqrt{100}=20$", "$\\frac{75}{3}=15$"),
        correct_option_index=0,
        task_family="fraction_operation")))
    r = run_practice_turn(store, fake, _payload())
    raw = json.dumps(r, ensure_ascii=False)
    assert "TAJNO-5-7" not in raw
    assert "correct_option_id" not in raw


# ---------------------------------------------------------------------------
# 5-7. Correct option
# ---------------------------------------------------------------------------

def test_inconsistent_marked_correct_option_is_rejected(force_family):
    force_family("direct_formula_application")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Pravilan šestougao ima stranicu $a=4$ cm. Izračunaj površinu.",
        expected="$24\\sqrt{3}$",
        options=make_options(
            "$P=\\frac{3\\cdot16\\sqrt3}{2}=48\\sqrt3\\,\\text{cm}^2$",  # WRONG, marked correct
            "$10\\,\\text{cm}^2$", "$20\\,\\text{cm}^2$", "$30\\,\\text{cm}^2$"),
        correct_option_index=0,
        task_family="direct_formula_application")))
    r = run_practice_turn(store, fake, _payload())
    assert r["answer"] == SAFE_ERROR_MESSAGE


def test_consistent_marked_correct_option_is_accepted(force_family):
    force_family("direct_formula_application")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Pravilan šestougao ima stranicu $a=4$ cm. Izračunaj površinu.",
        expected="$24\\sqrt{3}$",
        options=make_options(
            "$P=\\frac{3\\cdot16\\sqrt3}{2}=24\\sqrt3\\,\\text{cm}^2$",  # correct
            "$10\\,\\text{cm}^2$", "$20\\,\\text{cm}^2$", "$30\\,\\text{cm}^2$"),
        correct_option_index=0,
        task_family="direct_formula_application")))
    r = run_practice_turn(store, fake, _payload())
    assert r["status"] == "ready"


def test_wrong_equality_cannot_hide_by_being_marked_correct_only(force_family):
    """Distraktori smiju biti pogrešni, ali TAČNA opcija ne smije — provjera
    mora gledati koja je opcija OZNAČENA kao tačna, ne samo bilo koju."""
    force_family("direct_computation")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Izračunaj $\\sqrt{100}$.",
        expected="10",
        # Tačna opcija (index 2) je namjerno pogrešna jednakost.
        options=make_options("5", "15", "$\\sqrt{100}=20$", "25"),
        correct_option_index=2,
        task_family="direct_computation")))
    r = run_practice_turn(store, fake, _payload())
    assert r["answer"] == SAFE_ERROR_MESSAGE


# ---------------------------------------------------------------------------
# 8-14. Family-aware questions
# ---------------------------------------------------------------------------

def test_detect_student_error_accepts_false_equality_in_question(force_family):
    force_family("detect_student_error")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Učenik je napisao: $3\\cdot16/2=48$. Šta je pogriješio?",
        expected="Zaboravio je da rezultat treba biti 24.",
        options=make_options(
            "Ispravno je izračunao, nema greške.",  # wrong (correct index will differ)
            "Nije podijelio sa 2 na kraju, pa je rezultat duplo veći.",  # correct
            "Pomnožio je pogrešne brojeve.",
            "Trebao je oduzeti umjesto pomnožiti."),
        correct_option_index=1,
        task_family="detect_student_error")))
    r = run_practice_turn(store, fake, _payload())
    assert r["status"] == "ready"


def test_detect_formula_error_accepts_false_formula_in_question(force_family):
    force_family("detect_formula_error")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Učenik je za prizmu napisao $V=O_BH$. Šta je pogriješio?",
        expected="Zapremina je V=BH, ne V=O_BH.",
        options=make_options(
            "Zamijenio je B (površinu osnove) sa O_B (obimom osnove).",  # correct
            "Nema greške, formula je tačna.",
            "Trebao je koristiti $M=O_BH$ umjesto $V$.",
            "Trebao je podijeliti sa 3."),
        correct_option_index=0,
        task_family="detect_formula_error")))
    r = run_practice_turn(store, fake, _payload())
    assert r["status"] == "ready"


def test_choose_correct_formula_accepts_three_false_formulas(force_family):
    force_family("choose_correct_formula")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Koja formula daje zapreminu pravilne piramide?",
        expected="$V=\\frac{BH}{3}$",
        options=make_options(
            "$V=BH$", "$V=\\frac{BH}{3}$", "$V=2B+M$", "$V=\\frac{O_Bh_a}{2}$"),
        correct_option_index=1,
        task_family="choose_correct_formula")))
    r = run_practice_turn(store, fake, _payload())
    assert r["status"] == "ready"


def test_recognize_correct_statement_accepts_false_statements_in_options(force_family):
    force_family("recognize_correct_statement")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Koja tvrdnja o uniji skupova je tačna?",
        expected="Unija sadrži sve elemente oba skupa.",
        options=make_options(
            "Unija sadrži sve elemente oba skupa.",       # correct
            "Unija sadrži samo zajedničke elemente.",      # false statement, allowed
            "Unija je uvijek prazan skup.",                # false statement, allowed
            "Unija sadrži samo elemente prvog skupa."),    # false statement, allowed
        correct_option_index=0,
        task_family="recognize_correct_statement")))
    r = run_practice_turn(store, fake, _payload())
    assert r["status"] == "ready"


def test_verify_solution_accepts_false_attempted_solution_as_examined_object(force_family):
    force_family("verify_solution")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Da li je $x=10$ rješenje jednačine $2x-3=5$?",
        expected="Nije, jer $2\\cdot10-3=17\\ne5$.",
        options=make_options(
            "Nije, jer se ne poklapa.",  # correct (prose, not numerically checked)
            "Jeste, jednačina je zadovoljena.",
            "Nije moguće provjeriti.",
            "Jeste, ali samo približno."),
        correct_option_index=0,
        task_family="verify_solution")))
    r = run_practice_turn(store, fake, _payload())
    assert r["status"] == "ready"


def test_verify_ordered_pair_accepts_pair_that_does_not_satisfy_system(force_family):
    force_family("verify_ordered_pair")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Provjeri da li je uređeni par $(1,1)$ rješenje sistema: $x+y=10$ i $x-y=4$.",
        expected="Ne zadovoljava.",
        options=make_options(
            "Ne zadovoljava nijednu jednačinu.",  # correct
            "Zadovoljava obje jednačine.",
            "Zadovoljava samo prvu.",
            "Zadovoljava samo drugu."),
        correct_option_index=0,
        task_family="verify_ordered_pair")))
    r = run_practice_turn(store, fake, _payload())
    assert r["status"] == "ready"


def test_direct_computation_question_with_false_asserted_equality_is_rejected(force_family):
    """direct_computation NIJE u listi 'allow_intentional_mismatch' — pitanje
    predstavlja činjenice kao date, pa dokazano pogrešna jednakost u SAMOM
    PITANJU mora biti odbijena."""
    force_family("direct_computation")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Znajući da je $3\\cdot16/2=48$, izračunaj polovinu tog broja.",
        expected="24",
        options=make_options("24", "12", "48", "6"),
        correct_option_index=0,
        task_family="direct_computation")))
    r = run_practice_turn(store, fake, _payload())
    assert r["answer"] == SAFE_ERROR_MESSAGE


# ---------------------------------------------------------------------------
# 15-19. Feedback and reveal
# ---------------------------------------------------------------------------

def _bootstrap(store, fake):
    fake.queue(make_output(reply="Evo zadatka.",
                            new_task=make_task_for_family("expand_to_given_denominator")))
    run_practice_turn(store, fake, _payload())
    return store.peek("sess-numpol")


def _wrong_id(sess):
    return next(o["id"] for o in sess["current_options"] if o["id"] != sess["correct_option_id"])


def test_inconsistent_first_wrong_hint_is_replaced_safely():
    from matbot import feedback
    store, fake = SessionStore(), FakeLLM()
    sess = _bootstrap(store, fake)
    fake.queue(make_output(reply="", hint="Provjeri: $3\\cdot16/2=48$, pa je to tačno."))
    r = run_practice_turn(store, fake, _payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=_wrong_id(sess), client_turn_id="t1"))
    assert r["status"] == "ready"
    assert r["answer"].startswith("Netačno.")
    assert feedback.GENERIC_HINT in r["answer"]
    assert "48" not in r["answer"]


def test_inconsistent_correct_answer_feedback_is_rejected():
    store, fake = SessionStore(), FakeLLM()
    sess = _bootstrap(store, fake)
    fake.queue(make_output(
        reply="Tačno! Provjerimo: $3\\cdot16/2=48$, sve se slaže.",
        evaluation="correct"))
    r = run_practice_turn(store, fake, _payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=sess["correct_option_id"], client_turn_id="t1"))
    assert r["answer"] == SAFE_ERROR_MESSAGE


def test_inconsistent_reveal_solution_is_rejected():
    store, fake = SessionStore(), FakeLLM()
    sess = _bootstrap(store, fake)
    wrong = _wrong_id(sess)
    fake.queue(make_output(reply="x", hint="Prvi hint."))
    run_practice_turn(store, fake, _payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=wrong, client_turn_id="t1"))

    sess2 = store.peek("sess-numpol")
    second_wrong = next(o["id"] for o in sess2["current_options"]
                        if o["id"] not in (sess2["correct_option_id"], wrong))
    fake.queue(make_output(reply="Postupak: $\\sqrt{100}=20$, dakle rezultat je 20."))
    r = run_practice_turn(store, fake, _payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=second_wrong, client_turn_id="t2"))
    assert r["answer"] == SAFE_ERROR_MESSAGE


def test_invalid_continuation_does_not_mutate_state():
    store, fake = SessionStore(), FakeLLM()
    sess = _bootstrap(store, fake)
    before = store.peek("sess-numpol")
    fake.queue(make_output(
        reply="Tačno! $3\\cdot16/2=48$.", evaluation="correct"))
    run_practice_turn(store, fake, _payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=sess["correct_option_id"], client_turn_id="t1"))
    after = store.peek("sess-numpol")
    assert after["retry_required"] == before["retry_required"]
    assert after["correctly_completed_families"] == before["correctly_completed_families"]
    assert after["current_task"] == before["current_task"]
    assert after["current_options"] == before["current_options"]
    assert after["recent_task_signatures"] == before["recent_task_signatures"]
    assert after["task_completed"] == before["task_completed"]


def test_exactly_one_model_call_when_hint_is_replaced():
    store, fake = SessionStore(), FakeLLM()
    sess = _bootstrap(store, fake)
    fake.queue(make_output(reply="", hint="$3\\cdot16/2=48$."))
    run_practice_turn(store, fake, _payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=_wrong_id(sess), client_turn_id="t1"))
    assert fake.practice_call_count == 2  # bootstrap + klik, bez popravnog poziva


def test_exactly_one_model_call_when_task_rejected():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Izračunaj $\\sqrt{100}$.", expected="10",
        options=make_options("5", "15", "$\\sqrt{100}=20$", "25"),
        correct_option_index=2, task_family="direct_computation")))
    run_practice_turn(store, fake, _payload())
    assert fake.practice_call_count == 1


# ---------------------------------------------------------------------------
# Global invariant sanity: existing family/progression/mathcheck suites green
# ---------------------------------------------------------------------------

def test_practice_valid_task_still_accepted_end_to_end():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.",
                            new_task=make_task_for_family("expand_to_given_denominator")))
    r = run_practice_turn(store, fake, _payload())
    assert r["status"] == "ready"
    assert fake.practice_call_count == 1
