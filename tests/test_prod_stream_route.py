# -*- coding: utf-8 -*-
"""Production incident 2026-07-21 11:38, commit d89468c, MATBOT_MINIMAL_ENGINE=on.

  11:38:30Z  grade=6 practice topic=12880 "Daj mi jedan zadatak za vježbu iz ove teme."
             → "Zadatak: Rijesi jednacinu: 3x + 2 = 14."
             minimal_routing EMPTY, next_state LEGACY, task_validation expects x=4
  11:38:37Z  user typed "ne znam"
             → mode became "explain", student_message overwritten with
               "ADAPTIVNI_HINT_NIVO=1 ...", legacy hint_history used

ROOT CAUSE: ``templates/index.html`` calls ``/api/ai-tutor/chat/stream`` FIRST
(``streamTutorRequest``) and only falls back to ``/api/ai-tutor/chat`` when the
stream fails before any text. The minimal dispatch existed only in
``handle_chat``; ``handle_chat_stream`` had no minimal branch at all, so a real
browser turn never reached the minimal engine.

These tests drive the STREAMING route — the one the browser actually uses.
Unit-calling ``handle_chat`` would have passed throughout the incident.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import topic_resolver as tr

STREAM_URL = "/api/ai-tutor/chat/stream"
JSON_URL = "/api/ai-tutor/chat"

PROD_TOPIC = "12880"
PROD_MESSAGE = "Daj mi jedan zadatak za vježbu iz ove teme."
CANONICAL_NPP = "6-04-035"
EXPECTED_SKILL = "fraction_expand"


def prod_payload(**overrides):
    payload = {
        "session_id": "prod-1138",
        "grade": 6,
        "mode": "practice",
        "session_mode": "practice",
        "entry_source": "manual_topic_choice",
        "selected_topic": PROD_TOPIC,
        "selected_oblast": "Razlomci",
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
def fake_stream(monkeypatch):
    """Mock the STREAMING model call (legacy path only; minimal never streams)."""
    import app as app_mod
    state = {"deltas": ["Zadatak: ", "LEGACY 1/2 + 1/2."], "calls": 0}

    def _fake(model, messages, timeout=None, max_tokens=None):
        state["calls"] += 1
        for d in state["deltas"]:
            yield d

    monkeypatch.setattr(app_mod, "_tutor_openai_chat_stream", _fake)
    return state


@pytest.fixture()
def sheets(monkeypatch):
    rows = []
    monkeypatch.setattr(svc, "log_transcript_to_sheet",
                        lambda payload, response: rows.append((payload, response)))
    return rows


def sse_post(client, payload):
    """POST to the streaming route and parse the SSE stream like the browser."""
    resp = client.post(STREAM_URL, json=payload)
    assert resp.status_code == 200, resp.data
    text = resp.get_data(as_text=True)
    events, event_name = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            raw = line.split(":", 1)[1].strip()
            try:
                events.append((event_name, json.loads(raw)))
            except ValueError:
                events.append((event_name, raw))
    return events


def done_payload(events):
    for name, data in events:
        if name == "done":
            return data
    raise AssertionError(f"no done event in {events!r}")


def deltas(events):
    return "".join(d.get("delta", "") for n, d in events
                   if n == "delta" and isinstance(d, dict))


# =========================================================================== #
# The route the browser actually uses                                         #
# =========================================================================== #
def test_browser_uses_the_streaming_route_first():
    """Pin the routing fact this incident turned on."""
    html = open("templates/index.html", encoding="utf-8").read()
    assert "'/api/ai-tutor/chat/stream'" in html
    stream_at = html.index("streamTutorRequest(payload, ac)")
    json_at = html.index("jsonTutorRequest(payload, ac, imgFile)")
    assert stream_at < json_at, "stream is attempted before the JSON fallback"


def test_stream_route_dispatches_to_the_minimal_engine(client, sheets):
    """The exact 11:38:30Z turn, over the STREAMING route."""
    body = done_payload(sse_post(client, prod_payload()))
    assert body["engine"] == "minimal"
    assert body["minimal_routing"]["handled"] is True
    assert body["minimal_routing"]["decline_reason"] == ""
    assert body["minimal_routing"]["runtime_topic"] == PROD_TOPIC
    assert body["minimal_routing"]["canonical_topic"] == CANONICAL_NPP
    assert body["minimal_routing"]["resolved_skill"] == EXPECTED_SKILL


def test_stream_route_returns_a_fraction_expand_task(client):
    body = done_payload(sse_post(client, prod_payload()))
    task = body["last_tutor_task"]
    assert task.lower().startswith("proširi"), task
    for banned in ("jednačin", "jednacin", "3x", "= 14"):
        assert banned not in task.lower(), task
    assert body["next_state"]["task"]["skill_id"] == EXPECTED_SKILL


def test_stream_route_does_not_use_the_model_to_invent_the_task(client, fake_openai):
    fake_openai.state["reply"] = "Zadatak: Riješi jednačinu: 3x + 2 = 14."
    body = done_payload(sse_post(client, prod_payload()))
    assert body["engine"] == "minimal"
    assert fake_openai.calls.messages == []          # model never consulted
    assert "3x" not in body["last_tutor_task"]


def test_stream_route_emits_the_answer_as_deltas(client):
    events = sse_post(client, prod_payload())
    body = done_payload(events)
    assert deltas(events).strip() == (body["answer"] or "").strip()


def test_stream_next_state_is_minimal_not_legacy(client):
    body = done_payload(sse_post(client, prod_payload()))
    ns = body["next_state"]
    assert ns["engine"] == "minimal"
    assert isinstance(ns.get("minimal_state"), dict)
    for legacy_field in ("task_validation", "pending_action", "hint_history"):
        assert legacy_field not in ns, legacy_field


# =========================================================================== #
# The 11:38:37Z hint turn                                                     #
# =========================================================================== #
def test_ne_znam_stays_practice_and_keeps_the_task(client, sheets):
    """mode must NOT become explain; the task and its id must survive."""
    first = done_payload(sse_post(client, prod_payload()))
    question = first["last_tutor_task"]
    task_id = first["next_state"]["task_id"]

    second = done_payload(sse_post(client, prod_payload(
        student_message="ne znam",
        interaction_phase="answering_practice_task",
        last_tutor_task=question,
        previous_next_state=first["next_state"],
    )))
    assert second["engine"] == "minimal"
    assert second["mode"] == "practice"
    assert second["session_mode"] == "practice"      # never "explain"
    assert second["last_tutor_task"] == question     # task preserved
    assert second["next_state"]["task_id"] == task_id
    assert second["next_state"]["hint_count"] == 1


def test_ne_znam_is_not_rewritten_to_an_adaptive_hint_instruction(client, sheets):
    first = done_payload(sse_post(client, prod_payload()))
    sse_post(client, prod_payload(
        student_message="ne znam",
        interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"],
    ))
    payload, _response = sheets[-1]
    from matbot.sheets_log import _internal_instruction, _raw_student_message
    assert _raw_student_message(payload) == "ne znam"
    assert "ADAPTIVNI_HINT_NIVO" not in json.dumps(payload, ensure_ascii=False,
                                                   default=str)
    assert _internal_instruction(payload) == ""


def test_ne_znam_gets_a_minimal_hint_not_legacy_hint_history(client):
    first = done_payload(sse_post(client, prod_payload()))
    second = done_payload(sse_post(client, prod_payload(
        student_message="ne znam",
        interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"],
    )))
    assert "hint_history" not in second["next_state"]
    answer = (second["answer"] or "").lower()
    assert "proširivanje" in answer or "nazivnik" in answer   # topic-specific
    # the hint must never reveal the answer
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(first["last_tutor_task"])
    expected = getattr(exp, "expected_display", "") or _fmt_expected(exp)
    assert expected.lower() not in answer


def test_full_stream_session_keeps_one_task_id(client):
    first = done_payload(sse_post(client, prod_payload()))
    question, tid = first["last_tutor_task"], first["next_state"]["task_id"]
    state = first["next_state"]
    for msg in ("999/999", "ne znam", "pomozi"):
        body = done_payload(sse_post(client, prod_payload(
            student_message=msg,
            interaction_phase="answering_practice_task",
            last_tutor_task=question, previous_next_state=state)))
        state = body["next_state"]
        assert body["last_tutor_task"] == question, msg
        assert state["task_id"] == tid, msg
        assert body["session_mode"] == "practice", msg


# =========================================================================== #
# Both transports must behave identically                                     #
# =========================================================================== #
def test_json_and_stream_routes_agree(client):
    stream_body = done_payload(sse_post(client, prod_payload()))
    json_body = client.post(JSON_URL, json=prod_payload()).get_json()
    for field in ("engine", "mode", "session_mode", "status"):
        assert stream_body[field] == json_body[field], field
    assert stream_body["minimal_routing"]["resolved_skill"] == \
        json_body["minimal_routing"]["resolved_skill"]


def test_raw_message_is_pinned_at_the_outermost_endpoint(client):
    """capture happens in the route, before any handler can rewrite it."""
    payload = prod_payload()
    assert "raw_student_message" not in payload
    captured = svc.capture_raw_student_message(payload)
    assert captured["raw_student_message"] == PROD_MESSAGE
    # idempotent: never overwritten by a later capture
    captured["student_message"] = "REWRITTEN"
    assert svc.capture_raw_student_message(captured)["raw_student_message"] == \
        PROD_MESSAGE


def test_one_sheets_row_per_stream_turn(client, sheets):
    for msg in (PROD_MESSAGE, "ne znam", "5/20"):
        before = len(sheets)
        sse_post(client, prod_payload(student_message=msg))
        assert len(sheets) == before + 1, msg


# =========================================================================== #
# Dispatch ordering and flag isolation                                        #
# =========================================================================== #
def test_minimal_dispatch_runs_before_any_legacy_preprocessing():
    """Structural: in BOTH entry points the dispatch precedes prep/exam/legacy."""
    import inspect
    for func in (svc.handle_chat, svc.handle_chat_stream):
        src = inspect.getsource(func)
        assert "minimal_dispatch(" in src, func.__name__
        dispatch_at = src.index("minimal_dispatch(")
        for later in ("_prepare_chat(", "_exam_engine_should_handle("):
            if later in src:
                assert dispatch_at < src.index(later), (func.__name__, later)


def test_stream_flag_off_restores_legacy(client, fake_openai, fake_stream,
                                         monkeypatch):
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    fake_openai.state["reply"] = "Zadatak: LEGACY 1/2 + 1/2."
    body = done_payload(sse_post(client, prod_payload()))
    assert body.get("engine") != "minimal"
    assert "minimal_routing" not in body
    assert fake_stream["calls"] >= 1                 # legacy streamed as before


def test_stream_unresolvable_topic_never_falls_through(client, fake_openai):
    fake_openai.state["reply"] = "Zadatak: Riješi jednačinu: 3x + 2 = 14."
    body = done_payload(sse_post(client, prod_payload(selected_topic="999999")))
    assert body["engine"] == "minimal"
    assert body["last_tutor_task"] == ""
    assert body["minimal_routing"]["decline_reason"] == "unresolved_runtime_topic"
    assert fake_openai.calls.messages == []


def test_stream_non_practice_still_falls_back(client, fake_openai, fake_stream):
    fake_openai.state["reply"] = "Objašnjenje."
    body = done_payload(sse_post(client, prod_payload(mode="explain")))
    assert body.get("engine") != "minimal"
