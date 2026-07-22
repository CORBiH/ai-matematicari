# -*- coding: utf-8 -*-
"""Three focused production defects in divisibility (topic 6-03-024).

1. GENERATOR ONLY EVER PRODUCED DIVISOR 6. 19 consecutive "Daj mi novi
   zadatak." turns all asked about "÷ 6". Cause: the ``divisibility`` skill
   used ``task_templates._g_divisibility6`` — a FIXED generator whose divisor
   is hardcoded, never varied. ``skills.py`` had no entry for "divisibility"
   in ``_DIFFICULTY_AWARE``, so ``generate_question`` always fell back to
   that one legacy template. Fixed with a real generator (all 9 promised
   divisors, difficulty-banded, near-miss cases for the compound ones).

2. A CORRECT COMPOUND-RULE EXPLANATION WAS REJECTED. "da jer je djeljiv i sa
   2 i sa 3" for a divisor-6 task was graded partial/incomplete and counted
   as a WRONG attempt. Cause: ``answer_checker.divisibility_coverage``
   required the LITERAL digit "6" to appear in the student's text before it
   would even consider the explanation "addressed" — a student who correctly
   reasons about the FACTORS 2 and 3 never writes the digit "6" at all, so
   the rule was never recognised. Fixed by decomposing compound divisors (6,
   15) into their factors for coverage purposes (``_compound_coverage``,
   ``_COMPOUND_DIVISORS``). Policy chosen: Policy B, "correct-rule-missing-
   evidence" — the decision+rule are accepted as PARTIAL, not wrong, and the
   feedback asks the student to tie the rule to the ACTUAL number, instead of
   repeating the rule they already stated.

3. AN ARBITRARY NUMBER IN PROSE WAS GRADED AS A YES/NO DECISION. "sto me
   pitas samo za 6" produced student_answer="6" and verdict=incorrect. Cause:
   ``_check_divisibility_explanation`` returned ``None`` (not
   ``checkable=False``) whenever no da/ne decision was found, so ``_check``'s
   dispatcher fell through to the GENERIC single-item numeric branch, which
   extracts ANY bare number from the prose and compares it to the expected
   boolean's numeric value (1/0). Fixed: that branch now returns
   ``CheckResult(checkable=False)`` explicitly, stopping the fall-through.

Driven through the real SSE route the browser uses.
"""
import json
from collections import Counter

import pytest

from matbot import ai_tutor_service as svc
from matbot import topic_resolver as tr
from matbot.answer_checker import check_practice_answer, derive_expected
from matbot.minimal import divisibility_facts as df
from matbot.minimal import skills
from matbot.minimal.intent import TurnIntent, classify, is_pushback_insistence

STREAM_URL = "/api/ai-tutor/chat/stream"
DIV_TOPIC = "6-03-024"
DIV_OBLAST = "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25"
TASK6 = "Provjeri da li je broj 252 djeljiv sa 6. Obrazloži svoj odgovor."


def prod_payload(**overrides):
    payload = {
        "session_id": "div-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": DIV_TOPIC, "selected_oblast": DIV_OBLAST,
        "student_message": "daj mi zadatak", "conversation_history": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "div.sqlite3"))
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


@pytest.fixture()
def no_model(monkeypatch):
    calls = []

    def _boom(*a, **kw):
        calls.append(a)
        raise AssertionError("OpenAI was called on a deterministic path")

    monkeypatch.setattr(svc, "_openai_chat", _boom, raising=False)
    return calls


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


def seeded(client, question=TASK6, expected="da", turn_id="seed-0"):
    state = sse(client, prod_payload(client_turn_id=turn_id))["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_div", "skill_id": "divisibility", "question": question,
        "expected_display": expected, "npp_id": DIV_TOPIC, "tema_title": "t",
        "attempts": 0, "wrong_attempts": 0, "hints_given": 0,
        "solved": False, "solution_revealed": False,
        "pending_evidence_prompt": False,
    }
    state["task_id"] = "mt_div"
    return state


def turn(client, state, message, question=TASK6, turn_id="t"):
    return sse(client, prod_payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state,
        client_turn_id=turn_id))


