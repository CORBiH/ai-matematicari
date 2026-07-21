# -*- coding: utf-8 -*-
"""Two final pre-commit requirements.

1. Narrow HELP-typo tolerance: production typed "ne znmam" during an active task
   after two hints and got OTHER, so the third hint was never delivered.
2. Student-visible mathematics rendered as LaTeX using the delimiters the
   frontend's MathJax 3 config already supports, while every audit/checker
   value stays plain text.
"""
import json
import re

import pytest

from matbot import ai_tutor_service as svc
from matbot import sheets_log
from matbot import topic_resolver as tr
from matbot.minimal import mathfmt, solution_facts
from matbot.minimal.intent import TurnIntent, classify, is_help_typo

STREAM_URL = "/api/ai-tutor/chat/stream"
ADD_TOPIC = "6-04-040"
TASK = "Izračunaj: 1/3 + 4/5."


def payload(**overrides):
    base = {
        "session_id": "lx", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": ADD_TOPIC, "selected_oblast": "Razlomci",
        "student_message": "daj mi zadatak", "conversation_history": [],
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "a.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "on")
    for f in ("MATBOT_ENGINE_V2", "MATBOT_ENGINE_V2_GRADING",
              "MATBOT_ENGINE_V2_PRACTICE", "MATBOT_ENGINE_V2_EXAM"):
        monkeypatch.setenv(f, "off")
    tr.reset_cache()
    yield
    tr.reset_cache()


@pytest.fixture()
def sheets(monkeypatch):
    rows = []
    monkeypatch.setattr(svc, "log_transcript_to_sheet",
                        lambda p, r: rows.append((p, r)))
    return rows


def raw_sse(client, body):
    resp = client.post(STREAM_URL, json=body)
    assert resp.status_code == 200, resp.data
    return resp.get_data(as_text=True)


def parse_sse(text):
    """Return (assembled_deltas, done_payload) exactly as the browser builds it."""
    deltas, done, name = [], None, None
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1].strip())
            if name == "delta":
                deltas.append(data.get("delta", ""))
            elif name == "done":
                done = data
    return "".join(deltas), done


def sse(client, body):
    return parse_sse(raw_sse(client, body))[1]


def seeded(client, question=TASK, expected="17/15"):
    first = sse(client, payload())
    state = first["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_l", "skill_id": "fraction_add_unlike",
        "question": question, "expected_display": expected,
        "npp_id": ADD_TOPIC, "tema_title": "t", "attempts": 0,
        "wrong_attempts": 0, "hints_given": 0, "solved": False,
        "solution_revealed": False,
    }
    state["task_id"] = "mt_l"
    return state, question


def turn(client, state, question, message):
    return sse(client, payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state))


# =========================================================================== #
# 1. HELP typo tolerance                                                      #
# =========================================================================== #
@pytest.mark.parametrize("message", [
    "ne znmam", "ne znma", "nezanm", "neznam", "ne razmijem", "pomozii",
])
def test_help_typos_are_recognised_with_an_active_task(message):
    assert classify(message, has_active_task=True).intent is TurnIntent.HELP


@pytest.mark.parametrize("message", ["ne znmam", "nezanm", "ne razmijem"])
def test_help_typos_do_nothing_without_an_active_task(message):
    """Conservative by design: the tolerance is scoped to an open task."""
    assert classify(message).intent is not TurnIntent.HELP


@pytest.mark.parametrize("message", [
    "9/10", "9/1O", "11/15", "x = 4", "2/5 + 1/3", "17/15 mislim",
])
def test_numeric_answers_never_become_help(message):
    assert classify(message, has_active_task=True).intent is TurnIntent.ANSWER
    assert is_help_typo(message) is False


@pytest.mark.parametrize("message", [
    "asdf", "volim pse", "sta ima", "hmmm pa dobro", "danas je lijep dan",
])
def test_random_text_remains_other(message):
    assert classify(message, has_active_task=True).intent is TurnIntent.OTHER


def test_long_messages_are_not_fuzzy_matched():
    assert is_help_typo("ne znam kako se ovo radi jer nisam bio na casu") is False


