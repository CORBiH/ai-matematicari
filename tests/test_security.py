"""Testovi za security hardening: potpisani token, rate limiting, per-session
concurrency lock i input validacija. Ne pokreće stvarne OpenAI pozive — sve
ide kroz FakeLLM (conftest.fake_llm)."""
import queue
import re
import threading
import time

import pytest

from matbot import auth
from matbot.ratelimit import RateLimiter
from matbot.turnlock import TurnLockRegistry
from tests.conftest import FakeLLM, make_output, make_task


def chat_payload(msg="Daj mi jedan zadatak za vježbu iz ove teme.", mode="practice", **kw):
    base = {
        "session_id": "sec-sess-1",
        "client_turn_id": "turn-1",
        "grade": 6,
        "mode": mode,
        "entry_source": "manual_topic_choice",
        "selected_topic": "6-01-006",   # Unija skupova — stvarna lekcija u topics.json
        "selected_oblast": "",
        "student_message": msg,
        "conversation_history": [],
    }
    base.update(kw)
    return base


def _authed_client(flask_app):
    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()
    return c


# ---------------------------------------------------------------------------
# TOKEN
# ---------------------------------------------------------------------------

def test_index_route_issues_signed_embed_token(flask_app):
    raw_client = flask_app.test_client()
    r = raw_client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    m = re.search(r'name="matbot-embed-token" content="([^"]*)"', html)
    assert m and m.group(1)
    auth.verify_token(m.group(1))  # ne smije baciti TokenError


def test_valid_token_passes(flask_app, fake_llm):
    c = _authed_client(flask_app)
    fake_llm.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    r = c.post("/api/ai-tutor/chat", json=chat_payload())
    assert r.status_code == 200
    assert r.get_json()["status"] == "ready"


def test_missing_token_returns_401_and_never_calls_llm(flask_app, fake_llm):
    c = flask_app.test_client()  # bez tokena
    r = c.post("/api/ai-tutor/chat", json=chat_payload())
    assert r.status_code == 401
    assert r.get_json()["error"] == "AUTH_REQUIRED"
    assert fake_llm.call_count == 0


def test_tampered_token_returns_401_and_never_calls_llm(flask_app, fake_llm):
    c = flask_app.test_client()
    good = auth.issue_token()
    tampered = good[:-4] + ("XXXX" if not good.endswith("XXXX") else "YYYY")
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = tampered
    r = c.post("/api/ai-tutor/chat", json=chat_payload())
    assert r.status_code == 401
    assert r.get_json()["error"] == "AUTH_REQUIRED"
    assert fake_llm.call_count == 0


def test_expired_token_returns_401_and_never_calls_llm(flask_app, fake_llm, monkeypatch):
    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()
    from matbot import config
    monkeypatch.setattr(config, "TOKEN_TTL_SECONDS", -1)  # svaki token je odmah "istekao"
    r = c.post("/api/ai-tutor/chat", json=chat_payload())
    assert r.status_code == 401
    assert r.get_json()["error"] == "AUTH_REQUIRED"
    assert fake_llm.call_count == 0


def test_wrong_purpose_token_rejected():
    from itsdangerous import URLSafeTimedSerializer
    from matbot import config
    ser = URLSafeTimedSerializer(config.SECRET_KEY, salt=auth._SALT)
    bad_purpose_token = ser.dumps({"purpose": "something_else", "nonce": "x"})
    with pytest.raises(auth.TokenError) as exc:
        auth.verify_token(bad_purpose_token)
    assert exc.value.code == "BAD_PURPOSE"


def test_missing_token_error_code_distinguishable_internally():
    with pytest.raises(auth.TokenError) as exc:
        auth.verify_token("")
    assert exc.value.code == "MISSING"


def test_invalid_signature_error_code_distinguishable_internally():
    with pytest.raises(auth.TokenError) as exc:
        auth.verify_token("this-is-not-a-valid-token-at-all")
    assert exc.value.code == "INVALID"


def test_token_never_appears_in_response_or_logs(flask_app, fake_llm, caplog):
    c = flask_app.test_client()
    token = auth.issue_token()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = token
    fake_llm.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    with caplog.at_level("DEBUG"):
        r = c.post("/api/ai-tutor/chat", json=chat_payload())
    assert token not in r.get_data(as_text=True)
    for record in caplog.records:
        assert token not in record.getMessage()


# ---------------------------------------------------------------------------
# RATE LIMIT
# ---------------------------------------------------------------------------

def test_normal_student_under_limit_passes(flask_app, fake_llm):
    c = _authed_client(flask_app)
    flask_app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(per_minute=15, per_hour=150)
    for i in range(3):
        fake_llm.queue(make_output(reply="ok"))
        r = c.post("/api/ai-tutor/chat", json=chat_payload(msg=f"poruka {i}"))
        assert r.status_code == 200