# =========================================================================== #
# 1. Generator reaches every promised divisor                                 #
# =========================================================================== #
def test_generator_form_pool_covers_all_nine_divisors():
    all_divisors = set().union(*skills.DIVISIBILITY_BANDS.values())
    assert all_divisors == set(df.SUPPORTED_DIVISORS)


def test_seeded_distribution_covers_every_divisor_both_ways():
    """Deterministic seeds, not a probabilistic hope: the SAME 900 seeds
    every run, classifying each generated question's actual divisor/verdict."""
    counts = Counter()
    yes_no = Counter()
    for level in (1, 2, 3):
        for i in range(300):
            made = skills.generate_question("divisibility", f"{level}-{i}",
                                            difficulty=level)
            assert made is not None
            question, expected = made
            facts = df.resolve_divisibility_facts(question)
            assert facts is not None, question
            counts[facts.divisor] += 1
            yes_no[(facts.divisor, facts.holds)] += 1
            derived = derive_expected(question)
            assert derived is not None
            assert derived.expected_boolean == facts.holds, question
    assert set(counts) == set(df.SUPPORTED_DIVISORS)
    for k in df.SUPPORTED_DIVISORS:
        assert counts[k] >= 10, (k, counts)
        assert yes_no[(k, True)] >= 1, f"divisor {k} never produced a yes case"
        assert yes_no[(k, False)] >= 1, f"divisor {k} never produced a no case"


def test_near_miss_cases_reachable_for_6_and_15():
    near_miss = {6: 0, 15: 0}
    for i in range(400):
        made = skills.generate_question("divisibility", f"nm-{i}", difficulty=3)
        question, _ = made
        facts = df.resolve_divisibility_facts(question)
        if facts.divisor in near_miss and not facts.holds:
            f1, f2 = facts.factors
            if df.satisfies(facts.n, f1) != df.satisfies(facts.n, f2):
                near_miss[facts.divisor] += 1
    assert near_miss[6] > 0
    assert near_miss[15] > 0


def test_canonical_answers_self_validate_against_the_checker():
    for level in (1, 2, 3):
        for i in range(60):
            question, expected = skills.generate_question(
                "divisibility", f"self-{level}-{i}", difficulty=level)
            result = check_practice_answer(question, expected)
            assert result.checkable and result.items, question
            assert result.items[0].verdict == "correct", (question, expected)


def test_recent_question_avoidance_still_works():
    avoid: list[str] = []
    for i in range(20):
        made = skills.generate_question("divisibility", f"dup-{i}",
                                        avoid=tuple(avoid), difficulty=2)
        assert made is not None
        question, _ = made
        assert question not in avoid, "duplicate content was not avoided"
        avoid = ([question] + avoid)[:8]


def test_repeated_new_task_over_the_wire_reaches_multiple_divisors(client):
    """Plain "Daj mi novi zadatak." never changes difficulty, so this session
    stays at level 1 throughout — every divisor in level 1's band (2, 5, 10)
    must still be reachable, never just one."""
    state = seeded(client)
    seen = set()
    for i in range(30):
        body = turn(client, state, "Daj mi novi zadatak.", turn_id=f"nt-{i}")
        state = body["next_state"]
        question = state["minimal_state"]["active_task"]["question"]
        facts = df.resolve_divisibility_facts(question)
        seen.add(facts.divisor)
        if seen == set(skills.DIVISIBILITY_BANDS[1]):
            break
    assert seen == set(skills.DIVISIBILITY_BANDS[1]), seen  # NOT stuck on one


# =========================================================================== #
# 2. Correct compound-rule explanation must not be rejected                   #
# =========================================================================== #
def test_full_rule_level_explanation_is_fully_correct(client):
    """Policy (revised): naming BOTH required factors is a mathematically
    sufficient explanation on its own — the student is not required to also
    compute the digit sum/parity. Only naming ONE of the two, or none at all,
    remains incomplete."""
    state = seeded(client)
    body = turn(client, state, "da jer je djeljiv i sa 2 i sa 3", turn_id="p1")
    assert body["answer_verdict"] == "correct"
    assert body["answer_verdict_detail"] == "correct"
    assert body["task_status"] == "completed"