def test_typo_delivers_the_third_hint_after_two(client):
    """The exact production sequence."""
    state, question = seeded(client)
    first = turn(client, state, question, "ne znam")
    second = turn(client, first["next_state"], question, "ne znam")
    third = turn(client, second["next_state"], question, "ne znmam")

    assert third["next_state"]["hint_count"] == 3
    assert "5 + 12" in third["answer"], third["answer"]      # hint level 3
    assert third["next_state"]["task_id"] == "mt_l"          # same task
    assert third["last_tutor_task"] == question


def test_typo_turn_changes_no_counters(client):
    state, question = seeded(client)
    body = turn(client, state, question, "ne znmam")
    active = body["next_state"]["minimal_state"]["active_task"]
    assert active["attempts"] == 0
    assert active["wrong_attempts"] == 0
    assert body["next_state"]["correct_streak"] == 0
    assert body["answer_verdict"] is None


def test_typo_needs_no_model_call(client, fake_openai):
    state, question = seeded(client)
    before = len(fake_openai.calls.messages)
    turn(client, state, question, "ne znmam")
    assert len(fake_openai.calls.messages) == before


# =========================================================================== #
# 2. LaTeX rendering                                                          #
# =========================================================================== #
def test_frontend_delimiters_are_the_ones_we_emit():
    """We reuse the page's MathJax config; no second renderer is introduced."""
    html = open("templates/index.html", encoding="utf-8").read()
    assert 'inlineMath: [["$", "$"], ["\\\\(", "\\\\)"]]' in html
    assert "MathJax" in html
    assert mathfmt.INLINE_OPEN == r"\(" and mathfmt.INLINE_CLOSE == r"\)"
    assert mathfmt.BLOCK_OPEN == r"\[" and mathfmt.BLOCK_CLOSE == r"\]"


@pytest.mark.parametrize("plain,latex", [
    ("5/6", r"\frac{5}{6}"),
    ("1 2/15", r"1\frac{2}{15}"),
    ("1/3 + 4/5", r"\frac{1}{3}+\frac{4}{5}"),
    ("2x + 3 = 11", "2x+3=11"),
    ("60 = 2 × 2 × 3 × 5", r"60=2\cdot2\cdot3\cdot5"),
    ("2²", "2^{2}"),
])
def test_expression_conversion(plain, latex):
    assert mathfmt.to_latex(plain) == latex


def test_task_renders_both_fractions(client):
    body = sse(client, payload(student_message="daj mi zadatak"))
    answer = body["answer"]
    assert r"\frac" in answer, answer
    assert r"\(" in answer and r"\)" in answer
    # the stored task stays plain text
    assert "/" in body["last_tutor_task"]
    assert r"\frac" not in body["last_tutor_task"]


def test_named_task_renders_as_expected(client):
    state, question = seeded(client, "Izračunaj: 5/6 + 4/9.", "23/18")
    body = turn(client, state, question, "ne znam")
    rendered = mathfmt.format_question(question)
    assert r"\frac{5}{6}" in rendered and r"\frac{4}{9}" in rendered
    assert rendered.startswith("Izračunaj: \\(")
    assert "5/6" not in rendered and "4/9" not in rendered
    assert question in body["next_state"]["minimal_state"]["active_task"]["question"]


def test_hint_two_renders_all_four_fractions(client):
    state, question = seeded(client)
    state = turn(client, state, question, "ne znam")["next_state"]
    body = turn(client, state, question, "ne znam")
    for part in (r"\frac{1}{3}", r"\frac{5}{15}", r"\frac{4}{5}", r"\frac{12}{15}"):
        assert part in body["answer"], (part, body["answer"])
    assert r"\frac{1}{3}=\frac{5}{15}" in body["answer"]


def test_full_solution_is_valid_block_latex(client):
    state, question = seeded(client)
    body = turn(client, state, question, "uradi i objasni postupak")
    answer = body["answer"]
    assert r"\[" in answer and r"\]" in answer
    assert r"\begin{aligned}" in answer and r"\end{aligned}" in answer
    for part in (r"\frac{1}{3}+\frac{4}{5}", r"\frac{5}{15}+\frac{12}{15}",
                 r"\frac{17}{15}", r"1\frac{2}{15}"):
        assert part in answer, part
    # the block sits on ONE line: the page joins lines with <br>, which would
    # otherwise land inside the display math
    block_line = [ln for ln in answer.splitlines() if r"\[" in ln]
    assert len(block_line) == 1
    assert r"\]" in block_line[0]


