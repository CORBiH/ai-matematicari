"""Faza 3D+ — RAZRED SADRŽAJA nije TEKUĆI ŠKOLSKI RAZRED.

ŽIVI NALAZ KOJI OVAJ FAJL ČUVA (produkcijska forenzika, 2026-08-29): augustovski
Thinkific uvoz JESTE izvoz kursa šestog razreda — sekcije iz samog fajla su
SKUPOVI, DJELJIVOST BROJEVA, RAZLOMCI, DECIMALNI BROJEVI. U njemu potpuno
legitimno rade i učenici koje čovjek prepoznaje kao sedmi, osmi i deveti razred.

Iz toga slijedi razlika koju sistem do sada nije imao:

  RAZRED SADRŽAJA (koje gradivo je učenik koristio)
  NIJE
  TEKUĆI ŠKOLSKI RAZRED (koji razred pohađa).

`students.grade` od verzije 4 znači ADMINISTRATOROM POTVRĐEN TEKUĆI RAZRED, i
samo on bira kurikulum, otključava unos časa i ide na naslovnicu izvještaja.

OVAJ FAJL DOKAZUJE DVADESET ČETIRI TVRDNJE iz specifikacije faze, redom.

PII: svi učenici su sintetički.
"""
import ast
from pathlib import Path

import pytest

from matbot import (admin_students, config, report_input, reporting_db,
                    reporting_schema, student_grades)
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_thinkific_progress_import import (build_v1, migrate,
                                                  migrate_v3_only)

libsql = pytest.importorskip("libsql")

ROOT = Path(__file__).resolve().parent.parent

CONFIRMED_AT = "2026-08-29 09:00:00"
ADMIN = student_grades.GRADE_SOURCE_ADMIN


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
    database._path = path
    reporting_db.set_database(database)
    yield database
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def legacy(db, student_id, grade):
    """Zatečen red: razred postoji, potvrde nema — oblik svih 34 iz produkcije.

    Ide direktno kroz SQL jer ga nijedna funkcija sloja više ne može
    proizvesti, i upravo to je dokaz da je put zatvoren."""
    conn = db._connection()
    conn.execute("UPDATE students SET grade = ?, grade_confirmed_at = NULL, "
                 " grade_source = NULL WHERE id = ?", (grade, student_id))
    conn.commit()
    return student_id


def add_thinkific(db, student_id, month, grade, import_id=None):
    conn = db._connection()
    conn.execute(
        "INSERT INTO thinkific_progress_imports (report_month, course_key, "
        " course_name, grade, source_sha256, row_count) "
        "VALUES (?, ?, 'M', ?, ?, 1)",
        (month, "grade_%d" % grade, grade, "sha-%s-%s" % (month, grade)))
    new_id = conn.execute(
        "SELECT MAX(id) FROM thinkific_progress_imports").fetchall()[0][0]
    conn.execute(
        "INSERT INTO thinkific_progress_snapshots (import_id, student_id, "
        " report_month, course_key, course_name, grade) "
        "VALUES (?, ?, ?, ?, 'M', ?)",
        (import_id or new_id, student_id, month, "grade_%d" % grade, grade))
    conn.commit()


def add_assessment(db, student_id, grade, when="2026-08-25 18:06:49"):
    conn = db._connection()
    conn.execute(
        "INSERT INTO assessment_attempts (student_id, source, assessment_type, "
        " external_attempt_id, grade, score_percent, correct_count, total_count, "
        " completed_at) VALUES (?, 'matbot', 'kontrolni', ?, ?, 60.0, 3, 5, ?)",
        (student_id, "e-%s-%s" % (grade, when), grade, when))
    conn.commit()


def add_activity(db, student_id, grade, when="2026-08-25 18:06:31"):
    conn = db._connection()
    conn.execute(
        "INSERT INTO learning_activity (student_id, source, event_type, "
        " event_key, grade, occurred_at) "
        "VALUES (?, 'matbot', 'practice_answer_correct', ?, ?, ?)",
        (student_id, "k-%s-%s" % (grade, when), grade, when))
    conn.commit()


def profile_row(db, student_id):
    return db.fetch_student_profile(student_id)


def real_monthly_reports(db):
    """Zamijeni krnji fixture PRODUKCIJSKIM oblikom `monthly_reports`.

    v1 fixture ima samo `id` — dovoljno za dijagnostiku, nedovoljno za upis.
    Oblik dolazi iz `reporting_schema`, gdje je IZMJEREN na produkciji, nikad
    izmišljen (živi incident v1→v2)."""
    conn = db._connection()
    conn.execute("DROP TABLE monthly_reports")
    conn.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    conn.commit()


