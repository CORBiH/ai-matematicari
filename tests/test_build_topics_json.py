"""Testovi za scripts/build_topics_json.py — konverzija Excel → data/topics.json."""
import json
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_topics_json import (  # noqa: E402
    EXPECTED_HEADER,
    SHEET_NAME,
    XLSX_PATH,
    build_grades,
    load_rows,
)
from matbot.topics import lesson_info  # noqa: E402

DATA_PATH = ROOT / "data" / "topics.json"
CURRICULUM_DIR = ROOT / "reference" / "curriculum"
EXPECTED_COUNTS = {"6": 119, "7": 122, "8": 131, "9": 162}
EXPECTED_TOTAL = 534


def load_generated_json():
    with DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_generated_json_exists_and_is_valid():
    assert DATA_PATH.exists(), "Pokreni scripts/build_topics_json.py prije testova."
    data = load_generated_json()
    assert "grades" in data


def test_json_contains_all_four_grades():
    data = load_generated_json()
    assert set(data["grades"].keys()) == {"6", "7", "8", "9"}


def test_every_lesson_has_id_oblast_title():
    data = load_generated_json()
    for grade, grade_data in data["grades"].items():
        for lesson in grade_data["lessons"]:
            assert lesson["id"], f"Prazan id u razredu {grade}"
            assert lesson["oblast"], f"Prazan oblast u razredu {grade}"
            assert lesson["title"], f"Prazan title u razredu {grade}"


def test_no_empty_ids():
    data = load_generated_json()
    for grade_data in data["grades"].values():
        for lesson in grade_data["lessons"]:
            assert lesson["id"].strip() != ""


def test_ids_are_unique_across_whole_file():
    data = load_generated_json()
    all_ids = [
        lesson["id"]
        for grade_data in data["grades"].values()
        for lesson in grade_data["lessons"]
    ]
    assert len(all_ids) == len(set(all_ids))


def test_oblast_order_preserved_and_matches_lessons():
    data = load_generated_json()
    for grade, grade_data in data["grades"].items():
        oblasti_in_lessons = []
        for lesson in grade_data["lessons"]:
            if lesson["oblast"] not in oblasti_in_lessons:
                oblasti_in_lessons.append(lesson["oblast"])
        assert grade_data["oblast_order"] == oblasti_in_lessons, (
            f"Redoslijed oblasti u razredu {grade} ne odgovara redoslijedu iz lekcija"
        )


def test_build_grades_rejects_duplicate_ids():
    rows = [
        (6, "Oblast A", "Lekcija 1", "6-01-001"),
        (6, "Oblast A", "Lekcija 2", "6-01-001"),
    ]
    try:
        build_grades(rows)
        assert False, "Očekivana greška za dupli ID"
    except ValueError:
        pass


def test_build_grades_rejects_incomplete_row():
    rows = [
        (6, "Oblast A", "", "6-01-001"),
    ]
    try:
        build_grades(rows)
        assert False, "Očekivana greška za nepotpun red"
    except ValueError:
        pass


def test_build_grades_skips_fully_empty_trailing_row():
    rows = [
        (6, "Oblast A", "Lekcija 1", "6-01-001"),
        (None, None, None, None),
    ]
    grades = build_grades(rows)
    assert len(grades["6"]["lessons"]) == 1


def test_build_grades_rejects_duplicate_grade_oblast_lekcija_combo():
    rows = [
        (6, "Oblast A", "Lekcija 1", "6-01-001"),
        (6, "Oblast A", "Lekcija 1", "6-01-002"),
    ]
    with pytest.raises(ValueError):
        build_grades(rows)


# ---------------------------------------------------------------------------
# Kanonski workbook — jedina prihvaćena putanja, bez starih/backup fajlova.
# ---------------------------------------------------------------------------

def test_canonical_workbook_exists():
    assert XLSX_PATH.exists(), f"Kanonski workbook ne postoji: {XLSX_PATH}"
    assert XLSX_PATH.name == "MATBOT_Sve_Lekcije_6_7_8_9.xlsx"
    assert XLSX_PATH.parent == CURRICULUM_DIR