def test_one_factor_named_is_still_partial_not_wrong(client):
    state = seeded(client)
    body = turn(client, state, "da jer je djeljiv sa 2", turn_id="p1b")
    assert body["answer_verdict"] == "partial"
    assert body["answer_verdict_detail"] == "partially_correct"
    assert body["wrong_attempt_count"] == 0
    assert body["next_state"]["task_id"] == "mt_div"


def test_feedback_asks_for_evidence_not_a_repeated_rule(client):
    state = seeded(client)
    body = turn(client, state, "da jer je djeljiv sa 2", turn_id="p2")
    answer = body["answer"]
    assert "252" in answer
    # the production defect: verbatim repeat of the rule the student just said
    assert "Broj je djeljiv sa 6 ako je djeljiv i sa 2 i sa 3" not in answer


def test_fully_evidenced_answer_is_correct_and_completes(client):
    state = seeded(client)
    body = turn(client, state,
               "Da, jer je posljednja cifra 2 pa je broj djeljiv sa 2, a zbir "
               "cifara 2+5+2=9 je djeljiv sa 3.", turn_id="full")
    assert body["answer_verdict"] == "correct"
    assert body["task_status"] == "completed"


def test_no_case_accepts_one_valid_failing_condition(client):
    """255 fails divisor 6 ONLY on the "even" condition."""
    question = "Provjeri da li je broj 255 djeljiv sa 6. Obrazloži svoj odgovor."
    state = seeded(client, question=question, expected="ne")
    body = turn(client, state, "ne, jer broj nije paran", question=question,
               turn_id="nomiss")
    assert body["answer_verdict"] == "correct"


# =========================================================================== #
# 3. Incomplete is not a wrong mathematical attempt (shared, scoped policy)   #
# =========================================================================== #
def test_incomplete_does_not_break_streak_or_count_wrong(client, sheets):
    state = seeded(client)
    body = turn(client, state, "da", turn_id="bare-da")
    assert body["answer_verdict_detail"] == "incomplete"
    assert body["wrong_attempt_count"] == 0
    assert body["next_state"]["correct_streak"] == 0
    assert body["next_state"]["task_id"] == "mt_div"
    assert body["next_state"]["minimal_state"]["solved_count"] == 0


def test_wrong_decision_still_counts_normally(client):
    state = seeded(client)
    body = turn(client, state, "ne, jer nije paran", turn_id="wrong-decision")
    assert body["answer_verdict"] == "incorrect"
    assert body["wrong_attempt_count"] == 1
    assert body["total_attempt_count"] == 1


def test_fraction_expand_wrong_denominator_partial_still_counts_as_wrong(client):
    """Scoping check: the divisibility exemption must NOT bleed into
    fraction_expand's WRONG_TARGET_DENOMINATOR partial verdict."""
    state = sse(client, prod_payload(
        selected_topic="6-04-035", selected_oblast="Razlomci",
        client_turn_id="fe-seed"))["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_fe", "skill_id": "fraction_expand",
        "question": "Proširi 1/2 na nazivnik 4.", "expected_display": "2/4",
        "npp_id": "6-04-035", "tema_title": "t", "attempts": 0,
        "wrong_attempts": 0, "hints_given": 0, "solved": False,
        "solution_revealed": False,
    }
    state["task_id"] = "mt_fe"
    body = sse(client, prod_payload(
        student_message="4/8", interaction_phase="answering_practice_task",
        last_tutor_task="Proširi 1/2 na nazivnik 4.", previous_next_state=state,
        selected_topic="6-04-035", selected_oblast="Razlomci",
        client_turn_id="fe-wrong"))
    assert body["answer_verdict"] == "partial"
    assert body["answer_verdict_detail"] == "incorrect_target_denominator"
    assert body["wrong_attempt_count"] == 1     # UNCHANGED — still counts


