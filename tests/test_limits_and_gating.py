"""Limit veličine zahtjeva, rate limiting i zaštita dijagnostičkih endpointa.

Poslije brisanja legacy /submit stacka (2026-07-14) rate limit i 413 se testiraju
na ŽIVOJ ruti — /api/ai-tutor/chat (jedini skup poziv koji je ostao).
"""
import io

import app as matbot

CHAT_URL = "/api/ai-tutor/chat"


def test_oversized_upload_413(client):
    """MAX_CONTENT_LENGTH_MB=1 u testu → 2MB upload mora biti odbijen."""
    big = io.BytesIO(b"\xff\xd8\xff" + b"0" * (2 * 1024 * 1024))
    r = client.post(CHAT_URL, data={
        "payload": '{"grade": 6, "student_message": "evo slika"}',
        "image": (big, "velika.jpg"),
    }, content_type="multipart/form-data")
    assert r.status_code == 413


def test_rate_limit_chat(client, fake_openai, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_SUBMIT", "2 per minute")
    matbot.limiter.reset()
    codes = [
        client.post(CHAT_URL, json={"grade": 6, "student_message": f"2+{i}"}).status_code
        for i in range(3)
    ]
    assert codes[:2] == [200, 200]
    assert codes[2] == 429
    matbot.limiter.reset()


def test_rate_limit_429_is_json_bosnian(client, fake_openai, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_SUBMIT", "1 per minute")
    matbot.limiter.reset()
    client.post(CHAT_URL, json={"grade": 6, "student_message": "2+2"})
    r = client.post(CHAT_URL, json={"grade": 6, "student_message": "2+3"})
    assert r.status_code == 429
    j = r.get_json()
    assert j["error"] == "rate_limited"
    assert "Previše zahtjeva" in j["detail"]
    matbot.limiter.reset()


def test_diag_open_in_local_mode(client):
    """Dozvoljen pristup: 400 "no-keys" (Mathpix nije konfigurisan u testu) —
    bitno je da NIJE 403."""
    r = client.get("/mathpix/selftest")
    assert r.status_code == 400
    assert r.get_json()["reason"] == "no-keys"


def test_diag_blocked_outside_local(client, monkeypatch):
    monkeypatch.setattr(matbot, "LOCAL_MODE", False)
    monkeypatch.setattr(matbot, "DIAG_TOKEN", "")
    assert client.get("/mathpix/selftest").status_code == 403


def test_diag_allowed_with_token(client, monkeypatch):
    monkeypatch.setattr(matbot, "LOCAL_MODE", False)
    monkeypatch.setattr(matbot, "DIAG_TOKEN", "tajni-token")
    # ispravan token → prolazi kapiju (400 no-keys, ne 403)
    assert client.get("/mathpix/selftest",
                      headers={"X-Diag-Token": "tajni-token"}).status_code == 400
    assert client.get("/mathpix/selftest",
                      headers={"X-Diag-Token": "pogresan"}).status_code == 403


def test_secret_key_is_from_env():
    assert matbot.app.secret_key == "test-secret-key"


def test_openai_knobs_read_from_env():
    assert isinstance(matbot.OPENAI_TIMEOUT, float)
    assert isinstance(matbot.OPENAI_MAX_RETRIES, int)


def test_legacy_routes_are_gone(client):
    """Regres: legacy /submit stack je obrisan — rute NE smiju vaskrsnuti."""
    for path in ("/submit", "/clear", "/set-razred", "/gcs/signed-upload",
                 "/tasks/process", "/sheets/diag", "/sheets/selftest"):
        assert client.post(path).status_code == 404, path
    for path in ("/status/abc", "/result/abc", "/uploads/x.png"):
        assert client.get(path).status_code == 404, path


def test_index_is_get_only(client):
    """POST / je bio legacy forma — sada ne postoji."""
    assert client.get("/").status_code == 200
    assert client.post("/").status_code == 405
