"""Testovi za POST /api/ai-tutor/chat (NPP schema, razredi 6–9).

OpenAI se mockira postojećim ``fake_openai`` fixtureom. Testovi za ne-ready
statuse NAMJERNO ne koriste ``fake_openai`` — ako bi endpoint pogrešno pozvao
OpenAI, ``_isolate`` fixture bi podigao AssertionError. Nikad stvaran API poziv.

NPP anchori:
  g6: 6-01-001 (Skupovi → Pojam skupa; ima video), 6-03-029 (…/ NZS — heuristika
      pouzdano pogađa preko skraćenice NZS).
  g7: 7-01-002 (Sabiranje i oduzimanje cijelih brojeva).
  g8: 8-01-001 (Stepeni), 8-06-055 (Algebarski razlomci → DOMENA), 8-04-025 (Pitagora).
"""
import pytest

from matbot import content_loader as cl

CHAT_URL = "/api/ai-tutor/chat"

TOPIC6 = "6-01-001"
TOPIC6_NZS = "6-03-029"
TOPIC7 = "7-01-002"
TOPIC8 = "8-01-001"
TOPIC8_ALG = "8-06-055"


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield tmp_path / "activity.sqlite3"


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content(grade=6)


@pytest.fixture(scope="module")
def master7():
    return cl.load_master_content(grade=7)


@pytest.fixture(scope="module")
def master8():
    return cl.load_master_content(grade=8)


# --- selected_topic → 200 + final_topic -----------------------------------------

