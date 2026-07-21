# -*- coding: utf-8 -*-
"""Production incident 2026-07-21, commit f4acb3e, MATBOT_MINIMAL_ENGINE=on.

Three rows from the live sheet:

  10:41:37Z  grade=6 mode=practice topic=12880 selected_oblast=Razlomci
             "Daj mi jedan zadatak za vježbu iz ove teme."
             → "Riješi jednačinu: 3x + 2 = 14."          (an EQUATION)
  10:41:56Z  the equation was graded with gpt_check_used=true,
             answer_verdict_detail=gpt_correct           (LEGACY grading)
  10:42:44Z  a new/harder request returned the identical equation

Root cause: ``skills.resolve_topic`` could not resolve the runtime topic id
12880, so ``_try_minimal_engine`` declined and the turn fell through to free
legacy generation — which invented an equation under "Proširivanje razlomaka".

These tests drive the REAL endpoint with the exact production payload.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import topic_resolver as tr
from matbot.minimal import skills

CHAT_URL = "/api/ai-tutor/chat"

# --- the exact production payload -------------------------------------------
PROD_TOPIC = "12880"
PROD_OBLAST = "Razlomci"
PROD_MESSAGE = "Daj mi jedan zadatak za vježbu iz ove teme."
CANONICAL_NPP = "6-04-035"
CANONICAL_TITLE = "Proširivanje razlomaka"
EXPECTED_SKILL = "fraction_expand"


def prod_payload(**overrides):
    payload = {
        "session_id": "prod-2026-07-21",
        "grade": 6,
        "mode": "practice",
        "session_mode": "practice",
        "entry_source": "manual_topic_choice",
        "selected_topic": PROD_TOPIC,
        "selected_oblast": PROD_OBLAST,
        "student_message": PROD_MESSAGE,
        "conversation_history": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
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
                        lambda payload, response: rows.append((payload, response)))
    return rows


# =========================================================================== #
# 1. Topic resolution — the root cause                                        #
# =========================================================================== #
def test_runtime_topic_12880_resolves_to_the_canonical_lesson():
    canonical = tr.resolve_topic(6, PROD_TOPIC)
    assert canonical is not None, "12880 must resolve from curriculum data"
    assert canonical.npp_id == CANONICAL_NPP
    assert canonical.tema == CANONICAL_TITLE


def test_runtime_topic_12880_maps_to_the_minimal_skill():
    topic = skills.resolve_topic(6, PROD_TOPIC, PROD_OBLAST)
    assert topic.supported is True
    assert topic.skill_id == EXPECTED_SKILL
    assert topic.npp_id == CANONICAL_NPP
    assert topic.runtime_id == PROD_TOPIC       # runtime id kept distinct


def test_resolution_is_data_driven_not_hardcoded():
    """No id literal in executable resolution LOGIC.

    Comments and docstrings may cite the production id as provenance; what must
    not exist is a code path that special-cases it.
    """
    import re as _re
    for module in (tr, skills):
        src = open(module.__file__, encoding="utf-8").read()
        src = _re.sub(r'""".*?"""', "", src, flags=_re.S)      # docstrings
        code = " ".join(line.split("#")[0] for line in src.splitlines())
        assert PROD_TOPIC not in code, module.__name__


def test_overrides_are_loaded_generically(monkeypatch, tmp_path):
    """Any runtime id in the override file resolves — nothing is special."""
    override = tmp_path / "map.json"
    override.write_text(json.dumps({"6": {"77777": CANONICAL_NPP}}),
                        encoding="utf-8")
    monkeypatch.setenv("MATBOT_RUNTIME_TOPIC_MAP", str(override))
    tr.reset_cache()
    assert tr.resolve_topic(6, "77777").npp_id == CANONICAL_NPP


def test_thinkific_resources_sheet_is_exposed_by_the_loader():
    """The workbook's own runtime-id mapping sheet must reach the resolver."""
    from matbot import content_loader as cl
    master = cl.load_master_content(grade=6)
    assert "thinkific_resources" in master
    assert isinstance(master["thinkific_resources"], list)


# =========================================================================== #
# 2. The production turn, end to end                                          #
# =========================================================================== #
def test_prod_turn_is_handled_by_the_minimal_engine(client, sheets):
    resp = client.post(CHAT_URL, json=prod_payload())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["engine"] == "minimal"
    assert body["minimal_routing"]["handled"] is True
    assert body["minimal_routing"]["decline_reason"] == ""


