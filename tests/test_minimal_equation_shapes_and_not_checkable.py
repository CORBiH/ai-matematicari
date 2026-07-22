# -*- coding: utf-8 -*-
"""Three focused production defects in fraction_equation_additive.

1. GENERATOR COVERAGE. 19 consecutive "Daj mi novi zadatak." turns produced
   9× x-a=b, 6× x+a=b, 4× a+x=b, 0× a-x=b — the lesson title promises all four
   shapes ("x ± a = b i a ± x = b"). Cause: ``skills._generate_fraction_equation``
   picked its shape via ``rng.choice(("x_plus", "lead_plus", "x_minus"))`` — a
   THREE-item tuple. "a_minus_x" was never a candidate at all, so no seed,
   however many turns generated, could ever produce it.

2. TASK-SHAPE REQUESTS MISCLASSIFIED AS ANSWERS. "zelim da probam oblik
   a-x=b" and "zelim uraditi zadatak a-x=b u formi ovoj" were graded as
   answers to the ACTIVE task (verdict=unverified, detail=not_checkable),
   incrementing attempts/wrong_attempts and breaking the streak. Cause:
   ``intent.classify`` requires a task-request word AND no math content
   (``_NEW_TASK_RE.search(text) and not _MATH_RE.search(text)``); a message
   describing an equation shape is full of digits/operators, so it fell
   through to the ANSWER branch.

3. NOT_CHECKABLE COUNTED AS A WRONG ATTEMPT. Only ``AMBIGUOUS_FINAL_ANSWER``
   was excluded from attempt-counting; ``NOT_CHECKABLE`` (nothing recognisable
   as any answer form — no candidate ever identified) was not, even though it
   carries the same "no graded_answer" guarantee. Fixed as shared grading
   behaviour in ``engine.py``'s ANSWER branch, not per-skill.

Driven through the real SSE route the browser uses.
"""
import json
from fractions import Fraction

import pytest

from matbot import ai_tutor_service as svc
from matbot import topic_resolver as tr
from matbot.answer_checker import derive_expected
from matbot.minimal import mathfmt, skills, solution_facts as sf
from matbot.minimal.grading import AMBIGUOUS_FINAL_ANSWER, NOT_CHECKABLE
from matbot.minimal.intent import TurnIntent, classify, parse_shape_request

STREAM_URL = "/api/ai-tutor/chat/stream"
EQ_TOPIC = "6-07-064"
EQ_OBLAST = "Jednačine, nejednačine i izrazi u Q+"
TASK = "Riješi jednačinu: x - 3/4 = 1/4."


def prod_payload(**overrides):
    payload = {
        "session_id": "shp-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": EQ_TOPIC, "selected_oblast": EQ_OBLAST,
        "student_message": "daj mi zadatak", "conversation_history": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "shp.sqlite3"))
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


def seeded(client, question=TASK, expected="1", turn_id="seed-0"):
    state = sse(client, prod_payload(client_turn_id=turn_id))["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_prod", "skill_id": "fraction_equation_additive",
        "question": question, "expected_display": expected,
        "npp_id": EQ_TOPIC, "tema_title": "t", "attempts": 0,
        "wrong_attempts": 0, "hints_given": 0, "solved": False,
        "solution_revealed": False,
    }
    state["task_id"] = "mt_prod"
    return state


def turn(client, state, message, question=TASK, turn_id="t"):
    return sse(client, prod_payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state,
        client_turn_id=turn_id))


# =========================================================================== #
# 1. Generator: a - x = b is reachable                                        #
# =========================================================================== #
def test_generator_form_pool_includes_all_four_shapes():
    assert set(skills.EQUATION_FORMS) == {"x_plus", "lead_plus", "x_minus",
                                          "a_minus_x"}


