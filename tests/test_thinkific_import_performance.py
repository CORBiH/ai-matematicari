"""Uvoz Thinkific fajla: PAKETNI put — brzina BEZ gubitka ijedne garancije.

IZMJERENI PROBLEM (34 učenika × 7 sekcija, sintetički fajl oblika stvarnog
izvoza): red-po-red put je radio ~580 SQL izjava i **103 commita** — dakle ~683
mrežna obrta po fajlu. Na udaljenom Tursu je svaki obrt puna mrežna tura, a
commit je najskuplji jer mora trajno potvrditi upis. U ovom putu nema nijednog
modela; cijelo kašnjenje je bilo mrežno.

Ovaj fajl čuva OBJE strane pogodbe:
  1. BROJKE — jedna konekcija, JEDAN commit po fajlu, nekoliko `IN (...)` upita
     umjesto po nekoliko po učeniku, i nijedan commit po sekciji.
  2. ZNAČENJE — identitet, idempotentnost, zamjena skupa sekcija, sukob razreda
     i privatnost su bajt za bajt isti kao prije ubrzanja.

Broj izjava se mjeri OMOTAČEM oko konekcije: to je jedina provjera koja stvarno
dokazuje da ubrzanje postoji i da neće tiho nestati pri sljedećoj izmjeni.
"""
import collections
import logging
import re

import pytest

from matbot import report_input, reporting_db
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.fixtures.thinkific import build_csv, learner, with_sections
from tests.test_thinkific_progress_import import build_v1, migrate, rows

libsql = pytest.importorskip("libsql")

# Sedam sekcija, kao stvarni izvoz 6. razreda — uključujući naziv sa zarezom.
SECTIONS = ["SKUPOVI", "KRUŽNICA, KRUG, UGAO", "N i No SKUPOVI",
            "DJELJIVOST BROJEVA", "RAZLOMCI", "RAZLOMCI U DECIMALNOM OBLIKU",
            "DECIMALNI BROJEVI - OPERACIJE"]
LEARNERS = 34


class CountingConnection:
    """Prebrojava SQL izjave i commite. Ne mijenja nijedno ponašanje."""

    def __init__(self, inner, stats):
        self._inner = inner
        self._stats = stats
        stats["connections"] += 1

    def execute(self, sql, *args, **kwargs):
        verb = re.match(r"\s*(\w+)", sql).group(1).upper()
        self._stats["statements"] += 1
        self._stats[verb] += 1
        return self._inner.execute(sql, *args, **kwargs)

    def executemany(self, sql, seq):
        seq = list(seq)
        self._stats["executemany_calls"] += 1
        self._stats["executemany_rows"] += len(seq)
        return self._inner.executemany(sql, seq)

    def commit(self):
        self._stats["commits"] += 1
        return self._inner.commit()

    def rollback(self):
        self._stats["rollbacks"] += 1
        return self._inner.rollback()

    def close(self):
        return self._inner.close()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def real_shaped_csv(count=LEARNERS, **overrides):
    entries = []
    for index in range(count):
        row = learner("learner%02d@example.com" % index,
                      first="Ime%02d" % index, last="Prezime%02d" % index,
                      viewed=(index * 3) % 101, completed=(index * 2) % 101,
                      **overrides)
        entries.append(with_sections(
            row, {name: (index * 7 + position * 3) % 101
                  for position, name in enumerate(SECTIONS)}))
    return build_csv(entries, sections=SECTIONS)


@pytest.fixture
def measured(tmp_path, monkeypatch):
    """Baza na šemi v2 + brojači izjava."""
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    migrate(path)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    stats = collections.Counter()
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: CountingConnection(
            libsql.connect(path, timeout=10.0, _check_same_thread=False), stats))
    reporting_db.set_database(database)
    yield path, stats
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def snapshots(path):
    return rows(path, "SELECT id, student_id, percent_viewed, percent_completed "
                      "FROM thinkific_progress_snapshots ORDER BY id")


def sections_of(path, snapshot_id):
    return rows(path, "SELECT ordinal, section_name, progress_percent "
                      "FROM thinkific_progress_sections WHERE snapshot_id = ? "
                      "ORDER BY ordinal", (snapshot_id,))


