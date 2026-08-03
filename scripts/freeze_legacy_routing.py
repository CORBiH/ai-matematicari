"""Zamrzni PRE-Stage-A routiranje porodica za sve lekcije bez ugovora.

    python scripts/freeze_legacy_routing.py            # provjeri (ne piše)
    python scripts/freeze_legacy_routing.py --write    # upiši fixture

ZAŠTO NEZAVISNA IMPLEMENTACIJA: baseline koji bi se generisao pozivom
`task_families.applicable_families()` ne bi ništa dokazivao — mjerio bi kod
samim sobom. Zato je algoritam otprije uvođenja motora ovdje PONOVO NAPISAN iz
istorijskog stanja repozitorija, s doslovnim listama. Test parnosti onda poredi
PROIZVODNI kod s ovim zamrznutim ishodom.

Fixture SMIJE sadržavati ID-jeve lekcija — to su podaci migracije, a ne kod
generičkog motora.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matbot import geometry_rules                      # noqa: E402
from matbot.contracts import registry                  # noqa: E402
from matbot.rules import route_topic_rules             # noqa: E402
from matbot.task_families import select_family         # noqa: E402

import re                                              # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "legacy_routing_baseline.json"
TOPICS = ROOT / "data" / "topics.json"

# --- ISTORIJSKO STANJE (prepisano iz repozitorija prije uvođenja motora) -----

H_FRACTION = ["expand_to_given_denominator", "find_expansion_factor",
              "find_missing_numerator", "recognize_equivalent_fraction",
              "fraction_operation", "compare_fractions", "detect_student_error",
              "fraction_word_problem"]
H_SYSTEM = ["solve_system", "verify_ordered_pair", "choose_method",
            "determine_number_of_solutions", "identify_equivalent_system",
            "detect_student_error", "system_word_problem"]
H_EQUATION = ["solve_equation", "verify_solution", "identify_next_step",
              "detect_student_error", "translate_to_equation", "word_problem"]
H_GEOMETRY = ["direct_formula_application", "choose_correct_formula",
              "find_missing_dimension", "inverse_formula_problem",
              "detect_formula_error", "compare_figures", "unit_conversion",
              "practical_geometry_problem"]
H_GENERAL = ["direct_computation", "find_missing_value",
             "recognize_correct_statement", "detect_student_error",
             "compare_or_order", "word_problem"]
H_CONSTRUCTION = ["identify_next_step", "choose_correct_formula",
                  "recognize_correct_statement", "detect_student_error"]

H_GRADE6_BY_TOPIC = {
    "6-04-001": ["recognize_correct_statement", "find_missing_value", "detect_student_error"],
    "6-04-002": ["fraction_word_problem", "find_missing_value", "recognize_correct_statement"],
    "6-04-003": ["recognize_correct_statement", "direct_computation", "detect_student_error"],
    "6-04-004": ["compare_fractions", "find_missing_value", "recognize_correct_statement"],
    "6-04-005": ["expand_to_given_denominator", "find_expansion_factor",
                 "find_missing_numerator", "recognize_equivalent_fraction",
                 "detect_student_error"],
    "6-04-006": ["find_expansion_factor", "recognize_equivalent_fraction",
                 "detect_student_error"],
    "6-04-007": ["expand_to_given_denominator", "find_missing_numerator",
                 "recognize_equivalent_fraction", "compare_fractions"],
    "6-04-008": ["compare_fractions", "recognize_equivalent_fraction", "detect_student_error"],
    "6-04-009": ["fraction_add_subtract_equal", "detect_student_error", "fraction_word_problem"],
    "6-04-010": ["fraction_add_subtract_unlike", "detect_student_error", "fraction_word_problem"],
    "6-04-011": ["fraction_multiplication", "detect_student_error", "fraction_word_problem"],
    "6-04-012": ["fraction_division", "detect_student_error", "fraction_word_problem"],
    "6-04-013": ["fraction_operation", "recognize_correct_statement", "detect_student_error"],
    "6-04-014": ["fraction_expression", "detect_student_error", "compare_fractions"],
    "6-04-015": ["fraction_word_problem", "fraction_operation", "detect_student_error"],
}
H_GRADE6_NON_EXPANSION = ["fraction_operation", "compare_fractions",
                          "detect_student_error", "fraction_word_problem"]

_CONSTRUCTION_RE = re.compile(r"konstruk", re.IGNORECASE)


def historical_applicable_families(grade, oblast, lesson_title, lesson_id=""):
    """Doslovno ponašanje `applicable_families` prije uvođenja motora ugovora."""
    haystack = f"{oblast or ''} {lesson_title or ''}"
    if _CONSTRUCTION_RE.search(haystack):
        return list(H_CONSTRUCTION)
    topic_ids = route_topic_rules(oblast, lesson_title)
    geometry_scope, _ = geometry_rules.route_geometry_topic(oblast, lesson_title)
    if "sistemi" in topic_ids:
        return list(H_SYSTEM)
    if "razlomci" in topic_ids:
        if grade == 6 and lesson_id in H_GRADE6_BY_TOPIC:
            return list(H_GRADE6_BY_TOPIC[lesson_id])
        if grade == 6 and lesson_id:
            return list(H_GRADE6_NON_EXPANSION)
        return list(H_FRACTION)
    if geometry_scope:
        return list(H_GEOMETRY)
    if "jednacine" in topic_ids or "nejednacine" in topic_ids:
        return list(H_EQUATION)
    return list(H_GENERAL)


def build():
    data = json.loads(TOPICS.read_text(encoding="utf-8"))
    enabled = {
        topic_id for topic_id, contract in registry.load_all().items()
        if contract.status == "enabled"
    }
    rows = []
    for grade, grade_data in data["grades"].items():
        for lesson in grade_data["lessons"]:
            topic_id = lesson["id"]
            if topic_id in enabled:
                continue          # pilot lekcije ne koriste legacy routing
            families = historical_applicable_families(
                int(grade), lesson["oblast"], lesson["title"], lesson_id=topic_id)
            rows.append({
                "topic_id": topic_id,
                "grade": int(grade),
                "oblast": lesson["oblast"],
                "families": families,
                "first_family": select_family(families),
                "harder_family": select_family(
                    families, current_family=families[-1], difficulty_request="harder"),
                "easier_family": select_family(
                    families, current_family=families[-1], difficulty_request="easier"),
            })
    return {
        "_readme": (
            "ZAMRZNUTO PRE-Stage-A routiranje. Sve lekcije bez uključenog ugovora "
            "moraju i dalje davati identičan rezultat. Fixture smije sadržavati "
            "ID-jeve lekcija — to su podaci migracije, ne kod motora."
        ),
        "excluded_enabled_contracts": sorted(enabled),
        "lessons": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="upiši fixture na disk")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    payload = build()
    print(f"Lekcija bez ugovora: {len(payload['lessons'])}")
    print(f"Isključene (enabled): {len(payload['excluded_enabled_contracts'])}")

    if args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"upisano: {FIXTURE.relative_to(ROOT)}")
    else:
        print("(probni rad — ništa nije upisano; koristi --write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
