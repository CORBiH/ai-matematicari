"""Testovi za ProxyFix konfiguraciju (app.py) — potvrđeno na produkciji: Nginx
je JEDINI reverse proxy, postavlja X-Forwarded-For i X-Forwarded-Proto.
x_for=1/x_proto=1, BEZ x_host/x_port/x_prefix. Ne pokreće stvarne OpenAI
pozive — FakeLLM iz conftest.py.
"""
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.test import Client, EnvironBuilder

from matbot.ratelimit import RateLimiter
from tests.conftest import make_output


def _capturing_app():
    """Minimalna WSGI aplikacija koja snima environ koji joj ProxyFix proslijedi."""
    captured = {}

    def inner(environ, start_response):
        captured["REMOTE_ADDR"] = environ.get("REMOTE_ADDR")
        captured["wsgi.url_scheme"] = environ.get("wsgi.url_scheme")
        captured["HTTP_HOST"] = environ.get("HTTP_HOST")
        captured["SERVER_NAME"] = environ.get("SERVER_NAME")
        captured["SCRIPT_NAME"] = environ.get("SCRIPT_NAME")
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    return inner, captured


def _proxy_fixed_app():
    """Ista konfiguracija kao u app.py — x_for=1, x_proto=1, ostalo default 0."""
    inner, captured = _capturing_app()
    wrapped = ProxyFix(inner, x_for=1, x_proto=1)
    return wrapped, captured


# ---------------------------------------------------------------------------
# 1) Jedna X-Forwarded-For adresa postaje request.remote_addr (REMOTE_ADDR)
# ---------------------------------------------------------------------------

def test_single_x_forwarded_for_becomes_remote_addr():
    wrapped, captured = _proxy_fixed_app()
    client = Client(wrapped)
    builder = EnvironBuilder(path="/", headers={"X-Forwarded-For": "203.0.113.7"})
    env = builder.get_environ()
    env["REMOTE_ADDR"] = "10.0.0.1"  # originalna (nginx-ova) adresa prije ProxyFix-a
    client.open(environ_overrides=env)
    assert captured["REMOTE_ADDR"] == "203.0.113.7"


# ---------------------------------------------------------------------------
# 2) Kod više vrijednosti vjeruje se SAMO posljednjoj (koju je dodao Nginx)
# ---------------------------------------------------------------------------

def test_multiple_x_forwarded_for_values_trusts_only_last_one():
    wrapped, captured = _proxy_fixed_app()
    client = Client(wrapped)
    # lanac: 198.51.100.9 (originalni klijent, NEPOUZDAN — klijent ga sam ubacio),
    # 203.0.113.7 (POUZDAN — jedini stvarni nginx hop, on ga je dodao)
    builder = EnvironBuilder(
        path="/", headers={"X-Forwarded-For": "198.51.100.9, 203.0.113.7"}
    )
    env = builder.get_environ()
    env["REMOTE_ADDR"] = "10.0.0.1"
    client.open(environ_overrides=env)
    assert captured["REMOTE_ADDR"] == "203.0.113.7"
    assert captured["REMOTE_ADDR"] != "198.51.100.9"


# ---------------------------------------------------------------------------
# 3) X-Forwarded-Proto=https postavlja wsgi.url_scheme (request.is_secure)
# ---------------------------------------------------------------------------

def test_x_forwarded_proto_https_sets_secure_scheme():
    wrapped, captured = _proxy_fixed_app()
    client = Client(wrapped)
    builder = EnvironBuilder(
        path="/",
        headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"},
    )
    env = builder.get_environ()
    env["REMOTE_ADDR"] = "10.0.0.1"
    env["wsgi.url_scheme"] = "http"  # originalni (interni) scheme prema Gunicornu
    client.open(environ_overrides=env)
    assert captured["wsgi.url_scheme"] == "https"


