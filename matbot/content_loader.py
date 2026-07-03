"""Phase 1 — učitavanje modularnog sadržaja za 6. razred (MVP AI tutor).

Ovaj modul čita DVA finalna Excel fajla iz ``data/6_razred/`` i pretvara ih u
normalizovane Python strukture. Excel je **izvor istine**: nijedan naslov lekcije,
topic, greška, zadatak ili mapiranje ne smije biti hardkodiran u kodu (vidi
``docs/handoff/IT_HANDOFF_6_RAZRED_MVP_SEND_READY_FINAL.docx``).

Dva izvora:

* ``AI_MATH_CONTENT_MASTER_6_RAZRED_MODULAR_FINAL.xlsx`` — pedagoški izvor istine.
  Sheet ``TOPICS`` (obavezan) definiše sve validne topic-e. ``VIDEO_FLOW`` i
  ``PARENT_REPORT`` su opcionalni.
* ``THINKIFIC_MAP_6_RAZRED_MODULAR_FINAL.xlsx`` — mapiranje stvarnih Thinkific
  lekcija na topic-e. Sheet ``MAP`` (obavezan). ``TOPIC_REFERENCE`` opcionalan.
  Sheet-ovi ``PRIMJER``/``LISTS``/``UPUTSTVO``/``MODULAR_MODEL`` su primjeri i
  dokumentacija — namjerno se ignorišu.

Modul je **samostalan**: nije uvezan u ``app.py`` niti u postojeće rute, pa ne
mijenja ponašanje aplikacije (Phase 1). Lookup logika je u ``matbot.topic_lookup``.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import openpyxl

# --- Putanje do finalnih fajlova (repo_root/data/6_razred/) ---------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _REPO_ROOT / "data" / "6_razred"

MASTER_PATH = _DATA_DIR / "AI_MATH_CONTENT_MASTER_6_RAZRED_MODULAR_FINAL.xlsx"
THINKIFIC_MAP_PATH = _DATA_DIR / "THINKIFIC_MAP_6_RAZRED_MODULAR_FINAL.xlsx"

# --- Obavezni sheet-ovi i kolone ------------------------------------------------
MASTER_TOPICS_SHEET = "TOPICS"
MASTER_OPTIONAL_SHEETS = ("VIDEO_FLOW", "PARENT_REPORT")
THINKIFIC_MAP_SHEET = "MAP"
THINKIFIC_TOPIC_REFERENCE_SHEET = "TOPIC_REFERENCE"

# Minimalne kolone bez kojih lookup/validacija ne mogu raditi.
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
    """Podignut kada obavezni fajl / sheet / kolona nedostaje ili je nečitljiv."""


# --- Normalizacija --------------------------------------------------------------

def normalize_header(name: Any) -> str:
    """Kolone: strip → lowercase → razmaci/crtice u donju crtu.

    ``"Lesson  Title"`` → ``"lesson_title"``; ``"course-name"`` → ``"course_name"``.
    """
    if name is None:
        return ""
    s = str(name).strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    return s


def normalize_value(val: Any) -> str:
    """Vrijednosti ćelija u čist string. Cijeli brojevi (npr. ``lesson_order``)
    dolaze iz openpyxl-a kao ``int``/``float``; ``1.0`` → ``"1"`` da kompozitni
    ključ radi bez obzira kako je broj zapisan u fajlu ili payloadu."""
    if val is None:
        return ""
    if isinstance(val, bool):  # bool je podklasa int-a; ne želimo "True"/"False"
        return str(val)
    if isinstance(val, float) and val.is_integer():
        val = int(val)
    return str(val).strip()


def _read_sheet(wb, sheet_name: str) -> tuple[list[str], list[dict[str, str]]]:
    """Vrati ``(headers, rows)`` gdje je svaki red dict {kolona: vrijednost}.

    Prvi red je zaglavlje. Potpuno prazni redovi se preskaču. Kolone bez naziva
    se ignorišu (spriječava ``""`` ključ od spajanja praznih kolona)."""
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
            continue  # potpuno prazan red
        row: dict[str, str] = {}
        for i, col in enumerate(headers):
            if not col:
                continue
            row[col] = normalize_value(raw[i]) if i < len(raw) else ""
        rows.append(row)
    return headers, rows


def _open_workbook(path: Path):
    if not path.exists():
        raise ContentLoadError(f"Excel fajl nije pronađen: {path}")
    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # pragma: no cover - openpyxl podiže razne tipove
        raise ContentLoadError(f"Ne mogu otvoriti Excel fajl {path}: {exc}") from exc


# --- Master (AI_MATH_CONTENT_MASTER) -------------------------------------------

def load_master_content(path: str | Path | None = None) -> dict[str, Any]:
    """Učitaj master workbook.

    Vraća dict:
        topics        — lista redova (dict) iz sheet-a TOPICS koji imaju ``topic``
        topics_by_id  — {topic_id: red}
        topic_ids     — set validnih topic id-eva (izvor istine za final_topic)
        video_flow    — lista redova ili None ako sheet ne postoji
        parent_report — lista redova ili None ako sheet ne postoji
        columns       — normalizovana zaglavlja TOPICS sheet-a
        source_path   — apsolutna putanja fajla
    """
    path = Path(path) if path is not None else MASTER_PATH
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
            topics_by_id.setdefault(row["topic"], row)  # prvi red pobjeđuje

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
    }


# --- Thinkific mapa -------------------------------------------------------------

def load_thinkific_map(path: str | Path | None = None) -> dict[str, Any]:
    """Učitaj Thinkific mapu (samo sheet MAP kao stvarno mapiranje).

    Vraća dict:
        lessons              — lista redova iz MAP sheet-a (bez praznih)
        mapped_topics        — set topic-a na koje MAP redovi upućuju
        topic_reference      — lista redova TOPIC_REFERENCE ili None
        topic_reference_ids  — set topic id-eva iz TOPIC_REFERENCE
        columns              — normalizovana zaglavlja MAP sheet-a
        source_path          — apsolutna putanja fajla
    """
    path = Path(path) if path is not None else THINKIFIC_MAP_PATH
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
    }


# --- Cross-file validacija ------------------------------------------------------

def validate_mapped_topics(
    master: dict[str, Any] | None = None,
    tmap: dict[str, Any] | None = None,
) -> list[str]:
    """Vrati sortiranu listu topic-a iz THINKIFIC_MAP.MAP koji NE postoje u
    AI_MATH_CONTENT_MASTER.TOPICS. Prazna lista = mapa je konzistentna.

    Ne baca izuzetak: pozivalac odlučuje o ozbiljnosti (npr. test tvrdi da je
    prazna, a runtime može samo logovati)."""
    master = master if master is not None else get_master()
    tmap = tmap if tmap is not None else get_thinkific_map()
    topic_ids = master["topic_ids"]
    return sorted(t for t in tmap["mapped_topics"] if t not in topic_ids)


# --- Keširanje po default putanjama (učitaj jednom) -----------------------------
_lock = threading.Lock()
_master_cache: dict[str, Any] | None = None
_tmap_cache: dict[str, Any] | None = None


def get_master(path: str | Path | None = None, reload: bool = False) -> dict[str, Any]:
    """Keširani master za default putanju. Eksplicitna ``path`` uvijek učitava
    iznova (i ne dira keš) — zgodno za testove."""
    global _master_cache
    if path is not None:
        return load_master_content(path)
    with _lock:
        if _master_cache is None or reload:
            _master_cache = load_master_content()
        return _master_cache


def get_thinkific_map(
    path: str | Path | None = None, reload: bool = False
) -> dict[str, Any]:
    """Keširana Thinkific mapa za default putanju. Vidi ``get_master``."""
    global _tmap_cache
    if path is not None:
        return load_thinkific_map(path)
    with _lock:
        if _tmap_cache is None or reload:
            _tmap_cache = load_thinkific_map()
        return _tmap_cache
