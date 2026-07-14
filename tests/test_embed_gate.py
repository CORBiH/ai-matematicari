# -*- coding: utf-8 -*-
"""Embed kapija: aplikaciji se ulazi SAMO kroz dozvoljeni embed (Thinkific).

Rupa koju zatvara: token se ugrađuje u GET /, pa je svako ko zna URL mogao
otvoriti stranicu, pokupiti svjež token i koristiti bota direktno.

Kapija je na GET / (tu stiže cross-site zahtjev iz iframe-a), NE na /api/* —
tutor pozivi su same-origin (naša stranica zove naš API), pa bi traženje
Thinkific origina na API-ju odbilo legitimne pozive.

Napomena: stanje se mijenja monkeypatch-om modulnih globala (NE importlib.reload —
reload zamjenjuje modul pa curi u druge testove).
"""
import pytest

import app as app_mod


@pytest.fixture
def client():
    app_mod.app.config.update(TESTING=True)
    return app_mod.app.test_client()


@pytest.fixture
def gate_on(monkeypatch):
    """Kapija uključena; LOCAL_MODE isključen (u lokalnom modu kapija propušta)."""
    monkeypatch.setattr(app_mod, "EMBED_ALLOWED_ORIGINS", ["https://*.thinkific.com"])
    monkeypatch.setattr(app_mod, "LOCAL_MODE", False)


def test_direct_visit_is_blocked(client, gate_on):
    """Bez Referer/Origin (neko kucao URL direktno) → 403, i token se NE kuje."""
    resp = client.get("/")
    assert resp.status_code == 403
    body = resp.get_data(as_text=True)
    assert "dostupan kroz lekciju" in body
    assert "matbot-embed-token" not in body


def test_foreign_site_is_blocked(client, gate_on):
    resp = client.get("/", headers={"Referer": "https://zloban.com/x"})
    assert resp.status_code == 403


def test_thinkific_iframe_is_allowed_and_gets_token(client, gate_on):
    resp = client.get(
        "/", headers={"Referer": "https://skola.thinkific.com/courses/matematika"})
    assert resp.status_code == 200
    assert "matbot-embed-token" in resp.get_data(as_text=True)


def test_origin_header_also_accepted(client, gate_on):
    resp = client.get("/", headers={"Origin": "https://skola.thinkific.com"})
    assert resp.status_code == 200


def test_local_mode_bypasses_gate(client, monkeypatch):
    """Lokalni razvoj ne smije biti zaključan."""
    monkeypatch.setattr(app_mod, "EMBED_ALLOWED_ORIGINS", ["https://*.thinkific.com"])
    monkeypatch.setattr(app_mod, "LOCAL_MODE", True)
    assert client.get("/").status_code == 200


def test_gate_off_when_unset(client, monkeypatch):
    """Prazna lista = kapija ISKLJUČENA (kompatibilnost / postepeno uvođenje)."""
    monkeypatch.setattr(app_mod, "EMBED_ALLOWED_ORIGINS", [])
    monkeypatch.setattr(app_mod, "LOCAL_MODE", False)
    assert client.get("/").status_code == 200


def test_healthz_not_gated(client, gate_on):
    """Deploy healthcheck ne smije pasti zbog kapije."""
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("origin,allowed", [
    ("https://skola.thinkific.com", True),
    ("https://SKOLA.Thinkific.com/", True),        # case + trailing slash
    ("https://thinkific.com.zloban.net", False),   # sufiks-podvala
    ("http://skola.thinkific.com", False),         # http nije https
    ("", False),
])
def test_origin_wildcard_matching(origin, allowed):
    assert app_mod._origin_matches(origin, ["https://*.thinkific.com"]) is allowed
