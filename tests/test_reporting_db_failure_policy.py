"""Izvještajna baza NE SMIJE odlučivati da li učenik dobije odgovor.

Ovo je najvažniji fajl ovog podsistema. Sve ostalo (identitet, upsert, šema) je
korisno; OVO je invarijanta koja se ne smije slomiti ni kad se izvještavanje
kasnije spoji na zahtjevni put. Zato su tutorski testovi ispod napisani tako da
SABOTIRAJU izvještajnu bazu na proces-globalnom nivou (`set_database`) — kad se
poziv jednog dana doda u `matbot/api.py`, ovi testovi ga odmah mjere, bez ijedne
izmjene ovdje.

Nijedan test ne dodiruje živi Turso: sabotirana baza puca ili visi lokalno.
"""
import json
import logging
import threading
import time

import pytest

from matbot import config, reporting_db
from matbot.reporting_db import PROVIDER_THINKIFIC, ReportingUnavailable

from tests.conftest import queue_two_call, make_explain_output, make_quick_output

# Vrijednost koja SMIJE postojati samo u okruženju. Ako se ijednom pojavi u
# logu, poruci greške ili odgovoru — test pada.
FAKE_TOKEN = "turso-secret-token-MUST-NEVER-APPEAR-abc123"
FAKE_URL = "libsql://matbot-secret-host-MUST-NEVER-APPEAR.turso.io"


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", FAKE_URL)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", FAKE_TOKEN)
    yield
    reporting_db.set_database(None)


class ExplodingDatabase:
    """Baza koja pada na svaku operaciju — „Turso je nedostupan / pogrešno
    konfigurisan / vraća operativnu grešku“."""

    def __init__(self, error=None):
        self.error = error or ReportingUnavailable("reporting_db_connect_failed")
        self.calls = 0

    def get_or_create_student(self, *a, **kw):
        self.calls += 1
        raise self.error

    def touch_last_seen(self, *a, **kw):
        self.calls += 1
        raise self.error

    def check(self):
        raise self.error

    def close(self):
        pass


class HangingDatabase:
    """Baza koja VISI. `released` postoji da test ne ostavi nit koja spava do
    kraja svite."""

    def __init__(self, seconds=30.0):
        self.seconds = seconds
        self.released = threading.Event()
        self.calls = 0

    def get_or_create_student(self, *a, **kw):
        self.calls += 1
        self.released.wait(self.seconds)
        return 999

    def touch_last_seen(self, *a, **kw):
        self.calls += 1
        self.released.wait(self.seconds)
        return True

    def check(self):
        self.released.wait(self.seconds)
        return {}

    def close(self):
        self.released.set()


# --- 8) baza nedostupna -----------------------------------------------------
def test_unavailable_database_returns_none_and_never_raises(configured_env, caplog):
    broken = ExplodingDatabase()
    with caplog.at_level(logging.INFO, logger="matbot.reporting_db"):
        result = reporting_db.resolve_student(PROVIDER_THINKIFIC, "42",
                                              display_name="Amina", grade=7,
                                              database=broken)

    assert result is None
    assert broken.calls == 1, "jedan pokušaj, bez retryja"
    assert "student_resolution_failed" in caplog.text
    assert "reporting_db_connect_failed" in caplog.text


def test_unexpected_client_error_is_contained(configured_env, caplog):
    """Bilo koji izuzetak klijenta — ne samo naš tip — mora ostati unutra."""
    broken = ExplodingDatabase(error=RuntimeError("libsql exploded"))
    with caplog.at_level(logging.INFO, logger="matbot.reporting_db"):
        assert reporting_db.resolve_student(PROVIDER_THINKIFIC, "42",
                                            database=broken) is None
    assert "reporting_db_error:RuntimeError" in caplog.text


def test_missing_configuration_is_silent_and_makes_no_attempt(monkeypatch, caplog):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    with caplog.at_level(logging.INFO, logger="matbot.reporting_db"):
        assert reporting_db.resolve_student(PROVIDER_THINKIFIC, "42") is None
    # Nije kvar nego stanje — ne smije zatrpati log po svakom zahtjevu.
    assert caplog.text == ""


