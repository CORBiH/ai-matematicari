# -*- coding: utf-8 -*-
"""Dvoslojna zaštita razredne sposobnosti u Explainu.

ŽIVI NALAZ koji je ovo iznudio (mjerena kampanja od 12 poziva, izdanje
0a2f087): čišćenje protivrječnosti u promptu NIJE zaustavilo račun. Poslije
očišćenog prompta 6. razred je i dalje vratio „$\\sqrt{36}=6$", a jedan raniji
propust bio je ČISTA PROZA — „Kvadratni korijen broja $49$ je $7$" — bez
ijednog `\\sqrt`, pa ga izlazni validator notacije po konstrukciji ne vidi.

SLOJ 1 (ulaz): server dokaže da poruka traži IZVRŠENJE zabranjene operacije i
odgovori granicom kurikuluma — 0 poziva modela. Ono što model ne dobije, ne
može izračunati; to je jedini strukturni dokaz za prozni slučaj.
SLOJ 2 (izlaz): zapis koji razred nema, a model ga je uveo sam, pada zatvoreno.
"""
import pytest

from matbot import capability_requests, practice_policy
from matbot.explain import run_explain_turn
from matbot.practice import SAFE_ERROR_MESSAGE

EXPLAIN_CONTRACT = {"status", "answer", "answer_verdict", "last_tutor_task",
                    "next_state", "session_mode", "effective_topic"}


class CountingLLM:
    """Broji pozive. Svaki poziv je greška u testovima preflighta."""

    def __init__(self, reply="Neutralan odgovor bez matematike."):
        self.calls = 0
        self.reply = reply

    def explain_turn(self, instructions, input_text):
        self.calls += 1

        class _Out:
            reply = self.reply

        class _Res:
            output = _Out()
            latency_ms = 1
            usage = {}

        return _Res()


def _turn(grade, topic, message):
    return {"session_id": "t", "grade": grade, "selected_topic": topic,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": "", "interaction_phase": "",
            "last_tutor_task": "", "last_tutor_message": "",
            "conversation_history": []}


# ---------------------------------------------------------------------------
# DETEKTOR ZAHTJEVA
# ---------------------------------------------------------------------------

OPERATIONAL = (
    "Koliko je $\\sqrt{36}$?",
    "Izracunaj $\\sqrt{25}$",
    "Izračunaj $\\sqrt{25}$.",
    "Koliki je kvadratni korijen broja 49?",
    "Pojednostavi $\\sqrt{20}$",
    "Samo mi reci rezultat korijena iz 64",
    "Znam da nije gradivo, ali samo mi reci koliko je korijen iz 64.",
    "Koliko je korijen iz 36?",
)

CONCEPTUAL = (
    "Šta je kvadratni korijen?",
    "Kada se uči korjenovanje?",
    "Zašto ovo još ne radimo?",
    "U kojem razredu se uči korijen?",
)

UNRELATED = (
    "Riješi: $x + \\frac{2}{7} = \\frac{5}{7}$",
    "Koliko je $\\frac{1}{2}+\\frac{1}{3}$?",
    "Objasni mi sabiranje razlomaka.",
    "Koliko je $3^2$?",
)


@pytest.mark.parametrize("message", OPERATIONAL)
def test_operational_radical_requests_are_detected(message):
    assert capability_requests.requests_execution(message), message
    assert capability_requests.CAPABILITY_RADICAL in \
        capability_requests.named_capabilities(message)


@pytest.mark.parametrize("message", CONCEPTUAL)
def test_conceptual_questions_are_not_execution_requests(message):
    assert not capability_requests.requests_execution(message), message


@pytest.mark.parametrize("message", UNRELATED)
def test_unrelated_messages_never_name_a_forbidden_capability(message):
    policy = practice_policy.resolve(grade=6)
    assert capability_requests.forbidden_operation_requests(message, policy) == ()


@pytest.mark.parametrize("message", OPERATIONAL)
def test_grade_six_blocks_every_operational_radical_request(message):
    policy = practice_policy.resolve(grade=6)
    assert capability_requests.forbidden_operation_requests(message, policy) == \
        (capability_requests.CAPABILITY_RADICAL,)


@pytest.mark.parametrize("message", OPERATIONAL)
def test_grade_eight_blocks_nothing(message):
    policy = practice_policy.resolve(grade=8)
    assert capability_requests.forbidden_operation_requests(message, policy) == ()


def test_grade_seven_recognition_is_never_treated_as_execution():
    """7. razred SMIJE prepoznati iracionalan broj — to nije nalog za račun."""
    policy = practice_policy.resolve(grade=7)
    for message in ("Da li je $\\sqrt{2}$ racionalan broj?",
                    "Je li $\\sqrt{2}$ racionalan ili iracionalan?",
                    "Zašto skup $Q$ treba proširiti?"):
        assert capability_requests.forbidden_operation_requests(message, policy) == (), message


def test_grade_seven_still_blocks_an_explicit_operation_request():
    policy = practice_policy.resolve(grade=7)
    assert capability_requests.forbidden_operation_requests(
        "Izračunaj $\\sqrt{20}$.", policy) == (capability_requests.CAPABILITY_RADICAL,)


def test_detector_reads_only_server_owned_facts():
    source = open(capability_requests.__file__, encoding="utf-8").read()
    assert "radical_operation_allowed" in source
    assert "pythagoras_operation_allowed" in source
    for banned in ("task_type", "answer_type", "lesson_id ==", "grade == 6"):
        assert banned not in source, banned


