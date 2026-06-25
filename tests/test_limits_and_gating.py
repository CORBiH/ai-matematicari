"""Limit veličine zahtjeva, rate limiting i zaštita dijagnostičkih endpointa."""
import io

import app as matbot


def test_oversized_upload_413(client):
    """MAX_CONTENT_LENGTH_MB=1 u testu → 2MB upload mora biti odbijen."""
    big = io.BytesIO(b"\xff\xd8\xff" + b"0" * (2 * 1024 * 1024))
    r = client.post("/submit", data={"razred": "7", "file": (big, "velika.jpg")})
    assert r.status_code == 413


def test_rate_limit_submit(client, fake_openai, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_SUBMIT", "2 per minute")
    matbot.limiter.reset()
    codes = [
        client.post("/submit", data={"razred": "7", "user_text": f"2+{i}"}).status_code
        for i in range(3)
    ]
    assert codes[:2] == [200, 200]
    assert codes[2] == 429
    matbot.limiter.reset()

def test_rate_limit_429_is_json_bosnian(client, fake_openai, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_SUBMIT", "1 per minute")
    matbot.limiter.reset()
    client.post("/submit", data={"razred": "7", "user_text": "2+2"})
    r = client.post("/submit", data={"razred": "7", "user_text": "2+3"})
    assert r.status_code == 429
    j = r.get_json()
    assert j["error"] == "rate_limited"
    assert "Previše zahtjeva" in j["detail"]
    matbot.limiter.reset()


def test_diag_open_in_local_mode(client):
    assert client.get("/sheets/diag").status_code == 200

def test_diag_blocked_outside_local(client, monkeypatch):
    monkeypatch.setattr(matbot, "LOCAL_MODE", False)
    monkeypatch.setattr(matbot, "DIAG_TOKEN", "")
    assert client.get("/sheets/diag").status_code == 403
    assert client.post("/sheets/selftest").status_code == 403
    assert client.get("/mathpix/selftest").status_code == 403
    assert client.post("/gcs/signed-upload", json={}).status_code == 403

def test_diag_allowed_with_token(client, monkeypatch):
    monkeypatch.setattr(matbot, "LOCAL_MODE", False)
    monkeypatch.setattr(matbot, "DIAG_TOKEN", "tajni-token")
    r = client.get("/sheets/diag", headers={"X-Diag-Token": "tajni-token"})
    assert r.status_code == 200
    r2 = client.get("/sheets/diag", headers={"X-Diag-Token": "pogresan"})
    assert r2.status_code == 403


def test_sheets_selftest_local_reports_disabled(client):
    # U LOCAL_MODE dozvoljen, ali Sheets nije inicijalizovan → 500 sa porukom
    r = client.post("/sheets/selftest")
    assert r.status_code == 500
    assert r.get_json()["ok"] is False


def test_secret_key_is_from_env():
    assert matbot.app.secret_key == "test-secret-key"

def test_openai_knobs_read_from_env():
    # default iz conftest okruženja: nema override → vrijednosti su brojevi
    assert isinstance(matbot.OPENAI_TIMEOUT, float)
    assert isinstance(matbot.OPENAI_MAX_RETRIES, int)


def test_cleanup_stale_uploads(tmp_path, monkeypatch):
    import os, time
    monkeypatch.setattr(matbot, "UPLOAD_DIR", str(tmp_path))
    old = tmp_path / "star.png"
    new = tmp_path / "nov.png"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    past = time.time() - 7200
    os.utime(old, (past, past))
    matbot.cleanup_stale_uploads(max_age_s=3600)
    assert not old.exists()
    assert new.exists()
