# -*- coding: utf-8 -*-
"""Four production issues from the 2026-07-21 real-browser session.

1. Sheets coerced fraction answers to date serials ("4/12" → 46360).
2. "Daj mi teži zadatak" produced a different but not harder task.
3. Output contained Cyrillic and gender-marked wording ("riješio", "potrudila").
4. Routing telemetry lost the original runtime topic id (12880 → 6-04-035).

Driven through the SSE route the browser actually uses.
"""
import json
import random

import pytest

from matbot import ai_tutor_service as svc
from matbot import sheets_log
from matbot import topic_resolver as tr
from matbot.minimal import renderer, skills
from matbot.minimal.intent import TurnIntent, classify
from matbot.minimal.state import SessionState

STREAM_URL = "/api/ai-tutor/chat/stream"
JSON_URL = "/api/ai-tutor/chat"
PROD_TOPIC = "12880"
CANONICAL_NPP = "6-04-035"
PROD_MESSAGE = "Daj mi jedan zadatak za vježbu iz ove teme."


def prod_payload(**overrides):
    payload = {
        "session_id": "stab-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": PROD_TOPIC, "selected_oblast": "Razlomci",
        "student_message": PROD_MESSAGE, "conversation_history": [],
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


# =========================================================================== #
# 1. Fractions must survive Sheets as literal text                            #
# =========================================================================== #
def test_append_uses_raw_value_input_option():
    """USER_ENTERED is what made Sheets parse "4/12" as a date."""
    assert sheets_log.SHEETS_VALUE_INPUT_OPTION == "RAW"
    src = open(sheets_log.__file__, encoding="utf-8").read()
    assert 'value_input_option="USER_ENTERED"' not in src


@pytest.mark.parametrize("value", ["4/12", "2/8", "3/7", "1/2", "5/10", "8/12",
                                   "x=4", "obična rečenica", "2x + 3 = 11"])
def test_text_fields_are_written_unchanged(value):
    """The exact cell value handed to the API must equal the input string."""
    payload = {"session_id": "s", "raw_student_message": value,
               "student_message": value}
    response = {"answer": "ok", "next_state": {},
                "answer_check": {"items": [{"student_answer": value,
                                            "expected_answer": value,
                                            "normalized_student": value,
                                            "normalized_expected": value}]}}
    row = sheets_log._build_transcript_row(payload, response)
    headers = sheets_log.SHEET_HEADERS
    for field in ("student_message", "student_answer"):
        cell = row[headers.index(field)]
        assert cell == value, (field, cell)
        assert isinstance(cell, str)


def test_raw_mode_does_not_coerce_other_field_types():
    """Counters stay numbers, JSON stays JSON — RAW must not break them."""
    payload = {"session_id": "s", "student_message": "4/12"}
    response = {"answer": "ok", "answer_verdict": "correct",
                "next_state": {"correct_streak": 3, "task_id": "mt_1"}}
    row = sheets_log._build_transcript_row(payload, response)
    headers = sheets_log.SHEET_HEADERS
    assert row[headers.index("correct_streak")] == 3
    assert row[headers.index("answer_verdict")] == "correct"
    next_state_cell = row[headers.index("next_state")]
    assert json.loads(next_state_cell)["task_id"] == "mt_1"


def test_no_apostrophe_prefix_is_added():
    """Requirement: values must not be visibly prefixed."""
    payload = {"session_id": "s", "student_message": "4/12"}
    row = sheets_log._build_transcript_row(payload, {"answer": "", "next_state": {}})
    cell = row[sheets_log.SHEET_HEADERS.index("student_message")]
    assert not cell.startswith("'")


def test_fraction_answer_survives_the_whole_turn(client, sheets):
    first = sse(client, prod_payload())
    answer = "4/12"
    sse(client, prod_payload(
        student_message=answer, interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    stored = row[sheets_log.SHEET_HEADERS.index("student_message")]
    assert stored == "4/12"


# =========================================================================== #
# 2. Difficulty                                                               #
# =========================================================================== #
def test_direction_words_classify_as_harder_or_easier():
    assert classify("Daj mi teži zadatak").intent is TurnIntent.HARDER
    assert classify("Daj mi lakši zadatak").intent is TurnIntent.EASIER
    assert classify("daj mi zadatak").intent is TurnIntent.NEW_TASK


def test_difficulty_is_bounded_and_defaults_to_one():
    state = SessionState(session_id="s")
    assert state.difficulty_level == skills.DEFAULT_DIFFICULTY == 1
    assert state.with_difficulty(9).difficulty_level == skills.MAX_DIFFICULTY
    assert state.with_difficulty(-4).difficulty_level == skills.MIN_DIFFICULTY


@pytest.mark.parametrize("level", [1, 2, 3])
def test_generated_params_belong_to_their_band(level):
    """Seeded generation: difficulty is a property of the numbers."""
    denominators, (k_lo, k_hi) = skills.band_for(level)
    for seed in range(40):
        question, _ = skills.generate_question("fraction_expand", seed=seed,
                                               difficulty=level)
        a, b, k = skills.expand_params(question)
        assert b in denominators, (level, question)
        assert k_lo <= k <= k_hi, (level, question)
        assert 1 <= a < b, (level, question)


def test_higher_band_is_objectively_harder():
    """Mean expansion factor and denominator both increase with the level."""
    def mean_params(level):
        rows = [skills.expand_params(
            skills.generate_question("fraction_expand", seed=s, difficulty=level)[0])
            for s in range(60)]
        return (sum(r[1] for r in rows) / len(rows),
                sum(r[2] for r in rows) / len(rows))

    d1, k1 = mean_params(1)
    d2, k2 = mean_params(2)
    d3, k3 = mean_params(3)
    assert d1 < d2 < d3
    assert k1 < k2 < k3


def test_harder_request_raises_the_level_over_the_wire(client):
    first = sse(client, prod_payload())
    assert first["next_state"]["difficulty_level"] == 1
    second = sse(client, prod_payload(
        student_message="Daj mi teži zadatak.",
        previous_next_state=first["next_state"]))
    assert second["next_state"]["difficulty_level"] == 2
    assert second["next_state"]["task_id"] != first["next_state"]["task_id"]
    assert second["next_state"]["task"]["skill_id"] == "fraction_expand"
    # the new task genuinely belongs to the harder band
    _a, b, k = skills.expand_params(second["last_tutor_task"])
    denominators, (k_lo, k_hi) = skills.band_for(2)
    assert b in denominators and k_lo <= k <= k_hi


def test_easier_request_lowers_the_level(client):
    state = sse(client, prod_payload())["next_state"]
    state = sse(client, prod_payload(student_message="Daj mi teži zadatak.",
                                     previous_next_state=state))["next_state"]
    assert state["difficulty_level"] == 2
    state = sse(client, prod_payload(student_message="Daj mi lakši zadatak.",
                                     previous_next_state=state))["next_state"]
    assert state["difficulty_level"] == 1


def test_level_is_bounded_over_repeated_requests(client):
    state = sse(client, prod_payload())["next_state"]
    for _ in range(5):
        state = sse(client, prod_payload(student_message="Daj mi teži zadatak.",
                                         previous_next_state=state))["next_state"]
    assert state["difficulty_level"] == 3
    for _ in range(6):
        state = sse(client, prod_payload(student_message="Daj mi lakši zadatak.",
                                         previous_next_state=state))["next_state"]
    assert state["difficulty_level"] == 1


def test_plain_new_task_keeps_the_current_level(client):
    state = sse(client, prod_payload())["next_state"]
    state = sse(client, prod_payload(student_message="Daj mi teži zadatak.",
                                     previous_next_state=state))["next_state"]
    assert state["difficulty_level"] == 2
    state = sse(client, prod_payload(student_message="daj mi novi zadatak",
                                     previous_next_state=state))["next_state"]
    assert state["difficulty_level"] == 2


def test_harder_task_is_not_an_immediate_duplicate(client):
    first = sse(client, prod_payload())
    second = sse(client, prod_payload(student_message="Daj mi teži zadatak.",
                                      previous_next_state=first["next_state"]))
    assert second["last_tutor_task"] != first["last_tutor_task"]


def test_difficulty_appears_in_routing_telemetry(client):
    body = sse(client, prod_payload())
    assert body["minimal_routing"]["difficulty_level"] == 1


def test_skills_without_bands_do_not_claim_difficulty():
    assert skills.supports_difficulty("fraction_expand") is True
    assert skills.supports_difficulty("divisibility") is False


# =========================================================================== #
# 3. Bosnian Latin, neutral wording                                           #
# =========================================================================== #
GENDERED_WORDS = ("riješio", "riješila", "potrudila", "potrudio", "voljela",
                  "volio", "želio", "željela", "uradio", "uradila", "pokušao",
                  "pokušala", "siguran", "sigurna")


def _all_rendered_outputs(client):
    """Every kind of student-facing string the engine can produce."""
    outputs = []
    first = sse(client, prod_payload())
    outputs.append(first["answer"])                       # task presentation
    state, question = first["next_state"], first["last_tutor_task"]
    for msg in ("ne znam", "999/999"):
        body = sse(client, prod_payload(
            student_message=msg, interaction_phase="answering_practice_task",
            last_tutor_task=question, previous_next_state=state))
        state = body["next_state"]
        outputs.append(body["answer"])                    # hint, incorrect
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    correct = getattr(exp, "expected_display", "") or _fmt_expected(exp)
    body = sse(client, prod_payload(
        student_message=correct, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state))
    outputs.append(body["answer"])                        # correct feedback
    outputs.append(sse(client, prod_payload(
        selected_topic="999999"))["answer"])              # refusal
    return outputs


def test_no_cyrillic_in_any_rendered_output(client):
    for text in _all_rendered_outputs(client):
        assert not renderer.has_cyrillic(text), text


def test_no_gendered_wording_in_any_rendered_output(client):
    for text in _all_rendered_outputs(client):
        low = text.lower()
        for word in GENDERED_WORDS:
            assert word not in low, (word, text)
        assert not renderer.is_gendered(text), text


def test_no_slash_gender_forms(client):
    for text in _all_rendered_outputs(client):
        assert "/la" not in text and "o/a" not in text.replace("2/4", "")
        assert "riješio/riješila" not in text
        assert "želio/željela" not in text


def test_correct_feedback_does_not_start_with_naravno(client):
    first = sse(client, prod_payload())
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(first["last_tutor_task"])
    correct = getattr(exp, "expected_display", "") or _fmt_expected(exp)
    body = sse(client, prod_payload(
        student_message=correct, interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    assert not body["answer"].lower().startswith("naravno")


def test_diacritics_are_preserved(client):
    text = " ".join(_all_rendered_outputs(client))
    assert any(ch in text for ch in "čćžšđ"), "Bosnian diacritics must survive"


def test_transliteration_converts_cyrillic_to_bosnian_latin():
    assert renderer.to_latin("Тачно") == "Tačno"
    assert renderer.to_latin("Њива џak") == "Njiva džak"
    assert renderer.to_latin("Tačno") == "Tačno"          # Latin untouched


@pytest.mark.parametrize("text", ["4/12", "x = 4", "2x + 3 = 11",
                                  "Proširi 3/4 na nazivnik 20.", "12.57 cm"])
def test_language_gate_never_alters_math(text):
    assert renderer.enforce_language(text) == text


def test_language_gate_strips_a_sycophantic_opener():
    assert renderer.enforce_language("Naravno! Tačno.") == "Tačno."
    assert renderer.enforce_language("Tačno.") == "Tačno."


@pytest.mark.parametrize("drift", [
    "Bravo, riješio si to!",
    "Vidim da si se potrudila.",
    "Тачно је.",
    "Naravno! Tačno je.",
    "Želio si novi zadatak?",
])
def test_model_wording_that_breaks_policy_is_rejected(drift):
    class _Resp:
        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    original = "Tačno. Želiš li još jedan zadatak?"
    out = renderer.phrase_with_model(
        original, openai_chat=lambda *a, **kw: _Resp(drift),
        model="m", timeout=1, allow_verdict_words=True)
    assert out == original, drift


def test_neutral_model_wording_is_accepted():
    class _Resp:
        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    # "Idemo dalje?" is no longer acceptable: feedback never starts a task.
    original = "Tačno. Želiš li još jedan zadatak?"
    out = renderer.phrase_with_model(
        original, openai_chat=lambda *a, **kw: _Resp("Tačno je. Hoćeš li još jedan?"),
        model="m", timeout=1, allow_verdict_words=True)
    assert out == "Tačno je. Hoćeš li još jedan?"


def test_every_literal_in_the_renderer_is_policy_clean():
    """Sweep ALL Bosnian string literals in the module, not just the pools.

    Catches phrases reachable only from rarely-exercised branches — one such
    ("Nisam siguran…") survived the pool check.
    """
    import ast
    tree = ast.parse(open(renderer.__file__, encoding="utf-8").read())
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        # only student-facing Bosnian prose, not regexes/keys/docstrings
        if len(text) < 12 or "\\b" in text or text.strip().startswith(("http", "{")):
            continue
        if not any(ch in text.lower() for ch in "čćžšđ") and " " not in text:
            continue
        checked += 1
        assert not renderer.has_cyrillic(text), text
        assert not renderer.is_gendered(text), text
    assert checked > 15, "sweep found too few literals to be meaningful"


def test_deterministic_pools_are_policy_clean():
    """The engine's own phrases must satisfy the policy without any guard."""
    pools = (renderer._CORRECT + renderer._PARTIAL + renderer._INCORRECT
             + renderer._UNVERIFIED + renderer._NEXT_INVITE)
    for phrase in pools:
        assert not renderer.has_cyrillic(phrase), phrase
        assert not renderer.is_gendered(phrase), phrase
    for hints in renderer._HINTS.values():
        for hint in hints:
            assert not renderer.is_gendered(hint), hint


# =========================================================================== #
# 4. Original runtime topic id preserved                                      #
# =========================================================================== #
def test_routing_reports_the_original_runtime_id(client):
    routing = sse(client, prod_payload())["minimal_routing"]
    assert routing["runtime_topic"] == PROD_TOPIC
    assert routing["canonical_topic"] == CANONICAL_NPP
    assert routing["resolved_skill"] == "fraction_expand"


def test_runtime_id_survives_the_client_echoing_the_canonical_id(client):
    """index.html adopts effective_topic, so turn 2 sends 6-04-035."""
    first = sse(client, prod_payload())
    assert first["effective_topic"] == CANONICAL_NPP
    second = sse(client, prod_payload(
        selected_topic=CANONICAL_NPP,               # what the browser now sends
        student_message="daj mi novi zadatak",
        previous_next_state=first["next_state"]))
    routing = second["minimal_routing"]
    assert routing["runtime_topic"] == PROD_TOPIC   # ORIGINAL preserved
    assert routing["canonical_topic"] == CANONICAL_NPP
    assert routing["resolved_skill"] == "fraction_expand"


def test_origin_runtime_id_is_persisted_in_state(client):
    body = sse(client, prod_payload())
    assert body["next_state"]["minimal_state"]["origin_runtime_id"] == PROD_TOPIC


def test_both_routes_report_the_same_runtime_id(client):
    stream_routing = sse(client, prod_payload())["minimal_routing"]
    json_routing = client.post(JSON_URL, json=prod_payload()).get_json()["minimal_routing"]
    assert stream_routing["runtime_topic"] == json_routing["runtime_topic"] == PROD_TOPIC


def test_sheets_receives_the_routing_telemetry(client, sheets):
    sse(client, prod_payload())
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    cell = row[sheets_log.SHEET_HEADERS.index("minimal_routing")]
    stored = json.loads(cell)
    assert stored["runtime_topic"] == PROD_TOPIC
    assert stored["canonical_topic"] == CANONICAL_NPP
    assert stored["resolved_skill"] == "fraction_expand"


def test_runtime_and_canonical_stay_separate_concepts(client):
    body = sse(client, prod_payload())
    routing = body["minimal_routing"]
    assert routing["runtime_topic"] != routing["canonical_topic"]
    task = body["next_state"]["task"]
    assert task["tema_id"] == CANONICAL_NPP
    assert task["tema_title"] == "Proširivanje razlomaka"
    assert task["skill_id"] == "fraction_expand"


# =========================================================================== #
# Production-faithful acceptance flow                                         #
# =========================================================================== #
def test_full_acceptance_flow(client, sheets):
    # 1. new task
    first = sse(client, prod_payload())
    assert first["minimal_routing"]["handled"] is True
    assert first["next_state"]["task"]["skill_id"] == "fraction_expand"
    assert first["minimal_routing"]["runtime_topic"] == PROD_TOPIC
    assert first["minimal_routing"]["canonical_topic"] == CANONICAL_NPP
    assert first["next_state"]["difficulty_level"] == 1
    question, tid = first["last_tutor_task"], first["next_state"]["task_id"]

    # 2. a literal fraction answer
    wrong = sse(client, prod_payload(
        student_message="4/12", interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=first["next_state"]))
    assert wrong["answer_verdict"] in ("correct", "incorrect")
    assert not wrong.get("gpt_check_used")
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    assert row[sheets_log.SHEET_HEADERS.index("student_message")] == "4/12"

    # 3. harder
    harder = sse(client, prod_payload(
        student_message="Daj mi teži zadatak.",
        previous_next_state=wrong["next_state"]))
    assert harder["next_state"]["task_id"] != tid
    assert harder["next_state"]["task"]["skill_id"] == "fraction_expand"
    assert harder["next_state"]["difficulty_level"] == 2
    assert harder["last_tutor_task"] != question
    _a, b, k = skills.expand_params(harder["last_tutor_task"])
    dens, (k_lo, k_hi) = skills.band_for(2)
    assert b in dens and k_lo <= k <= k_hi

    # 4. "ne znam"
    hinted = sse(client, prod_payload(
        student_message="ne znam", interaction_phase="answering_practice_task",
        last_tutor_task=harder["last_tutor_task"],
        previous_next_state=harder["next_state"]))
    assert hinted["session_mode"] == "practice"
    assert hinted["next_state"]["task_id"] == harder["next_state"]["task_id"]
    assert hinted["last_tutor_task"] == harder["last_tutor_task"]
    payload, _ = sheets[-1]
    assert sheets_log._raw_student_message(payload) == "ne znam"

    # 5. language policy across every reply
    for body in (first, wrong, harder, hinted):
        assert not renderer.has_cyrillic(body["answer"])
        assert not renderer.is_gendered(body["answer"])


def test_flag_off_keeps_legacy_behaviour(client, fake_openai, monkeypatch):
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    body = client.post(JSON_URL, json=prod_payload()).get_json()
    assert body.get("engine") != "minimal"
    assert "minimal_routing" not in body
