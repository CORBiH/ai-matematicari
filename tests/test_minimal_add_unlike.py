# -*- coding: utf-8 -*-
"""fraction_add_unlike production session (topic 6-04-040).

1. Five "ne znam" turns for ``1/3 + 4/5`` produced the SAME generic hint, and
   "NE ZNAM URADI I OBJASNI POSTUPAK" was ignored.
   Cause: ``renderer._hint_text`` clamped the level to the last entry of a
   two-item pool, so level >= 2 always returned the same sentence.
2. "zajednicki nazivnik je 15 pa je rezultat 8/15" was reported unverified —
   the whole sentence went to the checker instead of the final answer.
3. "Daj mi lakši zadatak." produced a harder one; the skill had no bands.

Driven through the SSE route the browser uses.
"""
import json
import math
from fractions import Fraction

import pytest

from matbot import ai_tutor_service as svc
from matbot import sheets_log
from matbot import topic_resolver as tr
from matbot.minimal import skills, solution_facts
from matbot.minimal.grading import extract_answer_candidate
from matbot.minimal.intent import TurnIntent, classify

STREAM_URL = "/api/ai-tutor/chat/stream"
ADD_TOPIC = "6-04-040"
TASK = "Izračunaj: 1/3 + 4/5."
EXPECTED = "17/15"


def prod_payload(**overrides):
    payload = {
        "session_id": "add-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": ADD_TOPIC, "selected_oblast": "Razlomci",
        "student_message": "daj mi zadatak", "conversation_history": [],
    }
    payload.update(overrides)
    return payload


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


def sse(client, payload):
    resp = client.post(STREAM_URL, json=payload)
    assert resp.status_code == 200, resp.data
    name = None
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name == "done":
            return json.loads(line.split(":", 1)[1].strip())
    raise AssertionError("no done event")


def seeded(client, question=TASK, expected=EXPECTED):
    """Start a session and pin a specific active task via round-tripped state."""
    first = sse(client, prod_payload())
    state = first["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_add", "skill_id": "fraction_add_unlike",
        "question": question, "expected_display": expected,
        "npp_id": ADD_TOPIC, "tema_title": "Sabiranje razlomaka",
        "attempts": 0, "wrong_attempts": 0, "hints_given": 0,
        "solved": False, "solution_revealed": False,
    }
    state["task_id"] = "mt_add"
    return state, question


def turn(client, state, question, message):
    return sse(client, prod_payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state))


# =========================================================================== #
# 1. A real hint ladder                                                       #
# =========================================================================== #
def test_hint_ladder_progresses_over_the_wire(client):
    state, question = seeded(client)
    hints = []
    for _ in range(3):
        body = turn(client, state, question, "ne znam")
        state = body["next_state"]
        hints.append(body["answer"])

    assert "15" in hints[0]                       # common denominator
    assert "5/15" in hints[1] and "12/15" in hints[1]
    assert "5 + 12" in hints[2]
    assert len(set(hints)) == 3, hints             # genuinely different


def test_hint_ladder_keeps_the_task_and_counters(client):
    state, question = seeded(client)
    for index in range(3):
        body = turn(client, state, question, "ne znam")
        state = body["next_state"]
        assert body["next_state"]["task_id"] == "mt_add"
        assert body["last_tutor_task"] == question
        assert body["answer_verdict"] is None
        assert state["hint_count"] == index + 1
        assert state["correct_streak"] == 0
        assert state["minimal_state"]["active_task"]["attempts"] == 0


def test_hints_past_the_ladder_do_not_pretend_to_be_new(client):
    state, question = seeded(client)
    for _ in range(3):
        state = turn(client, state, question, "ne znam")["next_state"]
    fourth = turn(client, state, question, "ne znam")
    fifth = turn(client, fourth["next_state"], question, "ne znam")
    # the HINT BODY repeats (only the seeded opener varies)
    assert "isti korak" in fourth["answer"].lower()
    assert "isti korak" in fifth["answer"].lower()
    body = lambda t: t.split("Isti korak", 1)[1]
    assert body(fourth["answer"]) == body(fifth["answer"])
    assert "postupak" in fourth["answer"].lower()  # offers the full solution