def test_linear_equation_wrong_answer_unaffected(client):
    """Scoping check: an unrelated skill's ordinary wrong answer still counts."""
    state = sse(client, prod_payload(
        selected_topic="", selected_oblast="Jednostavne linearne jednačine",
        client_turn_id="lin-seed"))["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_lin", "skill_id": "linear_equation",
        "question": "Riješi jednačinu: 2x - 3 = 9.", "expected_display": "6",
        "npp_id": "", "tema_title": "t", "attempts": 0, "wrong_attempts": 0,
        "hints_given": 0, "solved": False, "solution_revealed": False,
    }
    state["task_id"] = "mt_lin"
    body = sse(client, prod_payload(
        student_message="7", interaction_phase="answering_practice_task",
        last_tutor_task="Riješi jednačinu: 2x - 3 = 9.", previous_next_state=state,
        selected_topic="", selected_oblast="Jednostavne linearne jednačine",
        client_turn_id="lin-wrong"))
    assert body["answer_verdict"] == "incorrect"
    assert body["wrong_attempt_count"] == 1


# =========================================================================== #
# 4. Boolean answer extraction must not use arbitrary numbers                 #
# =========================================================================== #
def test_production_prose_yields_no_candidate_no_mutation(client, sheets):
    state = seeded(client)
    body = turn(client, state, "sto me pitas samo za 6", turn_id="prose")
    assert body["answer_verdict_detail"] == "not_checkable"
    assert body["wrong_attempt_count"] == 0
    assert body["total_attempt_count"] == 0
    assert body["next_state"]["correct_streak"] == 0
    assert body["next_state"]["task_id"] == "mt_div"

    from matbot import sheets_log
    row = sheets_log._build_transcript_row(*sheets[-1])
    headers = sheets_log.SHEET_HEADERS
    assert row[headers.index("student_message")] == "sto me pitas samo za 6"
    assert row[headers.index("student_answer")] == ""


@pytest.mark.parametrize("message,expected_decision", [
    ("da, jer je zbir cifara 12", "da"),
    ("ne, posljednja cifra nije parna", "ne"),
])
def test_decision_extracted_correctly(client, message, expected_decision):
    state = seeded(client)
    body = turn(client, state, message, turn_id=f"dec-{expected_decision}")
    assert body["next_state"]["minimal_state"]["active_task"] is not None \
        or body["task_status"] == "completed"
    # the extracted decision, never a random number
    r = check_practice_answer(TASK6, message)
    assert r.checkable and r.items[0].given.raw == expected_decision


def test_arbitrary_number_in_prose_during_another_active_task(client):
    """"broj 6 je djeljiv sa 3" must not blindly use 6 as the answer."""
    state = seeded(client)
    body = turn(client, state, "broj 6 je djeljiv sa 3", turn_id="other-num")
    assert body["answer_verdict_detail"] in ("ambiguous_final_answer", "not_checkable")
    assert body["wrong_attempt_count"] == 0
    assert body["next_state"]["task_id"] == "mt_div"


def test_bare_number_is_not_a_valid_boolean_answer(client):
    state = seeded(client)
    body = turn(client, state, "6", turn_id="bare-num")
    assert body["answer_verdict_detail"] == "not_checkable"
    assert body["wrong_attempt_count"] == 0


def test_wrong_explicit_decision_counts_as_real_wrong_attempt(client):
    state = seeded(client)
    body = turn(client, state, "ne", turn_id="explicit-wrong")
    assert body["answer_verdict"] == "incorrect"
    assert body["wrong_attempt_count"] == 1


# =========================================================================== #
# 5. Contextual follow-up: "pa to sam i rekao"                                #
# =========================================================================== #
def test_pushback_phrase_detector():
    assert is_pushback_insistence("pa to sam i rekao")
    assert is_pushback_insistence("to sam vec rekao")
    assert not is_pushback_insistence("ne znam")


def test_pushback_after_incomplete_gets_tailored_reply(client, no_model):
    state = seeded(client)
    first = turn(client, state, "da jer je djeljiv sa 2", turn_id="pb1")
    assert first["next_state"]["minimal_state"]["active_task"][
        "pending_evidence_prompt"] is True

    second = turn(client, first["next_state"], "pa to sam i rekao",
                  turn_id="pb2")
    answer = second["answer"]
    assert "U pravu si" in answer
    assert "252" in answer
    # state and progress are untouched by the follow-up itself
    assert second["next_state"]["task_id"] == "mt_div"
    assert second["total_attempt_count"] == first["total_attempt_count"]
    assert second["wrong_attempt_count"] == first["wrong_attempt_count"]
    assert second["next_state"]["correct_streak"] == \
        first["next_state"]["correct_streak"]


