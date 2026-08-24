"""Thinkific e-mail → TAČNO jedan izvještajni učenik (MVP).

Dvije odvojene tvrdnje se ovdje dokazuju i ne smiju se brkati:

  1. NORMALIZACIJA — kad je e-mail isti učenik, a kad nije. Namjerno glupa:
     spajanje dva učenika je nepopravljivo, razdvajanje jednog nije.
  2. TRANSPORT — e-mail se čita SAMO na `GET /` i odmah veže u potpisani
     `X-Tutor-Token`. Poslije toga nijedan tutorski zahtjev ne može promijeniti
     pripisivanje. Test s lažnim e-mailom u chat JSON-u je najvažniji u fajlu.

ŠTA SE OVDJE NE TVRDI: da je identitet autentifikovan. Nije — učenik koji ručno
otvori `/?thinkific_email=neko@drugi.com` pripisaće aktivnost tuđoj adresi, i to
je izmjeren, prihvaćen MVP kompromis (`test_manual_url_can_attribute_to_another
_address_known_mvp_limit` to i dokazuje, da granica ostane vidljiva u kodu).

Nijedan test ne dodiruje živi Turso — sve ide na lokalnu libsql datoteku.
"""
import logging
import threading

import pytest

from matbot import auth, reporting_db, student_identity
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL, normalize_email

from tests.conftest import queue_two_call
from tests.test_reporting_db_identity import SCHEMA  # ista šema kao produkcijska

libsql = pytest.importorskip("libsql")


# ---------------------------------------------------------------------------
# Infrastruktura
# ---------------------------------------------------------------------------
@pytest.fixture
def reporting(tmp_path, monkeypatch):
    """Prava libsql baza na disku + ubačena u proces-globalni singleton."""
    path = str(tmp_path / "reporting.db")
    conn = libsql.connect(path)
    for statement in SCHEMA.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
    conn.commit()
    conn.close()

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0, _check_same_thread=False))
    reporting_db.set_database(database)
    yield path
    # Odvojene niti su daemon i prezivjele bi ovaj test; drenaza
    # sprjecava da zaostao upis obori sljedeci test.
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def rows(path, sql, params=()):
    conn = libsql.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def token_from(response):
    body = response.get_data(as_text=True)
    marker = 'name="matbot-embed-token" content="'
    start = body.index(marker) + len(marker)
    return body[start:body.index('"', start)]


def enter(client, email=None):
    """Učenik otvara MAT-BOT iz Thinkific lekcije; vraća token stranice."""
    query = {student_identity.QUERY_PARAM: email} if email is not None else {}
    response = client.get("/", query_string=query)
    assert response.status_code == 200
    return token_from(response)


def chat(client, token, mode="practice", **payload_overrides):
    payload = {
        "session_id": "sess-a",
        "client_turn_id": "turn-1",
        "grade": 6,
        "mode": mode,
        "entry_source": "manual_topic_choice",
        "selected_topic": "6-01-005",
        "selected_oblast": "",
        "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
        "conversation_history": [],
    }
    payload.update(payload_overrides)
    return client.post("/api/ai-tutor/chat", json=payload,
                       headers={auth.TOKEN_HEADER: token})


def students(path):
    return rows(path, "SELECT COUNT(*) FROM students")[0][0]


def student_id_for(path, email):
    found = rows(path,
                 "SELECT student_id FROM student_accounts "
                 "WHERE provider = ? AND external_user_id = ?",
                 (PROVIDER_THINKIFIC_EMAIL, email))
    return found[0][0] if found else None


