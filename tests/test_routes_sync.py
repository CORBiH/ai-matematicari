"""Sync ponašanje /submit i osnovne rute."""
import json

import app as matbot


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

def test_version(client):
    r = client.get("/version")
    assert r.status_code == 200
    assert "version" in r.get_json()

def test_options_submit_no_limit(client):
    r = client.open("/submit", method="OPTIONS")
    assert r.status_code == 204


def test_submit_sync_text_ok(client, fake_openai):
    r = client.post("/submit", data={"razred": "7", "user_text": "Koliko je 2+3?"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["mode"] == "auto(sync)"
    assert j["result"]["path"] == "text"
    assert "Test odgovor" in j["result"]["html"]
    # sync pokušaj ide preko fast klijenta
    assert fake_openai.calls.fast_flags[0] is True

def test_submit_sync_uses_grade_prompt(client, fake_openai):
    client.post("/submit", data={"razred": "9", "user_text": "Riješi 2x+4=10"})
    system = fake_openai.calls.messages[0][0]
    assert system["role"] == "system"
    assert "RAZREDNA PRAVILA — 9. RAZRED" in system["content"]

def test_submit_invalid_grade_defaults_to_5(client, fake_openai):
    client.post("/submit", data={"razred": "99", "user_text": "2+2"})
    system = fake_openai.calls.messages[0][0]
    assert "RAZREDNA PRAVILA — 5. RAZRED" in system["content"]

def test_submit_requested_tasks_clause(client, fake_openai):
    client.post("/submit", data={"razred": "7", "user_text": "riješi zadatak 3 i zadatak 7"})
    system = fake_openai.calls.messages[0][0]
    assert "ISKLJUČIVO" in system["content"]
    assert "3, 7" in system["content"]

def test_submit_grade_mention_adds_no_clause(client, fake_openai):
    client.post("/submit", data={"razred": "5", "user_text": "Imam 5. razred, pomozi mi sa sabiranjem"})
    system = fake_openai.calls.messages[0][0]
    assert "Riješi ISKLJUČIVO sljedeće zadatke" not in system["content"]


def test_submit_xss_escaped(client, fake_openai):
    """Regresija: odgovor modela mora biti escapovan u svim tokovima."""
    fake_openai.state["reply"] = '<script>alert(1)</script> i 2 < 5'
    r = client.post("/submit", data={"razred": "7", "user_text": "2+2"})
    html_out = r.get_json()["result"]["html"]
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "2 &lt; 5" in html_out


def test_submit_plot_div_sync(client, fake_openai):
    r = client.post("/submit", data={"razred": "8", "user_text": "nacrtaj graf y=2x+1"})
    html_out = r.get_json()["result"]["html"]
    assert 'class="plot-request"' in html_out
    assert "y=2x+1" in html_out

def test_submit_no_plot_without_trigger(client, fake_openai):
    r = client.post("/submit", data={"razred": "8", "user_text": "y=2x+1 nule funkcije"})
    assert 'class="plot-request"' not in r.get_json()["result"]["html"]


def test_submit_history_json_reaches_model(client, fake_openai):
    """Follow-up: 'objasni drugi korak' mora vidjeti prethodni odgovor (čist tekst)."""
    history = [{"user": "Riješi 2x+4=10", "bot": "<p>2x = 6<br>x = 3</p>"}]
    client.post("/submit", data={
        "razred": "7",
        "user_text": "objasni drugi korak",
        "history_json": json.dumps(history),
    })
    msgs = fake_openai.calls.messages[0]
    users = [m["content"] for m in msgs if m["role"] == "user"]
    bots = [m["content"] for m in msgs if m["role"] == "assistant"]
    assert "Riješi 2x+4=10" in users
    assert "2x = 6\nx = 3" in bots          # HTML skinut
    assert all("<p>" not in b for b in bots)
    assert users[-1] == "objasni drugi korak"

def test_submit_huge_history_is_capped(client, fake_openai):
    """Odgovor koji bi ranije prepunio kolačić sada se uredno sasiječe."""
    history = [{"user": "dugačko pitanje", "bot": "<p>" + "korak<br>" * 5000 + "</p>"}]
    client.post("/submit", data={
        "razred": "7", "user_text": "nastavi",
        "history_json": json.dumps(history),
    })
    msgs = fake_openai.calls.messages[0]
    bots = [m["content"] for m in msgs if m["role"] == "assistant"]
    assert len(bots) == 1
    assert len(bots[0]) <= matbot.HISTORY_MAX_CHARS

def test_submit_malformed_history_ignored(client, fake_openai):
    r = client.post("/submit", data={
        "razred": "7", "user_text": "2+2",
        "history_json": "{nije-json",
    })
    assert r.status_code == 200

def test_session_fallback_history_stays_small(client, fake_openai):
    """Sesijska kopija je čisti tekst i kratka — ne smije prepuniti kolačić."""
    fake_openai.state["reply"] = "vrlo dug odgovor " * 500
    client.post("/submit", data={"razred": "7", "user_text": "pitanje"})
    with client.session_transaction() as sess:
        hist = sess.get("api_history") or []
        assert hist, "očekivana fallback historija u sesiji"
        for m in hist:
            assert len(m["bot"]) <= 600
            assert "<p>" not in m["bot"]


def test_index_get(client):
    assert client.get("/").status_code == 200

def test_index_post_requires_grade(client):
    r = client.post("/", data={"pitanje": "2+2"})
    assert r.status_code == 400

def test_index_post_text_renders(client, fake_openai):
    # legacy tekstualna putanja na "/" rendera template (bez redirecta)
    r = client.post("/", data={"razred": "6", "pitanje": "Koliko je 4:2?"})
    assert r.status_code == 200

def test_clear_route(client):
    r = client.post("/clear", data={"confirm_clear": "1"})
    assert r.status_code == 302

def test_set_razred(client):
    r = client.post("/set-razred", data={"razred": "8"})
    assert r.status_code == 204