def test_pushback_without_prior_incomplete_falls_to_plain_clarification(client):
    """The flag gates this — a fresh task with no incomplete-rule turn yet
    gets the ordinary OTHER-intent clarification, not the tailored reply."""
    state = seeded(client)
    body = turn(client, state, "pa to sam i rekao", turn_id="pb-cold")
    assert "U pravu si" not in body["answer"]
    assert body["next_state"]["task_id"] == "mt_div"


# =========================================================================== #
# 6. Hint and solution facts, divisor- and number-specific                    #
# =========================================================================== #
def test_hint_ladder_is_divisor_and_number_specific(client, no_model):
    state = seeded(client, question="Provjeri da li je broj 156 djeljiv sa 6. "
                                    "Obrazloži svoj odgovor.", expected="da")
    q = "Provjeri da li je broj 156 djeljiv sa 6. Obrazloži svoj odgovor."
    hints = []
    for i in range(4):
        body = turn(client, state, "ne znam", question=q, turn_id=f"h{i}")
        state = body["next_state"]
        hints.append(body["answer"])
    assert "provjeri djeljivost sa 2 i sa 3" in hints[0].lower()
    assert "156" in hints[1] and ("paran" in hints[1].lower())
    assert "156" in hints[2] and "1+5+6=12" in hints[2]
    assert "Isti korak" in hints[3]
    assert len(set(hints[:3])) == 3            # no repeated rung
    assert state["task_id"] == "mt_div"


def test_help_does_not_mutate_progress(client):
    state = seeded(client)
    for i in range(3):
        body = turn(client, state, "ne znam", turn_id=f"help-{i}")
        state = body["next_state"]
    assert body["wrong_attempt_count"] == 0
    assert body["total_attempt_count"] == 0
    assert state["correct_streak"] == 0
    assert state["task_id"] == "mt_div"


def test_solution_request_identifies_failing_condition_for_no_case(client):
    question = "Provjeri da li je broj 155 djeljiv sa 6. Obrazloži svoj odgovor."
    state = seeded(client, question=question, expected="ne")
    body = turn(client, state, "daj rjesenje", question=question, turn_id="sol")
    answer = body["answer"]
    assert "NE" in answer
    assert "155" in answer
    assert "neparan" in answer.lower()          # the SPECIFIC failing condition
    assert body["task_status"] == "revealed"
    assert body["solution_revealed"] is True
    assert body["wrong_attempt_count"] == 0


def test_solution_request_is_not_independent_credit(client):
    state = seeded(client)
    body = turn(client, state, "daj rjesenje", turn_id="sol2")
    assert body["next_state"]["minimal_state"]["solved_count"] == 0
    assert body["next_state"]["correct_streak"] == 0


# =========================================================================== #
# 10. HARDER / EASIER                                                        #
# =========================================================================== #
def test_harder_easier_use_objective_bands(client):
    """HARDER moves difficulty by exactly one step (1 -> 2), so from the
    default level the divisor must come from level 2's band."""
    state = seeded(client)
    body = turn(client, state, "Daj mi teži zadatak.", turn_id="harder-1")
    assert body["next_state"]["difficulty_level"] == 2
    q = body["next_state"]["minimal_state"]["active_task"]["question"]
    facts = df.resolve_divisibility_facts(q)
    assert facts.divisor in skills.DIVISIBILITY_BANDS[2]

    harder2 = turn(client, body["next_state"], "Daj mi teži zadatak.",
                   turn_id="harder-2")
    assert harder2["next_state"]["difficulty_level"] == 3
    q2 = harder2["next_state"]["minimal_state"]["active_task"]["question"]
    facts2 = df.resolve_divisibility_facts(q2)
    assert facts2.divisor in skills.DIVISIBILITY_BANDS[3]

    easier = turn(client, harder2["next_state"], "Daj mi lakši zadatak.",
                  turn_id="easier-1")
    assert easier["next_state"]["difficulty_level"] == 2


def test_supports_difficulty_is_true_for_divisibility():
    assert skills.supports_difficulty("divisibility") is True
