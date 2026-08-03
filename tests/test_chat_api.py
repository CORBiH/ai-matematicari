"""Testovi API sloja: /chat, /chat/stream, /feedback + regresija postojećih ruta."""
import json

from tests.conftest import make_output, make_task


def chat_payload(msg="Daj mi jedan zadatak za vježbu iz ove teme.", mode="practice", **kw):
    base = {
        "session_id": "api-sess",
        "client_turn_id": "turn-1",
        "grade": 6,
        "mode": mode,
        "entry_source": "manual_topic_choice",
        "selected_topic": "6-01-006",
        "selected_oblast": "",
        "student_message": msg,
        "conversation_history": [],
    }
    base.update(kw)
    return base


def test_chat_json_payload_works(client, fake_llm):
    fake_llm.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    r = client.post("/api/ai-tutor/chat", json=chat_payload())
    assert r.status_code == 200
    j = r.get_json()
    assert j["status"] == "ready"
    assert j["session_mode"] == "practice"
    assert j["last_tutor_task"]
    assert fake_llm.call_count == 1   # stabilan jednopozivni put


def test_multipart_without_image_processed(client, fake_llm):
    fake_llm.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    r = client.post(
        "/api/ai-tutor/chat",
        data={"payload": json.dumps(chat_payload())},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.get_json()["status"] == "ready"
    assert fake_llm.call_count == 1   # stabilan jednopozivni put


def test_multipart_with_image_controlled_no_llm(client, fake_llm):
    import io

    r = client.post(
        "/api/ai-tutor/chat",
        data={
            "payload": json.dumps(chat_payload()),
            "image": (io.BytesIO(b"fake-image-bytes"), "zadatak.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    j = r.get_json()
    assert "Slike" in j["answer"]
    assert "next_state" not in j          # stanje netaknuto
    assert j["last_tutor_task"] == ""
    assert fake_llm.call_count == 0


def test_non_ai_modes_do_not_call_llm(client, fake_llm):
    # explain i quick su STVARNI AI modovi (imaju svoje testove u
    # tests/test_explain.py i tests/test_quick.py) — ovdje ostaju samo
    # modovi bez AI poziva.
    for mode in ("exam",):
        r = client.post("/api/ai-tutor/chat", json=chat_payload(mode=mode))
        assert r.status_code == 200
        j = r.get_json()
        assert j["status"] == "ready"
        assert j["session_mode"] == mode
        assert j["last_tutor_task"] == ""
        assert "Vježbaj sa mnom" in j["answer"]
    assert fake_llm.call_count == 0


def test_sse_stream_returns_valid_done_event(client, fake_llm):
    fake_llm.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    r = client.post("/api/ai-tutor/chat/stream", json=chat_payload())
    assert r.status_code == 200
    assert r.content_type.startswith("text/event-stream")
    body = r.get_data(as_text=True)
    assert body.startswith("event: done\ndata: ")
    assert body.endswith("\n\n")
    data = json.loads(body.split("data: ", 1)[1].strip())
    assert data["status"] == "ready"
    assert data["answer"]
    assert "last_tutor_task" in data
    assert fake_llm.call_count == 1   # stabilan jednopozivni put


def test_last_tutor_task_always_present(client, fake_llm):
    # normalan turn
    fake_llm.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    j1 = client.post("/api/ai-tutor/chat", json=chat_payload()).get_json()
    assert "last_tutor_task" in j1
    # ne-practice mod
    j2 = client.post("/api/ai-tutor/chat", json=chat_payload(mode="exam")).get_json()
    assert j2["last_tutor_task"] == ""
    # greška LLM-a
    from matbot.llm import LLMTimeout
    fake_llm.queue(LLMTimeout("t"))
    j3 = client.post("/api/ai-tutor/chat", json=chat_payload(msg="5/8")).get_json()
    assert "last_tutor_task" in j3
    # prazna poruka
    j4 = client.post("/api/ai-tutor/chat", json=chat_payload(msg="")).get_json()
    assert "last_tutor_task" in j4


def test_next_state_never_exposes_expected_answer(client, fake_llm, store):
    fake_llm.queue(make_output(reply="Evo zadatka.",
                               new_task=make_task(expected="TAJNO-RJESENJE-5/8")))
    r = client.post("/api/ai-tutor/chat", json=chat_payload())
    raw = r.get_data(as_text=True)
    assert "TAJNO-RJESENJE" not in raw
    assert "expected_answer" not in raw
    # ...a server ga interno IMA (samo na serveru)
    assert store.peek("api-sess")["expected_answer_summary"] == "TAJNO-RJESENJE-5/8"


def test_internal_exception_not_shown_to_student(client, fake_llm):
    class Boom(Exception):
        pass

    fake_llm.queue(Boom("interni stack trace detalji"))
    r = client.post("/api/ai-tutor/chat", json=chat_payload())
    assert r.status_code == 200
    j = r.get_json()
    assert "Boom" not in r.get_data(as_text=True)
    assert "stack trace" not in j["answer"]
    assert "zapelo" in j["answer"]


def test_error_response_has_no_status_and_no_next_state(client, fake_llm):
    from matbot.llm import LLMUnavailable
    fake_llm.queue(LLMUnavailable("down"))
    j = client.post("/api/ai-tutor/chat", json=chat_payload()).get_json()
    assert "status" not in j
    assert "next_state" not in j


def test_garbage_body_handled(client, fake_llm):
    r = client.post("/api/ai-tutor/chat", data="nije json",
                    content_type="application/json")
    assert r.status_code == 200
    assert "answer" in r.get_json()
    assert fake_llm.call_count == 0


def test_too_long_message_rejected_without_llm(client, fake_llm):
    r = client.post("/api/ai-tutor/chat", json=chat_payload(msg="x" * 5000))
    assert r.status_code == 200
    assert "preduga" in r.get_json()["answer"]
    assert fake_llm.call_count == 0


def test_feedback_valid_payload_ok(client):
    r = client.post("/api/ai-tutor/feedback", json={
        "session_id": "api-sess", "message_index": 0, "verdict": "up",
        "mode": "practice", "topic": "6-01-006",
    })
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}


def test_feedback_invalid_payloads_rejected(client):
    bad_payloads = [
        {},
        {"session_id": "s", "message_index": -1, "verdict": "up", "mode": "m", "topic": "t"},
        {"session_id": "s", "message_index": 0, "verdict": "sideways", "mode": "m", "topic": "t"},
        {"session_id": "", "message_index": 0, "verdict": "up", "mode": "m", "topic": "t"},
        {"session_id": "s", "message_index": "nula", "verdict": "up", "mode": "m", "topic": "t"},
    ]
    for p in bad_payloads:
        r = client.post("/api/ai-tutor/feedback", json=p)
        assert r.status_code == 400, p
        assert r.get_json() == {"ok": False}


def test_existing_routes_still_work(client):
    assert client.get("/").status_code == 200
    assert client.get("/healthz").get_json() == {"ok": True}
    assert client.get("/_healthz").get_json() == {"ok": True}
    topics = client.get("/api/ai-tutor/topics?grade=6").get_json()
    assert topics["oblast_order"]