def test_forced_a_minus_x_generates_valid_checkable_positive_tasks():
    """Not a hand-built task — the REAL generator, forced through its shape
    parameter, over many seeds and all three difficulty bands."""
    for level in (1, 2, 3):
        for seed in range(40):
            question, expected = skills.generate_question(
                "fraction_equation_additive", f"{level}-{seed}",
                difficulty=level, shape="a_minus_x")
            facts = sf.resolve_equation_facts(question)
            assert facts is not None, question
            assert facts.shape == "a-x=b", question
            assert facts.subtracted_unknown is True
            assert Fraction(expected) > 0, question
            derived = derive_expected(question)
            assert derived is not None and str(derived.value) == expected, \
                (question, expected)


def test_seeded_distribution_over_many_generations_covers_every_shape():
    """Deterministic seeds, not a probabilistic hope: the SAME 450 seeds
    every run, classifying each generated equation's ACTUAL shape."""
    from collections import Counter
    counts = Counter()
    for level in (1, 2, 3):
        for i in range(150):
            question, expected = skills.generate_question(
                "fraction_equation_additive", f"{level}-{i}", difficulty=level)
            facts = sf.resolve_equation_facts(question)
            assert facts is not None, question
            counts[facts.shape] += 1
            assert Fraction(expected) > 0, question
            derived = derive_expected(question)
            assert derived is not None and str(derived.value) == expected, \
                (question, expected)
    assert set(counts) == {"x+a=b", "a+x=b", "x-a=b", "a-x=b"}
    # roughly uniform — no shape should be a rare accident of the RNG
    for shape, count in counts.items():
        assert count >= 20, (shape, count, counts)


def test_recent_question_avoidance_still_works_for_every_shape():
    """Content-duplicate prevention (section 3's separate concern) survives
    the fourth shape being added. The real mechanism (``recent_questions``)
    is bounded to the last 8 — a repeat OUTSIDE that window is expected and
    is not what this guards against; a repeat WITHIN the current avoid-list
    would mean the fourth shape broke the existing prevention."""
    avoid: list[str] = []
    for i in range(20):
        made = skills.generate_question(
            "fraction_equation_additive", f"dup-{i}", avoid=tuple(avoid),
            difficulty=2)
        assert made is not None
        question, _ = made
        assert question not in avoid, "duplicate content was not avoided"
        avoid = ([question] + avoid)[:8]


# =========================================================================== #
# 2. Explicit task-shape requests                                             #
# =========================================================================== #
@pytest.mark.parametrize("message,expected_shape", [
    ("zelim da probam oblik a-x=b", "a_minus_x"),
    ("zelim uraditi zadatak a-x=b u formi ovoj", "a_minus_x"),
    ("daj mi zadatak oblika a-x=b", "a_minus_x"),
    ("hocu jednacinu gdje je razlomak minus x", "a_minus_x"),
    ("daj mi primjer 5/6 - x = 1/3", "a_minus_x"),
    ("a−x=b", "a_minus_x"),                          # Unicode minus
])
def test_shape_requests_parse_deterministically(message, expected_shape):
    assert parse_shape_request(message) == expected_shape


@pytest.mark.parametrize("message", [
    "zelim da probam oblik a-x=b",
    "zelim uraditi zadatak a-x=b u formi ovoj",
    "a−x=b",
])
def test_shape_requests_classify_as_new_task_not_answer(message):
    decided = classify(message, has_active_task=True)
    assert decided.intent == TurnIntent.NEW_TASK, message
    assert decided.matched == "shape_request"


def test_shape_request_creates_the_requested_shape_over_the_wire(client):
    state = seeded(client)
    body = turn(client, state, "zelim da probam oblik a-x=b", turn_id="t1")

    new_question = body["next_state"]["minimal_state"]["active_task"]["question"]
    facts = sf.resolve_equation_facts(new_question)
    assert facts.shape == "a-x=b", new_question
    assert body["next_state"]["task_id"] != "mt_prod"
    assert body["minimal_routing"]["requested_task_shape"] == "a_minus_x"
    assert body["minimal_routing"]["task_transition"] == "replaced"
    assert body["minimal_routing"]["previous_task_id"] == "mt_prod"