# ===========================================================================
# 1-3) NIJEDAN SADRŽAJNI IZVOR NE PIŠE TEKUĆI RAZRED
# ===========================================================================
def test_1_thinkific_import_never_writes_students_grade(db):
    """Uvoz kursa 6. razreda ne smije proglasiti nikoga šestakom."""
    from tests.test_thinkific_progress_import import build_csv, learner

    report_input.import_progress_files(
        "2026-09", {"grade_6": build_csv([learner("uvoz@example.com")],
                                         sections=["SKUPOVI"])})
    conn = db._connection()
    rows = conn.execute("SELECT grade, grade_confirmed_at, grade_source "
                        "FROM students").fetchall()
    assert rows and all(row[0] is None for row in rows)
    assert all(row[1] is None and row[2] is None for row in rows)
    # Razred SADRŽAJA je sačuvan tamo gdje mu je mjesto.
    assert conn.execute("SELECT grade FROM thinkific_progress_snapshots"
                        ).fetchall()[0][0] == 6


def test_1b_import_write_path_contains_no_students_grade_update(db):
    """Dokaz iz IZVORA, ne samo iz ponašanja: upisa naprosto nema."""
    source = (ROOT / "matbot" / "reporting_db.py").read_text(encoding="utf-8")
    body = source.split("def _apply_profiles(")[1].split("\n    def ")[0]
    assert "SET grade" not in body
    body = source.split("def import_progress_file(")[1].split("\n    def ")[0]
    assert "INSERT INTO students (display_name, grade" not in body


def test_2_matbot_content_grade_never_writes_students_grade(db):
    """Tutorski turnus zna razred zahtjeva, ali profil ne dira."""
    student_id = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                          "tutor@example.com")
    add_activity(db, student_id, 9)
    assert profile_row(db, student_id)["grade"] is None
    # Ni potpis identiteta ne prima razred.
    import inspect

    assert "grade" not in inspect.signature(
        db.get_or_create_student).parameters
    assert "grade" not in inspect.signature(
        reporting_db.resolve_student).parameters


def test_3_assessment_grade_never_writes_students_grade(db):
    student_id = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                          "kontrolni@example.com")
    add_assessment(db, student_id, 8)
    assert profile_row(db, student_id)["grade"] is None


def test_3b_profile_sync_can_no_longer_receive_a_grade(db):
    import inspect

    assert "grade" not in inspect.signature(
        db.update_student_profile).parameters


# ===========================================================================
# 4-5) POTVRDU PIŠE SAMO ČOVJEK
# ===========================================================================
def test_4_manual_creation_creates_a_confirmed_current_grade(db):
    student_id = db.create_student("Ručno Upisan", 7)
    saved = profile_row(db, student_id)
    assert saved["grade"] == 7
    assert saved["grade_source"] == student_grades.GRADE_SOURCE_MANUAL_CREATION
    assert saved["grade_confirmed_at"]
    assert student_grades.is_confirmed(saved["grade"], saved["grade_confirmed_at"],
                                       saved["grade_source"])


def test_5_admin_edit_confirms_the_grade(db):
    student_id = legacy(db, db.create_student("Zatecen", 6), 6)
    assert student_grades.needs_confirmation(
        *[profile_row(db, student_id)[k] for k in
          ("grade", "grade_confirmed_at", "grade_source")])
    db.set_student_grade(student_id, 7)
    saved = profile_row(db, student_id)
    assert saved["grade"] == 7 and saved["grade_source"] == ADMIN
    assert saved["grade_confirmed_at"]


def test_5b_confirming_the_same_value_is_still_a_confirmation(db):
    student_id = legacy(db, db.create_student("Zatecen", 6), 6)
    db.set_student_grade(student_id, 6)
    saved = profile_row(db, student_id)
    assert saved["grade"] == 6, "potvrda ne smije promijeniti vrijednost"
    assert saved["grade_source"] == ADMIN


