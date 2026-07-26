"""Konvertuje reference/curriculum/MATBOT_Sve_Lekcije_6_7_8_9.xlsx u data/topics.json.

Razvojni izvor podataka je Excel; produkcijski backend čita samo generisani
JSON i nikad ne otvara Excel u runtime-u. Pokreni ručno kad se kurikulum
promijeni:

    python scripts/build_topics_json.py
"""
import json
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
XLSX_PATH = ROOT / "reference" / "curriculum" / "MATBOT_Sve_Lekcije_6_7_8_9.xlsx"
JSON_PATH = ROOT / "data" / "topics.json"
SHEET_NAME = "Sve lekcije"


def build_grades(rows):
    grades = {}
    seen_ids = set()
    for razred, oblast, lekcija, topic_id in rows:
        if razred is None and oblast is None and lekcija is None and topic_id is None:
            continue
        grade_key = str(int(razred)).strip()
        oblast = (oblast or "").strip()
        lekcija = (lekcija or "").strip()
        topic_id = (topic_id or "").strip()
        if not grade_key or not oblast or not lekcija or not topic_id:
            raise ValueError(f"Nepotpun red u Excelu: {(razred, oblast, lekcija, topic_id)!r}")
        if topic_id in seen_ids:
            raise ValueError(f"Dupli ID lekcije: {topic_id!r}")
        seen_ids.add(topic_id)

        grade = grades.setdefault(grade_key, {"oblast_order": [], "lessons": []})
        if oblast not in grade["oblast_order"]:
            grade["oblast_order"].append(oblast)
        grade["lessons"].append({"id": topic_id, "oblast": oblast, "title": lekcija})
    return grades


def main():
    if not XLSX_PATH.exists():
        print(f"Excel fajl ne postoji: {XLSX_PATH}", file=sys.stderr)
        sys.exit(1)

    wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        print(f"Sheet '{SHEET_NAME}' ne postoji u {XLSX_PATH}", file=sys.stderr)
        sys.exit(1)
    ws = wb[SHEET_NAME]

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        print("Excel sheet je prazan.", file=sys.stderr)
        sys.exit(1)
    data_rows = rows[1:]  # preskoči header (Razred, Oblast, Lekcija, ID lekcije)

    grades = build_grades(data_rows)

    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JSON_PATH.open("w", encoding="utf-8") as f:
        json.dump({"grades": grades}, f, ensure_ascii=False, indent=2)
        f.write("\n")

    total_lessons = sum(len(g["lessons"]) for g in grades.values())
    print(f"OK: {JSON_PATH} — {len(grades)} razreda, {total_lessons} lekcija.")


if __name__ == "__main__":
    main()
