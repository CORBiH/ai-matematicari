"""Phase 2 (audit) — potpisani kratkotrajni token za /api/ai-tutor/*.

Enforcement važi SAMO kada je AI_TUTOR_EMBED_SECRET postavljen i nije LOCAL_MODE;
testno okruženje je LOCAL_MODE=1 pa se produkcijsko ponašanje simulira
monkeypatchom modulskih varijabli.
"""
import time

import pytest

import app as matbot

CHAT_URL = "/api/ai-tutor/chat"
STREAM_URL = "/api/ai-tutor/chat/stream"
SECRET = "test-embed-secret"


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield


@pytest.fixture()
def prod_gate(monkeypatch):
    """Simulacija produkcije sa uključenim enforcementom."""
    monkeypatch.setattr(matbot, "LOCAL_MODE", False)
    monkeypatch.setattr(matbot, "AI_TUTOR_EMBED_SECRET", SECRET)
    yield


# --- mint/verify jedinice -------------------------------------------------------------

def test_mint_and_verify_roundtrip(prod_gate):
    tok = matbot.mint_embed_token()
    assert tok and "." in tok
    assert matbot.verify_embed_token(tok) is True


def test_verify_rejects_garbage(prod_gate):
    for bad in ("", None, "abc", "123", "123.deadbeef", "x.y.z"):
        assert matbot.verify_embed_token(bad) is False, bad


def test_verify_rejects_expired(prod_gate):
    expired = matbot.mint_embed_token(expires_at=int(time.time()) - 10)
    assert matbot.verify_embed_token(expired) is False


def test_verify_rejects_tampered_expiry(prod_gate):
    tok = matbot.mint_embed_token()
    exp, sig = tok.split(".", 1)
    tampered = f"{int(exp) + 99999}.{sig}"          # produžen rok, stari potpis
    assert matbot.verify_embed_token(tampered) is False


def test_mint_empty_without_secret(monkeypatch):
    monkeypatch.setattr(matbot, "AI_TUTOR_EMBED_SECRET", "")
    assert matbot.mint_embed_token() == ""


# --- endpoint gating ------------------------------------------------------------------

def test_chat_missing_token_rejected_in_production(client, prod_gate):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "invalid_token"


def test_chat_invalid_token_rejected(client, prod_gate):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"},
                       headers={"X-Tutor-Token": "999999.pogresan"})
    assert resp.status_code == 403


def test_chat_expired_token_rejected(client, prod_gate):
    expired = matbot.mint_embed_token(expires_at=int(time.time()) - 5)
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"},
                       headers={"X-Tutor-Token": expired})
    assert resp.status_code == 403


def test_chat_valid_token_accepted(client, prod_gate, fake_openai):
    tok = matbot.mint_embed_token()
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"},
                       headers={"X-Tutor-Token": tok})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


def test_stream_missing_token_rejected(client, prod_gate):
    resp = client.post(STREAM_URL, json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 403


def test_stream_valid_token_accepted(client, prod_gate, fake_openai, monkeypatch):
    monkeypatch.setattr(matbot, "_tutor_openai_chat_stream",
                        lambda *a, **k: iter(["ok"]))
    tok = matbot.mint_embed_token()
    resp = client.post(STREAM_URL, json={"selected_topic": "skupovi_uvod"},
                       headers={"X-Tutor-Token": tok})
    assert resp.status_code == 200


# --- sigurno uvođenje / dev ------------------------------------------------------------

def test_local_mode_needs_no_token(client, fake_openai):
    """Testno okruženje je LOCAL_MODE=1 — bez tokena i dalje radi (dev UX)."""
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 200


def test_no_secret_means_no_enforcement(client, fake_openai, monkeypatch):
    """Produkcija BEZ postavljenog secreta: prolazi (rollout bez loma) +
    startup warning (ENV SANITY)."""
    monkeypatch.setattr(matbot, "LOCAL_MODE", False)
    monkeypatch.setattr(matbot, "AI_TUTOR_EMBED_SECRET", "")
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 200


def test_topics_not_gated(client, prod_gate):
    """Jeftini /topics endpoint NIJE iza tokena (home ekran mora raditi)."""
    assert client.get("/api/ai-tutor/topics?grade=6").status_code == 200


def test_page_embeds_token_meta(client, monkeypatch):
    monkeypatch.setattr(matbot, "AI_TUTOR_EMBED_SECRET", SECRET)
    html = client.get("/").get_data(as_text=True)
    assert 'name="matbot-embed-token"' in html
    # sadržaj meta taga je validan token
    import re
    m = re.search(r'name="matbot-embed-token" content="([^"]*)"', html)
    assert m and "." in m.group(1)
