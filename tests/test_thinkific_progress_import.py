"""Faza 3A — Thinkific „Student Progress" → mjesečni snimak.

DVIJE ODVOJENE TVRDNJE:
  1. ČITANJE — izvoz se parsira tačno onako kako STVARNI izvoz izgleda
     (izmjereno, ne opisano), a sve neispravno pada vidljivo.
  2. STANJE — jedan snimak po (učenik, kurs, mjesec), idempotentno, atomično,
     bez PII u tabelama i bez izmišljenog napretka.

PII: fixture su SINTETIČKI. Stvarni izvoz je gitignorisan i ovdje se ne čita —
osim u jednom testu koji SAMO provjerava da je i dalje van gita.
"""
import io
import logging

import pytest

from matbot import activity, reporting_db, reporting_schema, report_input
from matbot import thinkific_progress as progress
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL
from matbot.thinkific_progress import ProgressFormatError

from tests.fixtures.thinkific import (GRADE6_SECTIONS, MATBOT_COLUMN, build_csv,
                                      learner, with_sections)
from tests.test_assessment_capture import ASSESSMENT_SCHEMA
from tests.test_learning_activity_capture import ACTIVITY_SCHEMA
from tests.test_reporting_db_identity import SCHEMA as IDENTITY_SCHEMA

libsql = pytest.importorskip("libsql")

E1 = "student1@example.com"
E2 = "student2@example.com"


def build_v1(path):
    """Šema TAČNO verzije 1 — bez ijedne Faze 3A tabele."""
    conn = libsql.connect(path)
    for block in (IDENTITY_SCHEMA, ACTIVITY_SCHEMA, ASSESSMENT_SCHEMA):
        for statement in block.strip().split(";"):
            if not statement.strip():
                continue
            if block is IDENTITY_SCHEMA and any(
                    statement.lstrip().startswith("CREATE TABLE " + name)
                    for name in ("learning_activity", "assessment_attempts",
                                 "assessment_item_results")):
                continue
            conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
    conn.commit()
    conn.close()


def migrate(path):
    conn = libsql.connect(path)
    try:
        return reporting_schema.migrate_to_v2(conn)
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    migrate(path)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    yield path
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def rows(path, sql, params=()):
    conn = libsql.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def snapshots(path):
    return rows(path, "SELECT id, student_id, report_month, course_key, course_name, "
                      "grade, percent_viewed, percent_completed, started_at, "
                      "completed_at, activated_at, expires_at, last_sign_in "
                      "FROM thinkific_progress_snapshots ORDER BY id")


def sections_of(path, snapshot_id):
    return rows(path, "SELECT ordinal, section_name, progress_percent "
                      "FROM thinkific_progress_sections WHERE snapshot_id = ? "
                      "ORDER BY ordinal", (snapshot_id,))


def simple_csv(email=E1, **over):
    row = with_sections(learner(email, **over),
                        {name: 10 * (i + 1) for i, name in enumerate(GRADE6_SECTIONS)})
    return build_csv([row])


# ---------------------------------------------------------------------------
# 1-6) Čitanje stvarne strukture
# ---------------------------------------------------------------------------
def test_real_shaped_csv_parses_with_utf8_and_quoted_comma():
    parsed = progress.parse_progress_csv(simple_csv(), "grade_6", "2026-09")

    assert len(parsed.rows) == 1 and parsed.row_count == 1
    assert parsed.grade == 6 and parsed.course_name == "Matematika za 6. razred"
    # Naziv sekcije sa zarezom mora preživjeti — `split(",")` bi ga razbio.
    assert "KRUŽNICA, KRUG, UGAO" in [name for _, name in parsed.section_names]


def test_dynamic_sections_and_ordinal_follow_csv_order():
    parsed = progress.parse_progress_csv(simple_csv(), "grade_6", "2026-09")

    assert [name for _, name in parsed.section_names] == GRADE6_SECTIONS
    assert [ordinal for ordinal, _ in parsed.section_names] == [1, 2, 3, 4, 5]


def test_company_column_is_not_a_section():
    parsed = progress.parse_progress_csv(
        simple_csv(company="Neka firma"), "grade_6", "2026-09")
    assert "Company" not in [name for _, name in parsed.section_names]