def test_hints_never_reveal_the_final_answer(client):
    state, question = seeded(client)
    for _ in range(4):
        body = turn(client, state, question, "ne znam")
        state = body["next_state"]
        assert "17/15" not in body["answer"], body["answer"]


def test_hint_facts_are_computed_from_the_task():
    facts = solution_facts.resolve_add_facts(TASK)
    assert facts.common == 15
    assert facts.over_common_a == "5/15"
    assert facts.over_common_b == "12/15"
    assert facts.result_numerator == 17
    assert facts.simplified == "17/15"
    assert facts.mixed == "1 2/15"


# =========================================================================== #
# 2. Explicit solution request                                                #
# =========================================================================== #
@pytest.mark.parametrize("message", [
    "ne znam uradi ti",
    "NE ZNAM URADI I OBJASNI POSTUPAK",
    "daj rješenje",
    "riješi ti",
    "objasni cijeli postupak",
    "pokaži kompletno rješenje",
])
def test_solution_request_intents(message):
    assert classify(message).intent is TurnIntent.SOLUTION_REQUEST, message


@pytest.mark.parametrize("message", ["ne znam", "Ne znam.", "pomozi"])
def test_plain_help_is_still_help(message):
    assert classify(message).intent is TurnIntent.HELP, message


def test_solution_request_shows_the_full_procedure(client):
    state, question = seeded(client)
    body = turn(client, state, question, "NE ZNAM URADI I OBJASNI POSTUPAK")
    answer = body["answer"]
    for line in ("1/3 + 4/5", "5/15 + 12/15", "17/15", "1 2/15"):
        assert line in answer, (line, answer)


def test_solution_request_does_not_count_as_solved(client):
    state, question = seeded(client)
    solved_before = state["minimal_state"]["solved_count"]
    body = turn(client, state, question, "uradi ti")
    ms = body["next_state"]["minimal_state"]
    assert body["next_state"]["correct_streak"] == 0
    assert ms["solved_count"] == solved_before
    assert body["answer_verdict"] is None          # nothing was graded


def test_solution_request_marks_the_task_revealed(client):
    state, question = seeded(client)
    body = turn(client, state, question, "uradi ti")
    assert body["next_state"]["task_status"] == "revealed"
    assert body["next_state"]["solution_revealed"] is True
    assert body["last_tutor_task"] == ""           # task closed
    assert body["next_state"]["active_task_kind"] is None


def test_solution_request_preserves_the_task_audit(client, sheets):
    state, question = seeded(client)
    state = turn(client, state, question, "ne znam")["next_state"]
    state = turn(client, state, question, "999/999")["next_state"]
    body = turn(client, state, question, "uradi i objasni postupak")
    ns = body["next_state"]
    assert ns["task_id"] == "mt_add"
    assert ns["hint_count"] == 1
    assert ns["wrong_attempt_count"] == 1
    assert ns["total_attempt_count"] == 1


def test_solution_request_invites_an_independent_task(client):
    state, question = seeded(client)
    body = turn(client, state, question, "daj rješenje")
    assert "daj mi zadatak" in body["answer"].lower()
    assert body["next_state"]["minimal_state"]["pending_confirmation"] == "new_task"


# =========================================================================== #
# 3. Final answer extracted from prose                                        #
# =========================================================================== #
@pytest.mark.parametrize("message,expected_candidate", [
    ("rezultat je 11/15", "11/15"),
    ("odgovor je 11/15", "11/15"),
    ("dobijem 11/15", "11/15"),
    ("zajednicki nazivnik je 15 pa je rezultat 11/15", "11/15"),
    ("1/3 = 5/15 i 2/5 = 6/15, zato je 11/15", "11/15"),
    ("1/3 = 5/15\n2/5 = 6/15\n11/15", "11/15"),
])
def test_candidate_extraction(message, expected_candidate):
    assert extract_answer_candidate(message) == expected_candidate, message


