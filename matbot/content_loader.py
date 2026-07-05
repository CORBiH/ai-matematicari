"""Load modular AI tutor content from grade-specific Excel workbooks.

The Excel files remain the source of truth. This module only normalizes sheet
data, validates required columns, and caches default loads per grade.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import openpyxl

_REPO_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_GRADES = frozenset({6, 7, 8})
DEFAULT_GRADE = 6

# Backwards-compatible constants for grade 6 callers/tests.
MASTER_PATH = (
    _REPO_ROOT
    / "data"
    / "6_razred"
    / "AI_MATH_CONTENT_MASTER_6_RAZRED_MODULAR_FINAL.xlsx"
)
THINKIFIC_MAP_PATH = (
    _REPO_ROOT
    / "data"
    / "6_razred"
    / "THINKIFIC_MAP_6_RAZRED_MODULAR_FINAL.xlsx"
)

MASTER_TOPICS_SHEET = "TOPICS"
MASTER_OPTIONAL_SHEETS = ("VIDEO_FLOW", "PARENT_REPORT")
THINKIFIC_MAP_SHEET = "MAP"
THINKIFIC_TOPIC_REFERENCE_SHEET = "TOPIC_REFERENCE"

REQUIRED_TOPIC_COLUMNS = frozenset(
    {"grade", "oblast", "topic", "display_name", "lesson_scope"}
)
REQUIRED_MAP_COLUMNS = frozenset(
    {
        "course_name",
        "section_name",
        "lesson_order",
        "lesson_title",
        "lesson_url",
        "thinkific_lesson_id",
        "topic",
        "status",
    }
)


class ContentLoadError(Exception):
    """Raised when content files, sheets, columns, or grade are invalid."""


def normalize_header(name: Any) -> str:
    if name is None:
        return ""
    s = str(name).strip().lower()
    return re.sub(r"[\s\-]+", "_", s)


def normalize_value(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, float) and val.is_integer():
        val = int(val)
    return str(val).strip()


def normalize_grade(grade: Any = DEFAULT_GRADE) -> int:
    raw = normalize_value(grade)
    match = re.search(r"\d+", raw)
    g = int(match.group(0)) if match else DEFAULT_GRADE
    if g not in SUPPORTED_GRADES:
        supported = ", ".join(str(x) for x in sorted(SUPPORTED_GRADES))
        raise ContentLoadError(
            f"Nepodrzan razred: {raw or grade}. Podrzani razredi su: {supported}."
        )
    return g


def _grade_dir(grade: Any = DEFAULT_GRADE) -> Path:
    return _REPO_ROOT / "data" / f"{normalize_grade(grade)}_razred"


def master_path_for_grade(grade: Any = DEFAULT_GRADE) -> Path:
    g = normalize_grade(grade)
    preferred = _grade_dir(g) / f"AI_MATH_CONTENT_MASTER_{g}_RAZRED_MODULAR_FINAL.xlsx"
    if g == 6 and not preferred.exists():
        legacy = _REPO_ROOT / "AI_MATH_CONTENT_MASTER_6_RAZRED_MODULAR_FINAL.xlsx"
        if legacy.exists():
            return legacy
    return preferred


def thinkific_map_path_for_grade(grade: Any = DEFAULT_GRADE) -> Path:
    g = normalize_grade(grade)
    preferred = _grade_dir(g) / f"THINKIFIC_MAP_{g}_RAZRED_MODULAR_FINAL.xlsx"
    if g == 6 and not preferred.exists():
        legacy = _REPO_ROOT / "THINKIFIC_MAP_6_RAZRED_MODULAR_FINAL.xlsx"
        if legacy.exists():
            return legacy
    return preferred


def _read_sheet(wb, sheet_name: str) -> tuple[list[str], list[dict[str, str]]]:
    ws = wb[sheet_name]
    row_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(row_iter)
    except StopIteration:
        return [], []

    headers = [normalize_header(h) for h in header_row]
    rows: list[dict[str, str]] = []
    for raw in row_iter:
        if raw is None:
            continue
        if all(c is None or str(c).strip() == "" for c in raw):
            continue
        row: dict[str, str] = {}
        for i, col in enumerate(headers):
            if not col:
                continue
            row[col] = normalize_value(raw[i]) if i < len(raw) else ""
        rows.append(row)
    return headers, rows


def _open_workbook(path: Path):
    if not path.exists():
        raise ContentLoadError(f"Excel fajl nije pronadjen: {path}")
    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # pragma: no cover - openpyxl raises varied types
        raise ContentLoadError(f"Ne mogu otvoriti Excel fajl {path}: {exc}") from exc


def load_master_content(
    path: str | Path | None = None, grade: Any = DEFAULT_GRADE
) -> dict[str, Any]:
    g = normalize_grade(grade)
    path = Path(path) if path is not None else master_path_for_grade(g)
    wb = _open_workbook(path)
    try:
        if MASTER_TOPICS_SHEET not in wb.sheetnames:
            raise ContentLoadError(
                f"Master nema obavezni sheet '{MASTER_TOPICS_SHEET}'."
            )
        columns, all_rows = _read_sheet(wb, MASTER_TOPICS_SHEET)
        missing = REQUIRED_TOPIC_COLUMNS - set(columns)
        if missing:
            raise ContentLoadError(
                f"Sheet '{MASTER_TOPICS_SHEET}' nema obavezne kolone: "
                f"{sorted(missing)}"
            )

        topics = [r for r in all_rows if r.get("topic")]
        topics_by_id: dict[str, dict[str, str]] = {}
        for row in topics:
            topics_by_id.setdefault(row["topic"], row)

        video_flow = None
        if "VIDEO_FLOW" in wb.sheetnames:
            _, video_flow = _read_sheet(wb, "VIDEO_FLOW")
        parent_report = None
        if "PARENT_REPORT" in wb.sheetnames:
            _, parent_report = _read_sheet(wb, "PARENT_REPORT")
    finally:
        wb.close()

    return {
        "topics": topics,
        "topics_by_id": topics_by_id,
        "topic_ids": set(topics_by_id),
        "video_flow": video_flow,
        "parent_report": parent_report,
        "columns": columns,
        "source_path": str(path),
        "grade": g,
    }


def load_thinkific_map(
    path: str | Path | None = None, grade: Any = DEFAULT_GRADE
) -> dict[str, Any]:
    g = normalize_grade(grade)
    path = Path(path) if path is not None else thinkific_map_path_for_grade(g)
    wb = _open_workbook(path)
    try:
        if THINKIFIC_MAP_SHEET not in wb.sheetnames:
            raise ContentLoadError(
                f"Thinkific mapa nema obavezni sheet '{THINKIFIC_MAP_SHEET}'."
            )
        columns, lessons = _read_sheet(wb, THINKIFIC_MAP_SHEET)
        missing = REQUIRED_MAP_COLUMNS - set(columns)
        if missing:
            raise ContentLoadError(
                f"Sheet '{THINKIFIC_MAP_SHEET}' nema obavezne kolone: "
                f"{sorted(missing)}"
            )

        mapped_topics = {r["topic"] for r in lessons if r.get("topic")}

        topic_reference = None
        topic_reference_ids: set[str] = set()
        if THINKIFIC_TOPIC_REFERENCE_SHEET in wb.sheetnames:
            _, topic_reference = _read_sheet(wb, THINKIFIC_TOPIC_REFERENCE_SHEET)
            topic_reference_ids = {
                r["topic"] for r in topic_reference if r.get("topic")
            }
    finally:
        wb.close()

    return {
        "lessons": lessons,
        "mapped_topics": mapped_topics,
        "topic_reference": topic_reference,
        "topic_reference_ids": topic_reference_ids,
        "columns": columns,
        "source_path": str(path),
        "grade": g,
    }


def validate_mapped_topics(
    master: dict[str, Any] | None = None,
    tmap: dict[str, Any] | None = None,
    grade: Any = DEFAULT_GRADE,
) -> list[str]:
    master = master if master is not None else get_master(grade=grade)
    tmap = tmap if tmap is not None else get_thinkific_map(grade=grade)
    topic_ids = master["topic_ids"]
    return sorted(t for t in tmap["mapped_topics"] if t not in topic_ids)


_lock = threading.Lock()
_master_cache: dict[int, dict[str, Any]] = {}
_tmap_cache: dict[int, dict[str, Any]] = {}


def get_master(
    path: str | Path | None = None,
    reload: bool = False,
    grade: Any = DEFAULT_GRADE,
) -> dict[str, Any]:
    if path is not None:
        return load_master_content(path, grade=grade)
    g = normalize_grade(grade)
    with _lock:
        if g not in _master_cache or reload:
            _master_cache[g] = load_master_content(grade=g)
        return _master_cache[g]


def get_thinkific_map(
    path: str | Path | None = None,
    reload: bool = False,
    grade: Any = DEFAULT_GRADE,
) -> dict[str, Any]:
    if path is not None:
        return load_thinkific_map(path, grade=grade)
    g = normalize_grade(grade)
    with _lock:
        if g not in _tmap_cache or reload:
            _tmap_cache[g] = load_thinkific_map(grade=g)
        return _tmap_cache[g]