def test_session_limit_exceeded_returns_429_without_llm_call(flask_app, fake_llm):
    c = _authed_client(flask_app)
    flask_app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(per_minute=2, per_hour=150)
    for _ in range(2):
        fake_llm.queue(make_output(reply="ok"))
        assert c.post("/api/ai-tutor/chat", json=chat_payload()).status_code == 200
    r = c.post("/api/ai-tutor/chat", json=chat_payload())
    assert r.status_code == 429
    j = r.get_json()
    assert j["error"] == "RATE_LIMITED"
    assert "retry_after" in j
    assert r.headers.get("Retry-After") is not None
    assert fake_llm.call_count == 2  # treći poziv NIJE dosegao LLM


def test_ip_limit_exceeded_returns_429_across_sessions(flask_app, fake_llm):
    c = _authed_client(flask_app)
    flask_app.config["MATBOT_IP_LIMITER"] = RateLimiter(per_minute=2, per_hour=150)
    flask_app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(per_minute=1000, per_hour=100000)
    for i in range(2):
        fake_llm.queue(make_output(reply="ok"))
        r = c.post("/api/ai-tutor/chat", json=chat_payload(session_id=f"sess-{i}"))
        assert r.status_code == 200
    r = c.post("/api/ai-tutor/chat", json=chat_payload(session_id="sess-treci"))
    assert r.status_code == 429
    assert r.get_json()["error"] == "RATE_LIMITED"
    assert fake_llm.call_count == 2


def test_health_and_topics_never_rate_limited(flask_app):
    c = _authed_client(flask_app)
    flask_app.config["MATBOT_IP_LIMITER"] = RateLimiter(per_minute=1, per_hour=1)
    flask_app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(per_minute=1, per_hour=1)
    for _ in range(10):
        assert c.get("/healthz").status_code == 200
        assert c.get("/_healthz").status_code == 200
        assert c.get("/api/ai-tutor/topics?grade=6").status_code == 200


def test_rate_limiter_reset_controlled_for_testing():
    limiter = RateLimiter(per_minute=2, per_hour=10)
    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is False
    limiter.reset()
    assert limiter.check("k")[0] is True