def test_result_marker_beats_an_intermediate_fraction():
    assert extract_answer_candidate(
        "prvo 5/15 pa 6/15 ali rezultat je 11/15") == "11/15"


def test_no_candidate_when_there_are_no_numbers():
    assert extract_answer_candidate("ne znam nista") == ""
    assert extract_answer_candidate("") == ""


def test_prose_with_a_wrong_answer_is_incorrect_not_unverified(client):
    """The exact production message."""
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    body = turn(client, state, question,
                "zajednicki nazivnik je 15 pa je rezultat 8/15")
    assert body["answer_verdict"] == "incorrect"
    assert body["answer_verdict_detail"] != "not_checkable"


def test_prose_with_the_right_answer_is_correct(client):
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    body = turn(client, state, question,
                "zajednicki nazivnik je 15 pa je rezultat 11/15")
    assert body["answer_verdict"] == "correct"


def test_full_procedure_selects_the_final_candidate(client):
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    body = turn(client, state, question,
                "1/3 = 5/15 i 2/5 = 6/15, zato je 11/15")
    assert body["answer_verdict"] == "correct"


def test_mixed_number_equivalent_is_accepted(client):
    state, question = seeded(client)          # 1/3 + 4/5 = 17/15
    body = turn(client, state, question, "1 2/15")
    assert body["answer_verdict"] == "correct"


def test_unparseable_answer_is_still_not_checkable(client):
    state, question = seeded(client)
    body = turn(client, state, question, "hmmm pa evo ovako nekako")
    assert body["answer_verdict"] in (None, "unverified")
    assert body["last_tutor_task"] == question      # task preserved


def test_audit_shows_candidate_and_keeps_raw_message(client, sheets):
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    raw = "zajednicki nazivnik je 15 pa je rezultat 8/15"
    turn(client, state, question, raw)
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    headers = sheets_log.SHEET_HEADERS
    assert row[headers.index("student_message")] == raw          # verbatim
    assert row[headers.index("student_answer")] == "8/15"        # candidate
    assert row[headers.index("normalized_student")]
    evidence = json.loads(row[headers.index("deterministic_check")])
    assert evidence["student_raw"] == raw                        # original kept
    assert evidence["extracted_candidate"] == "8/15"
    assert evidence["graded_text"] == "8/15"


# =========================================================================== #
# 4. Difficulty bands                                                         #
# =========================================================================== #
def test_add_unlike_supports_difficulty():
    assert skills.supports_difficulty("fraction_add_unlike") is True


@pytest.mark.parametrize("level", [1, 2, 3])
def test_generated_params_belong_to_their_band(level):
    pairs, _allow = skills.add_band_for(level)
    for seed in range(30):
        question, _ = skills.generate_question("fraction_add_unlike", seed=seed,
                                               difficulty=level)
        a, den_a, b, den_b = skills.add_params(question)
        assert (den_a, den_b) in pairs or (den_b, den_a) in pairs, question
        assert 1 <= a < den_a and 1 <= b < den_b, question


def test_higher_band_is_objectively_harder():
    def stats(level):
        lcms, over_one = [], 0
        for seed in range(60):
            question, _ = skills.generate_question(
                "fraction_add_unlike", seed=seed, difficulty=level)
            a, den_a, b, den_b = skills.add_params(question)
            lcms.append(math.lcm(den_a, den_b))
            if Fraction(a, den_a) + Fraction(b, den_b) > 1:
                over_one += 1
        return sum(lcms) / len(lcms), over_one

    lcm1, over1 = stats(1)
    lcm2, over2 = stats(2)
    lcm3, over3 = stats(3)
    assert lcm1 < lcm2 < lcm3
    assert over1 == 0                      # level 1 never exceeds 1
    assert over3 > over2 >= over1