def test_matbot_embed_column_is_never_a_section():
    """DIO 19: Thinkific „MAT BOT" kolona mjeri posjetu stranici, ne upotrebu
    tutora. Autoritet za MAT-BOT je isključivo Faza 2."""
    parsed = progress.parse_progress_csv(
        simple_csv(matbot=99), "grade_6", "2026-09")

    names = [name for _, name in parsed.section_names]
    assert MATBOT_COLUMN not in names
    assert not any("MAT BOT" in name.upper() for name in names)
    assert all(MATBOT_COLUMN not in dict((n, v) for _, n, v in row.sections)
               for row in parsed.rows)


def test_matbot_column_never_reaches_the_sections_table(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv(matbot=99)})
    stored = rows(db, "SELECT section_name FROM thinkific_progress_sections")

    assert MATBOT_COLUMN not in [s[0] for s in stored]
    assert not any("MAT BOT" in s[0].upper() for s in stored)


def test_unknown_extra_column_is_treated_as_a_section():
    """7/8/9. razred imaju druge sekcije — spisak se NE ugrađuje po razredu."""
    csv_bytes = build_csv([learner(E1)], sections=["NOVA OBLAST"])
    parsed = progress.parse_progress_csv(csv_bytes, "grade_7", "2026-09")

    assert [name for _, name in parsed.section_names] == ["NOVA OBLAST"]
    assert parsed.grade == 7


# ---------------------------------------------------------------------------
# 7-13) Identitet i profil
# ---------------------------------------------------------------------------
def test_email_is_normalized_and_identity_is_reused(db):
    database = reporting_db.get_database()
    existing = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, E1)

    summary = report_input.import_progress_files(
        "2026-09", {"grade_6": simple_csv(email="  Student1@Example.COM  ")})

    assert summary.students_reused == 1 and summary.students_created == 0
    assert rows(db, "SELECT COUNT(*) FROM students")[0][0] == 1
    assert snapshots(db)[0][1] == existing


def test_thinkific_only_learner_is_created(db):
    summary = report_input.import_progress_files("2026-09", {"grade_6": simple_csv()})

    assert summary.students_created == 1
    assert rows(db, "SELECT provider, external_user_id FROM student_accounts") == \
        [(PROVIDER_THINKIFIC_EMAIL, E1)]


def test_display_name_is_filled_but_never_overwrites(db):
    report_input.import_progress_files(
        "2026-09", {"grade_6": simple_csv(first="Ana", last="Anić")})
    assert rows(db, "SELECT display_name FROM students")[0][0] == "Ana Anić"

    # Prazno ime iz kasnijeg izvoza NE SMIJE obrisati korisnu vrijednost.
    report_input.import_progress_files(
        "2026-10", {"grade_6": simple_csv(first="", last="")})
    assert rows(db, "SELECT display_name FROM students")[0][0] == "Ana Anić"


def test_display_name_collapses_whitespace_and_ignores_blank_parts():
    assert progress.build_display_name("  Ana   Marija ", " Anić ") == "Ana Marija Anić"
    assert progress.build_display_name("Ana", "") == "Ana"
    assert progress.build_display_name("", "") is None
    assert progress.build_display_name(None, None) is None


def test_grade_fills_null_profile_but_conflict_is_not_silently_overwritten(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv()})
    assert rows(db, "SELECT grade FROM students")[0][0] == 6

    # Isti učenik u izvozu 7. razreda: snimak se uvozi, profil se NE mijenja.
    summary = report_input.import_progress_files(
        "2026-10", {"grade_7": build_csv([learner(E1)], sections=["NOVA OBLAST"])})

    assert summary.grade_conflicts == 1
    assert rows(db, "SELECT grade FROM students")[0][0] == 6, "profil je tiho prepisan"
    stored = [(s[2], s[3], s[5]) for s in snapshots(db)]
    assert ("2026-10", "grade_7", 7) in stored, "snimak mora zadržati svoj razred"


