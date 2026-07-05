"""Async tok /submit, /status, /result, slike i fallback putanje."""
import io
import json

import app as matbot

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _wait_done(client, job_id):
    s = client.get(f"/status/{job_id}")
    data = s.get_json()
    assert data.get("status") == "done", f"job nije gotov: {data}"
    return data


def test_submit_explicit_async_text(client, fake_openai, sync_enqueue):
    r = client.post("/submit", data={"razred": "7", "user_text": "2+2", "mode": "async"})
    assert r.status_code == 202
    j = r.get_json()
    assert j["mode"] == "async"
    data = _wait_done(client, j["job_id"])
    assert data["result"]["path"] == "text"
    assert "Test odgovor" in data["result"]["html"]

def test_submit_heavy_text_goes_async(client, fake_openai, sync_enqueue):
    r = client.post("/submit", data={"razred": "7", "user_text": "x" * 10000})
    assert r.status_code == 202
    assert r.get_json()["mode"] == "auto→async"

def test_submit_image_goes_async(client, fake_openai, sync_enqueue):
    fake_openai.state["reply"] = "Sa slike: x = 4"
    r = client.post("/submit", data={
        "razred": "7",
        "user_text": "uradi zadatak",
        "file": (io.BytesIO(PNG), "zadatak.png"),
    })
    assert r.status_code == 202
    data = _wait_done(client, r.get_json()["job_id"])
    assert data["result"]["path"] == "vision_direct"
    assert "Sa slike" in data["result"]["html"]

def test_async_image_xss_escaped(client, fake_openai, sync_enqueue):
    """Regresija: vision odgovor je ranije išao u HTML BEZ escapovanja."""
    fake_openai.state["reply"] = "<img src=x onerror=alert(1)> i 2 < 5"
    r = client.post("/submit", data={
        "razred": "7",
        "file": (io.BytesIO(PNG), "zadatak.png"),
    })
    data = _wait_done(client, r.get_json()["job_id"])
    html_out = data["result"]["html"]
    assert "<img" not in html_out
    assert "&lt;img" in html_out

def test_async_image_receives_history(client, fake_openai, sync_enqueue):
    """Follow-up 'uradi i b)' uz sliku mora dobiti prethodni kontekst (ranije: prazno)."""
    history = [{"user": "Zadatak 5a", "bot": "<p>a) rezultat je 12</p>"}]
    client.post("/submit", data={
        "razred": "8",
        "user_text": "uradi i b)",
        "file": (io.BytesIO(PNG), "zadatak.png"),
        "history_json": json.dumps(history),
    })
    msgs = fake_openai.calls.messages[0]
    bots = [m["content"] for m in msgs if m["role"] == "assistant"]
    assert "a) rezultat je 12" in bots

def test_async_plot_div(client, fake_openai, sync_enqueue):
    """Graf sada radi i za async odgovore (ranije samo sync)."""
    r = client.post("/submit", data={"razred": "8", "user_text": "nacrtaj graf y=3x", "mode": "async"})
    data = _wait_done(client, r.get_json()["job_id"])
    assert 'class="plot-request"' in data["result"]["html"]

def test_sync_failure_falls_back_to_async(client, fake_openai, sync_enqueue):
    """Brzi sync pokušaj pukne → zahtjev ide u async red i tamo uspije."""
    fake_openai.state["raise_fast"] = TimeoutError("sporo")
    r = client.post("/submit", data={"razred": "7", "user_text": "teže pitanje"})
    assert r.status_code == 202
    j = r.get_json()
    assert j["mode"] == "auto(sync→async)"
    data = _wait_done(client, j["job_id"])
    assert "Test odgovor" in data["result"]["html"]

def test_async_job_error_stores_friendly_message(client, fake_openai, sync_enqueue):
    fake_openai.state["raise_always"] = RuntimeError("model je pao")
    r = client.post("/submit", data={"razred": "7", "user_text": "2+2", "mode": "async"})
    data = _wait_done(client, r.get_json()["job_id"])
    assert data["result"]["path"] == "error"
    assert "Nije uspjela obrada" in data["result"]["html"]

def test_status_unknown_job_pending(client):
    r = client.get("/status/nepostojeci-id")
    assert r.status_code == 200
    assert r.get_json()["status"] == "pending"

def test_result_endpoint_shapes(client, fake_openai, sync_enqueue):
    r = client.post("/submit", data={"razred": "7", "user_text": "2+2", "mode": "async"})
    job_id = r.get_json()["job_id"]
    res = client.get(f"/result/{job_id}")
    assert res.status_code == 200
    assert res.get_json()["result"]["path"] == "text"

    pending = client.get("/result/nepostojeci")
    assert pending.status_code == 202


def test_mathpix_path_used_when_available(client, fake_openai, sync_enqueue, monkeypatch):
    monkeypatch.setattr(matbot, "_mathpix_enabled", lambda: True)
    monkeypatch.setattr(matbot, "MATHPIX_MODE", "prefer")
    monkeypatch.setattr(matbot, "mathpix_ocr_to_text", lambda b: ("12/3 + 5", 0.95))
    r = client.post("/submit", data={
        "razred": "6",
        "file": (io.BytesIO(PNG), "zadatak.png"),
    })
    data = _wait_done(client, r.get_json()["job_id"])
    assert data["result"]["path"] == "mathpix"
    # OCR tekst je stigao do modela
    user_contents = [m["content"] for m in fake_openai.calls.messages[0] if m["role"] == "user"]
    assert any("12/3 + 5" in str(c) for c in user_contents)

def test_mathpix_empty_falls_back_to_vision(client, fake_openai, sync_enqueue, monkeypatch):
    monkeypatch.setattr(matbot, "_mathpix_enabled", lambda: True)
    monkeypatch.setattr(matbot, "MATHPIX_MODE", "prefer")
    monkeypatch.setattr(matbot, "mathpix_ocr_to_text", lambda b: (None, 0.0))
    r = client.post("/submit", data={
        "razred": "6",
        "file": (io.BytesIO(PNG), "zadatak.png"),
    })
    data = _wait_done(client, r.get_json()["job_id"])
    assert data["result"]["path"] == "vision_direct"


def test_tasks_process_requires_secret_outside_local(client, fake_openai, monkeypatch):
    monkeypatch.setattr(matbot, "LOCAL_MODE", False)
    r = client.post("/tasks/process", data=json.dumps({"job_id": "j1"}),
                    content_type="application/json")
    assert r.status_code == 403

def test_tasks_process_with_secret(client, fake_openai, monkeypatch):
    # Phase 1 (audit): TASKS_SECRET više nema default — mora biti eksplicitno
    # postavljen i header se mora poklopiti.
    monkeypatch.setattr(matbot, "LOCAL_MODE", False)
    monkeypatch.setattr(matbot, "TASKS_SECRET", "test-tasks-secret")
    payload = {"job_id": "j-test", "razred": "7", "user_text": "2+2", "requested": [],
               "history": [{"user": "ranije", "bot": "odgovor"}]}
    r = client.post("/tasks/process", data=json.dumps(payload),
                    content_type="application/json",
                    headers={"X-Tasks-Secret": "test-tasks-secret"})
    assert r.status_code == 200
    assert matbot.JOB_STORE["j-test"]["status"] == "done"
