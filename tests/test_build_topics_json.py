"""Testovi za scripts/build_topics_json.py — konverzija Excel → data/topics.json."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_topics_json import build_grades  # noqa: E402

DATA_PATH = ROOT / "data" / "topics.json"


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