def test_real_app_uses_proxyfix_with_x_proto_trusted(flask_app):
    """Integracijski dio: stvarni app.wsgi_app (isti objekat koji app.py servira
    produkciji) mora biti ProxyFix sa x_proto=1 — nižа-nivo test iznad već
    dokazuje da ProxyFix sam po sebi ispravno mapira X-Forwarded-Proto na
    wsgi.url_scheme (iz čega Werkzeug/Flask izvodi request.is_secure)."""
    c = flask_app.test_client()
    r = c.get(
        "/healthz",
        headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"},
    )
    assert r.status_code == 200
    from app import app as real_app
    assert isinstance(real_app.wsgi_app, ProxyFix)
    assert real_app.wsgi_app.x_proto == 1


# ---------------------------------------------------------------------------
# 4) Dvije različite IP adrese imaju odvojene rate-limit brojače
# ---------------------------------------------------------------------------

def chat_payload(msg="Daj mi jedan zadatak za vježbu iz ove teme.", **kw):
    base = {
        "session_id": "proxyfix-sess",
        "client_turn_id": "turn-1",
        "grade": 6,
        "mode": "practice",
        "entry_source": "manual_topic_choice",
        "selected_topic": "6-01-006",
        "selected_oblast": "",
        "student_message": msg,
        "conversation_history": [],
    }
    base.update(kw)
    return base


def test_two_different_forwarded_ips_get_separate_rate_limit_buckets(flask_app, fake_llm):
    from matbot import auth

    flask_app.config["MATBOT_IP_LIMITER"] = RateLimiter(per_minute=1, per_hour=100000)
    # session limit ostaje visok da ne interferira — testiramo ISKLJUČIVO IP bucket
    flask_app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(per_minute=100000, per_hour=100000)

    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()

    fake_llm.queue(make_output(reply="ok"))
    r1 = c.post(
        "/api/ai-tutor/chat",
        json=chat_payload(session_id="s1"),
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    assert r1.status_code == 200

    # ISTA IP adresa, druga sesija -> IP limit (1/min) mora blokirati
    r2 = c.post(
        "/api/ai-tutor/chat",
        json=chat_payload(session_id="s2"),
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    assert r2.status_code == 429

    # DRUGA IP adresa -> odvojen bucket, mora proći
    fake_llm.queue(make_output(reply="ok"))
    r3 = c.post(
        "/api/ai-tutor/chat",
        json=chat_payload(session_id="s3"),
        headers={"X-Forwarded-For": "198.51.100.20"},
    )
    assert r3.status_code == 200
    assert fake_llm.call_count == 2  # r1 i r3 su pozvali LLM, r2 (429) nije


# ---------------------------------------------------------------------------
# 5) X-Forwarded-Host, X-Forwarded-Port i X-Forwarded-Prefix se NE vjeruju
# ---------------------------------------------------------------------------

def test_x_forwarded_host_port_prefix_not_trusted():
    wrapped, captured = _proxy_fixed_app()
    client = Client(wrapped)
    builder = EnvironBuilder(
        path="/",
        headers={
            "X-Forwarded-For": "203.0.113.7",
            "X-Forwarded-Host": "napadac.example.com",
            "X-Forwarded-Port": "9999",
            "X-Forwarded-Prefix": "/napadac-prefix",
        },
    )
    env = builder.get_environ()
    env["REMOTE_ADDR"] = "10.0.0.1"
    original_host = env.get("HTTP_HOST")
    original_server_name = env.get("SERVER_NAME")
    original_script_name = env.get("SCRIPT_NAME", "")
    client.open(environ_overrides=env)
    assert captured["HTTP_HOST"] == original_host
    assert captured["SERVER_NAME"] == original_server_name
    assert captured["SCRIPT_NAME"] == original_script_name
    assert captured["HTTP_HOST"] != "napadac.example.com"
    assert captured["SCRIPT_NAME"] != "/napadac-prefix"


def test_app_proxyfix_configured_without_host_port_prefix_trust():
    """Direktna provjera KONFIGURACIJE u app.py — x_host/x_port/x_prefix
    moraju ostati na defaultnoj (netrusted) vrijednosti 0."""
    from app import app as real_app

    assert isinstance(real_app.wsgi_app, ProxyFix)
    assert real_app.wsgi_app.x_for == 1
    assert real_app.wsgi_app.x_proto == 1
    assert real_app.wsgi_app.x_host == 0
    assert real_app.wsgi_app.x_port == 0
    assert real_app.wsgi_app.x_prefix == 0