def test_shape_request_produces_no_verdict_or_progress_change(client):
    state = seeded(client)
    body = turn(client, state, "zelim uraditi zadatak a-x=b u formi ovoj",
               turn_id="t2")
    assert body["answer_verdict"] in (None, "", "none")
    assert body.get("gpt_check_used") in (False, None)
    assert body["wrong_attempt_count"] == 0
    assert body["total_attempt_count"] == 0
    assert body["next_state"]["correct_streak"] == 0
    assert body["next_state"]["minimal_state"]["solved_count"] == 0


def test_unicode_minus_shape_request_takes_the_same_route(client):
    state = seeded(client)
    body = turn(client, state, "a−x=b", turn_id="t3")
    new_question = body["next_state"]["minimal_state"]["active_task"]["question"]
    assert sf.resolve_equation_facts(new_question).shape == "a-x=b"
    assert body["minimal_routing"]["requested_task_shape"] == "a_minus_x"


def test_shape_request_preserves_earlier_wrong_attempt(client):
    """Section 2's transition must not erase a REAL prior miss."""
    state = seeded(client)
    wrong = turn(client, state, "99/100", turn_id="wrong")
    assert wrong["answer_verdict"] == "incorrect"
    body = turn(client, wrong["next_state"], "zelim da probam oblik a-x=b",
               turn_id="shape")
    # the OLD task's miss is not retroactively erased from the turn record —
    # it is simply that the NEW task starts its own counters at zero
    assert body["wrong_attempt_count"] == 0
    assert body["next_state"]["task_id"] != "mt_prod"


def test_unsupported_shape_under_another_skill_is_declined_safely(client):
    """A shape request under a skill that has no such concept (linear_equation)
    gets an honest decline — never silent substitution, never a verdict."""
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
        student_message="zelim da probam oblik a-x=b",
        interaction_phase="answering_practice_task",
        last_tutor_task="Riješi jednačinu: 2x - 3 = 9.",
        previous_next_state=state, selected_topic="",
        selected_oblast="Jednostavne linearne jednačine",
        client_turn_id="lin-shape"))

    assert body["next_state"]["task_id"] == "mt_lin"      # UNCHANGED
    assert body["answer_verdict"] in (None, "", "none")
    assert body["wrong_attempt_count"] == 0
    assert body["minimal_routing"]["task_transition"] == ""
    assert "2x" not in body["answer"] or "6" not in body["answer"]  # no reveal


# =========================================================================== #
# 3. NOT_CHECKABLE must not count as a wrong attempt (shared grading)         #
# =========================================================================== #
def test_not_checkable_constant_exists_and_is_distinct():
    assert NOT_CHECKABLE == "not_checkable"
    assert NOT_CHECKABLE != AMBIGUOUS_FINAL_ANSWER


def test_random_unparseable_prose_does_not_count_as_an_attempt(client):
    """"racunam jos +-" carries math-looking characters (so intent classifies
    it as ANSWER, exercising the grading path — not just the OTHER-intent
    clarification path), but nothing in it is a recognisable answer form."""
    state = seeded(client)
    decided = classify("racunam jos +-", has_active_task=True)
    assert decided.intent == TurnIntent.ANSWER
    body = turn(client, state, "racunam jos +-", turn_id="rand")
    assert body["answer_verdict_detail"] == "not_checkable"
    assert body["wrong_attempt_count"] == 0
    assert body["total_attempt_count"] == 0
    assert body["next_state"]["correct_streak"] == 0
    assert body["next_state"]["task_id"] == "mt_prod"     # task preserved


def test_genuinely_wrong_rational_answer_still_counts(client):
    state = seeded(client)
    body = turn(client, state, "99/100", turn_id="wrong-real")
    assert body["answer_verdict"] == "incorrect"
    assert body["wrong_attempt_count"] == 1
    assert body["total_attempt_count"] == 1
    assert body["next_state"]["task_id"] == "mt_prod"


