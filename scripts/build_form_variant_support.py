"""Koje ALGEBARSKE OBLIKE lekcija DEKLARIŠE svojim gradivom (0 modelskih poziva).

Izvor je isključivo autoritativan tekst lekcije — naslov iz `data/topics.json` i
NPP ishodi iz `data/lesson_objectives.compiled.json`. Ako gradivo izričito
nabraja `x ± a` i `a ± x`, oba oblika postaju podržana; ako ne nabraja ništa,
lekcija NEMA unos i ponaša se bajt-identično zatečenom.

Nema nijednog ID-ja lekcije u pravilima — obrasci su opšti i primjenjuju se na
svih 534 lekcije jednako.

    python scripts/build_form_variant_support.py [--write]
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from matbot import form_variants                                     # noqa: E402

OUT = ROOT / "data" / "task_form_variants.json"

# Nepoznata u gradivu se piše `x`. Broj/parametar je `a` ili `b`.
_UNKNOWN = r"x"
_PARAMETER = r"[ab]"
# `x ± a`, `x + a`, `x - a` — nepoznata je PRVI član.
_X_FIRST_RE = re.compile(
    rf"(?<![a-zA-Z]){_UNKNOWN}\s*(±|\+/-|[+\-])\s*{_PARAMETER}(?![a-zA-Z])")
# `a ± x`, `a + x`, `a - x` — nepoznata je DRUGI član.
_A_FIRST_RE = re.compile(
    rf"(?<![a-zA-Z]){_PARAMETER}\s*(±|\+/-|[+\-])\s*{_UNKNOWN}(?![a-zA-Z])")

_BY_OPERATOR = {
    ("x", "+"): form_variants.X_PLUS_A,
    ("x", "-"): form_variants.X_MINUS_A,
    ("a", "+"): form_variants.A_PLUS_X,
    ("a", "-"): form_variants.A_MINUS_X,
}


def _variants_in(text):
    """Oblici koje ovaj tekst izričito nabraja."""
    found = set()
    for pattern, position in ((_X_FIRST_RE, "x"), (_A_FIRST_RE, "a")):
        for match in pattern.finditer(text):
            operator = match.group(1)
            operators = ("+", "-") if operator in ("±", "+/-") else (operator,)
            for sign in operators:
                found.add(_BY_OPERATOR[(position, sign)])
    return found


def _authoritative_text(title, objective):
    parts = [title or ""]
    for key in ("primary_skills", "supporting_concepts"):
        parts.extend(objective.get(key) or ())
    return "\n".join(str(part) for part in parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    objectives = json.loads(
        (ROOT / "data" / "lesson_objectives.compiled.json").read_text(encoding="utf-8"))
    objective_rows = objectives.get("lessons") or objectives

    lessons = {}
    histogram = Counter()
    for grade, payload in (topics.get("grades") or {}).items():
        for lesson in payload.get("lessons") or ():
            lesson_id = lesson.get("id")
            title = lesson.get("title") or ""
            objective = objective_rows.get(lesson_id) or {}
            found = _variants_in(_authoritative_text(title, objective))
            if len(found) < 2:
                continue           # bez izričito nabrojanih oblika — nema unosa
            ordered = [variant for variant in form_variants.ALL_VARIANTS
                       if variant in found]
            lessons[lesson_id] = {
                "grade": int(grade), "title": title, "supported": ordered,
                "source": "npp_declared_forms",
            }
            histogram[tuple(ordered)] += 1

    artifact = {
        "_readme": ("Algebarski OBLICI koje lekcija DEKLARIŠE svojim gradivom "
                    "(naslov + NPP ishodi). Izvedeno, ne rucno pisano: "
                    "scripts/build_form_variant_support.py. Lekcija bez unosa "
                    "nema rotaciju oblika i ponasa se kao ranije."),
        "schema_version": 1,
        "lessons": lessons,
    }
    print(f"lekcija s deklarisanim oblicima: {len(lessons)}")
    for combination, count in histogram.most_common():
        print(f"  {count:>3}  {' + '.join(combination)}")
    for lesson_id, row in list(lessons.items())[:12]:
        print(f"  {lesson_id} r{row['grade']} {row['title'][:58]}")
        print(f"        {row['supported']}")
    if args.write:
        OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"\nzapisano {OUT}")
    else:
        print("\n(probni hod — bez --write nista se ne upisuje)")


if __name__ == "__main__":
    main()