def test_harder_and_easier_move_the_level_over_the_wire(client):
    first = sse(client, prod_payload())
    assert first["next_state"]["difficulty_level"] == 1
    harder = sse(client, prod_payload(student_message="Daj mi teži zadatak.",
                                      previous_next_state=first["next_state"]))
    assert harder["next_state"]["difficulty_level"] == 2
    a, den_a, b, den_b = skills.add_params(harder["last_tutor_task"])
    pairs, _ = skills.add_band_for(2)
    assert (den_a, den_b) in pairs or (den_b, den_a) in pairs

    easier = sse(client, prod_payload(student_message="Daj mi lakši zadatak.",
                                      previous_next_state=harder["next_state"]))
    assert easier["next_state"]["difficulty_level"] == 1


def test_easier_never_produces_a_harder_task(client):
    """The production complaint: 1/3 + 2/5 then "lakši" gave 1/3 + 4/5."""
    first = sse(client, prod_payload())
    harder = sse(client, prod_payload(student_message="Daj mi teži zadatak.",
                                      previous_next_state=first["next_state"]))
    easier = sse(client, prod_payload(student_message="Daj mi lakši zadatak.",
                                      previous_next_state=harder["next_state"]))
    a1, d1, b1, e1 = skills.add_params(harder["last_tutor_task"])
    a2, d2, b2, e2 = skills.add_params(easier["last_tutor_task"])
    assert math.lcm(d2, e2) <= math.lcm(d1, e1)
    assert Fraction(a2, d2) + Fraction(b2, e2) <= 1     # level 1 stays <= 1


def test_skill_without_bands_says_so_honestly(client):
    """divisibility has no bands: a new task, described as the same difficulty."""
    body = sse(client, prod_payload(
        selected_topic="", selected_oblast="Djeljivost brojeva",
        student_message="daj mi zadatak"))
    assert body["last_tutor_task"]
    harder = sse(client, prod_payload(
        selected_topic="", selected_oblast="Djeljivost brojeva",
        student_message="daj mi teži zadatak",
        previous_next_state=body["next_state"]))
    assert "ne mogu birati težinu" in harder["answer"].lower(), harder["answer"]
    assert harder["next_state"]["difficulty_level"] == 1       # unchanged


# =========================================================================== #
# Ownership invariants                                                        #
# =========================================================================== #
def test_grading_still_has_one_owner(client):
    state, question = seeded(client)
    body = turn(client, state, question, "17/15")
    assert body["answer_verdict"] == "correct"
    assert body["gpt_check_used"] is False


def test_solution_and_hints_never_call_the_model(client, fake_openai):
    state, question = seeded(client)
    before = len(fake_openai.calls.messages)
    turn(client, state, question, "ne znam")
    turn(client, state, question, "uradi ti")
    assert len(fake_openai.calls.messages) == before


def test_concept_questions_still_work(client, fake_openai):
    fake_openai.state["reply"] = "Parafraza."
    body = sse(client, prod_payload(
        student_message="zasto mnozimo i brojnik i nazivnik"))
    assert body["minimal_routing"]["concept_fact_kind"] == "why_same_factor"
    assert body["last_tutor_task"] == ""


def test_sheets_columns_unchanged():
    headers = sheets_log.SHEET_HEADERS
    assert len(headers) == 62
    assert headers.index("student_message") == 16
    assert headers.index("student_answer") == 25
    assert headers[-2:] == ["internal_instruction", "minimal_routing"]


# =========================================================================== #
# Final safety pass: safe extraction + solution-request counters              #
# =========================================================================== #
from matbot.minimal.grading import (  # noqa: E402
    AMBIGUOUS, AMBIGUOUS_FINAL_ANSWER, FOUND, NONE, extract_final_answer,
)


@pytest.mark.parametrize("message,expected", [
    # the declared answer, even though other fractions FOLLOW it
    ("Mislim da je odgovor 11/15 jer je 1/3 = 5/15, a 2/5 = 6/15.", "11/15"),
    ("1/3 = 5/15 i 2/5 = 6/15, zato je 11/15", "11/15"),
    ("rezultat je 11/15", "11/15"),
    ("konačno 11/15", "11/15"),
    ("zajednicki nazivnik je 15 pa je rezultat 8/15", "8/15"),
    ("pa valjda 11/15", "11/15"),               # single candidate in prose
    ("1 2/15", "1 2/15"),                       # mixed number
    ("1/3 = 5/15\n2/5 = 6/15\n= 11/15", "11/15"),   # final equality line
])
def test_final_answer_extraction(message, expected):
    value, status = extract_final_answer(message)
    assert value == expected, message
    assert status == FOUND