# ---------------------------------------------------------------------------
# 14-22) Parsiranje vrijednosti
# ---------------------------------------------------------------------------
def test_percentages_are_parsed_exactly(db):
    report_input.import_progress_files(
        "2026-09", {"grade_6": simple_csv(viewed=63, completed=31)})
    row = snapshots(db)[0]

    assert row[6] == 63.0 and row[7] == 31.0
    stored = sections_of(db, row[0])
    assert [s[2] for s in stored] == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert [s[0] for s in stored] == [1, 2, 3, 4, 5]
    assert [s[1] for s in stored] == GRADE6_SECTIONS


def test_blank_percentage_becomes_null(db):
    report_input.import_progress_files(
        "2026-09", {"grade_6": build_csv([learner(E1, viewed="", completed=10)])})
    assert snapshots(db)[0][6] is None


@pytest.mark.parametrize("bad", ["abc", "31%%", "-", "1e3", "31 posto", "--5"])
def test_malformed_percentage_is_rejected(bad):
    with pytest.raises(ProgressFormatError) as error:
        progress.parse_progress_csv(build_csv([learner(E1, viewed=bad)]),
                                    "grade_6", "2026-09")
    assert error.value.code == "percent_malformed"
    assert error.value.row == 2


@pytest.mark.parametrize("bad", ["101", "200", "-5"])
def test_out_of_range_percentage_is_rejected_not_clamped(bad):
    with pytest.raises(ProgressFormatError) as error:
        progress.parse_progress_csv(build_csv([learner(E1, completed=bad)]),
                                    "grade_6", "2026-09")
    assert error.value.code in ("percent_out_of_range", "percent_malformed")


def test_utc_timestamps_are_parsed_and_blanks_become_null(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv(
        started="2026-08-13 10:48:04 UTC", completed_at="", expires="")})
    row = snapshots(db)[0]

    assert row[8] == "2026-08-13 10:48:04", "UTC se ne pomjera u lokalno vrijeme"
    assert row[9] is None and row[11] is None


@pytest.mark.parametrize("bad", ["13.08.2026", "2026-08-13", "not a date",
                                 "2026-13-01 10:00:00 UTC"])
def test_malformed_timestamp_is_rejected(bad):
    with pytest.raises(ProgressFormatError) as error:
        progress.parse_progress_csv(build_csv([learner(E1, started=bad)]),
                                    "grade_6", "2026-09")
    assert error.value.code in ("timestamp_malformed", "timestamp_out_of_range")


def test_missing_email_aborts_the_file():
    with pytest.raises(ProgressFormatError) as error:
        progress.parse_progress_csv(build_csv([learner("")]), "grade_6", "2026-09")
    assert error.value.code == "email_missing" and error.value.row == 2


@pytest.mark.parametrize("bad", ["", "2026", "2026-13", "26-09", "septembar", None])
def test_report_month_is_validated_strictly(bad):
    with pytest.raises(ProgressFormatError):
        progress.parse_report_month(bad)


def test_course_key_must_be_an_explicit_slot():
    with pytest.raises(ProgressFormatError) as error:
        progress.parse_progress_csv(simple_csv(), "grade_5", "2026-09")
    assert error.value.code == "course_key_invalid"


# ---------------------------------------------------------------------------
# 23-27) Idempotentnost i atomičnost
# ---------------------------------------------------------------------------
def test_source_hash_is_deterministic_over_bytes():
    payload = simple_csv()
    assert progress.source_sha256(payload) == progress.source_sha256(payload)
    assert progress.source_sha256(payload) != progress.source_sha256(simple_csv(viewed=99))


def test_importing_the_same_file_twice_creates_no_duplicates(db):
    payload = simple_csv()
    first = report_input.import_progress_files("2026-09", {"grade_6": payload})
    second = report_input.import_progress_files("2026-09", {"grade_6": payload})

    assert first.snapshots_inserted == 1 and second.snapshots_updated == 1
    assert len(snapshots(db)) == 1
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_sections")[0][0] == 5
    assert rows(db, "SELECT COUNT(*) FROM students")[0][0] == 1
    # Revizija ipak pamti OBA fajla — to je namjerno.
    assert rows(db, "SELECT COUNT(*) FROM thinkific_progress_imports")[0][0] == 2


