"""Phase 2 (audit) — SSE streaming za tutor (/api/ai-tutor/chat/stream).

Stream OpenAI poziv se mockira monkeypatchom na ``app._tutor_openai_chat_stream``
(nikad stvarni API); pomoćni ne-streaming pozivi (LLM klasifikator) idu kroz
postojeći ``fake_openai`` fixture.
"""
import json

import pytest

import app as app_mod

STREAM_URL = "/api/ai-tutor/chat/stream"


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield tmp_path / "activity.sqlite3"


def _parse_sse(text):
    """Parsiraj SSE tijelo u listu (event, data_dict)."""
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name, data = "message", None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        events.append((name, data))
    return events


@pytest.fixture()
def fake_stream(monkeypatch):
    """Mock streaming OpenAI: vraća zadane delte; broji pozive; može bacati."""
    state = {"deltas": ["Zdravo ", "svijete!"], "raise_after": None, "calls": 0,
             "models": [], "messages": []}

    def _fake(model, messages, timeout=None, max_tokens=None):
        state["calls"] += 1
        state["models"].append(model)
        state["messages"].append(messages)
        for i, d in enumerate(state["deltas"]):
            if state["raise_after"] is not None and i >= state["raise_after"]:
                raise RuntimeError("stream pao")
            yield d

    monkeypatch.setattr(app_mod, "_tutor_openai_chat_stream", _fake)
    return state


# --- happy path ---------------------------------------------------------------------

def test_stream_ready_deltas_and_done(client, fake_openai, fake_stream):
    resp = client.post(STREAM_URL, json={"selected_topic": "skupovi_uvod",
                                         "mode": "explain",
                                         "student_message": "Objasni mi ovu temu."})
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    events = _parse_sse(resp.get_data(as_text=True))
    deltas = [d["delta"] for name, d in events if name == "delta"]
    assert "".join(deltas) == "Zdravo svijete!"
    done = [d for name, d in events if name == "done"]
    assert len(done) == 1
    body = done[0]
    # done nosi ISTI oblik kao non-streaming odgovor
    assert body["answer"] == "Zdravo svijete!"
    assert body["final_topic"] == "skupovi_uvod"
    assert body["status"] == "ready"
    for key in ("recommended_mode", "recommend_video", "mode", "effective_topic"):
        assert key in body
    assert fake_stream["calls"] == 1


def test_streamed_practice_response_includes_last_tutor_task(client, fake_openai, fake_stream):
    task = "Uporedi brojeve: 7 205 i 7 250. Koji je veći broj? Koristi znakove <, > ili =."
    fake_stream["deltas"] = [task[:35], task[35:]]
    resp = client.post(STREAM_URL, json={
        "selected_topic": "n_n0_uporedjivanje_poluprava_prethodnik_sljedbenik",
        "mode": "practice",
        "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
    })
    assert resp.status_code == 200
    events = _parse_sse(resp.get_data(as_text=True))
    done = [d for name, d in events if name == "done"]
    assert done and done[0]["answer"] == task
    assert done[0]["last_tutor_task"] == task


def test_stream_messages_include_system_history_user(client, fake_openai, fake_stream):
    history = [{"role": "user", "content": "HISTORIJA-1"},
               {"role": "assistant", "content": "ODGOVOR-1"}]
    client.post(STREAM_URL, json={"selected_topic": "skupovi_uvod",
                                  "conversation_history": history,
                                  "student_message": "nastavimo"})
    sent = fake_stream["messages"][-1]
    assert sent[0]["role"] == "system"
    assert sent[1] == {"role": "user", "content": "HISTORIJA-1"}
    assert sent[2]["role"] == "assistant"
    assert sent[-1]["role"] == "user"


# --- fallback (bez OpenAI streama) ----------------------------------------------------

def test_stream_fallback_single_done_no_model_call(client, fake_stream):
    """Vague poruka bez teme → deterministički fallback kao JEDAN done event,
    stream model se NE zove (bez fake_openai — _isolate čuva od pravog poziva)."""
    resp = client.post(STREAM_URL, json={"student_message": "Kako ovo"})
    assert resp.status_code == 200
    events = _parse_sse(resp.get_data(as_text=True))
    assert [name for name, _ in events] == ["done"]
    body = events[0][1]
    assert body["status"] == "fallback"
    assert body["answer"]
    assert fake_stream["calls"] == 0


# --- greške ---------------------------------------------------------------------------

def test_stream_error_before_any_text(client, fake_openai, fake_stream):
    fake_stream["raise_after"] = 0            # pukne prije prve delte
    resp = client.post(STREAM_URL, json={"selected_topic": "skupovi_uvod"})
    events = _parse_sse(resp.get_data(as_text=True))
    assert [name for name, _ in events] == ["error"]
    assert "Pokušaj ponovo" in events[0][1]["detail"]


def test_stream_partial_then_done_with_partial_answer(client, fake_openai, fake_stream):
    fake_stream["deltas"] = ["Pola ", "odgovora", " nikad ne stigne"]
    fake_stream["raise_after"] = 2            # pukne poslije 2 delte
    resp = client.post(STREAM_URL, json={"selected_topic": "skupovi_uvod"})
    events = _parse_sse(resp.get_data(as_text=True))
    names = [name for name, _ in events]
    assert names == ["delta", "delta", "done"]
    assert events[-1][1]["answer"] == "Pola odgovora"


def test_stream_empty_answer_friendly_fallback(client, fake_openai, fake_stream):
    fake_stream["deltas"] = []
    resp = client.post(STREAM_URL, json={"selected_topic": "skupovi_uvod"})
    events = _parse_sse(resp.get_data(as_text=True))
    done = [d for name, d in events if name == "done"]
    assert done and "Pokušaj ponovo" in done[0]["answer"]


def test_stream_invalid_json_400(client):
    resp = client.post(STREAM_URL, data="nije json", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_json"


# --- ostalo ---------------------------------------------------------------------------

def test_stream_logs_activity(client, fake_openai, fake_stream, _tmp_activity_db):
    from matbot import activity_log as al
    resp = client.post(STREAM_URL, json={"selected_topic": "skupovi_uvod",
                                         "session_id": "sess-stream-1"})
    resp.get_data()          # stream je lijen — konzumiraj tijelo da generator prođe
    rows = al.get_recent_activity(session_id="sess-stream-1", path=_tmp_activity_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "ready"


def test_stream_options_preflight(client):
    assert client.open(STREAM_URL, method="OPTIONS").status_code == 204


def test_nonstreaming_endpoint_untouched(client, fake_openai):
    """Non-streaming put ostaje netaknut (fallback za slike i starije klijente)."""
    resp = client.post("/api/ai-tutor/chat", json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 200
    assert resp.get_json()["answer"] == fake_openai.state["reply"]
