"""Testovi za POST /api/ai-tutor/chat (Phase 3).

OpenAI se mockira postojećim ``fake_openai`` fixtureom (monkeypatch na
``app._openai_chat``). Testovi za ne-ready statuse NAMJERNO ne koriste
``fake_openai`` — ako bi endpoint pogrešno pozvao OpenAI, ``_isolate`` fixture bi
podigao AssertionError i test bi pao. Dakle: nikad stvaran API/mrežni poziv.
"""
import pytest

from matbot import content_loader as cl

CHAT_URL = "/api/ai-tutor/chat"


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    """Phase 5: svaki test u ovom modulu loguje u svoj tmp SQLite (ne u repo storage/)."""
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield tmp_path / "activity.sqlite3"


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


@pytest.fixture(scope="module")
def composite_payload(tmap):
    """Stvaran kompozitni payload iz MAP-a (robusno na izmjene sadržaja)."""
    row = next(
        l for l in tmap["lessons"]
        if l["topic"] and l["course_name"] and l["section_name"]
        and l["lesson_order"] and l["lesson_title"]
    )
    return {
        "entry_source": "thinkific_lesson",
        "course_name": row["course_name"],
        "section_name": row["section_name"],
        "lesson_order": row["lesson_order"],
        "lesson_title": row["lesson_title"],
        "mode": "explain",
    }, row["topic"]


@pytest.fixture(scope="module")
def ambiguous_title(tmap):
    by_title: dict[str, set] = {}
    for l in tmap["lessons"]:
        by_title.setdefault(l["lesson_title"], set()).add(l["topic"])
    titles = [t for t, topics in by_title.items() if len(topics) > 1]
    assert titles, "očekivan bar jedan dvosmislen naslov u MAP-u"
    return titles[0]


# --- 1: thinkific_lesson composite → 200 + final_topic --------------------------