def test_updated_same_month_export_replaces_state_and_sections(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv(completed=31)})
    # Novi izvoz ISTOG mjeseca: napredovao je i kurikulum se promijenio.
    newer = build_csv([with_sections(learner(E1, completed=48),
                                     {"SKUPOVI": 90, "NOVA OBLAST": 15})],
                      sections=["SKUPOVI", "NOVA OBLAST"])
    report_input.import_progress_files("2026-09", {"grade_6": newer})

    assert len(snapshots(db)) == 1
    assert snapshots(db)[0][7] == 48.0
    stored = sections_of(db, snapshots(db)[0][0])
    assert [s[1] for s in stored] == ["SKUPOVI", "NOVA OBLAST"], \
        "stari skup sekcija nije zamijenjen u cjelini"
    assert [s[2] for s in stored] == [90.0, 15.0]


def test_failed_section_write_rolls_back_to_the_previous_snapshot(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv(completed=31)})
    before = snapshots(db)[0]
    before_sections = sections_of(db, before[0])

    database = reporting_db.get_database()
    with pytest.raises(reporting_db.ReportingUnavailable):
        database.upsert_progress_snapshot(
            import_id=1, student_id=before[1], report_month="2026-09",
            course_key="grade_6", course_name="Matematika za 6. razred", grade=6,
            percent_viewed=99.0, percent_completed=99.0, started_at=None,
            completed_at=None, activated_at=None, expires_at=None, last_sign_in=None,
            # `section_name` je NOT NULL — drugi red obara transakciju.
            sections=[(1, "SKUPOVI", 10.0), (2, None, 20.0)])

    assert snapshots(db)[0] == before, "snimak je ostao polovično izmijenjen"
    assert sections_of(db, before[0]) == before_sections


# ---------------------------------------------------------------------------
# 28-32) Mjesec naspram prethodnog
# ---------------------------------------------------------------------------
def _two_months(db):
    report_input.import_progress_files("2026-08", {"grade_6": build_csv(
        [with_sections(learner(E1, viewed=40, completed=31),
                       {"SKUPOVI": 50, "RAZLOMCI": 10})],
        sections=["SKUPOVI", "RAZLOMCI"])})
    report_input.import_progress_files("2026-09", {"grade_6": build_csv(
        [with_sections(learner(E1, viewed=62, completed=48),
                       {"SKUPOVI": 75, "RAZLOMCI": 10, "NOVA OBLAST": 20})],
        sections=["SKUPOVI", "RAZLOMCI", "NOVA OBLAST"])})
    return rows(db, "SELECT id FROM students")[0][0]


def test_month_over_month_deltas(db):
    student_id = _two_months(db)
    view = report_input.build_thinkific_section(student_id, "2026-09")

    assert view["percent_completed"] == 48.0
    assert view["previous_percent_completed"] == 31.0
    assert view["delta_percent_completed"] == 17.0
    assert view["delta_percent_viewed"] == 22.0
    assert view["previous_report_month"] == "2026-08"

    by_name = {s["section_name"]: s for s in view["sections"]}
    assert by_name["SKUPOVI"]["delta_progress_percent"] == 25.0
    assert by_name["RAZLOMCI"]["delta_progress_percent"] == 0.0
    # Sekcija koje prošlog mjeseca NIJE bilo: ne pravimo se da je rasla od nule.
    assert by_name["NOVA OBLAST"]["previous_progress_percent"] is None
    assert by_name["NOVA OBLAST"]["delta_progress_percent"] is None


def test_first_month_has_null_deltas_not_zero(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv()})
    student_id = rows(db, "SELECT id FROM students")[0][0]
    view = report_input.build_thinkific_section(student_id, "2026-09")

    assert view["previous_percent_completed"] is None
    assert view["delta_percent_completed"] is None
    assert all(s["delta_progress_percent"] is None for s in view["sections"])


def test_section_order_is_preserved_in_the_read_model(db):
    student_id = _two_months(db)
    view = report_input.build_thinkific_section(student_id, "2026-09")
    assert [s["section_name"] for s in view["sections"]] == \
        ["SKUPOVI", "RAZLOMCI", "NOVA OBLAST"]
    assert [s["ordinal"] for s in view["sections"]] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 33-34) Više fajlova