# ---------------------------------------------------------------------------
# BROJKE
# ---------------------------------------------------------------------------
def test_whole_file_uses_one_connection_and_one_commit(measured):
    path, stats = measured
    stats.clear()

    summary = report_input.import_progress_files(
        "2026-09", {"grade_6": real_shaped_csv()})

    assert summary.rows_seen == LEARNERS and summary.files_imported == 1
    # JEDAN commit za cijeli fajl — ranije ih je bilo 103.
    assert stats["commits"] == 1, "commit po ucenika se vratio"
    assert stats["connections"] == 1, "otvorena je vise od jedne konekcije"
    assert stats["rollbacks"] == 0


def test_no_per_section_commit_and_sections_are_written_in_one_batch(measured):
    path, stats = measured
    stats.clear()
    report_input.import_progress_files("2026-09", {"grade_6": real_shaped_csv()})

    # 34 x 7 = 238 sekcija. Viseredni `VALUES` ih upisuje u NAJVISE dvije
    # izjave (200 redova po izjavi), a brisanje ide jednim `DELETE ... IN`.
    # Namjerno se NE koristi `executemany`: on ne garantuje jednu mreznu turu.
    assert stats["executemany_calls"] == 0, "executemany ne dokazuje broj tura"
    assert stats["DELETE"] == 1, "brisanje sekcija ide po snimku umjesto grupno"
    # 1 uvoz + 34 ucenika + 34 naloga + 34 snimka + 2 paketa sekcija = 105
    assert stats["INSERT"] == 105, "broj INSERT izjava: %d" % stats["INSERT"]
    assert stats["commits"] == 1


def test_lookups_are_batched_not_per_learner(measured):
    path, stats = measured
    stats.clear()
    report_input.import_progress_files("2026-09", {"grade_6": real_shaped_csv()})

    # Tri `IN (...)` upita (nalozi, profili, snimci) za CIJELI fajl.
    assert stats["SELECT"] <= 5, "vratio se upit po ucenika (SELECT=%d)" % stats["SELECT"]
    # Gornja granica ukupnog broja izjava — cuva izmjereni dobitak od regresije.
    assert stats["statements"] < 150, "broj izjava je narastao: %d" % stats["statements"]


def test_reimport_of_the_same_file_stays_batched(measured):
    path, stats = measured
    payload = real_shaped_csv()
    report_input.import_progress_files("2026-09", {"grade_6": payload})
    stats.clear()

    report_input.import_progress_files("2026-09", {"grade_6": payload})

    assert stats["commits"] == 1
    assert stats["SELECT"] <= 5
    # Ponovni uvoz ne pravi nijednog ucenika: 1 uvoz + 2 paketa sekcija.
    assert stats["INSERT"] == 3, "broj INSERT izjava: %d" % stats["INSERT"]


# ---------------------------------------------------------------------------
# ZNACENJE (nepromijenjeno)
# ---------------------------------------------------------------------------
def test_thirty_four_row_file_imports_correctly(measured):
    path, _ = measured
    summary = report_input.import_progress_files(
        "2026-09", {"grade_6": real_shaped_csv()})

    assert summary.students_created == LEARNERS
    assert summary.snapshots_inserted == LEARNERS
    assert summary.sections_written == LEARNERS * len(SECTIONS)
    assert rows(path, "SELECT COUNT(*) FROM students")[0][0] == LEARNERS
    stored = snapshots(path)
    assert len(stored) == LEARNERS
    # Vrijednosti su TACNO one iz fajla, ne priblizne.
    assert stored[0][2] == 0.0 and stored[1][2] == 3.0
    assert stored[0][3] == 0.0 and stored[1][3] == 2.0
    first = sections_of(path, stored[0][0])
    assert [s[1] for s in first] == SECTIONS
    assert [s[0] for s in first] == list(range(1, len(SECTIONS) + 1))
    assert [s[2] for s in first] == [0.0, 3.0, 6.0, 9.0, 12.0, 15.0, 18.0]


def test_existing_students_are_reused_not_duplicated(measured):
    path, _ = measured
    database = reporting_db.get_database()
    known = {}
    for index in range(17):
        email = "learner%02d@example.com" % index
        known[email] = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, email)

    summary = report_input.import_progress_files(
        "2026-09", {"grade_6": real_shaped_csv()})

    assert summary.students_reused == 17 and summary.students_created == 17
    assert rows(path, "SELECT COUNT(*) FROM students")[0][0] == LEARNERS
    for email, student_id in known.items():
        stored = rows(path, "SELECT student_id FROM student_accounts "
                            "WHERE provider = ? AND external_user_id = ?",
                      (PROVIDER_THINKIFIC_EMAIL, email))
        assert stored == [(student_id,)], "identitet postojeceg ucenika se promijenio"