@pytest.mark.parametrize("message", [
    "1/3 = 5/15 i 2/5 = 6/15",
    "5/15 6/15",
    "prvo 5/15 pa onda 6/15",
])
def test_multiple_candidates_without_a_declared_answer_are_ambiguous(message):
    value, status = extract_final_answer(message)
    assert value == ""
    assert status == AMBIGUOUS


def test_no_candidate_is_none():
    assert extract_final_answer("ne znam nista") == ("", NONE)
    assert extract_final_answer("") == ("", NONE)


def test_never_blindly_takes_the_last_fraction():
    """The regression this pass exists for."""
    message = "Mislim da je odgovor 11/15 jer je 1/3 = 5/15, a 2/5 = 6/15."
    assert extract_final_answer(message)[0] != "6/15"


def test_declared_answer_after_reasoning_is_graded_correct(client):
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    body = turn(client, state, question,
                "Mislim da je odgovor 11/15 jer je 1/3 = 5/15, a 2/5 = 6/15.")
    assert body["answer_verdict"] == "correct", body["answer"]


def test_ambiguous_message_asks_which_answer_is_final(client):
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    body = turn(client, state, question, "1/3 = 5/15 i 2/5 = 6/15")
    assert body["answer_verdict"] == "unverified"
    assert body["answer_verdict_detail"] == AMBIGUOUS_FINAL_ANSWER
    assert "konačan odgovor" in body["answer"].lower(), body["answer"]
    assert body["last_tutor_task"] == question       # task stays active
    assert body["next_state"]["task_id"] == "mt_add"


def test_ambiguous_message_does_not_change_progress(client):
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    body = turn(client, state, question, "1/3 = 5/15 i 2/5 = 6/15")
    assert body["next_state"]["correct_streak"] == 0
    assert body["next_state"]["minimal_state"]["solved_count"] == 0


def test_extractor_is_reusable_across_rational_skills(client):
    """Same policy on fraction_expand, not hardcoded for addition."""
    first = sse(client, prod_payload(selected_topic="6-04-035",
                                     selected_oblast="Razlomci"))
    question = first["last_tutor_task"]
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    answer = getattr(exp, "expected_display", "") or _fmt_expected(exp)
    body = sse(client, prod_payload(
        selected_topic="6-04-035", selected_oblast="Razlomci",
        student_message=f"mislim da je rezultat {answer}",
        interaction_phase="answering_practice_task", last_tutor_task=question,
        previous_next_state=first["next_state"]))
    assert body["answer_verdict"] == "correct", body["answer"]


# --- solution-request counters --------------------------------------------
def test_immediate_solution_request_records_no_attempts(client):
    """task -> "uradi i objasni postupak" with no answer submitted first."""
    state, question = seeded(client)
    body = turn(client, state, question, "uradi i objasni postupak")
    ns = body["next_state"]
    assert ns["solution_revealed"] is True
    assert ns["task_status"] == "revealed"
    assert ns["total_attempt_count"] == 0
    assert ns["wrong_attempt_count"] == 0
    assert ns["attempt_number"] == 0
    assert ns["task_id"] == "mt_add"                 # audit preserved
    assert body["answer_verdict"] is None            # not a wrong attempt


def test_immediate_solution_request_leaves_streak_untouched(client):
    state, question = seeded(client)
    state["minimal_state"]["correct_streak"] = 3
    state["minimal_state"]["solved_count"] = 3
    body = turn(client, state, question, "uradi i objasni postupak")
    ms = body["next_state"]["minimal_state"]
    assert ms["correct_streak"] == 3                 # unchanged, not reset
    assert ms["solved_count"] == 3                   # not credited either


