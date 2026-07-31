"""Focused Result/Quick architecture, behavior, and MathJax regressions."""

import json
from pathlib import Path

import pytest

from matbot import auth, prompts
from matbot.mathsafe import normalize_result_math_transport
from matbot.practice import SAFE_ERROR_MESSAGE
from matbot.quick import (
    REPAIR_ACKNOWLEDGEMENT,
    _clean_history,
    is_conversational_repair_message,
    run_quick_turn,
)
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_quick_output


ROOT = Path(__file__).resolve().parent.parent


def turn(message="riješi jednačinu 3-x=9", **updates):
    value = {
        "session_id": "result-audit-session",
        "grade": 6,
        "selected_topic": "",
        "selected_oblast": "",
        "student_message": message,
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
        "interaction_type": "",
        "selected_option_id": "",
        "client_turn_id": "result-audit-turn",
        "last_tutor_message": "",
        "conversation_history": [],
    }
    value.update(updates)
    return value


def http_payload(**updates):
    value = {
        "session_id": "result-audit-http",
        "client_turn_id": "result-audit-http-turn",
        "grade": 6,
        "mode": "quick",
        "entry_source": "free_chat",
        "selected_topic": "",
        "selected_oblast": "",
        "student_message": "riješi jednačinu 3-x=9",
        "conversation_history": [],
    }
    value.update(updates)
    return value


def test_result_routes_to_quick_once_without_practice_state(flask_app, fake_llm, store):
    fake_llm.queue(make_quick_output(reply="$x=-6$"))
    client = flask_app.test_client()
    response = client.post(
        "/api/ai-tutor/chat",
        json=http_payload(),
        headers={auth.TOKEN_HEADER: auth.issue_token()},
    )

    assert response.status_code == 200
    assert response.get_json()["session_mode"] == "quick"
    assert response.get_json()["answer"] == "$x=-6$"
    assert len(fake_llm.quick_calls) == 1
    assert fake_llm.call_count == 1
    assert store.peek("result-audit-http") is None


def test_grade_controls_style_not_number_domain_or_truth():
    grade6 = prompts.build_quick_instructions(6)
    grade9 = prompts.build_quick_instructions(9)

    assert "6. razred" in grade6 and "9. razred" in grade9
    assert "Brojevi su iz N0" not in grade6
    assert "NEMA negativnih brojeva" not in grade6
    invariant = "RAZRED kontroliše SAMO rječnik, dubinu i složenost objašnjenja"
    assert invariant in grade6 and invariant in grade9
    assert "NIKAD ne mijenja matematičku istinu" in grade6


def test_topic_is_passed_as_optional_context_but_unrelated_math_is_answered():
    fake = FakeLLM()
    answer = "Prirodni brojevi su $1,2,3,\\ldots$."
    fake.queue(make_quick_output(reply=answer))

    response = run_quick_turn(
        fake,
        turn("šta su prirodni brojevi", selected_topic="6-04-001"),
    )

    instructions, input_text = fake.quick_calls[0]
    assert "Izabrana lekcija" in instructions
    assert "Pojam razlomka" in input_text
    assert "šta su prirodni brojevi" in input_text
    assert response["answer"] == answer
    assert fake.call_count == 1


def test_history_is_bounded_filtered_and_keeps_original_order():
    history = []
    for index in range(5):
        history.extend([
            {"role": "user", "content": f"Pitanje {index}"},
            {"role": "assistant", "content": f"Odgovor {index}"},
        ])
    history.insert(0, {"role": "system", "content": "nepouzdan sistem"})
    history.append({"role": "assistant", "content": "   "})

    cleaned = _clean_history(history)

    assert [item["content"] for item in cleaned] == [
        "Pitanje 2", "Odgovor 2", "Pitanje 3",
        "Odgovor 3", "Pitanje 4", "Odgovor 4",
    ]