# ===========================================================================
# 6) ZATEČENI RED OSTAJE NEPOTVRĐEN POSLIJE MIGRACIJE
# ===========================================================================
def test_6_legacy_grade_is_unconfirmed_after_migration(tmp_path, monkeypatch):
    """Migracija ne smije IZMISLITI potvrdu iz postojeće vrijednosti."""
    path = str(tmp_path / "legacy.db")
    build_v1(path)
    migrate_v3_only(path)
    conn = libsql.connect(path)
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('Zatecen', 6)")
    conn.commit()

    assert reporting_schema.migrate_to_v4(conn) is True
    row = conn.execute("SELECT grade, grade_confirmed_at, grade_source "
                       "FROM students").fetchall()[0]
    conn.close()
    # Vrijednost je SAČUVANA (istorijski kontekst), potvrda je PRAZNA.
    assert row[0] == 6
    assert row[1] is None and row[2] is None
    assert student_grades.needs_confirmation(row[0], row[1], row[2])


# ===========================================================================
# 7-8) UNOS ČASA TRAŽI POTVRĐEN RAZRED
# ===========================================================================
def test_7_unconfirmed_grade_blocks_curriculum_entry(db):
    student_id = legacy(db, db.create_student("Zatecen", 6), 6)
    with pytest.raises(admin_students._GradeUnknown):
        admin_students._student_grade(student_id)


def test_8_confirmed_grade_enables_curriculum_entry(db):
    student_id = db.create_student("Potvrdjen", 6)
    assert admin_students._student_grade(student_id) == 6


# ===========================================================================
# 9-10) OBNAVLJANJE GRADIVA NE MIJENJA PROFIL
# ===========================================================================
def test_9_grade_seven_student_may_use_grade_six_thinkific_content(db):
    student_id = db.create_student("Sedmak", 7)
    add_thinkific(db, student_id, "2026-08", 6)

    evidence = db.fetch_grade_evidence(student_id)
    saved = profile_row(db, student_id)
    status, content = student_grades.classify(
        saved["grade"], saved["grade_confirmed_at"], saved["grade_source"],
        evidence)
    assert status == student_grades.STATUS_CONTENT_MISMATCH
    assert content["thinkific"]["grade"] == 6
    assert profile_row(db, student_id)["grade"] == 7, "profil je promijenjen"


def test_10_grade_seven_student_may_use_grade_six_matbot_content(db):
    student_id = db.create_student("Sedmak", 7)
    add_activity(db, student_id, 6)

    evidence = db.fetch_grade_evidence(student_id)
    saved = profile_row(db, student_id)
    status, _ = student_grades.classify(
        saved["grade"], saved["grade_confirmed_at"], saved["grade_source"],
        evidence)
    assert status == student_grades.STATUS_CONTENT_MISMATCH
    assert profile_row(db, student_id)["grade"] == 7


# ===========================================================================
# 11-15) ISTORIJA SE NE DIRA
# ===========================================================================
def test_11_to_14_confirming_a_grade_leaves_every_observation_untouched(db):
    from matbot import student_sessions

    student_id = legacy(db, db.create_student("Napredni", 6), 6)
    add_thinkific(db, student_id, "2026-05", 6)
    add_assessment(db, student_id, 6, when="2026-05-02 10:00:00")
    add_activity(db, student_id, 6, when="2026-05-01 10:00:00")
    record = student_sessions.validate_session(
        session_date="2026-05-03", attendance="present", activity_rating=4,
        homework_status="done", area_name="Djeljivost brojeva",
        lesson_name="Djeljivost zbira, razlike i proizvoda", grade=6)
    db.insert_session(student_id, record)

    conn = db._connection()
    before = {
        "snapshots": conn.execute(
            "SELECT grade, report_month FROM thinkific_progress_snapshots"
        ).fetchall(),
        "assessments": conn.execute(
            "SELECT grade FROM assessment_attempts").fetchall(),
        "activity": conn.execute("SELECT grade FROM learning_activity").fetchall(),
    }
    sessions_before = db.fetch_sessions(student_id)

    db.set_student_grade(student_id, 7)

    assert conn.execute("SELECT grade, report_month FROM "
                        "thinkific_progress_snapshots").fetchall() == \
        before["snapshots"]                                     # 11
    assert conn.execute("SELECT grade FROM assessment_attempts").fetchall() == \
        before["assessments"]                                   # 12
    assert conn.execute("SELECT grade FROM learning_activity").fetchall() == \
        before["activity"]                                      # 13
    assert db.fetch_sessions(student_id) == sessions_before      # 14
    assert profile_row(db, student_id)["grade"] == 7