def test_selected_topic_returns_final_topic(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": TOPIC6, "mode": "explain"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == TOPIC6
    assert body["answer"] == fake_openai.state["reply"]
    assert body["recommended_mode"] == "practice"
    # 6-01-001 ima video → explain mod nudi video → recommend_video True
    assert body["recommend_video"] is True


def test_thinkific_lesson_falls_through_to_selected(client, fake_openai):
    """NPP nema zasebnu Thinkific lesson mapu → lesson kontekst pada na selected."""
    resp = client.post(CHAT_URL, json={
        "entry_source": "thinkific_lesson",
        "lesson_title": "Bilo koja lekcija",
        "selected_topic": TOPIC6,
        "mode": "explain",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == TOPIC6


# --- unknown → fallback, bez pada, bez OpenAI -----------------------------------

def test_unknown_topic_returns_fallback(client):
    resp = client.post(CHAT_URL, json={"student_message": "trebam pomoć"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "fallback"
    assert body["final_topic"] == "unknown"
    assert body["answer"]
    assert "oblast" in body["answer"].lower()


# --- invalid detected_topic → invalid -------------------------------------------

def test_invalid_detected_topic(client):
    resp = client.post(CHAT_URL, json={"detected_topic": "izmisljeno_xyz"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "invalid"
    assert body["final_topic"] == "unknown"


# --- nevalidan mode → default explain -------------------------------------------

def test_invalid_mode_defaults_to_explain(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": TOPIC6, "mode": "blabla"})
    assert resp.status_code == 200
    assert resp.get_json()["mode"] == "explain"


# --- conversation_history kao role poruke ---------------------------------------

def test_conversation_history_passed_as_role_messages(client, fake_openai):
    history = [{"role": "user", "content": f"MSG{i}"} for i in range(8)]
    resp = client.post(CHAT_URL, json={"selected_topic": TOPIC6, "conversation_history": history})
    assert resp.status_code == 200
    sent = fake_openai.calls.messages[-1]
    assert sent[0]["role"] == "system"
    middle = sent[1:-1]
    assert [m["content"] for m in middle] == ["MSG3", "MSG4", "MSG5", "MSG6", "MSG7"]
    assert all(m["role"] == "user" for m in middle)
    final_user = sent[-1]["content"]
    assert "MSG7" not in final_user and "MSG3" not in final_user
    assert "PODACI O TEMI" in final_user


def test_history_roles_preserved_in_messages(client, fake_openai):
    history = [
        {"role": "user", "content": "Objasni mi skupove"},
        {"role": "assistant", "content": "Skup je cjelina objekata. Hoćeš primjer?"},
    ]
    client.post(CHAT_URL, json={"selected_topic": TOPIC6, "conversation_history": history})
    sent = fake_openai.calls.messages[-1]
    assert sent[1] == {"role": "user", "content": "Objasni mi skupove"}
    assert sent[2]["role"] == "assistant"
    assert "Hoćeš primjer?" in sent[2]["content"]


# --- student_id nije obavezan ---------------------------------------------------

def test_student_id_not_required(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": TOPIC6})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


# --- forgiving parsing ----------------------------------------------------------

def test_non_json_body_returns_400(client):
    resp = client.post(CHAT_URL, data="ovo nije json", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_json"


def test_json_array_returns_400(client):
    assert client.post(CHAT_URL, json=[1, 2, 3]).status_code == 400


def test_empty_object_is_forgiving_fallback(client):
    resp = client.post(CHAT_URL, json={})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "fallback"


def test_options_preflight(client):
    assert client.open(CHAT_URL, method="OPTIONS").status_code == 204


# --- Vježbajmo: detekcija teme (heuristika + LLM klasifikator) -------------------

def test_free_chat_heuristic_hit_ready_single_call(client, fake_openai, master):
    """Heuristika pogađa NPP temu (NZS) → ready; SAMO jedan OpenAI poziv."""
    resp = client.post(CHAT_URL, json={
        "entry_source": "free_chat",
        "student_message": "ne razumijem NZS",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == TOPIC6_NZS
    assert body["final_topic"] in master["topic_ids"]
    assert len(fake_openai.calls.messages) == 1


def test_free_chat_classifier_valid_topic_accepted(client, fake_openai, master):
    """Heuristika ne pogađa; LLM klasifikator (mock) vraća validan npp_topic_id."""
    fake_openai.state["reply"] = '{"detected_topic": "6-05-047"}'
    resp = client.post(CHAT_URL, json={"student_message": "Izračunaj 25 · 37"})
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "6-05-047"
    assert body["final_topic"] in master["topic_ids"]
    assert len(fake_openai.calls.messages) == 2      # klasifikator + odgovor


def test_free_chat_classifier_garbage_general_answer(client, fake_openai):
    """Klasifikator vrati smeće → unknown → opšti odgovor BEZ izmišljene teme."""
    resp = client.post(CHAT_URL, json={"student_message": "Izračunaj 25 · 37 - 4"})
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "unknown"
    assert len(fake_openai.calls.messages) == 2
    assert "PODACI O TEMI" not in fake_openai.calls.messages[-1][-1]["content"]


def test_classifier_not_called_when_topic_selected(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "selected_topic": TOPIC6,
        "student_message": "Izračunaj 25 · 37",
    })
    assert resp.get_json()["final_topic"] == TOPIC6
    assert len(fake_openai.calls.messages) == 1


def test_vague_free_chat_still_fallback(client):
    resp = client.post(CHAT_URL, json={"student_message": "Kako ovo"})
    body = resp.get_json()
    assert body["status"] == "fallback"
    assert body["final_topic"] == "unknown"


# --- grade 7 / 8 / 9: razred-uslovni kontekst -----------------------------------

def test_grade_7_selected_topic_ready_with_grade_7_context(client, fake_openai, master7):
    resp = client.post(CHAT_URL, json={"grade": 7, "selected_topic": TOPIC7, "mode": "explain"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == TOPIC7
    system_prompt = fake_openai.calls.messages[-1][0]["content"]
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "7. RAZRED" in system_prompt
    assert "DIDAKTIKA — 6. RAZRED" not in system_prompt
    assert master7["topics_by_id"][TOPIC7]["display_name"] in user_prompt


def test_grade_8_selected_topic_ready_with_grade_8_context(client, fake_openai, master8):
    resp = client.post(CHAT_URL, json={
        "grade": 8, "selected_topic": TOPIC8_ALG, "mode": "explain",
        "student_message": "Objasni mi domenu.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == TOPIC8_ALG
    system_prompt = fake_openai.calls.messages[-1][0]["content"]
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "8. RAZRED" in system_prompt
    assert "nazivnik ne smije biti nula" in system_prompt
    assert "Definiciono" in user_prompt


def test_grade_9_selected_topic_ready_with_grade_9_context(client, fake_openai, ):
    m9 = cl.load_master_content(grade=9)
    topic9 = m9["topics"][0]["topic"]
    resp = client.post(CHAT_URL, json={"grade": 9, "selected_topic": topic9, "mode": "explain"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == topic9
    assert "DIDAKTIKA — 9. RAZRED" in fake_openai.calls.messages[-1][0]["content"]


def test_grade_7_exam_oblast_ready(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "grade": 7, "mode": "exam", "selected_oblast": "Cijeli brojevi",
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "exam"
    assert body["final_topic"] == "unknown"
    assert "OBLAST KONTROLNOG: Cijeli brojevi" in fake_openai.calls.messages[-1][-1]["content"]


def test_grade_8_exam_oblast_ready(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "grade": 8, "mode": "exam", "selected_oblast": "Pitagorina teorema",
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    up = fake_openai.calls.messages[-1][-1]["content"]
    assert "OBLAST KONTROLNOG: Pitagorina teorema" in up
    assert "KONTROLNI IZ OBLASTI" in up


def test_grade_7_quick_mode_ready(client, fake_openai):
    resp = client.post(CHAT_URL, json={"grade": 7, "mode": "quick", "student_message": "-5 + 8"})
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "quick"
    assert body["answer"] == fake_openai.state["reply"]


def test_grade_8_quick_mode_context_free(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "grade": 8, "mode": "quick", "selected_topic": TOPIC8,
        "student_message": "Izračunaj (x+3)^2",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "quick"
    assert body["final_topic"] is None
    assert body["effective_topic"] is None
    assert body["recommend_video"] is False
    assert body["context_policy"] == "disabled_for_result_mode"
    assert body["debug"]["ignored_opened_lesson_topic"] == TOPIC8


# --- practice follow-up ---------------------------------------------------------

def test_nonstreaming_practice_response_includes_last_tutor_task(client, fake_openai):
    task = "Uporedi brojeve: 7 205 i 7 250. Koristi znakove <, > ili =."
    fake_openai.state["reply"] = task
    resp = client.post(CHAT_URL, json={
        "mode": "practice", "selected_topic": TOPIC6,
        "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["last_tutor_task"] == task


def test_practice_followup_keeps_exact_task(client, fake_openai):
    # "da" je valjan odgovor na da/ne zadatak (ne 'ne znam', koje ide u help)
    visible_task = "Da li je 2 element skupa S = {1,2,3}?"
    resp = client.post(CHAT_URL, json={
        "mode": "practice", "selected_topic": TOPIC6,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": visible_task, "student_message": "da",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "practice"
    up = fake_openai.calls.messages[-1][-1]["content"]
    assert f"The student is responding to this exact previous task: {visible_task}" in up
    # BUG 2 (2026-07-10): poslije tačnog odgovora tutor ODMAH daje novi zadatak.
    assert "ODMAH daj JEDAN novi zadatak" in up
    assert "Tipičan zadatak" not in up


def test_practice_stuck_twice_recommends_video(client, fake_openai):
    """F5: dva uzastopna 'ne znam' na practice zadatku → recommend_video True."""
    common = {
        "mode": "practice", "selected_topic": TOPIC6,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Da li je 2 element skupa S?",
        "student_message": "ne znam",
    }
    first = client.post(CHAT_URL, json=common).get_json()
    assert first["recommend_video"] is False
    ns = first["next_state"]
    assert ns["stuck_count"] == 1
    second = client.post(CHAT_URL, json={**common, "previous_next_state": ns}).get_json()
    assert second["next_state"]["stuck_count"] == 2
    assert second["recommend_video"] is True
    assert "UČENIK JE ZAPEO" in fake_openai.calls.messages[-1][-1]["content"]


def test_new_task_request_uses_fresh_practice_prompt(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "mode": "practice", "selected_topic": TOPIC6,
        "student_message": "Daj mi novi zadatak.",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    up = fake_openai.calls.messages[-1][-1]["content"]
    assert "MOD: VJEŽBAJ (practice)" in up
    assert "PROVJERA ODGOVORA" not in up


# --- exam bez teme --------------------------------------------------------------

def test_exam_no_topic_asks_oblast(client, master):
    resp = client.post(CHAT_URL, json={"mode": "exam", "student_message": "Sutra imam kontrolni"})
    body = resp.get_json()
    assert body["status"] == "fallback"
    assert "Iz koje oblasti je kontrolni?" in body["answer"]
    assert master["topics"][0]["oblast"] in body["answer"]


# --- caps / sigurnost -----------------------------------------------------------

def test_message_cap_4000(client, fake_openai):
    long_msg = "Izračunaj " + "X" * 8000
    client.post(CHAT_URL, json={"selected_topic": TOPIC6, "student_message": long_msg})
    sent = fake_openai.calls.messages[-1][-1]["content"]
    assert "X" * 3000 in sent
    assert "X" * 4001 not in sent


def test_history_caps(client, fake_openai):
    history = [{"role": "user", "content": f"H{i}" + "y" * 3000} for i in range(7)]
    client.post(CHAT_URL, json={"selected_topic": TOPIC6, "conversation_history": history})
    sent = fake_openai.calls.messages[-1]
    hist_contents = [m["content"] for m in sent[1:-1]]
    assert any(c.startswith("H6") for c in hist_contents)
    assert any(c.startswith("H2") for c in hist_contents)
    assert not any(c.startswith("H1") or c.startswith("H0") for c in hist_contents)
    for c in hist_contents:
        assert len(c) <= 1500


def test_500_does_not_leak_exception(client, monkeypatch):
    import app as app_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("SUPER TAJNA INTERNA GREŠKA 42")

    monkeypatch.setattr(app_mod.ai_tutor_service, "handle_chat", _boom)
    resp = client.post(CHAT_URL, json={"selected_topic": TOPIC6})
    assert resp.status_code == 500
    assert "SUPER TAJNA" not in resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "ai_tutor_failed"


# --- result mod (5-1) + slika ---------------------------------------------------

def test_quick_simple_expression_no_topic(client, fake_openai):
    resp = client.post(CHAT_URL, json={"mode": "quick", "student_message": "5-1"})
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["answer"] == fake_openai.state["reply"]
    assert body["final_topic"] is None
    system_sent = fake_openai.calls.messages[-1][0]["content"]
    assert "Samo rezultat" in system_sent
    assert "DIDAKTIKA — 6. RAZRED" not in system_sent
    assert "MODULARNA PRAVILA" not in system_sent
    assert "TERMINOLOGIJA I ZAPIS" in system_sent


import io as _io
import json as _json


def _multipart(payload: dict, filename="zadatak.png", content=b"fake-image-bytes"):
    return {"payload": _json.dumps(payload), "image": (_io.BytesIO(content), filename)}


def test_multipart_image_vision_path(client, fake_openai):
    resp = client.post(
        CHAT_URL, data=_multipart({"selected_topic": TOPIC6, "mode": "quick"}),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] is None
    content = fake_openai.calls.messages[-1][-1]["content"]
    assert isinstance(content, list)
    assert any(
        p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:")
        for p in content
    )
    assert "TERMINOLOGIJA I ZAPIS" in fake_openai.calls.messages[-1][0]["content"]


def test_multipart_rejects_non_image(client):
    resp = client.post(
        CHAT_URL, data=_multipart({"mode": "quick"}, filename="zadatak.txt"),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_image"


def test_multipart_bad_payload_json(client):
    resp = client.post(CHAT_URL, data={"payload": "ovo nije json"},
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_json"


def test_multipart_without_image_still_works(client, fake_openai):
    resp = client.post(CHAT_URL, data={"payload": _json.dumps({"selected_topic": TOPIC6})},
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


# --- activity logging -----------------------------------------------------------

def test_chat_logs_ready_response(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    resp = client.post(CHAT_URL, json={
        "selected_topic": TOPIC6, "entry_source": "manual_topic_choice",
        "session_id": "sess-log-1", "student_message": "OVO JE TAJNA PORUKA 12345",
    })
    assert resp.status_code == 200
    rows = al.get_recent_activity(session_id="sess-log-1", path=_tmp_activity_db)
    assert len(rows) == 1
    r = rows[0]
    assert r["event_type"] == "topic_selected"
    assert r["final_topic"] == TOPIC6
    assert r["status"] == "ready"
    assert r["student_id"] is None
    raw = _tmp_activity_db.read_bytes()
    assert b"OVO JE TAJNA PORUKA 12345" not in raw
    assert fake_openai.state["reply"].encode("utf-8") not in raw


def test_chat_logs_grade(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    client.post(CHAT_URL, json={
        "grade": 7, "selected_topic": TOPIC7,
        "entry_source": "manual_topic_choice", "session_id": "sess-log-grade-7",
    })
    rows = al.get_recent_activity(session_id="sess-log-grade-7", path=_tmp_activity_db)
    assert rows and rows[0]["grade"] == 7


def test_chat_logs_grade_8(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    client.post(CHAT_URL, json={
        "grade": 8, "selected_topic": TOPIC8,
        "entry_source": "manual_topic_choice", "session_id": "sess-log-grade-8",
    })
    rows = al.get_recent_activity(session_id="sess-log-grade-8", path=_tmp_activity_db)
    assert rows and rows[0]["grade"] == 8


def test_chat_logs_fallback_response(client, _tmp_activity_db):
    from matbot import activity_log as al
    client.post(CHAT_URL, json={"session_id": "sess-log-2", "student_message": "nepoznato pitanje"})
    rows = al.get_recent_activity(session_id="sess-log-2", path=_tmp_activity_db)
    assert rows and rows[0]["status"] == "fallback"
    assert rows[0]["event_type"] == "ai_message"


def test_chat_logs_practice_answer_event(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    client.post(CHAT_URL, json={
        "selected_topic": TOPIC6, "session_id": "sess-log-3",
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Da li je 2∈S?", "student_message": "da",
    })
    rows = al.get_recent_activity(session_id="sess-log-3", path=_tmp_activity_db)
    assert rows and rows[0]["event_type"] == "practice_answer"
    assert rows[0]["mode"] == "practice"


def test_chat_ok_when_logging_fails(client, fake_openai, monkeypatch):
    import matbot.ai_tutor_service as svc

    def _boom(*args, **kwargs):
        raise RuntimeError("baza nedostupna")

    monkeypatch.setattr(svc, "log_student_activity", _boom)
    resp = client.post(CHAT_URL, json={"selected_topic": TOPIC6})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


def test_chat_ok_when_sheets_logging_fails(client, fake_openai, monkeypatch):
    import matbot.ai_tutor_service as svc

    def _boom(*args, **kwargs):
        raise RuntimeError("sheets nedostupan")

    monkeypatch.setattr(svc, "log_transcript_to_sheet", _boom)
    resp = client.post(CHAT_URL, json={"selected_topic": TOPIC6})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


def test_feedback_endpoint_logs_vote(client, _tmp_activity_db):
    from matbot import activity_log as al
    resp = client.post("/api/ai-tutor/feedback", json={
        "session_id": "sess-fb-1",
        "message_index": 2,
        "verdict": "up",
        "mode": "practice",
        "topic": TOPIC6,
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    rows = al.get_recent_feedback(session_id="sess-fb-1", path=_tmp_activity_db)
    assert len(rows) == 1
    assert rows[0]["message_index"] == 2
    assert rows[0]["verdict"] == "up"
    assert rows[0]["mode"] == "practice"


def test_feedback_endpoint_attempts_sheets_log(client, _tmp_activity_db, monkeypatch):
    import app as matbot

    seen = {}

    def _fake(payload):
        seen.update(payload)
        return True

    monkeypatch.setattr(matbot, "log_feedback_to_sheet", _fake)
    resp = client.post("/api/ai-tutor/feedback", json={
        "session_id": "sess-fb-sheet",
        "message_index": 7,
        "verdict": "down",
        "mode": "exam",
        "topic": TOPIC6,
    })
    assert resp.status_code == 200
    assert resp.get_json()["sheets_logged"] is True
    assert seen == {
        "session_id": "sess-fb-sheet",
        "message_index": 7,
        "verdict": "down",
        "mode": "exam",
        "topic": TOPIC6,
    }


def test_feedback_endpoint_validates_payload(client):
    resp = client.post("/api/ai-tutor/feedback", json={
        "session_id": "sess-fb-bad",
        "message_index": 0,
        "verdict": "maybe",
    })
    assert resp.status_code == 400


# --- response shape -------------------------------------------------------------

def test_response_has_all_fields(client, fake_openai):
    body = client.post(CHAT_URL, json={"selected_topic": TOPIC6, "mode": "quick"}).get_json()
    for key in (
        "answer", "final_topic", "opened_lesson_topic", "effective_topic",
        "entry_source_used", "topic_conflict", "recommended_mode", "recommend_video",
        "video_title", "video_url", "parent_report_signal", "status", "mode",
        "answer_verdict",
    ):
        assert key in body
    assert body["recommended_mode"] == "explain"


# --- exam za CIJELU OBLAST -------------------------------------------------------

@pytest.fixture(scope="module")
def oblast_name(master):
    return master["topics_by_id"][TOPIC6]["oblast"]


def test_exam_by_oblast_returns_ready(client, fake_openai, oblast_name):
    resp = client.post(CHAT_URL, json={
        "mode": "exam", "selected_oblast": oblast_name,
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "exam"
    assert body["final_topic"] == "unknown"
    assert body["answer"] == fake_openai.state["reply"]
    assert len(fake_openai.calls.messages) == 1
    up = fake_openai.calls.messages[0][-1]["content"]
    assert "OBLAST KONTROLNOG" in up
    assert "KONTROLNI IZ OBLASTI" in up


def test_exam_by_unknown_oblast_falls_back_without_openai(client):
    resp = client.post(CHAT_URL, json={
        "mode": "exam", "selected_oblast": "nepostojeca_oblast_xyz",
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    body = resp.get_json()
    assert body["status"] == "fallback"
    assert body["final_topic"] == "unknown"
    assert "Iz koje oblasti je kontrolni?" in body["answer"]


def test_exam_with_topic_still_topic_based(client, fake_openai, oblast_name):
    resp = client.post(CHAT_URL, json={
        "mode": "exam", "selected_topic": TOPIC6, "selected_oblast": oblast_name,
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == TOPIC6


def test_exam_by_oblast_logged(client, fake_openai, oblast_name, _tmp_activity_db):
    from matbot import activity_log as al
    client.post(CHAT_URL, json={
        "mode": "exam", "selected_oblast": oblast_name, "session_id": "sess-log-oblast",
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    rows = al.get_recent_activity(session_id="sess-log-oblast", path=_tmp_activity_db)
    assert rows and rows[0]["event_type"] == "exam_mode_used"
    assert rows[0]["status"] == "ready"


# --- nastavak razgovora (continuing_explanation) --------------------------------

def test_continuation_vague_message_not_fallback(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "mode": "explain", "student_message": "može",
        "interaction_phase": "continuing_explanation",
        "last_tutor_message": "NZS je najmanji zajednički sadržilac. Hoćeš primjer?",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "unknown"
    assert body["answer"] == fake_openai.state["reply"]
    assert len(fake_openai.calls.messages) == 1
    up = fake_openai.calls.messages[0][-1]["content"]
    assert "NASTAVAK RAZGOVORA" in up
    assert "Hoćeš primjer?" in up


def test_continuation_with_topic_uses_continuation_block(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "selected_topic": TOPIC6, "mode": "explain", "student_message": "nastavi",
        "interaction_phase": "continuing_explanation",
        "last_tutor_message": "Hoćeš da zajedno riješimo primjer?",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == TOPIC6
    up = fake_openai.calls.messages[-1][-1]["content"]
    assert "NASTAVAK RAZGOVORA" in up
    assert "MOD: OBJASNI" not in up


def test_continuation_without_message_still_fallback(client):
    resp = client.post(CHAT_URL, json={
        "interaction_phase": "continuing_explanation",
        "last_tutor_message": "Hoćeš primjer?",
    })
    assert resp.get_json()["status"] == "fallback"


def test_last_tutor_message_capped(client, fake_openai):
    client.post(CHAT_URL, json={
        "selected_topic": TOPIC6, "student_message": "može",
        "interaction_phase": "continuing_explanation", "last_tutor_message": "Y" * 5000,
    })
    sent = fake_openai.calls.messages[-1][-1]["content"]
    assert "Y" * 600 in sent
    assert "Y" * 1001 not in sent