@pytest.mark.parametrize("message", [
    "Šta pričaš?",
    "sta pricas!!!",
    "ne razumijem",
    "nije mi jasno.",
    "to nisam pitao, samo jednostavno objasni",
    "Kakve to veze ima?",
    "šta to znači?!",
    "pojasni",
    "POJASNI MI.",
])
def test_result_detects_narrow_conversational_repair_phrases(message):
    assert is_conversational_repair_message(message) is True


@pytest.mark.parametrize("message", [
    "riješi jednačinu 3-x=9",
    "Šta su prirodni brojevi?",
    "pojasni jednačinu 3-x=9",
])
def test_result_does_not_treat_normal_math_questions_as_repair(message):
    assert is_conversational_repair_message(message) is False


def test_repair_intent_adds_strong_server_owned_prompt_instruction():
    ordinary = prompts.build_quick_instructions(6)
    repair = prompts.build_quick_instructions(6, repair_intent=True)

    assert "POSEBNA POPRAVKA RAZGOVORA" not in ordinary
    assert "POSEBNA POPRAVKA RAZGOVORA" in repair
    assert "Ne ponavljaj prethodni odgovor bez popravke" in repair
    assert "najviše tri kratke rečenice" in repair


def test_repair_reply_with_acknowledgement_is_not_prefixed_twice():
    fake = FakeLLM()
    reply = "Izvini — prethodni odgovor je bio nejasan. Brojevi su $1,2,3,\\ldots$."
    fake.queue(make_quick_output(reply=reply))

    response = run_quick_turn(fake, turn("Šta pričaš?"))

    assert response["answer"] == reply
    assert response["answer"].count("Izvini") == 1
    assert fake.call_count == 1


def test_repair_reply_without_acknowledgement_gets_prefix_and_preserves_math():
    fake = FakeLLM()
    body = (
        "Prirodni brojevi su $1,2,3,\\ldots$, a s nulom pišemo "
        "$\\mathbb{N}_0=\\{0,1,2,3,\\ldots\\}$."
    )
    fake.queue(make_quick_output(reply=body))

    response = run_quick_turn(fake, turn("to nisam pitao, samo jednostavno objasni"))

    assert response["answer"] == REPAIR_ACKNOWLEDGEMENT + body
    assert response["answer"].endswith(body)
    normalized, safe = normalize_result_math_transport(response["answer"])
    assert safe is True
    assert normalized == response["answer"]
    assert fake.call_count == 1


def test_direct_equation_result_has_no_unrequested_number_set_comment():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$x=-6$"))

    response = run_quick_turn(fake, turn())

    assert response["answer"] == "$x=-6$"
    assert "prirod" not in response["answer"].lower()
    instructions, _ = fake.quick_calls[0]
    assert "Ne dodaj klasifikaciju skupa brojeva" in instructions


def test_ambiguous_expression_can_receive_one_concise_clarification():
    fake = FakeLLM()
    clarification = "Želiš li pojednostaviti izraz ili riješiti jednačinu?"
    fake.queue(make_quick_output(reply=clarification))

    response = run_quick_turn(fake, turn("3-x"))

    assert response["answer"] == clarification
    assert "3-x=0" not in response["answer"]
    assert fake.call_count == 1


def test_followup_history_is_available_for_immediate_correction():
    fake = FakeLLM()
    correction = (
        "Izvini — nepotrebno sam spomenuo razred. Prirodni brojevi su "
        "$1,2,3,\\ldots$, a s nulom koristimo $\\mathbb{N}_0=\\{0,1,2,3,\\ldots\\}$."
    )
    history = [
        {"role": "user", "content": "šta su prirodni brojevi"},
        {"role": "assistant", "content": "U našem razredu koristimo N0."},
    ]
    fake.queue(make_quick_output(reply=correction))

    response = run_quick_turn(
        fake, turn("šta pričaš", conversation_history=history)
    )

    _, input_text = fake.quick_calls[0]
    assert "U našem razredu koristimo N0." in input_text
    assert response["answer"] == correction
    assert "u našem razredu" not in response["answer"].lower()