def test_same_file_twice_is_idempotent(measured):
    path, _ = measured
    payload = real_shaped_csv()
    first = report_input.import_progress_files("2026-09", {"grade_6": payload})
    second = report_input.import_progress_files("2026-09", {"grade_6": payload})

    assert first.snapshots_inserted == LEARNERS
    assert second.snapshots_updated == LEARNERS and second.snapshots_inserted == 0
    assert rows(path, "SELECT COUNT(*) FROM students")[0][0] == LEARNERS
    assert len(snapshots(path)) == LEARNERS
    assert rows(path, "SELECT COUNT(*) FROM thinkific_progress_sections")[0][0] == \
        LEARNERS * len(SECTIONS)


def test_same_month_update_replaces_the_section_set_exactly(measured):
    path, _ = measured
    report_input.import_progress_files("2026-09", {"grade_6": real_shaped_csv()})
    newer = build_csv(
        [with_sections(learner("learner00@example.com", completed=99),
                       {"SKUPOVI": 90, "NOVA OBLAST": 15})],
        sections=["SKUPOVI", "NOVA OBLAST"])

    report_input.import_progress_files("2026-09", {"grade_6": newer})

    target = [s for s in snapshots(path) if s[3] == 99.0]
    assert len(target) == 1
    stored = sections_of(path, target[0][0])
    assert [s[1] for s in stored] == ["SKUPOVI", "NOVA OBLAST"], \
        "stari skup sekcija nije zamijenjen u cjelini"
    assert [s[2] for s in stored] == [90.0, 15.0]
    assert len(snapshots(path)) == LEARNERS


def test_one_invalid_row_rejects_the_whole_file(measured):
    path, _ = measured
    entries = []
    for index in range(LEARNERS):
        viewed = "besmislica" if index == 20 else (index * 3) % 101
        entries.append(with_sections(
            learner("learner%02d@example.com" % index, viewed=viewed),
            {name: 10 for name in SECTIONS}))
    payload = build_csv(entries, sections=SECTIONS)

    summary = report_input.import_progress_files("2026-09", {"grade_6": payload})

    assert summary.files_imported == 0
    assert summary.errors[0]["code"] == "percent_malformed"
    assert summary.errors[0]["row"] == 22          # zaglavlje + 21. red podataka
    # NISTA nije upisano -- ni ucenici, ni snimci, ni sekcije, ni uvoz.
    for table in ("students", "student_accounts", "thinkific_progress_imports",
                  "thinkific_progress_snapshots", "thinkific_progress_sections"):
        assert rows(path, "SELECT COUNT(*) FROM " + table)[0][0] == 0, table


def test_database_failure_midway_leaves_nothing_written(measured):
    """JACA garancija nego ranije: prije je pad na 20. redu ostavljao prvih 19
    ucenika UPISANIH, jer je svaki imao svoj commit. Sada je fajl jedna
    transakcija."""
    path, _ = measured
    database = reporting_db.get_database()
    original = database._replace_sections

    def explode(conn, sections, counters):
        raise ValueError("simulirani pad baze pri upisu sekcija")

    database._replace_sections = explode
    try:
        summary = report_input.import_progress_files(
            "2026-09", {"grade_6": real_shaped_csv()})
    finally:
        database._replace_sections = original

    assert summary.files_imported == 0
    for table in ("students", "thinkific_progress_imports",
                  "thinkific_progress_snapshots", "thinkific_progress_sections"):
        assert rows(path, "SELECT COUNT(*) FROM " + table)[0][0] == 0, table


def test_content_difference_behaviour_is_unchanged(measured):
    path, _ = measured
    database = reporting_db.get_database()
    student_id = database.get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "learner00@example.com")
    database.set_student_grade(student_id, 6)          # potvrdio administrator
    assert rows(path, "SELECT grade FROM students WHERE id = ?",
                (student_id,))[0][0] == 6

    summary = report_input.import_progress_files(
        "2026-09", {"grade_7": build_csv([learner("learner00@example.com")],
                                         sections=["OBLAST"])})

    assert summary.grade_conflicts == 1
    # Profil se NE prepisuje tiho, a snimak zadrzava SVOJ razred.
    assert rows(path, "SELECT grade FROM students WHERE id = ?",
                (student_id,))[0][0] == 6
    assert rows(path, "SELECT grade, course_key FROM thinkific_progress_snapshots") == \
        [(7, "grade_7")]