def test_wrong_attempt_before_a_solution_request_is_still_recorded(client):
    state, question = seeded(client)
    wrong = turn(client, state, question, "999/999")
    assert wrong["answer_verdict"] == "incorrect"
    body = turn(client, wrong["next_state"], question, "uradi ti")
    ns = body["next_state"]
    assert ns["total_attempt_count"] == 1
    assert ns["wrong_attempt_count"] == 1
    assert ns["solution_revealed"] is True
    assert ns["task_status"] == "revealed"


# =========================================================================== #
# Audit fields for ambiguous vs identified answers                            #
# =========================================================================== #
def _audit_row(sheets_rows):
    payload, response = sheets_rows[-1]
    row = sheets_log._build_transcript_row(payload, response)
    headers = sheets_log.SHEET_HEADERS
    return {name: row[headers.index(name)] for name in (
        "student_message", "student_answer", "normalized_student",
        "deterministic_check", "answer_verdict", "answer_verdict_detail")}


def test_audit_for_ambiguous_answer_records_no_student_answer(client, sheets):
    """Several fractions, no declared final answer."""
    prose = "1/3 = 5/15 i 2/5 = 6/15"
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    body = turn(client, state, question, prose)
    cells = _audit_row(sheets)

    assert cells["student_message"] == prose          # exact full prose
    assert cells["student_answer"] == ""              # nothing was identified
    assert cells["normalized_student"] == ""
    assert cells["answer_verdict_detail"] == AMBIGUOUS_FINAL_ANSWER
    # no mathematical verdict was emitted
    assert cells["answer_verdict"] not in ("correct", "incorrect", "partial")
    # the prose is still preserved in the evidence
    evidence = json.loads(cells["deterministic_check"])
    assert evidence["student_raw"] == prose
    assert evidence["extracted_candidate"] == ""
    assert evidence["graded_text"] == ""
    assert evidence["gpt_check_used"] is False

    # task and progress untouched
    assert body["last_tutor_task"] == question
    assert body["next_state"]["task_id"] == "mt_add"
    assert body["next_state"]["correct_streak"] == 0
    assert body["next_state"]["minimal_state"]["solved_count"] == 0
    assert body["next_state"]["minimal_state"]["active_task"]["attempts"] == 0


def test_audit_for_an_identified_answer_records_only_the_candidate(client, sheets):
    """An explicit final answer inside prose."""
    prose = "Mislim da je odgovor 11/15 jer je 1/3 = 5/15, a 2/5 = 6/15."
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    body = turn(client, state, question, prose)
    cells = _audit_row(sheets)

    assert cells["student_message"] == prose          # exact full prose
    assert cells["student_answer"] == "11/15"         # ONLY the candidate
    assert cells["normalized_student"] == "11/15"
    assert body["answer_verdict"] == "correct"
    evidence = json.loads(cells["deterministic_check"])
    assert evidence["student_raw"] == prose           # prose kept too
    assert evidence["extracted_candidate"] == "11/15"


def test_directly_checkable_answer_is_unchanged_in_the_audit(client, sheets):
    state, question = seeded(client, "Izračunaj: 1/3 + 2/5.", "11/15")
    turn(client, state, question, "11/15")
    cells = _audit_row(sheets)
    assert cells["student_message"] == "11/15"
    assert cells["student_answer"] == "11/15"
    assert cells["normalized_student"] == "11/15"


def test_student_raw_stays_the_verbatim_message():
    """GradingResult.student_raw is always what was typed, never the candidate."""
    from matbot.minimal.grading import grade
    from matbot.minimal.state import ActiveTask
    task = ActiveTask(task_id="t", skill_id="fraction_add_unlike",
                      question="Izračunaj: 1/3 + 2/5.", expected_display="11/15")
    prose = "Mislim da je odgovor 11/15 jer je 1/3 = 5/15, a 2/5 = 6/15."
    result = grade(task, prose)
    assert result.student_raw == prose
    assert result.graded_answer == "11/15"