def test_rate_limiter_thread_safe_never_exceeds_limit():
    limiter = RateLimiter(per_minute=50, per_hour=10000)
    allowed_count = [0]
    count_lock = threading.Lock()

    def worker():
        allowed, _ = limiter.check("shared-key")
        if allowed:
            with count_lock:
                allowed_count[0] += 1

    threads = [threading.Thread(target=worker) for _ in range(300)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert allowed_count[0] == 50


# ---------------------------------------------------------------------------
# CONCURRENT TURN (per-session_id lock)
# ---------------------------------------------------------------------------

def test_two_concurrent_locks_same_session_only_one_wins():
    registry = TurnLockRegistry()
    results = []

    def attempt(tag):
        got = registry.try_acquire("shared-session")
        results.append((tag, got))
        if got:
            time.sleep(0.05)
            registry.release("shared-session")

    t1 = threading.Thread(target=attempt, args=("a",))
    t2 = threading.Thread(target=attempt, args=("b",))
    t1.start()
    time.sleep(0.01)
    t2.start()
    t1.join()
    t2.join()
    winners = [tag for tag, got in results if got]
    assert len(winners) == 1


def test_different_sessions_acquire_independently():
    registry = TurnLockRegistry()
    assert registry.try_acquire("s1") is True
    assert registry.try_acquire("s2") is True
    registry.release("s1")
    registry.release("s2")


def test_lock_released_after_exception_via_finally():
    registry = TurnLockRegistry()
    assert registry.try_acquire("s1") is True
    try:
        raise RuntimeError("simulirani bug u obradi turna")
    except RuntimeError:
        pass
    finally:
        registry.release("s1")
    assert registry.try_acquire("s1") is True


def test_lock_released_after_simulated_timeout():
    registry = TurnLockRegistry()
    assert registry.try_acquire("s1") is True
    try:
        pass  # ovdje bi bio dugotrajan LLM poziv koji istekne (practice.py to već hvata interno)
    finally:
        registry.release("s1")
    assert registry.try_acquire("s1") is True


def test_concurrent_http_requests_same_session_one_call_gets_409(flask_app, fake_llm):
    """Integracijski test: dva stvarna HTTP zahtjeva iste session_id gotovo
    istovremeno — samo jedan smije stići do FakeLLM, drugi dobija 409."""
    c = _authed_client(flask_app)

    original_practice_turn = fake_llm.practice_turn

    def slow_practice_turn(instructions, input_text):
        time.sleep(0.15)  # drži lock dovoljno dugo da se drugi zahtjev sudari s njim
        return original_practice_turn(instructions, input_text)

    fake_llm.practice_turn = slow_practice_turn
    fake_llm.queue(make_output(reply="ok"))

    results = queue.Queue()
    barrier = threading.Barrier(2)

    def call():
        barrier.wait()
        r = c.post("/api/ai-tutor/chat", json=chat_payload())
        results.put(r.status_code)

    t1 = threading.Thread(target=call)
    t2 = threading.Thread(target=call)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    codes = sorted([results.get(), results.get()])
    assert codes == [200, 409]
    assert fake_llm.call_count == 1


# ---------------------------------------------------------------------------
# INPUT VALIDATION
# ---------------------------------------------------------------------------

def test_invalid_grade_rejected_with_400_no_llm(flask_app, fake_llm):
    c = _authed_client(flask_app)
    r = c.post("/api/ai-tutor/chat", json=chat_payload(grade=12))
    assert r.status_code == 400
    assert r.get_json()["error"] == "INVALID_GRADE"
    assert fake_llm.call_count == 0


def test_non_numeric_grade_rejected_with_400_no_llm(flask_app, fake_llm):
    c = _authed_client(flask_app)
    r = c.post("/api/ai-tutor/chat", json=chat_payload(grade="abc"))
    assert r.status_code == 400
    assert r.get_json()["error"] == "INVALID_GRADE"
    assert fake_llm.call_count == 0


def test_invalid_mode_rejected_with_400_no_llm(flask_app, fake_llm):
    c = _authed_client(flask_app)
    r = c.post("/api/ai-tutor/chat", json=chat_payload(mode="banana"))
    assert r.status_code == 400
    assert r.get_json()["error"] == "INVALID_MODE"
    assert fake_llm.call_count == 0


def test_unknown_selected_topic_rejected_with_400_no_llm(flask_app, fake_llm):
    c = _authed_client(flask_app)
    r = c.post("/api/ai-tutor/chat", json=chat_payload(selected_topic="99-99-999"))
    assert r.status_code == 400
    assert r.get_json()["error"] == "UNKNOWN_TOPIC"
    assert fake_llm.call_count == 0


def test_too_many_history_items_rejected_with_400_no_llm(flask_app, fake_llm):
    c = _authed_client(flask_app)
    history = [{"role": "user", "content": "x"} for _ in range(50)]
    r = c.post("/api/ai-tutor/chat", json=chat_payload(conversation_history=history))
    assert r.status_code == 400
    assert r.get_json()["error"] == "HISTORY_TOO_LARGE"
    assert fake_llm.call_count == 0


def test_history_item_too_long_rejected_with_400_no_llm(flask_app, fake_llm):
    c = _authed_client(flask_app)
    history = [{"role": "user", "content": "x" * 5000}]
    r = c.post("/api/ai-tutor/chat", json=chat_payload(conversation_history=history))
    assert r.status_code == 400
    assert r.get_json()["error"] == "HISTORY_ITEM_TOO_LONG"
    assert fake_llm.call_count == 0


def test_selected_oblast_not_trusted_canonical_used_instead(flask_app, fake_llm):
    """selected_topic postoji u topics.json → server MORA koristiti canonical
    oblast iz topics.json, ne proizvoljan selected_oblast koji klijent pošalje."""
    c = _authed_client(flask_app)
    fake_llm.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    r = c.post("/api/ai-tutor/chat", json=chat_payload(selected_oblast="Potpuno Izmisljena Oblast"))
    assert r.status_code == 200
    prompt_input = fake_llm.calls[-1][1]
    assert "Potpuno Izmisljena Oblast" not in prompt_input


def test_too_long_message_still_returns_200_without_llm_call(flask_app, fake_llm):
    """Postojeće ponašanje (prije ovog hardeninga) — namjerno OSTAJE 200 sa
    prijateljskom porukom, ne 400, da se ne pokvari postojeći ugovor."""
    c = _authed_client(flask_app)
    r = c.post("/api/ai-tutor/chat", json=chat_payload(msg="x" * 5000))
    assert r.status_code == 200
    assert "preduga" in r.get_json()["answer"]
    assert fake_llm.call_count == 0


def test_validation_errors_never_expose_internal_exception(flask_app, fake_llm):
    c = _authed_client(flask_app)
    r = c.post("/api/ai-tutor/chat", json=chat_payload(grade="not-a-number"))
    assert r.status_code == 400
    raw = r.get_data(as_text=True)
    assert "Traceback" not in raw
    assert "ValueError" not in raw