def test_prod_turn_returns_an_expansion_task_not_an_equation(client):
    body = client.post(CHAT_URL, json=prod_payload()).get_json()
    task = body["last_tutor_task"]
    assert task, "a task must be offered"
    assert task.lower().startswith("proširi"), task
    for banned in ("jednačin", "jednacin", "3x", "= 14"):
        assert banned not in task.lower(), task


def test_prod_turn_task_carries_the_canonical_identity(client):
    body = client.post(CHAT_URL, json=prod_payload()).get_json()
    task = body["next_state"]["task"]
    assert task["tema_id"] == CANONICAL_NPP
    assert task["tema_title"] == CANONICAL_TITLE
    assert task["skill_id"] == EXPECTED_SKILL


def test_prod_next_state_is_the_minimal_state_not_the_legacy_one(client):
    body = client.post(CHAT_URL, json=prod_payload()).get_json()
    ns = body["next_state"]
    assert ns["engine"] == "minimal"
    assert isinstance(ns.get("minimal_state"), dict)
    # the legacy fields observed in the production row must be absent
    for legacy_field in ("task_validation", "pending_action", "hint_history"):
        assert legacy_field not in ns, legacy_field


def test_prod_grading_is_deterministic_not_gpt(client, sheets):
    """Row 2: the answer was graded by GPT. It must now be deterministic."""
    first = client.post(CHAT_URL, json=prod_payload()).get_json()
    question = first["last_tutor_task"]
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    answer = getattr(exp, "expected_display", "") or _fmt_expected(exp)

    second = client.post(CHAT_URL, json=prod_payload(
        student_message=answer,
        interaction_phase="answering_practice_task",
        last_tutor_task=question,
        previous_next_state=first["next_state"],
    )).get_json()
    assert second["answer_verdict"] == "correct"
    assert second["engine"] == "minimal"
    assert not second.get("gpt_check_used")
    assert second["answer_verdict_detail"] != "gpt_correct"


def test_prod_new_task_request_does_not_repeat_the_same_task(client):
    """Row 3: a new/harder request returned the identical equation."""
    first = client.post(CHAT_URL, json=prod_payload()).get_json()
    q1 = first["last_tutor_task"]
    second = client.post(CHAT_URL, json=prod_payload(
        student_message="Daj mi teži zadatak iz iste teme.",
        previous_next_state=first["next_state"],
        recent_tasks=[q1],
    )).get_json()
    q2 = second["last_tutor_task"]
    assert q2 and q2 != q1
    assert q2.lower().startswith("proširi")      # still the same tema


# =========================================================================== #
# 3. Routing telemetry                                                        #
# =========================================================================== #
_ROUTING_FIELDS = ("minimal_engine_enabled", "handled", "decline_reason",
                   "runtime_topic", "canonical_topic", "resolved_skill")


def test_routing_telemetry_is_complete_on_a_handled_turn(client):
    body = client.post(CHAT_URL, json=prod_payload()).get_json()
    routing = body["minimal_routing"]
    for field in _ROUTING_FIELDS:
        assert field in routing, field
    assert routing["runtime_topic"] == PROD_TOPIC
    assert routing["canonical_topic"] == CANONICAL_NPP
    assert routing["resolved_skill"] == EXPECTED_SKILL


def test_routing_telemetry_records_the_decline_reason(client, monkeypatch):
    """An unresolvable id is reported, not silently swallowed."""
    body = client.post(CHAT_URL, json=prod_payload(
        selected_topic="999999")).get_json()
    routing = body["minimal_routing"]
    assert routing["handled"] is True
    assert routing["decline_reason"] == "unresolved_runtime_topic"
    assert routing["canonical_topic"] == ""
    assert routing["resolved_skill"] == ""


def test_routing_telemetry_reaches_sheets(client, sheets):
    client.post(CHAT_URL, json=prod_payload())
    _payload, response = sheets[-1]
    from matbot.sheets_log import SHEET_HEADERS, _build_transcript_row
    row = _build_transcript_row(_payload, response)
    routing_cell = row[SHEET_HEADERS.index("minimal_routing")]
    assert EXPECTED_SKILL in routing_cell


