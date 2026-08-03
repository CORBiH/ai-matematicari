"""Rekoncilijacija kurikuluma: jedan red po lekciji, za svih 534.

SAMO ČITA. Ne mijenja nijedan fajl, ne poziva model, ne dira mrežu.

    python scripts/audit_curriculum.py            # sažetak
    python scripts/audit_curriculum.py --csv      # cio izvještaj na stdout

Kolone su namjerno one koje su tražene u reviziji arhitekture: da li lekcija
postoji u učeničkom biraču, da li je uključena, da li je duplikat/alias, koje
je vrste red, kako se trenutno rutira i šta je preporučeni status migracije.
"""
import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matbot import geometry_rules, task_families as tf              # noqa: E402
from matbot.contracts import registry                               # noqa: E402
from matbot.rules import route_topic_rules                          # noqa: E402
from matbot.topics import lesson_info, oblast_id_for_topic, topics_response  # noqa: E402

DATA_PATH = ROOT / "data" / "topics.json"
_TOPIC_ID_RE = re.compile(r"^\d-\d{2}-\d{3}$")
_HEADING_RE = re.compile(
    r"^(ponavljanje|sistematizacija|utvr|obnavljanje|kontroln|godisnj|polugod)", re.I
)

COLUMNS = [
    "topic_id", "grade", "oblast_id", "oblast_name", "lesson_title",
    "in_student_selector", "enabled", "duplicate_or_alias", "row_type",
    "current_family_bucket", "current_families", "practice_reachable",
    "recommended_migration_status",
]


def _fold(text):
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()


def _routing_bucket(grade, oblast, title):
    haystack = f"{oblast} {title}"
    if re.search(r"konstruk", haystack, re.I):
        return "construction"
    topic_ids = route_topic_rules(oblast, title)
    scope, _ = geometry_rules.route_geometry_topic(oblast, title)
    if "sistemi" in topic_ids:
        return "systems"
    if "razlomci" in topic_ids:
        return "fractions"
    if scope:
        return "geometry"
    if "jednacine" in topic_ids or "nejednacine" in topic_ids:
        return "equations"
    return "general-fallback"


def build_rows():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    selector = set()
    for grade in data["grades"]:
        for items in topics_response(grade)["grouped"].values():
            selector.update(item["topic"] for item in items)

    titles = defaultdict(list)
    for grade, grade_data in data["grades"].items():
        for lesson in grade_data["lessons"]:
            titles[(grade, _fold(lesson["title"]))].append(lesson["id"])

    rows = []
    for grade, grade_data in data["grades"].items():
        for lesson in grade_data["lessons"]:
            topic_id, title, oblast = lesson["id"], lesson["title"], lesson["oblast"]
            families = tf.applicable_families(
                int(grade), oblast, title, lesson_id=topic_id)
            # Alias = ISTI naziv unutar ISTOG razreda. Isti naziv u dva razreda
            # je spiralni kurikulum (npr. „Kružnica i krug“ u 6. i 7.), ne alias.
            alias = len(titles[(grade, _fold(title))]) > 1
            row_type = "heading" if _HEADING_RE.match(_fold(title)) else "lesson"
            rows.append({
                "topic_id": topic_id,
                "grade": grade,
                "oblast_id": oblast_id_for_topic(topic_id),
                "oblast_name": oblast,
                "lesson_title": title,
                "in_student_selector": "yes" if topic_id in selector else "no",
                "enabled": "yes",
                "duplicate_or_alias": "yes" if alias else "no",
                "row_type": row_type,
                "current_family_bucket": _routing_bucket(int(grade), oblast, title),
                "current_families": "|".join(families),
                "practice_reachable":
                    "yes" if lesson_info(int(grade), topic_id) else "no",
                "recommended_migration_status": registry.report_status(topic_id),
            })
    return rows


def integrity_report(rows):
    ids = [row["topic_id"] for row in rows]
    problems = {
        "malformed_ids": [i for i in ids if not _TOPIC_ID_RE.fullmatch(i)],
        "duplicate_ids": [i for i, n in Counter(ids).items() if n > 1],
        "grade_prefix_mismatch": [
            row["topic_id"] for row in rows if row["topic_id"][0] != row["grade"]
        ],
        "not_in_selector": [
            row["topic_id"] for row in rows if row["in_student_selector"] != "yes"
        ],
        "not_practice_reachable": [
            row["topic_id"] for row in rows if row["practice_reachable"] != "yes"
        ],
        "aliases_within_a_grade": [
            row["topic_id"] for row in rows if row["duplicate_or_alias"] == "yes"
        ],
    }
    return {key: value for key, value in problems.items() if value}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", action="store_true", help="ispiši sve redove kao CSV")
    args = parser.parse_args()

    # Nazivi lekcija nose dijakritiku i „cm²“; Windows konzola je često cp1250.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    rows = build_rows()
    if args.csv:
        writer = csv.DictWriter(sys.stdout, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return 0

    print(f"Lekcija ukupno: {len(rows)}")
    print("\nPo razredu:")
    for grade, count in sorted(Counter(row["grade"] for row in rows).items()):
        print(f"  razred {grade}: {count}")

    print("\nTrenutni routing:")
    for bucket, count in Counter(
            row["current_family_bucket"] for row in rows).most_common():
        print(f"  {bucket:22} {count:4}")

    print("\nStatus migracije:")
    for status, count in Counter(
            row["recommended_migration_status"] for row in rows).most_common():
        print(f"  {status:22} {count:4}")

    problems = integrity_report(rows)
    print("\nIntegritet:", "sve čisto" if not problems else "PROBLEMI")
    for key, value in problems.items():
        print(f"  {key}: {len(value)} → {value[:8]}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