def test_15_saved_reports_are_untouched_by_a_grade_change(db):
    real_monthly_reports(db)
    student_id = legacy(db, db.create_student("Sa Izvjestajem", 6), 6)
    db.save_monthly_report(student_id=student_id, report_month="2026-08",
                           metrics_json='{"a": 1}', ai_summary="stari tekst",
                           instructor_comment="komentar")
    before = db.fetch_monthly_report(student_id, "2026-08")

    db.set_student_grade(student_id, 9)

    after = db.fetch_monthly_report(student_id, "2026-08")
    assert after["ai_summary"] == before["ai_summary"] == "stari tekst"
    assert after["instructor_comment"] == "komentar"
    assert after["metrics_json"] == before["metrics_json"]


# ===========================================================================
# 16-19) NIJEDAN IZVOR NE PREPORUČUJE RAZRED
# ===========================================================================
def test_16_no_recommendation_comes_from_the_display_name():
    """Broj u imenu smije samo da se POKAŽE čovjeku."""
    assert student_grades.name_grade_hint("Adjan 7 PLUS") == 7
    source = (ROOT / "matbot" / "student_grades.py").read_text(encoding="utf-8")
    body = source.split("def classify(")[1].split("\ndef ")[0]
    assert "name_grade_hint" not in body and "display_name" not in body


@pytest.mark.parametrize("key, kwargs", [
    ("thinkific", {"thinkific_rows": [("2026-08", 6)]}),      # 17
    ("assessment", {"assessment_rows": [("2026-08-25 10:00:00", 8)]}),   # 18
    ("matbot", {"matbot_rows": [("2026-08-25 10:00:00", 9)]}),           # 19
])
def test_17_to_19_no_source_ever_recommends_a_grade(key, kwargs):
    evidence = student_grades.evidence_from_rows(**kwargs)
    status, content = student_grades.classify(7, CONFIRMED_AT, ADMIN, evidence)
    # Drugi element su DOKAZI, ne razred: rječnik izvora, nikad cifra.
    assert isinstance(content, dict) and key in content
    assert isinstance(content[key], dict)
    assert status == student_grades.STATUS_CONTENT_MISMATCH


def test_19b_the_module_offers_no_recommendation_api():
    for gone in ("strongest_evidence", "_by_authority", "needs_review",
                 "STATUS_LIKELY_STALE", "STATUS_CONFLICTING",
                 "STATUS_CONSISTENT", "STATUS_INSUFFICIENT"):
        assert not hasattr(student_grades, gone), gone