# ---------------------------------------------------------------------------
def test_single_file_import(db):
    summary = report_input.import_progress_files("2026-09", {"grade_6": simple_csv()})
    assert (summary.files_received, summary.files_imported) == (1, 1)
    assert summary.rows_seen == 1 and summary.snapshots_inserted == 1


def test_four_file_import(db):
    files = {}
    for index, key in enumerate(("grade_6", "grade_7", "grade_8", "grade_9")):
        files[key] = build_csv([learner("s%d@example.com" % index)],
                               sections=["OBLAST %d" % index])
    summary = report_input.import_progress_files("2026-09", files)

    assert (summary.files_received, summary.files_imported) == (4, 4)
    assert summary.students_created == 4 and summary.snapshots_inserted == 4
    assert sorted(s[3] for s in snapshots(db)) == \
        ["grade_6", "grade_7", "grade_8", "grade_9"]


def test_one_invalid_file_does_not_block_the_valid_ones(db):
    files = {"grade_6": simple_csv(),
             "grade_7": build_csv([learner(E2, viewed="nonsense")],
                                  sections=["OBLAST"])}
    summary = report_input.import_progress_files("2026-09", files)

    assert summary.files_imported == 1
    rejected = [f for f in summary.files if f["status"] == "rejected"]
    assert len(rejected) == 1 and rejected[0]["course_key"] == "grade_7"
    assert summary.errors[0]["code"] == "percent_malformed"
    assert summary.errors[0]["row"] == 2
    # Odbijen fajl ne smije ostaviti NIJEDAN red.
    assert [s[3] for s in snapshots(db)] == ["grade_6"]


# ---------------------------------------------------------------------------
# 35-36) Privatnost
# ---------------------------------------------------------------------------
def test_no_email_or_raw_csv_is_persisted(db):
    report_input.import_progress_files(
        "2026-09", {"grade_6": simple_csv(company="Tajna firma")})

    for table in ("thinkific_progress_imports", "thinkific_progress_snapshots",
                  "thinkific_progress_sections"):
        dump = str(rows(db, "SELECT * FROM " + table))
        assert "@" not in dump, table
        assert E1 not in dump and "example.com" not in dump
        assert "Tajna firma" not in dump
        assert "First Name" not in dump and "% Viewed" not in dump
    # Adresa i dalje postoji SAMO na jednom mjestu.
    assert rows(db, "SELECT external_user_id FROM student_accounts") == [(E1,)]


def test_import_logs_carry_no_email_or_name(db, caplog):
    with caplog.at_level(logging.DEBUG):
        report_input.import_progress_files(
            "2026-09", {"grade_6": simple_csv(first="Tajna", last="Osoba")})
        report_input.import_progress_files(
            "2026-10", {"grade_7": build_csv([learner(E1, first="Tajna",
                                                      last="Osoba")],
                                             sections=["OBLAST"])})
    assert E1 not in caplog.text and "student1" not in caplog.text
    assert "Tajna" not in caplog.text and "Osoba" not in caplog.text


def test_summary_contains_no_pii(db):
    summary = report_input.import_progress_files(
        "2026-09", {"grade_6": simple_csv(first="Tajna", last="Osoba")})
    rendered = str(summary.as_dict())

    assert "@" not in rendered and "Tajna" not in rendered
    assert summary.as_dict()["students_created"] == 1


def test_real_sample_stays_untracked_and_ignored():
    """DIO 27/50: stvarni izvoz sadrži PII i ne smije se moći commitovati."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sample = root / "scratchpad" / "thinkific" / "progress_grade6_sample.csv"
    tracked = subprocess.run(["git", "ls-files", "scratchpad/thinkific/"],
                             cwd=root, capture_output=True, text=True)
    assert tracked.stdout.strip() == "", "stvarni Thinkific izvoz je u gitu"

    ignored = subprocess.run(["git", "check-ignore", "scratchpad/thinkific/"],
                             cwd=root, capture_output=True, text=True)
    assert ignored.returncode == 0, "scratchpad/thinkific/ nije u .gitignore"
    if sample.exists():
        assert subprocess.run(["git", "check-ignore", str(sample)], cwd=root,
                              capture_output=True).returncode == 0