# ---------------------------------------------------------------------------
# 1) Normalizacija
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("  Student@Example.com  ", "student@example.com"),
    ("student@example.com", "student@example.com"),
    ("STUDENT@EXAMPLE.COM", "student@example.com"),
    ("\tstudent@example.com\n", "student@example.com"),
])
def test_normalization_is_case_and_whitespace_only(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("left, right", [
    ("john.smith@gmail.com", "johnsmith@gmail.com"),     # tačke se NE uklanjaju
    ("user+1@example.com", "user@example.com"),          # +tag se NE skida
    ("a@example.com", "a@example.org"),                  # različit domen
])
def test_normalization_never_merges_distinct_addresses(left, right):
    assert normalize_email(left) != normalize_email(right)


@pytest.mark.parametrize("bad", [
    "", "   ", "nema-majmuna", "dva@@example.com", "bez-domena@x",
    "@example.com", "student@", "sa razmakom@example.com", None, 42,
    "x@example.com\nBcc: napad@zlo.com",                 # ubacivanje novog reda
    "a" * 65 + "@example.com",                           # predug lokalni dio
    "a@" + "b" * 250 + ".com",                           # preduga adresa
    "{{email}}",                                         # nezamijenjen Liquid
])
def test_invalid_email_is_rejected_not_guessed(bad):
    assert normalize_email(bad) is None


def test_invalid_email_creates_no_student(reporting):
    identity = {"provider": PROVIDER_THINKIFIC_EMAIL, "external_user_id": ""}
    assert student_identity.resolve_student(identity) is None
    assert students(reporting) == 0


# ---------------------------------------------------------------------------
# 2) GET / — jedini ulaz identiteta
# ---------------------------------------------------------------------------
def test_get_reads_only_the_thinkific_email_parameter(client):
    token = enter(client, "Student@Example.com")
    identity = auth.reporting_identity(auth.verify_token(token))

    assert identity == {"provider": PROVIDER_THINKIFIC_EMAIL,
                        "external_user_id": "student@example.com"}


@pytest.mark.parametrize("value", ["", "   ", "nije-email", "{{email}}"])
def test_invalid_query_email_yields_an_anonymous_token(client, value):
    token = enter(client, value)
    assert auth.reporting_identity(auth.verify_token(token)) is None


def test_absent_parameter_yields_an_anonymous_token(client):
    assert auth.reporting_identity(auth.verify_token(enter(client))) is None


def test_other_query_parameters_are_ignored(client):
    """Samo `thinkific_email` se čita — nijedno drugo ime nije ulaz."""
    response = client.get("/", query_string={"email": "napadac@example.com",
                                             "sub": "napadac@example.com",
                                             "external_user_id": "napadac@example.com"})
    assert auth.reporting_identity(auth.verify_token(token_from(response))) is None


# ---------------------------------------------------------------------------
# 3) Kroz cijelu aplikaciju: e-mail -> tačno jedan učenik
# ---------------------------------------------------------------------------
def test_case_and_space_variants_are_one_student(client, fake_llm, reporting):
    for raw in ("Student@Example.com", "student@example.com",
                "  STUDENT@example.com  "):
        queue_two_call(fake_llm)
        assert chat(client, enter(client, raw)).status_code == 200

    assert students(reporting) == 1
    assert student_id_for(reporting, "student@example.com") is not None


def test_same_email_from_a_different_browser_is_the_same_student(client, fake_llm,
                                                                 reporting):
    queue_two_call(fake_llm)
    chat(client, enter(client, "student@example.com"), session_id="browser-1")
    first = student_id_for(reporting, "student@example.com")

    # Drugi pregledač: nov session_id, nov token, ISTI e-mail iz lekcije.
    queue_two_call(fake_llm)
    chat(client, enter(client, "student@example.com"), session_id="browser-2")

    assert students(reporting) == 1
    assert student_id_for(reporting, "student@example.com") == first


def test_different_emails_are_different_students(client, fake_llm, reporting):
    for email in ("amina@example.com", "emir@example.com"):
        queue_two_call(fake_llm)
        chat(client, enter(client, email))

    assert students(reporting) == 2
    assert (student_id_for(reporting, "amina@example.com")
            != student_id_for(reporting, "emir@example.com"))


def test_first_use_creates_the_student_automatically(client, fake_llm, reporting):
    assert students(reporting) == 0
    queue_two_call(fake_llm)
    chat(client, enter(client, "novi@example.com"))

    assert students(reporting) == 1
    assert rows(reporting, "SELECT provider, external_user_id FROM student_accounts") \
        == [(PROVIDER_THINKIFIC_EMAIL, "novi@example.com")]


def test_grade_is_metadata_and_never_splits_an_identity(client, fake_llm, reporting):
    """DIO 7: razred bira učenik u MAT-BOT meniju — nije identitet."""
    queue_two_call(fake_llm)
    chat(client, enter(client, "amina@example.com"), grade=6)
    first = student_id_for(reporting, "amina@example.com")

    # Isti učenik, drugi razred u meniju (druga lekcija tog razreda).
    queue_two_call(fake_llm)
    chat(client, enter(client, "amina@example.com"), grade=7,
         selected_topic="7-01-001")

    assert students(reporting) == 1
    assert student_id_for(reporting, "amina@example.com") == first


def test_display_name_is_never_invented_from_the_email(client, fake_llm, reporting):
    """DIO 8: ime se NE izvodi iz adrese — ostaje prazno."""
    queue_two_call(fake_llm)
    chat(client, enter(client, "amina.hodzic@example.com"))

    assert rows(reporting, "SELECT display_name FROM students") == [(None,)]


def test_kontrolni_resolves_the_same_student_as_chat(client, flask_app, fake_llm,
                                                     reporting):
    from tests.test_kontrolni import EchoKontrolniLLM, start_payload

    queue_two_call(fake_llm)
    token = enter(client, "amina@example.com")
    chat(client, token)
    from_chat = student_id_for(reporting, "amina@example.com")

    flask_app.config["MATBOT_LLM"] = EchoKontrolniLLM()
    r = client.post("/api/ai-tutor/exam/start", json=start_payload(),
                    headers={auth.TOKEN_HEADER: token})

    assert r.status_code == 200
    assert students(reporting) == 1
    assert student_id_for(reporting, "amina@example.com") == from_chat


# ---------------------------------------------------------------------------
# 4) NAJVAŽNIJE: zahtjev poslije učitavanja ne smije promijeniti pripisivanje
# ---------------------------------------------------------------------------
def test_email_in_the_chat_payload_never_overrides_the_token(client, fake_llm,
                                                             reporting):
    """Zahtjev nosi ISPRAVAN token žrtve i LAŽNU adresu u pet JSON polja."""
    queue_two_call(fake_llm)
    token = enter(client, "zrtva@example.com")

    response = chat(client, token,
                    email="napadac@example.com",
                    student_email="napadac@example.com",
                    thinkific_email="napadac@example.com",
                    external_user_id="napadac@example.com",
                    sub="napadac@example.com")

    assert response.status_code == 200
    assert students(reporting) == 1
    assert student_id_for(reporting, "zrtva@example.com") is not None
    assert student_id_for(reporting, "napadac@example.com") is None


def test_query_parameter_on_the_api_endpoint_is_ignored(client, fake_llm, reporting):
    """E-mail se troši SAMO na `GET /` — ne i na tutorskim endpointima."""
    queue_two_call(fake_llm)
    token = enter(client, "zrtva@example.com")

    response = client.post(
        "/api/ai-tutor/chat?thinkific_email=napadac@example.com",
        json={"session_id": "sess-a", "client_turn_id": "t1", "grade": 6,
              "mode": "practice", "entry_source": "manual_topic_choice",
              "selected_topic": "6-01-005", "selected_oblast": "",
              "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
              "conversation_history": []},
        headers={auth.TOKEN_HEADER: token})

    assert response.status_code == 200
    assert student_id_for(reporting, "napadac@example.com") is None
    assert student_id_for(reporting, "zrtva@example.com") is not None


def test_tampered_token_cannot_carry_a_reporting_identity(client, fake_llm,
                                                          reporting):
    """Bez serverskog potpisa se identitet ne može ni proizvesti ni izmijeniti."""
    token = enter(client, "zrtva@example.com")
    forged = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")

    with pytest.raises(auth.TokenError):
        auth.verify_token(forged)

    response = chat(client, forged)
    assert response.status_code == 401
    assert students(reporting) == 0


def test_anonymous_token_cannot_be_upgraded_by_the_request(client, fake_llm,
                                                           reporting):
    queue_two_call(fake_llm)
    token = enter(client)                       # bez identiteta

    assert chat(client, token, thinkific_email="napadac@example.com").status_code == 200
    assert students(reporting) == 0


def test_unknown_claim_version_is_treated_as_anonymous():
    """Nepoznata verzija tvrdnje = ODSUTAN identitet, nikad pogrešno tumačenje."""
    from itsdangerous import URLSafeTimedSerializer
    from matbot import config

    serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="matbot-embed-token")
    token = serializer.dumps({
        "purpose": auth.TOKEN_PURPOSE, "nonce": "x",
        auth.REPORTING_IDENTITY_CLAIM: {"v": 99, "provider": PROVIDER_THINKIFIC_EMAIL,
                                        "external_user_id": "a@b.com"}})

    assert auth.reporting_identity(auth.verify_token(token)) is None


