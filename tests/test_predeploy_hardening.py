"""Testovi za pre-deployment hardening: SECRET_KEY/FLASK_SECRET_KEY
kompatibilnost i lifecycle TurnLockRegistry-ja. Ne pokreće stvarne OpenAI
pozive — FakeLLM iz conftest.py."""
import threading
import time

import pytest

from matbot import config
from matbot.turnlock import TurnLockRegistry
from tests.conftest import FakeLLM, make_output


# ---------------------------------------------------------------------------
# SECRET_KEY / FLASK_SECRET_KEY kompatibilnost
# ---------------------------------------------------------------------------

def test_only_flask_secret_key_set(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "flask-value")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert config._resolve_secret_key() == "flask-value"


def test_only_legacy_secret_key_alias_set(monkeypatch):
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", "legacy-value")
    assert config._resolve_secret_key() == "legacy-value"


def test_both_set_flask_secret_key_takes_precedence(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "new-value")
    monkeypatch.setenv("SECRET_KEY", "legacy-value")
    assert config._resolve_secret_key() == "new-value"


def test_neither_set_causes_controlled_startup_failure(monkeypatch):
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    resolved = config._resolve_secret_key()
    assert resolved == ""
    with pytest.raises(RuntimeError) as exc:
        config.require_secret_key(resolved)
    assert "FLASK_SECRET_KEY" in str(exc.value)
    assert "SECRET_KEY" in str(exc.value)


def test_secret_value_never_appears_in_error_message(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "super-tajna-vrijednost-xyz")
    # simuliramo grešku (prazan secret) i provjeravamo da PORUKA nikad ne
    # uključuje bilo koju konfigurisanu tajnu vrijednost iz environmenta
    with pytest.raises(RuntimeError) as exc:
        config.require_secret_key("")
    assert "super-tajna-vrijednost-xyz" not in str(exc.value)


def test_valid_secret_passes_through_unchanged():
    assert config.require_secret_key("neki-validan-secret") == "neki-validan-secret"


def test_app_starts_with_only_secret_key_alias(monkeypatch):
    """Regresija za stvarni VPS scenario: produkcija ima SAMO SECRET_KEY
    (staru varijablu), bez FLASK_SECRET_KEY — app.py mora i dalje raditi."""
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", "vps-legacy-secret-value")
    resolved = config._resolve_secret_key()
    assert config.require_secret_key(resolved) == "vps-legacy-secret-value"


# ---------------------------------------------------------------------------
# TurnLockRegistry lifecycle
# ---------------------------------------------------------------------------

def test_same_session_still_limited_to_one_concurrent_lock():
    registry = TurnLockRegistry()
    assert registry.try_acquire("s1") is True
    assert registry.try_acquire("s1") is False  # već zauzeta
    registry.release("s1")
    assert registry.try_acquire("s1") is True  # sad opet slobodna


def test_different_sessions_run_in_parallel():
    registry = TurnLockRegistry()
    assert registry.try_acquire("s1") is True
    assert registry.try_acquire("s2") is True
    assert registry.try_acquire("s3") is True
    registry.release("s1")
    registry.release("s2")
    registry.release("s3")


def test_lock_released_after_success_is_removed_from_registry():
    registry = TurnLockRegistry()
    registry.try_acquire("s1")
    assert registry.size() == 1
    registry.release("s1")
    assert registry.size() == 0  # eager cleanup — nema trajno visećeg unosa


def test_lock_released_after_exception_via_finally_and_removed():
    registry = TurnLockRegistry()
    registry.try_acquire("s1")
    try:
        raise RuntimeError("simulirani bug")
    except RuntimeError:
        pass
    finally:
        registry.release("s1")
    assert registry.size() == 0
    assert registry.try_acquire("s1") is True


def test_registry_does_not_grow_after_many_completed_unique_sessions():
    """Nakon 5000 RAZLIČITIH, ali svaki put ODMAH ZAVRŠENIH sesija, registry
    ne smije akumulirati 5000 unosa — eager cleanup u release() mora držati
    veličinu blizu broja trenutno AKTIVNIH (a ne ikad viđenih) sesija."""
    registry = TurnLockRegistry()
    for i in range(5000):
        session_id = f"sess-{i}"
        registry.try_acquire(session_id)
        registry.release(session_id)
    assert registry.size() == 0


def test_cleanup_does_not_race_with_concurrent_acquire_same_session():
    """Konkurentni test: dok jedan thread stalno acquire/release-uje istu
    session_id, drugi thread paralelno pokušava acquire — nikad ne smije doći
    do stanja gdje oba threada MISLE da drže ekskluzivan pristup istovremeno,
    niti do izuzetka u registru."""
    registry = TurnLockRegistry()
    errors = []
    both_locked_detected = [False]
    stop = threading.Event()
    state_lock = threading.Lock()
    holder = [None]

    def churn(tag):
        try:
            while not stop.is_set():
                got = registry.try_acquire("shared")
                if got:
                    with state_lock:
                        if holder[0] is not None:
                            both_locked_detected[0] = True
                        holder[0] = tag
                    time.sleep(0.001)
                    with state_lock:
                        holder[0] = None
                    registry.release("shared")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=churn, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.5)
    stop.set()
    for t in threads:
        t.join()

    assert not errors
    assert both_locked_detected[0] is False
    assert registry.size() == 0


def test_capacity_guard_never_evicts_an_actively_held_lock():
    """Uz eager cleanup u release(), evikcija po kapacitetu je rijetko potreban
    backstop — samo za slučaj kad SVE praćene sesije budu istovremeno aktivno
    zaključane. Pravilo se ne smije prekršiti: aktivno držan lock se NIKAD ne
    uklanja, čak ni kad se pređe max_tracked_sessions."""
    registry = TurnLockRegistry(max_tracked_sessions=2)
    registry.try_acquire("a")
    registry.try_acquire("b")
    # a i b su i dalje AKTIVNO zaključani (nema release-a) — treća RAZLIČITA
    # sesija ne smije izbaciti nijedan od njih jer nema šta slobodno da evikuje
    registry.try_acquire("c")
    assert registry.size() == 3  # kapacitet namjerno nadjačan — nema šta da se evikuje
    for sid in ("a", "b"):
        assert registry.try_acquire(sid) is False  # i dalje zaključani, nisu izbačeni
    for sid in ("a", "b", "c"):
        registry.release(sid)
    assert registry.size() == 0  # eager cleanup poslije oslobađanja svih