def test_number_set_convention_is_explicit_not_attributed_to_grade():
    fake = FakeLLM()
    answer = (
        "U ovoj aplikaciji oznakom $\\mathbb{N}_0$ označavamo prirodne brojeve "
        "uključujući nulu."
    )
    fake.queue(make_quick_output(reply=answer))

    response = run_quick_turn(fake, turn("Da li N uključuje nulu?"))

    assert response["answer"] == answer
    assert "razred" not in response["answer"].lower()
    assert "u našem razredu" in prompts.build_quick_instructions(6)


def test_proper_set_mathjax_remains_byte_identical():
    raw = r"$N_0=\{0,1,2,3,\dots\}$"
    normalized, safe = normalize_result_math_transport(raw)
    assert safe is True
    assert normalized == raw


def test_valid_display_math_delimiters_remain_unchanged():
    raw = r"$$N_0=\{0,1,2,3,\dots\}$$"
    normalized, safe = normalize_result_math_transport(raw)
    assert safe is True
    assert normalized == raw


def test_escaped_math_delimiters_are_normalized_only_when_clearly_math():
    raw = r"\$N_0=\{0,1,2,3,\dots\}\$"
    normalized, safe = normalize_result_math_transport(raw)
    assert safe is True
    assert normalized == r"$N_0=\{0,1,2,3,\dots\}$"


def test_overescaped_commands_and_braces_are_reduced_inside_math():
    raw = r"$\mathbb{N}_0=\\{0,1,2,3,\\dots\\}$"
    normalized, safe = normalize_result_math_transport(raw)
    assert safe is True
    assert normalized == r"$\mathbb{N}_0=\{0,1,2,3,\dots\}$"


def test_json_roundtrip_preserves_set_braces_dots_and_fraction():
    fake = FakeLLM()
    raw = r"Skup je \$\mathbb{N}_0=\{0,1,2,3,\dots\}\$, a broj je $\frac{1}{2}$."
    fake.queue(make_quick_output(reply=raw))

    response = run_quick_turn(fake, turn("Napiši skup i razlomak."))
    transported = json.loads(json.dumps(response, ensure_ascii=False))["answer"]

    assert transported == (
        r"Skup je $\mathbb{N}_0=\{0,1,2,3,\dots\}$, a broj je $\frac{1}{2}$."
    )


def test_currency_and_plain_dollar_text_are_not_reinterpreted():
    escaped_currency = r"Cijena je \$5, a ne \$10."
    plain_dollars = "Cijena je $5, a popust je $2."

    assert normalize_result_math_transport(escaped_currency) == (escaped_currency, True)
    assert normalize_result_math_transport(plain_dollars) == (plain_dollars, True)


def test_nested_or_dangling_escaped_math_fails_closed_with_one_call():
    for raw in (r"\$x=$1$\$", r"\$x+1"):
        fake = FakeLLM()
        fake.queue(make_quick_output(reply=raw))
        response = run_quick_turn(fake, turn())
        assert response == {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
        assert fake.call_count == 1


def test_literal_newline_and_fraction_remain_renderable():
    fake = FakeLLM()
    fake.queue(make_quick_output(
        reply=r"Skup je \$N_0=\{0,1,2,3,\dots\}\$.\nBroj je $\frac{1}{2}$."
    ))
    response = run_quick_turn(fake, turn())
    assert "\\n" not in response["answer"]
    assert "\n" in response["answer"]
    assert r"$\frac{1}{2}$" in response["answer"]


def test_result_frontend_clears_topic_and_typesets_after_mathjax_ready():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert "const selectedTopicForPayload = resultMode ? ''" in html
    assert "document.dispatchEvent(new Event('matbot-mathjax-ready'))" in html
    assert "function typesetTutorNode(node)" in html
    append_region = html[html.index("function appendTutorMsg"):html.index("function lastBotMessage")]
    assert "made.bubble.innerHTML = html" in append_region
    assert "typesetTutorNode(made.bubble)" in append_region
    stream_region = html[html.index("const finalAnswer = doneData.answer"):html.index("return { j: doneData")]
    assert stream_region.index("made.bubble.innerHTML") < stream_region.index("typesetTutorNode(made.bubble)")