def test_unknown_provider_is_treated_as_anonymous():
    assert auth.issue_token({"provider": "izmisljeno",
                             "external_user_id": "a@b.com"}) is not None
    token = auth.issue_token({"provider": "izmisljeno",
                              "external_user_id": "a@b.com"})
    assert auth.reporting_identity(auth.verify_token(token)) is None


def test_expired_token_still_behaves_exactly_as_before(client, monkeypatch):
    """DIO 14.11: postojeće auth ponašanje se ne mijenja identitetom."""
    from matbot import config

    token = enter(client, "student@example.com")
    monkeypatch.setattr(config, "TOKEN_TTL_SECONDS", -1)
    with pytest.raises(auth.TokenError) as expired:
        auth.verify_token(token)
    assert expired.value.code == "EXPIRED"


def test_missing_token_is_still_rejected(flask_app, reporting):
    unauthenticated = flask_app.test_client()
    response = unauthenticated.post("/api/ai-tutor/chat", json={})

    assert response.status_code == 401
    assert students(reporting) == 0


# ---------------------------------------------------------------------------
# 5) Prihvaćena MVP granica — dokazana, da ostane vidljiva
# ---------------------------------------------------------------------------
def test_manual_url_can_attribute_to_another_address_known_mvp_limit(
        client, fake_llm, reporting):
    """IZRIČITO PRIHVAĆENO OGRANIČENJE, nije propust.

    Bez potpisa Thinkific strane server ne može razlikovati stvarni ulazak kroz
    lekciju od ručno otkucanog URL-a. Zato pripisivanje NIJE ovlaštenje: ovaj
    identitet ne smije otvoriti tuđ izvještaj niti dati ijedno pravo. Test
    postoji da granica ostane MJERENA i vidljiva, a ne usmena."""
    queue_two_call(fake_llm)
    chat(client, enter(client, "tudja.adresa@example.com"))

    assert student_id_for(reporting, "tudja.adresa@example.com") is not None