def test_no_backup_or_novo_workbook_present():
    forbidden_names = {
        "MATBOT_Sve_Lekcije_BACKUP.xlsx",
        "MATBOT_Sve_Lekcije_BACKUP.xlsx.xlsx",
        "MATBOT_Sve_Lekcije_6_7_8_9_NOVO.xlsx",
    }
    present = {p.name for p in CURRICULUM_DIR.glob("*.xlsx")}
    assert present == {"MATBOT_Sve_Lekcije_6_7_8_9.xlsx"}
    assert not (present & forbidden_names)


def test_generator_reads_only_the_canonical_path():
    assert str(XLSX_PATH) == str(CURRICULUM_DIR / "MATBOT_Sve_Lekcije_6_7_8_9.xlsx")


def test_load_rows_rejects_wrong_header(tmp_path):
    bad = tmp_path / "bad_header.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ws.append(["Razred", "Oblast", "Naziv lekcije", "ID lekcije"])
    ws.append([6, "Oblast A", "Lekcija 1", "6-01-001"])
    wb.save(bad)
    with pytest.raises(ValueError):
        load_rows(bad)


def test_load_rows_rejects_missing_sheet(tmp_path):
    bad = tmp_path / "bad_sheet.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pogrešan naziv"
    ws.append(list(EXPECTED_HEADER))
    ws.append([6, "Oblast A", "Lekcija 1", "6-01-001"])
    wb.save(bad)
    with pytest.raises(ValueError):
        load_rows(bad)


def test_load_rows_accepts_canonical_workbook():
    rows = load_rows(XLSX_PATH)
    assert len(rows) >= EXPECTED_TOTAL


# ---------------------------------------------------------------------------
# Brojevi lekcija po razredu i ukupno — tačno prema novom kurikulumu.
# ---------------------------------------------------------------------------

def test_counts_per_grade_match_new_curriculum():
    data = load_generated_json()
    counts = {grade: len(gd["lessons"]) for grade, gd in data["grades"].items()}
    assert counts == EXPECTED_COUNTS


def test_total_lesson_count_is_534():
    data = load_generated_json()
    total = sum(len(gd["lessons"]) for gd in data["grades"].values())
    assert total == EXPECTED_TOTAL


def test_generated_ids_exactly_match_workbook_ids():
    rows = load_rows(XLSX_PATH)
    workbook_ids = {
        (row[3] or "").strip()
        for row in rows
        if row and any(c is not None for c in row)
    }
    data = load_generated_json()
    json_ids = {
        lesson["id"]
        for grade_data in data["grades"].values()
        for lesson in grade_data["lessons"]
    }
    assert json_ids == workbook_ids


def test_regenerating_produces_identical_json():
    rows = load_rows(XLSX_PATH)
    first = build_grades(rows)
    second = build_grades(rows)
    assert first == second


def test_rerunning_generator_script_is_idempotent():
    before = DATA_PATH.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_topics_json.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    after = DATA_PATH.read_text(encoding="utf-8")
    assert before == after


# ---------------------------------------------------------------------------
# Stari topic ID-jevi ne smiju više biti prepoznati.
# ---------------------------------------------------------------------------

def test_old_curriculum_topic_id_no_longer_recognized():
    # 6-04-031 je postojao u starom (obrisanom) kurikulumu; u novom ne postoji.
    assert lesson_info(6, "6-04-031") is None


def test_representative_new_ids_recognized_in_every_grade():
    data = load_generated_json()
    for grade, grade_data in data["grades"].items():
        first_lesson = grade_data["lessons"][0]
        info = lesson_info(int(grade), first_lesson["id"])
        assert info is not None
        assert info["id"] == first_lesson["id"]


# ---------------------------------------------------------------------------
# Repo-wide provjera da nigdje ne ostane referenca na stari kurikulum.
# ---------------------------------------------------------------------------

def test_no_code_references_old_curriculum_names_or_counts():
    forbidden = ["BACKUP.xlsx", "_NOVO.xlsx", "359 lekcij"]
    text_suffixes = {".py", ".md", ".txt", ".yml", ".yaml", ".json", ".cfg", ".ini"}
    offenders = []
    skip_dirs = {".git", ".venv", "__pycache__", "node_modules"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in text_suffixes:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path == Path(__file__).resolve():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for needle in forbidden:
            if needle in content:
                offenders.append((str(path.relative_to(ROOT)), needle))
    assert not offenders, f"Stare reference pronađene: {offenders}"