# =========================================================================== #
# 4. No silent fallthrough (the safety requirement)                           #
# =========================================================================== #
def test_unresolved_topic_never_falls_through_to_free_generation(client, fake_openai):
    """The decisive test: legacy must not be allowed to invent a task."""
    fake_openai.state["reply"] = "Zadatak: Riješi jednačinu: 3x + 2 = 14."
    body = client.post(CHAT_URL, json=prod_payload(
        selected_topic="999999")).get_json()
    assert body["engine"] == "minimal"           # minimal answered honestly
    assert body["last_tutor_task"] == ""         # nothing activated
    # The refusal may NAME supported topics (one of them is about equations);
    # what must never appear is the invented TASK.
    assert "3x + 2" not in (body["answer"] or "")
    assert "= 14" not in (body["answer"] or "")
    assert fake_openai.calls.messages == []      # the model was never asked


def test_unsupported_but_resolvable_topic_is_refused_by_name(client, fake_openai):
    """A real tema outside the supported five: named honestly, no substitute."""
    fake_openai.state["reply"] = "Zadatak: Riješi jednačinu: 3x + 2 = 14."
    body = client.post(CHAT_URL, json=prod_payload(
        selected_topic="6-08-079",
        selected_oblast="Skupovi tačaka, kružnica i krug")).get_json()
    assert body["engine"] == "minimal"
    assert body["last_tutor_task"] == ""
    assert body["minimal_routing"]["decline_reason"] == "topic_not_supported"
    assert "Odnos dvije kružnice" in body["answer"]


def test_free_chat_without_a_selected_topic_still_falls_back(client, fake_openai):
    """No explicit topic → the honest-refusal rule does not apply."""
    fake_openai.state["reply"] = "Zadatak: Izračunaj 1/2 + 1/2."
    body = client.post(CHAT_URL, json=prod_payload(
        selected_topic="", selected_oblast="")).get_json()
    assert body.get("engine") != "minimal"       # legacy handled it


def test_non_practice_mode_still_falls_back(client, fake_openai):
    fake_openai.state["reply"] = "Objašnjenje."
    body = client.post(CHAT_URL, json=prod_payload(mode="explain")).get_json()
    assert body.get("engine") != "minimal"


# =========================================================================== #
# 5. Raw student message integrity                                            #
# =========================================================================== #
def test_raw_student_message_is_logged_verbatim(client, sheets):
    client.post(CHAT_URL, json=prod_payload())
    payload, _response = sheets[-1]
    from matbot.sheets_log import _raw_student_message
    assert _raw_student_message(payload) == PROD_MESSAGE


def test_internal_rewrites_are_stored_separately(client):
    """A rewritten instruction must never overwrite the student column."""
    from matbot.sheets_log import _internal_instruction, _raw_student_message
    payload = {"raw_student_message": PROD_MESSAGE,
               "student_message": "INTERNAL: daj hint za zadatak X"}
    assert _raw_student_message(payload) == PROD_MESSAGE
    assert _internal_instruction(payload) == "INTERNAL: daj hint za zadatak X"


def test_no_rewrite_means_empty_internal_column():
    from matbot.sheets_log import _internal_instruction
    payload = {"raw_student_message": PROD_MESSAGE,
               "student_message": PROD_MESSAGE}
    assert _internal_instruction(payload) == ""


def test_sheets_columns_are_append_only():
    """Existing indices must not shift — old rows still parse."""
    from matbot.sheets_log import SHEET_HEADERS
    assert SHEET_HEADERS.index("student_message") == 16
    assert SHEET_HEADERS.index("engine_canary") == 59
    assert SHEET_HEADERS[-2:] == ["internal_instruction", "minimal_routing"]


def test_one_sheets_row_per_turn(client, sheets):
    for msg in (PROD_MESSAGE, "5/20", "ne znam"):
        before = len(sheets)
        client.post(CHAT_URL, json=prod_payload(student_message=msg))
        assert len(sheets) == before + 1, msg


# =========================================================================== #
# 6. Flag-off parity                                                          #
# =========================================================================== #
def test_flag_off_restores_the_legacy_path(client, fake_openai, monkeypatch):
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    fake_openai.state["reply"] = "Zadatak: LEGACY 1/2 + 1/2."
    body = client.post(CHAT_URL, json=prod_payload()).get_json()
    assert body.get("engine") != "minimal"
    assert "minimal_routing" not in body