# ---------------------------------------------------------------------------
# 6) Anonimno i otkazi
# ---------------------------------------------------------------------------
def test_without_an_email_tutoring_works_and_no_student_is_created(client, fake_llm,
                                                                   reporting):
    queue_two_call(fake_llm)
    result = chat(client, enter(client))

    assert result.status_code == 200
    assert result.get_json()["status"] == "ready"
    assert students(reporting) == 0, "session_id se NE smije koristiti kao identitet"


def test_invalid_email_leaves_tutoring_anonymous(client, fake_llm, reporting):
    queue_two_call(fake_llm)
    result = chat(client, enter(client, "nije-email"))

    assert result.status_code == 200
    assert result.get_json()["status"] == "ready"
    assert students(reporting) == 0


def test_reporting_outage_does_not_affect_an_identified_student(client, fake_llm,
                                                                monkeypatch):
    from tests.test_reporting_db_failure_policy import ExplodingDatabase

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    reporting_db.set_database(ExplodingDatabase())
    try:
        queue_two_call(fake_llm)
        response = chat(client, enter(client, "student@example.com"))
    finally:
        reporting_db.set_database(None)

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    body = response.get_data(as_text=True)
    assert "reporting" not in body and "turso" not in body.lower()


def test_concurrent_first_requests_create_one_student(reporting):
    """Ista adresa, četiri istovremena prva zahtjeva."""
    path = reporting
    barrier = threading.Barrier(4)
    results, errors = [], []

    def worker():
        database = reporting_db.ReportingDatabase(
            connect_factory=lambda: libsql.connect(path, timeout=10.0, _check_same_thread=False))
        try:
            barrier.wait(timeout=10)
            results.append(database.get_or_create_student(
                PROVIDER_THINKIFIC_EMAIL, "student@example.com"))
        except Exception as exc:   # pragma: no cover - dijagnostika pri padu
            errors.append(repr(exc))
        finally:
            database.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    assert len(set(results)) == 1
    assert students(path) == 1


# ---------------------------------------------------------------------------
# 7) Privatnost: sirovi e-mail nikad u logu
# ---------------------------------------------------------------------------
def test_raw_email_never_appears_in_logs(client, fake_llm, monkeypatch, caplog):
    from tests.test_reporting_db_failure_policy import ExplodingDatabase

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    reporting_db.set_database(ExplodingDatabase())
    try:
        with caplog.at_level(logging.DEBUG):
            queue_two_call(fake_llm)
            chat(client, enter(client, "tajna.adresa@example.com"))
    finally:
        reporting_db.set_database(None)

    assert "tajna.adresa@example.com" not in caplog.text
    assert "tajna.adresa" not in caplog.text


def test_rejected_email_logs_only_a_code(client, caplog):
    with caplog.at_level(logging.INFO, logger="matbot.student_identity"):
        client.get("/", query_string={student_identity.QUERY_PARAM:
                                      "napadac-nije-email"})

    assert "email_unusable" in caplog.text
    assert "napadac" not in caplog.text


def test_email_is_not_duplicated_into_the_students_table(client, fake_llm, reporting):
    """DIO 12: adresa smije postojati SAMO u `student_accounts.external_user_id`."""
    queue_two_call(fake_llm)
    chat(client, enter(client, "amina@example.com"))

    everything = str(rows(reporting, "SELECT * FROM students"))
    assert "amina@example.com" not in everything
    assert "@" not in everything


def test_fingerprint_is_stable_and_not_the_address():
    first = student_identity.fingerprint("student@example.com")
    again = student_identity.fingerprint("student@example.com")
    other = student_identity.fingerprint("drugi@example.com")

    assert first == again
    assert first != other
    assert "student" not in first and "@" not in first