def test_half_configured_is_treated_as_disabled(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", FAKE_URL)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    assert config.reporting_db_configured() is False
    assert reporting_db.resolve_student(PROVIDER_THINKIFIC, "42") is None


# --- 9) baza spora / timeout ------------------------------------------------
def test_timeout_returns_none_within_the_configured_bound(configured_env, monkeypatch,
                                                          caplog):
    monkeypatch.setattr(config, "REPORTING_DB_TIMEOUT_S", 0.2)
    hanging = HangingDatabase()
    try:
        started = time.perf_counter()
        with caplog.at_level(logging.INFO, logger="matbot.reporting_db"):
            result = reporting_db.resolve_student(PROVIDER_THINKIFIC, "42",
                                                  database=hanging)
        elapsed = time.perf_counter() - started
    finally:
        hanging.close()

    assert result is None
    assert elapsed < 3.0, "spora baza je zadržala pozivaoca preko roka"
    assert "reporting_db_timeout" in caplog.text


def test_overload_is_shed_not_queued(configured_env, monkeypatch, caplog):
    """Preopterećenje se ODBACUJE. Red čekanja bi sporu bazu pretvorio u
    kašnjenje tutorskog turna — tačno ono što je zabranjeno."""
    monkeypatch.setattr(config, "REPORTING_DB_TIMEOUT_S", 0.2)
    monkeypatch.setattr(config, "REPORTING_DB_MAX_INFLIGHT", 1)
    reporting_db.shutdown()          # svjež executor s novim ograničenjem
    hanging = HangingDatabase()
    try:
        first = threading.Thread(
            target=reporting_db.resolve_student,
            args=(PROVIDER_THINKIFIC, "1"), kwargs={"database": hanging})
        first.start()
        time.sleep(0.05)
        started = time.perf_counter()
        with caplog.at_level(logging.INFO, logger="matbot.reporting_db"):
            assert reporting_db.resolve_student(PROVIDER_THINKIFIC, "2",
                                                database=hanging) is None
        elapsed = time.perf_counter() - started
        first.join(timeout=5)
    finally:
        hanging.close()
        reporting_db.shutdown()

    assert elapsed < 0.2, "drugi poziv je čekao u redu umjesto da bude odbačen"
    assert "reporting_db_busy" in caplog.text


def test_touch_last_seen_failure_is_contained(configured_env, caplog):
    broken = ExplodingDatabase()
    with caplog.at_level(logging.INFO, logger="matbot.reporting_db"):
        assert reporting_db.touch_last_seen(1, database=broken) is False
    assert "reporting_db_unavailable" in caplog.text


# --- 10) tajna ne curi nigdje ----------------------------------------------
def test_secrets_never_appear_in_logs_or_errors(configured_env, caplog):
    broken = ExplodingDatabase(error=RuntimeError("connection to %s with token %s failed"
                                                  % (FAKE_URL, FAKE_TOKEN)))
    with caplog.at_level(logging.DEBUG):
        assert reporting_db.resolve_student(PROVIDER_THINKIFIC, "42",
                                            display_name="Amina Hodžić",
                                            grade=7, database=broken) is None

    assert FAKE_TOKEN not in caplog.text
    assert FAKE_URL not in caplog.text
    assert "Amina" not in caplog.text, "puno ime učenika ne smije u log"
    # Ni sirovi vanjski ID — samo nepovratan otisak.
    assert "external_user_id=42" not in caplog.text
    assert "subject=" in caplog.text


def test_diagnostic_report_carries_no_credentials(configured_env):
    broken = ExplodingDatabase()
    report = reporting_db.diagnose(database=broken)
    rendered = json.dumps(report, default=str) + reporting_db._format_report(report)

    assert FAKE_TOKEN not in rendered
    assert FAKE_URL not in rendered


def test_fingerprint_is_not_the_raw_identifier():
    first = reporting_db._fingerprint(PROVIDER_THINKIFIC, "42")
    again = reporting_db._fingerprint(PROVIDER_THINKIFIC, "42")
    other = reporting_db._fingerprint(PROVIDER_THINKIFIC, "43")

    assert first == again, "otisak mora biti stabilan da bi bio koristan u logu"
    assert first != other
    assert "42" not in first
    assert config.SECRET_KEY not in first


def test_healthz_never_exposes_reporting_database_state(client, configured_env):
    """Javni endpoint ne smije otkrivati stanje ni kredencijale baze (Dio 11)."""
    body = client.get("/healthz").get_data(as_text=True)

    assert FAKE_TOKEN not in body and FAKE_URL not in body
    for forbidden in ("turso", "reporting", "schema_version", "students"):
        assert forbidden not in body.lower()


# --- 8/9) tutorski tokovi ostaju netaknuti ---------------------------------
def _chat(client, mode="practice", msg="Daj mi jedan zadatak za vježbu iz ove teme."):
    return client.post("/api/ai-tutor/chat", json={
        "session_id": "reporting-sess",
        "client_turn_id": "turn-1",
        "grade": 6,
        "mode": mode,
        "entry_source": "manual_topic_choice",
        "selected_topic": "6-01-005",
        "selected_oblast": "",
        "student_message": msg,
        "conversation_history": [],
    })


@pytest.fixture
def sabotaged_reporting(configured_env):
    """Izvještajna baza je konfigurisana i PUCA na svaki dodir."""
    broken = ExplodingDatabase()
    reporting_db.set_database(broken)
    yield broken
    reporting_db.set_database(None)


def test_practice_turn_unaffected_by_reporting_outage(client, fake_llm, sabotaged_reporting):
    queue_two_call(fake_llm)
    r = _chat(client)

    assert r.status_code == 200
    j = r.get_json()
    assert j["status"] == "ready"
    assert j["last_tutor_task"]


def test_explain_turn_unaffected_by_reporting_outage(client, fake_llm, sabotaged_reporting):
    fake_llm.queue(make_explain_output("Evo objašnjenja."))
    r = _chat(client, mode="explain", msg="Objasni mi razlomke.")

    assert r.status_code == 200
    assert "objašnjenja" in r.get_json()["answer"]


def test_quick_turn_unaffected_by_reporting_outage(client, fake_llm, sabotaged_reporting):
    fake_llm.queue(make_quick_output("Rezultat je $x=5$."))
    r = _chat(client, mode="quick", msg="Koliko je 2+3?")

    assert r.status_code == 200
    assert r.get_json()["answer"]


def test_kontrolni_start_unaffected_by_reporting_outage(client, flask_app,
                                                        sabotaged_reporting):
    from tests.test_kontrolni import EchoKontrolniLLM, start_payload

    flask_app.config["MATBOT_LLM"] = EchoKontrolniLLM()
    r = client.post("/api/ai-tutor/exam/start", json=start_payload())

    assert r.status_code == 200
    assert len(r.get_json()["questions"]) == 5


def test_reporting_outage_never_reaches_the_student(client, fake_llm, sabotaged_reporting):
    """Interni kod izvještajne baze ne smije se pojaviti u tijelu odgovora
    (CLAUDE.md, tačka 7 — interni kodovi idu SAMO u log)."""
    queue_two_call(fake_llm)
    body = _chat(client).get_data(as_text=True)

    for forbidden in ("reporting_db", "student_resolution_failed", "turso",
                      FAKE_TOKEN, FAKE_URL):
        assert forbidden not in body


def test_cli_output_survives_a_non_utf8_console():
    """Dijagnostika koja pukne pri ispisu nije dijagnostika.

    IZMJERENO: bosanski dijakritik u argparse pomoćniku obara `python -m
    matbot.reporting_db` na Windows konzoli (cp1252, UnicodeEncodeError). Sav
    tekst koji ide na stdout mora zato biti ASCII — poruke i `_format_report`
    jednako."""
    import argparse
    import io as _io

    parser = argparse.ArgumentParser(
        prog="python -m matbot.reporting_db",
        description="Provjera izvjestajne baze (samo citanje, ne mijenja podatke).")
    buffer = _io.StringIO()
    parser.print_help(buffer)

    report = {"configured": True, "connected": True, "foreign_keys_on": True,
              "schema_version": 1, "expected_schema_version": 1,
              "schema_version_matches": True, "missing_tables": [],
              "columns": {"students": ["id", "display_name"]}}
    printable = reporting_db._format_report(report)

    for text in (printable, reporting_db._format_report({"configured": False,
                                                         "connected": False,
                                                         "error": "x"})):
        text.encode("ascii")   # padne na dijakritiku


# --- Dio 14) ograničeni resursi: niti se ne gomilaju i ne drže gašenje ------
def test_hung_calls_never_accumulate_threads(configured_env, monkeypatch):
    """Trajno zaglavljen udaljeni poziv ne smije nagomilati niti.

    Semafor se uzima NEBLOKIRAJUĆE, pa i stotinu zahtjeva nad bazom koja visi
    ostavlja najviše `REPORTING_DB_MAX_INFLIGHT` živih izvještajnih niti —
    ostalo padne na `reporting_db_busy` bez ijedne nove niti."""
    monkeypatch.setattr(config, "REPORTING_DB_TIMEOUT_S", 0.05)
    monkeypatch.setattr(config, "REPORTING_DB_MAX_INFLIGHT", 2)
    reporting_db.shutdown()
    hanging = HangingDatabase()

    def reporting_threads():
        return [t for t in threading.enumerate() if t.name == "matbot-reporting"]

    try:
        for _ in range(100):
            assert reporting_db.resolve_student(PROVIDER_THINKIFIC, "42",
                                                database=hanging) is None
        live = reporting_threads()
        assert len(live) <= 2, "izvještajne niti se gomilaju: %d" % len(live)
        assert all(t.daemon for t in live), (
            "zaglavljena nit koja nije daemon bi zadržala gašenje procesa")
    finally:
        hanging.close()
        reporting_db.shutdown()


def test_reporting_threads_are_daemon_threads(configured_env, monkeypatch):
    """Zašto NE `ThreadPoolExecutor`: od Pythona 3.9 njegove niti nisu daemon i
    spaja ih atexit kuka, pa bi JEDAN zaglavljen poziv blokirao izlaz
    interpretera — uredan gunicorn restart bi postao SIGKILL."""
    monkeypatch.setattr(config, "REPORTING_DB_TIMEOUT_S", 0.05)
    reporting_db.shutdown()
    hanging = HangingDatabase()
    try:
        reporting_db.resolve_student(PROVIDER_THINKIFIC, "42", database=hanging)
        live = [t for t in threading.enumerate() if t.name == "matbot-reporting"]
        assert live and all(t.daemon for t in live)
    finally:
        hanging.close()
        reporting_db.shutdown()