def test_mixed_number_renders_as_a_mixed_fraction():
    assert mathfmt.to_latex("1 2/15") == r"1\frac{2}{15}"
    facts = solution_facts.resolve_add_facts(TASK)
    assert r"1\frac{2}{15}" in mathfmt.block(solution_facts.solution_steps(facts))


@pytest.mark.parametrize("text", [
    r"\( \frac{1}{2} \) je pola",
    r"\[\begin{aligned} a &= b \end{aligned}\]",
    r"vec \frac{3}{4} formatirano",
])
def test_existing_latex_is_not_double_escaped(text):
    assert mathfmt.format_math_tokens(text) == text
    assert mathfmt.to_latex(text) == text
    assert r"\\frac" not in mathfmt.format_math_tokens(text)


@pytest.mark.parametrize("prose", [
    "datum 12/05 je rok",
    "vidi http://primjer.ba/a/b",
    "ne znam nista o tome",
    "www.test.com/x/y",
])
def test_plain_prose_with_slashes_is_untouched(prose):
    assert mathfmt.format_math_tokens(prose) == prose


def test_delimiters_are_always_balanced(client):
    """Every rendered reply must contain complete math expressions."""
    state, question = seeded(client)
    answers = [sse(client, payload())["answer"]]
    for message in ("ne znam", "ne znam", "ne znam", "999/999",
                    "uradi i objasni postupak"):
        body = turn(client, state, question, message)
        state = body["next_state"] if body["last_tutor_task"] else state
        answers.append(body["answer"])
    for answer in answers:
        assert answer.count(r"\(") == answer.count(r"\)"), answer
        assert answer.count(r"\[") == answer.count(r"\]"), answer
        assert answer.count(r"\begin{aligned}") == answer.count(r"\end{aligned}")
        assert not re.search(r"\\frac\{[^}]*$", answer), answer


def test_sse_stream_preserves_the_latex(client):
    """The assembled browser text must equal the final answer, delimiters intact."""
    text = raw_sse(client, payload(student_message="daj mi zadatak"))
    assembled, done = parse_sse(text)
    assert assembled.strip() == (done["answer"] or "").strip()
    assert r"\frac" in assembled
    assert assembled.count(r"\(") == assembled.count(r"\)")


def test_sse_stream_preserves_a_block_solution(client):
    state, question = seeded(client)
    body = payload(student_message="uradi i objasni postupak",
                   interaction_phase="answering_practice_task",
                   last_tutor_task=question, previous_next_state=state)
    assembled, done = parse_sse(raw_sse(client, body))
    assert assembled.strip() == (done["answer"] or "").strip()
    assert r"\begin{aligned}" in assembled and r"\end{aligned}" in assembled


# =========================================================================== #
# Audit / checker values stay plain text                                      #
# =========================================================================== #
def test_audit_values_contain_no_latex(client, sheets):
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    turn(client, state, question, "11/15")
    payload_row, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload_row, response)
    headers = sheets_log.SHEET_HEADERS
    for name in ("student_message", "student_answer", "expected_answer",
                 "normalized_student", "normalized_expected",
                 "deterministic_check", "last_tutor_task"):
        cell = str(row[headers.index(name)])
        assert "\\frac" not in cell, (name, cell)
        assert "\\(" not in cell and "\\[" not in cell, (name, cell)
    assert row[headers.index("student_answer")] == "11/15"
    assert row[headers.index("expected_answer")] == "11/15"


def test_internal_task_state_stays_plain(client):
    body = sse(client, payload(student_message="daj mi zadatak"))
    task = body["next_state"]["minimal_state"]["active_task"]
    assert r"\frac" not in task["question"]
    assert r"\frac" not in task["expected_display"]
    assert r"\frac" not in body["last_tutor_task"]
    assert r"\frac" not in body["next_state"]["task"]["question"]


def test_checker_still_grades_the_plain_task(client):
    """Rendering must not change what the checker sees."""
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    body = turn(client, state, question, "11/15")
    assert body["answer_verdict"] == "correct"
    assert body["gpt_check_used"] is False


def test_sheets_columns_unchanged():
    headers = sheets_log.SHEET_HEADERS
    assert len(headers) == 62
    assert headers.index("student_message") == 16
    assert headers.index("student_answer") == 25