def test_display_name_policy_is_unchanged(measured):
    path, _ = measured
    report_input.import_progress_files(
        "2026-09", {"grade_6": build_csv([learner("learner00@example.com",
                                                  first="Ana", last="Anić")],
                                         sections=["OBLAST"])})
    assert rows(path, "SELECT display_name FROM students")[0][0] == "Ana Anić"

    # Prazno ime iz kasnijeg izvoza NE SMIJE obrisati korisnu vrijednost.
    report_input.import_progress_files(
        "2026-10", {"grade_6": build_csv([learner("learner00@example.com",
                                                  first="", last="")],
                                         sections=["OBLAST"])})
    assert rows(path, "SELECT display_name FROM students")[0][0] == "Ana Anić"


def test_import_leaves_every_profile_grade_null(measured):
    """Paketni put dijeli pravilo s red-po-red putem: razred se NE upisuje."""
    path, _ = measured
    report_input.import_progress_files("2026-09", {"grade_6": real_shaped_csv()})
    assert {r[0] for r in rows(path, "SELECT grade FROM students")} == {None}
    assert {r[0] for r in rows(path, "SELECT grade_confirmed_at FROM students")} \
        == {None}


# ---------------------------------------------------------------------------
# PRIVATNOST (nepromijenjena)
# ---------------------------------------------------------------------------
def test_no_email_or_raw_csv_is_persisted(measured):
    path, _ = measured
    report_input.import_progress_files(
        "2026-09", {"grade_6": real_shaped_csv(company="Tajna firma")})

    for table in ("thinkific_progress_imports", "thinkific_progress_snapshots",
                  "thinkific_progress_sections"):
        dump = str(rows(path, "SELECT * FROM " + table))
        assert "@" not in dump, table
        assert "Tajna firma" not in dump and "First Name" not in dump
    stored = rows(path, "SELECT external_user_id FROM student_accounts ORDER BY id")
    assert len(stored) == LEARNERS          # adresa postoji SAMO ovdje


def test_no_email_in_logs(measured, caplog):
    path, _ = measured
    with caplog.at_level(logging.DEBUG):
        report_input.import_progress_files("2026-09", {"grade_6": real_shaped_csv()})
    assert "@" not in caplog.text
    assert "learner00" not in caplog.text


def test_content_difference_log_carries_only_a_count(measured, caplog):
    path, _ = measured
    database = reporting_db.get_database()
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                                "learner00@example.com")
    database.set_student_grade(student_id, 6)          # potvrdio administrator
    with caplog.at_level(logging.INFO):
        report_input.import_progress_files(
            "2026-09", {"grade_7": build_csv([learner("learner00@example.com",
                                                      first="Tajna", last="Osoba")],
                                             sections=["OBLAST"])})
    assert "thinkific_content_grade_differs" in caplog.text
    assert "@" not in caplog.text and "Tajna" not in caplog.text


def test_duplicate_email_in_one_file_keeps_last_row_wins(measured):
    """Paritet s ranijim red-po-red putem (izmjeren na roditeljskom komitu):
    dva reda s istom adresom daju JEDAN snimak i POSLJEDNJI red pobjeđuje u
    cjelini. Paketni upis ovdje ne smije nagomilati oba skupa sekcija i oboriti
    `UNIQUE(snapshot_id, ordinal)` — to bi fajl pretvorilo iz „prihvaćen" u
    „odbijen"."""
    path, _ = measured
    payload = build_csv(
        [with_sections(learner("dup@example.com", completed=10),
                       {"SKUPOVI": 11, "B": 22, "C": 33}),
         with_sections(learner("dup@example.com", completed=90),
                       {"SKUPOVI": 99, "B": "", "C": ""})],
        sections=["SKUPOVI", "B", "C"])

    summary = report_input.import_progress_files("2026-09", {"grade_6": payload})

    assert summary.files_imported == 1 and summary.errors == []
    stored = snapshots(path)
    assert len(stored) == 1 and stored[0][3] == 90.0
    assert sections_of(path, stored[0][0]) == [
        (1, "SKUPOVI", 99.0), (2, "B", None), (3, "C", None)]
    assert rows(path, "SELECT COUNT(*) FROM students")[0][0] == 1