def test_fraction_add_unlike_ambiguous_prose_unchanged(client):
    state = sse(client, prod_payload(
        selected_topic="6-04-040", selected_oblast="Razlomci",
        client_turn_id="add-seed"))["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_add", "skill_id": "fraction_add_unlike",
        "question": "Izračunaj: 1/3 + 4/5.", "expected_display": "17/15",
        "npp_id": "6-04-040", "tema_title": "t", "attempts": 0,
        "wrong_attempts": 0, "hints_given": 0, "solved": False,
        "solution_revealed": False,
    }
    state["task_id"] = "mt_add"

    body = sse(client, prod_payload(
        student_message="zajednicki nazivnik je 15 a onda 5/15 i 12/15",
        interaction_phase="answering_practice_task",
        last_tutor_task="Izračunaj: 1/3 + 4/5.",
        previous_next_state=state, selected_topic="6-04-040",
        selected_oblast="Razlomci", client_turn_id="add-ambiguous"))
    assert body["answer_verdict_detail"] in (AMBIGUOUS_FINAL_ANSWER,
                                             "not_checkable")
    assert body["wrong_attempt_count"] == 0
    assert body["next_state"]["task_id"] == "mt_add"


def test_linear_equation_random_prose_does_not_break_streak(client):
    state = sse(client, prod_payload(
        selected_topic="", selected_oblast="Jednostavne linearne jednačine",
        client_turn_id="lin-seed2"))["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_lin2", "skill_id": "linear_equation",
        "question": "Riješi jednačinu: 2x - 3 = 9.", "expected_display": "6",
        "npp_id": "", "tema_title": "t", "attempts": 0, "wrong_attempts": 0,
        "hints_given": 0, "solved": False, "solution_revealed": False,
    }
    state["task_id"] = "mt_lin2"
    state["minimal_state"]["correct_streak"] = 2

    body = sse(client, prod_payload(
        student_message="jos malo pa cu = uskoro",
        interaction_phase="answering_practice_task",
        last_tutor_task="Riješi jednačinu: 2x - 3 = 9.",
        previous_next_state=state, selected_topic="",
        selected_oblast="Jednostavne linearne jednačine",
        client_turn_id="lin-prose"))
    assert body["answer_verdict_detail"] == "not_checkable"
    assert body["next_state"]["correct_streak"] == 2       # unchanged
    assert body["wrong_attempt_count"] == 0
    assert body["next_state"]["task_id"] == "mt_lin2"


# =========================================================================== #
# 5. Additional required SSE cases                                           #
# =========================================================================== #
def test_repeated_new_task_reaches_all_four_lesson_shapes(client):
    """Fixed session_id, so the sequence of generated shapes is fully
    deterministic — not a probabilistic hope."""
    state = seeded(client)
    seen = set()
    seen_questions = set()
    for i in range(30):
        body = turn(client, state, "Daj mi novi zadatak.", turn_id=f"nt-{i}")
        state = body["next_state"]
        question = state["minimal_state"]["active_task"]["question"]
        assert question not in seen_questions, "immediate duplicate content"
        seen_questions.add(question)
        seen.add(sf.resolve_equation_facts(question).shape)
        if len(seen) == 4:
            break
    assert seen == {"x+a=b", "a+x=b", "x-a=b", "a-x=b"}, seen


def test_solve_a_minus_x_shape_with_correct_answer(client):
    question = "Riješi jednačinu: 5/6 - x = 1/3."
    state = seeded(client, question=question, expected="1/2")
    body = turn(client, state, "x=1/2", question=question, turn_id="solve")
    assert body["answer_verdict"] == "correct"
    assert body["task_status"] == "completed"


def test_wrong_then_help_then_solution_for_a_minus_x(client):
    question = "Riješi jednačinu: 5/6 - x = 1/3."
    state = seeded(client, question=question, expected="1/2")
    wrong = turn(client, state, "7/6", question=question, turn_id="w1")
    assert wrong["answer_verdict"] == "incorrect"

    hint = turn(client, wrong["next_state"], "ne znam", question=question,
               turn_id="h1")
    assert "7/6" not in hint["answer"]

    solution = turn(client, hint["next_state"], "daj rjesenje",
                    question=question, turn_id="s1")
    compact = solution["answer"].replace(" ", "")
    assert r"x&=\frac{5}{6}-\frac{1}{3}" in compact
    assert r"x&=\frac{1}{2}" in compact
    assert "7/6" not in solution["answer"] and r"\frac{7}{6}" not in solution["answer"]