# ---------------------------------------------------------------------------
# SLOJ 1 — PREFLIGHT: NULA POZIVA MODELA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", OPERATIONAL)
def test_preflight_spends_no_model_call(message):
    llm = CountingLLM()
    result = run_explain_turn(llm, _turn(6, "6-08-004", message))
    assert llm.calls == 0, "model je pozvan za zabranjenu operaciju"
    assert result["status"] == "ready"
    assert result["answer"] != SAFE_ERROR_MESSAGE


def test_b3_pure_prose_request_never_reaches_the_model():
    """REGRESIJSKI TEST ZA B3 — jedini strukturni dokaz za prozni propust."""
    llm = CountingLLM(reply="Kvadratni korijen broja $49$ je $7$, jer je $7^2=49$.")
    result = run_explain_turn(
        llm, _turn(6, "6-08-004", "Koliki je kvadratni korijen broja 49?"))
    assert llm.calls == 0
    assert "7" not in result["answer"].replace("6. razredu", "").replace("8. razredu", "")


@pytest.mark.parametrize("message", OPERATIONAL)
def test_preflight_answer_carries_no_computed_result(message):
    llm = CountingLLM()
    answer = run_explain_turn(llm, _turn(6, "6-08-004", message))["answer"]
    assert "=" not in answer
    assert not practice_policy.find_radical_notation(answer), \
        "6. razred ne smije vidjeti ni zapis"


def test_preflight_response_matches_the_frontend_contract():
    llm = CountingLLM()
    result = run_explain_turn(llm, _turn(6, "6-08-004", "Koliko je $\\sqrt{36}$?"))
    assert set(result) == EXPLAIN_CONTRACT
    assert result["session_mode"] == "explain"
    assert result["answer_verdict"] is None
    assert result["last_tutor_task"] == ""
    assert result["effective_topic"] == "6-08-004"


def test_grade_seven_preflight_may_show_notation_it_is_allowed_to_see():
    llm = CountingLLM()
    answer = run_explain_turn(
        llm, _turn(7, "7-03-001", "Izračunaj $\\sqrt{20}$."))["answer"]
    assert llm.calls == 0
    assert practice_policy.find_radical_notation(answer), "7. razred zapis SMIJE"
    assert "8. razredu" in answer


def test_grade_eight_operational_request_still_reaches_the_model():
    llm = CountingLLM(reply="$\\sqrt{169}=13$.")
    result = run_explain_turn(llm, _turn(8, "8-01-008", "Izračunaj $\\sqrt{169}$."))
    assert llm.calls == 1
    assert result["answer"] == "$\\sqrt{169}=13$."


@pytest.mark.parametrize("message", CONCEPTUAL)
def test_conceptual_questions_still_reach_the_model(message):
    """Ne pretvaramo SVAKI spomen korijena u istu konzervu."""
    llm = CountingLLM()
    run_explain_turn(llm, _turn(6, "6-08-004", message))
    assert llm.calls == 1, message


# ---------------------------------------------------------------------------
# SLOJ 2 — IZLAZNA KAPIJA
# ---------------------------------------------------------------------------

def test_grade_six_notation_leak_fails_closed():
    llm = CountingLLM(reply="Za ovaj broj važi $\\sqrt{36}=6$, jer je $6\\cdot6=36$.")
    result = run_explain_turn(llm, _turn(6, "6-08-004", "Objasni mi mnogougao."))
    assert llm.calls == 1
    assert result["answer"] == SAFE_ERROR_MESSAGE


def test_grade_six_ordinary_mathematics_publishes_normally():
    llm = CountingLLM(reply="Mnogougao je zatvorena izlomljena linija. "
                            "Zbir uglova je $180^\\circ$ za trougao.")
    result = run_explain_turn(llm, _turn(6, "6-08-004", "Objasni mi mnogougao."))
    assert result["status"] == "ready"
    assert result["answer"] != SAFE_ERROR_MESSAGE


def test_grade_seven_recognition_prose_publishes():
    llm = CountingLLM(reply="Broj $\\sqrt{2}$ nije racionalan broj.")
    result = run_explain_turn(llm, _turn(7, "7-03-001", "Objasni mi skup $Q$."))
    assert result["status"] == "ready"
    assert "\\sqrt{2}" in result["answer"]


def test_grade_eight_root_computation_publishes():
    llm = CountingLLM(reply="Vrijedi $\\sqrt{36}=6$.")
    result = run_explain_turn(llm, _turn(8, "8-01-008", "Objasni mi korijen."))
    assert result["status"] == "ready"
    assert "\\sqrt{36}" in result["answer"]


def test_output_gate_is_not_coupled_to_scan_method_prose():
    """10/536 lekcija ima scan_method_prose=True; zaštita ne smije o njoj ovisiti."""
    policy = practice_policy.resolve(
        grade=6, lesson_id="6-08-004", lesson_title="Mnogougao/mnogokut",
        oblast="Skupovi tačaka, kružnica i krug")
    assert policy.scan_method_prose is False
    llm = CountingLLM(reply="Vrijedi $\\sqrt{36}=6$.")
    result = run_explain_turn(llm, _turn(6, "6-08-004", "Objasni mi mnogougao."))
    assert result["answer"] == SAFE_ERROR_MESSAGE


def test_method_prose_observer_stays_log_only():
    """Leksički osmatrač metode 6. razreda NE smije postati tvrda kapija."""
    llm = CountingLLM(reply="Prebacimo član na drugu stranu, pa je $x=3$.")
    result = run_explain_turn(
        llm, _turn(6, "6-07-002", "Riješi $x+2=5$."))
    assert result["status"] == "ready"
    assert result["answer"] != SAFE_ERROR_MESSAGE