def test_composite_thinkific_returns_final_topic(client, fake_openai, composite_payload):
    payload, expected_topic = composite_payload
    resp = client.post(CHAT_URL, json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == expected_topic
    assert body["answer"] == fake_openai.state["reply"]
    assert body["entry_source_used"] == "thinkific_lesson"


# --- 2: selected_topic → 200 + final_topic --------------------------------------

def test_selected_topic_returns_final_topic(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod", "mode": "explain"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "skupovi_uvod"
    assert body["answer"] == fake_openai.state["reply"]
    # recommended_mode: explain → practice
    assert body["recommended_mode"] == "practice"
    # skupovi_uvod ima when_to_recommend_video → recommend_video True
    assert body["recommend_video"] is True


# --- 3: unknown → fallback, bez pada, bez OpenAI --------------------------------

def test_unknown_topic_returns_fallback(client):
    resp = client.post(CHAT_URL, json={"student_message": "trebam pomoć"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "fallback"
    assert body["final_topic"] == "unknown"
    assert body["answer"]  # neprazan bosanski fallback
    assert "oblast" in body["answer"].lower()


# --- 4: ambiguous → status ambiguous + traži izbor ------------------------------

def test_ambiguous_lesson_title(client, ambiguous_title):
    resp = client.post(CHAT_URL, json={"lesson_title": ambiguous_title})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ambiguous"
    assert body["final_topic"] == "unknown"
    low = body["answer"].lower()
    assert "izaberi" in low or "oblast" in low


# --- 5: invalid detected_topic → invalid, bez izmišljanja teme ------------------

def test_invalid_detected_topic(client):
    resp = client.post(CHAT_URL, json={"detected_topic": "izmisljeno_xyz"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "invalid"
    assert body["final_topic"] == "unknown"


# --- 6: nevalidan mode → default explain ----------------------------------------

def test_invalid_mode_defaults_to_explain(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod", "mode": "blabla"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["mode"] == "explain"


# --- 7: conversation_history stiže do prompt buildera (i trimuje se na 5) --------

def test_conversation_history_passed_to_prompt(client, fake_openai):
    history = [{"role": "user", "content": f"MSG{i}"} for i in range(8)]
    resp = client.post(
        CHAT_URL,
        json={"selected_topic": "skupovi_uvod", "conversation_history": history},
    )
    assert resp.status_code == 200
    # provjeri šta je stvarno poslano modelu (zadnji mock poziv)
    sent_messages = fake_openai.calls.messages[-1]
    user_content = sent_messages[-1]["content"]
    assert "MSG7" in user_content and "MSG3" in user_content  # zadnjih 5
    assert "MSG2" not in user_content and "MSG0" not in user_content


# --- 8: student_id nije obavezan ------------------------------------------------

def test_student_id_not_required(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


# --- 9: forgiving parsing; 400 samo kada je tijelo zaista neispravno ------------

def test_non_json_body_returns_400(client):
    resp = client.post(CHAT_URL, data="ovo nije json", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_json"


def test_json_array_returns_400(client):
    resp = client.post(CHAT_URL, json=[1, 2, 3])
    assert resp.status_code == 400


def test_empty_object_is_forgiving_fallback(client):
    resp = client.post(CHAT_URL, json={})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "fallback"


# --- OPTIONS preflight ----------------------------------------------------------

def test_options_preflight(client):
    resp = client.open(CHAT_URL, method="OPTIONS")
    assert resp.status_code == 204


# --- Phase 5: activity logging ---------------------------------------------------

def test_chat_logs_ready_response(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    resp = client.post(CHAT_URL, json={
        "selected_topic": "skupovi_uvod",
        "entry_source": "manual_topic_choice",
        "session_id": "sess-log-1",
        "student_message": "OVO JE TAJNA PORUKA 12345",
    })
    assert resp.status_code == 200
    rows = al.get_recent_activity(session_id="sess-log-1", path=_tmp_activity_db)
    assert len(rows) == 1
    r = rows[0]
    assert r["event_type"] == "topic_selected"
    assert r["final_topic"] == "skupovi_uvod"
    assert r["status"] == "ready"
    assert r["session_id"] == "sess-log-1"
    assert r["student_id"] is None                       # student_id nije obavezan
    # u DB NEMA pune poruke niti AI odgovora
    raw = _tmp_activity_db.read_bytes()
    assert b"OVO JE TAJNA PORUKA 12345" not in raw
    assert fake_openai.state["reply"].encode("utf-8") not in raw


def test_chat_logs_fallback_response(client, _tmp_activity_db):
    from matbot import activity_log as al
    resp = client.post(CHAT_URL, json={
        "session_id": "sess-log-2",
        "student_message": "nepoznato pitanje",
    })
    assert resp.status_code == 200
    rows = al.get_recent_activity(session_id="sess-log-2", path=_tmp_activity_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "fallback"
    assert rows[0]["event_type"] == "ai_message"


def test_chat_logs_practice_answer_event(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    client.post(CHAT_URL, json={
        "selected_topic": "skupovi_uvod",
        "session_id": "sess-log-3",
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Da li je 2∈S?",
        "student_message": "da",
    })
    rows = al.get_recent_activity(session_id="sess-log-3", path=_tmp_activity_db)
    assert rows and rows[0]["event_type"] == "practice_answer"
    assert rows[0]["mode"] == "practice"


def test_chat_ok_when_logging_fails(client, fake_openai, monkeypatch):
    import matbot.ai_tutor_service as svc

    def _boom(*args, **kwargs):
        raise RuntimeError("baza nedostupna")

    monkeypatch.setattr(svc, "log_student_activity", _boom)
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"          # odgovor preživi pad logovanja


# --- response shape -------------------------------------------------------------

def test_response_has_all_fields(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod", "mode": "quick"})
    body = resp.get_json()
    for key in (
        "answer",
        "final_topic",
        "opened_lesson_topic",
        "effective_topic",
        "entry_source_used",
        "topic_conflict",
        "recommended_mode",
        "recommend_video",
        "parent_report_signal",
        "status",
        "mode",
    ):
        assert key in body
    # quick → recommended_mode explain
    assert body["recommended_mode"] == "explain"