def test_19c_neither_cli_produces_a_recommended_grade():
    for module in ("student_grade_audit", "thinkific_grade_forensics"):
        source = (ROOT / "matbot" / (module + ".py")).read_text(encoding="utf-8")
        tree = ast.parse(source)
        keys = {node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        assert "recommended_grade" not in keys, module


# ===========================================================================
# 20-21) ADMINISTRATORSKI PUT: POST + CSRF, stanje vidljivo
# ===========================================================================
def test_20_every_grade_mutation_is_post_and_csrf_protected():
    source = (ROOT / "matbot" / "admin_students.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    guarded = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "update_grade":
            continue
        guarded += 1
        routes = [d for d in node.decorator_list if isinstance(d, ast.Call)]
        methods = [kw for route in routes for kw in route.keywords
                   if kw.arg == "methods"]
        assert methods, node.name
        for kw in methods:
            assert [c.value for c in kw.value.elts] == ["POST"], node.name
        # CSRF se traži prvom naredbom tijela.
        first = node.body[1] if isinstance(node.body[0], ast.Expr) else node.body[0]
        assert isinstance(first, ast.Expr)
        assert first.value.func.id == "_require_csrf", node.name
        # `require_admin` je i dalje na ruti.
        assert any(isinstance(d, ast.Name) and d.id == "require_admin"
                   for d in node.decorator_list), node.name
    assert guarded == 1
    # Jedina radnja nad tekucim razredom; jednoklik ruta vise ne postoji.
    assert "def confirm_grade(" not in source


def test_21_confirmation_state_is_visible_in_the_registry(db):
    listed = db.list_students()
    assert listed == []
    confirmed = db.create_student("Potvrdjen", 7)
    pending = legacy(db, db.create_student("Zatecen", 6), 6)

    rows = {row["student_id"]: row for row in db.list_students()}
    assert rows[confirmed]["grade_confirmed_at"]
    assert rows[pending]["grade_confirmed_at"] is None
    # Filter po stanju potvrde radi u SQL-u.
    only_pending = db.list_students(confirmed=False)
    assert [row["student_id"] for row in only_pending] == [pending]
    only_confirmed = db.list_students(confirmed=True)
    assert [row["student_id"] for row in only_confirmed] == [confirmed]


def test_21b_the_return_target_is_a_closed_set_not_a_url():
    """Potvrda s liste vraća na listu — bez otvorenog preusmjeravanja."""
    source = (ROOT / "matbot" / "admin_students.py").read_text(encoding="utf-8")
    body = source.split("def _return_to_listing(")[1].split("\ndef ")[0]
    assert '== "index"' in body
    # Odrediste se NIKAD ne gradi iz vrijednosti koju je poslao klijent.
    assert "redirect(" not in body and "url_for(" not in body


# ===========================================================================
# 22) NOV IZVJEŠTAJ ODBIJA NEPOTVRĐEN RAZRED
# ===========================================================================
def test_22_report_payload_carries_the_confirmation_state(db):
    student_id = legacy(db, db.create_student("Zatecen", 6), 6)
    payload = report_input.build_report_input(student_id, "2026-08")
    assert payload["profile"]["grade"] == 6
    assert payload["profile"]["grade_confirmed"] is False

    db.set_student_grade(student_id, 6)
    payload = report_input.build_report_input(student_id, "2026-08")
    assert payload["profile"]["grade_confirmed"] is True


def test_22b_generation_is_blocked_before_any_model_call():
    """Blokada mora biti PRIJE poziva — inače se troši plaćeni poziv."""
    source = (ROOT / "matbot" / "admin_reports.py").read_text(encoding="utf-8")
    body = source.split("def generate_report(")[1].split("\n@admin_reports_bp")[0]
    guard = body.index("_grade_confirmed(payload)")
    call = body.index("generate_narrative")
    assert guard < call, "provjera potvrde mora prethoditi pozivu modela"
    assert "import llm" in body
    assert body.index("_grade_confirmed(payload)") < body.index("import llm")


def test_22c_old_saved_reports_stay_readable_without_confirmation(db):
    """Blokira se samo NOVO generisanje; sačuvani nacrt ostaje čitljiv."""
    real_monthly_reports(db)
    student_id = legacy(db, db.create_student("Zatecen", 6), 6)
    db.save_monthly_report(student_id=student_id, report_month="2026-08",
                           metrics_json="{}", ai_summary="stari nacrt")
    saved = db.fetch_monthly_report(student_id, "2026-08")
    assert saved["ai_summary"] == "stari nacrt"


# ===========================================================================
# 23) MIGRACIJA v3 → v4 JE SIGURNA
# ===========================================================================
def _v3(tmp_path, name="v3.db"):
    path = str(tmp_path / name)
    build_v1(path)
    migrate_v3_only(path)
    return path


def test_23_v4_is_additive_idempotent_and_verified(tmp_path):
    path = _v3(tmp_path)
    conn = libsql.connect(path)
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('A', 6)")
    conn.commit()
    before = {t: [row[1] for row in
                  conn.execute("PRAGMA table_info(%s)" % t).fetchall()]
              for t in reporting_schema.V1_TABLES + reporting_schema.V2_TABLES
              + reporting_schema.V3_TABLES}

    assert reporting_schema.migrate_to_v4(conn) is True
    assert reporting_schema.verify_v4_schema(conn) == []
    # Druga migracija ne radi ništa.
    assert reporting_schema.migrate_to_v4(conn) is False

    for table, columns in before.items():
        after = [row[1] for row in
                 conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
        extra = ["grade_confirmed_at", "grade_source"] if table == "students" else []
        assert after == columns + extra, table
    assert reporting_schema.applied_versions(conn) >= {1, 2, 3, 4}
    conn.close()


def test_23b_v4_is_resumable_after_a_half_applied_alter(tmp_path):
    """Prekid poslije PRVE kolone: sljedeće pokretanje dovršava, ne pada."""
    path = _v3(tmp_path)
    conn = libsql.connect(path)
    conn.execute("ALTER TABLE students ADD COLUMN grade_confirmed_at TEXT")
    conn.commit()
    assert 4 not in reporting_schema.applied_versions(conn)

    assert reporting_schema.migrate_to_v4(conn) is True
    assert reporting_schema.verify_v4_schema(conn) == []
    conn.close()


def test_23c_v4_refuses_a_malformed_partial_state(tmp_path):
    """Postojeća kolona s pogrešnim svojstvima pada ZATVORENO."""
    path = _v3(tmp_path)
    conn = libsql.connect(path)
    # NOT NULL kolona istog imena nije ono što v4 ugovara.
    conn.execute("ALTER TABLE students ADD COLUMN grade_source TEXT NOT NULL "
                 "DEFAULT 'x'")
    conn.commit()
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v4(conn)
    assert caught.value.code.startswith("v4_not_nullable")
    assert 4 not in reporting_schema.applied_versions(conn)
    conn.close()


def test_23d_v4_refuses_to_run_before_v3(tmp_path):
    path = str(tmp_path / "v2only.db")
    build_v1(path)
    from tests.test_thinkific_progress_import import migrate_v2_only

    migrate_v2_only(path)
    conn = libsql.connect(path)
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v4(conn)
    assert caught.value.code == "v3_migration_record_missing"
    conn.close()


def test_23e_a_recorded_v4_without_the_columns_fails_closed(tmp_path):
    path = _v3(tmp_path)
    conn = libsql.connect(path)
    conn.execute("INSERT INTO schema_migrations (version, description) "
                 "VALUES (4, 'lazni zapis')")
    conn.commit()
    with pytest.raises(reporting_schema.MigrationError) as caught:
        reporting_schema.migrate_to_v4(conn)
    assert caught.value.code.startswith("v4_columns_missing")
    conn.close()


def test_23f_version_row_is_written_only_after_verification():
    """Redoslijed u izvoru: DDL, pa PROVJERA, pa tek zapis verzije."""
    source = (ROOT / "matbot" / "reporting_schema.py").read_text(encoding="utf-8")
    body = source.split("def migrate_to_v4(")[1].split("\ndef ")[0]
    # `rindex` namjerno: prva provjera je RANA GRANA (baza koja već tvrdi v4),
    # a dokazuje se ona POSLIJE DDL-a — ona koja odlučuje o zapisu verzije.
    assert body.index("_apply_v4_ddl(conn)") < body.rindex("verify_v4_schema(conn)")
    assert body.rindex("verify_v4_schema(conn)") < body.index("_record_migration(")


def test_23g_config_and_schema_module_agree_on_the_current_version():
    """v4 je i dalje OBAVEZAN korak; tekuća verzija je od tada odmakla na v5."""
    assert config.REPORTING_SCHEMA_VERSION == reporting_schema.CURRENT_SCHEMA_VERSION
    assert reporting_schema.CURRENT_SCHEMA_VERSION >= 4
    assert reporting_schema.MIGRATION_DESCRIPTIONS[4].strip()


# ===========================================================================
# 24) RASPOREĐIVANJE: MIGRACIJA PRIJE ZAMJENE APLIKACIJE
# ===========================================================================
def test_24_deploy_runs_the_migration_before_replacing_the_app():
    workflow = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(
        encoding="utf-8")
    migrate_at = workflow.index("matbot.reporting_db --migrate")
    up_at = workflow.index("docker compose up -d")
    assert migrate_at < up_at, "migracija mora prethoditi zamjeni kontejnera"
    # Migracija ide u ODVOJENOM kontejneru, pa stari nastavlja da poslužuje.
    assert "--no-deps" in workflow


def test_24b_old_app_queries_never_select_star_from_students():
    """Zašto dodate kolone ne mogu oboriti staru verziju."""
    source = (ROOT / "matbot" / "reporting_db.py").read_text(encoding="utf-8")
    assert "SELECT * FROM students" not in source
    assert "select * from students" not in source.lower()


# ===========================================================================
# DODATNO: bez šeme v4 se potvrda NE TVRDI
# ===========================================================================
def test_confirmation_write_fails_closed_on_a_v3_database(tmp_path, monkeypatch):
    """Potvrda koja se ne može ZAPISATI ne smije se ni tvrditi."""
    path = _v3(tmp_path, name="v3only.db")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0))
    try:
        conn = database._connection()
        conn.execute("INSERT INTO students (display_name, grade) "
                     "VALUES ('Zatecen', 6)")
        conn.commit()
        student_id = conn.execute("SELECT id FROM students").fetchall()[0][0]

        with pytest.raises(reporting_db.ReportingUnavailable) as caught:
            database.set_student_grade(student_id, 7)
        assert caught.value.code == "grade_confirmation_unavailable"
        # Ništa nije promijenjeno.
        assert conn.execute("SELECT grade FROM students").fetchall()[0][0] == 6
        # A čitanje i dalje radi — samo nikad kao „potvrđeno".
        saved = database.fetch_student_profile(student_id)
        assert saved["grade"] == 6 and saved["grade_confirmed_at"] is None
        assert student_grades.needs_confirmation(
            saved["grade"], saved["grade_confirmed_at"], saved["grade_source"])
    finally:
        database.close()
