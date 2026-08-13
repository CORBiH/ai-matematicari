"""Globalna revizija semantičke pokrivenosti kurikuluma (EVALUACIJA, 0 poziva).

Za SVAKU lekciju iz `data/topics.json` javlja šta server zna prije generisanja:
primarnu vještinu, semantičku porodicu, susjedne zabrane, ugovor, profil težine
i rutu. Postoji da „not_applicable“ nikad više ne prođe kao „uredu“ — odsustvo
mjerila nije dokaz ispravnosti.

    python scripts/audit_lesson_semantic_coverage.py [--json putanja]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

DATA = ROOT / "data"


def _load(name, key=None):
    payload = json.loads((DATA / name).read_text(encoding="utf-8"))
    return payload if key is None else payload[key]


def all_lessons():
    grades = _load("topics.json", "grades")
    rows = []
    for grade in sorted(grades, key=int):
        for lesson in grades[grade]["lessons"]:
            rows.append({"lesson_id": lesson["id"], "grade": int(grade),
                         "oblast": lesson.get("oblast", ""),
                         "title": lesson.get("title", "")})
    return rows


def coverage():
    objectives = _load("lesson_objectives.compiled.json", "lessons")
    semantics = _load("lesson_semantics.compiled.json", "lessons")
    overrides = _load("lesson_scope_overrides.json", "overrides")
    contracts = {c["canonical_topic_id"] for c in _load("lesson_contracts.json", "contracts")}
    practice = {c["lesson_id"] for c in _load("semantic_practice_contracts.json", "contracts")}
    rows = []
    for row in all_lessons():
        lid = row["lesson_id"]
        obj = objectives.get(lid) or {}
        sem = semantics.get(lid) or {}
        override = overrides.get(lid) or {}
        primary = list(override.get("objectives") or obj.get("primary_skills") or ())
        concepts = list((sem.get("parameters") or {}).get("concepts") or ())
        forbidden = list(sem.get("forbidden_neighbour_skills") or ())
        neighbours = list(obj.get("neighbour_exclusions") or ())
        source = obj.get("objective_source", "")
        confidence = obj.get("objective_confidence", "")
        if override.get("objectives"):
            source, confidence = "scope_override", "high"
        elif not source and concepts:
            source, confidence = "semantic_family", "medium"
        elif not source:
            source, confidence = "none", "none"
        rows.append({
            **row,
            "has_primary_skill": bool(primary),
            "primary_skills": primary,
            "supporting_concepts": list(obj.get("supporting_concepts") or ()),
            "semantic_family": sem.get("family_id", ""),
            "detector": sem.get("detector", ""),
            "activation_class": sem.get("activation_class", ""),
            "enforcement_mode": sem.get("enforcement_mode", ""),
            "family_concepts": concepts,
            "level_bounds": sem.get("level_bounds") or {},
            "neighbour_exclusions": sorted(set(neighbours) | set(forbidden)),
            "objective_source": source,
            "objective_confidence": confidence,
            "has_lesson_contract": lid in contracts,
            "has_practice_contract": lid in practice,
            "evidence_ids": list(override.get("evidence_ids")
                                 or obj.get("evidence_ids") or sem.get("evidence_ids") or ()),
        })
    return rows


def report(rows):
    total = len(rows)
    have_primary = [r for r in rows if r["has_primary_skill"]]
    # MJERILO je sve što server zna prije generisanja: dokazana vještina,
    # semantička porodica, susjedne zabrane ILI kanonski naslov kao izdvojena,
    # označena klasa. Nemjerljivo je samo ono što nema nijedno od toga.
    family_only = [r for r in rows
                   if not r["has_primary_skill"]
                   and (r["family_concepts"] or r["neighbour_exclusions"]
                        or r["objective_source"] == "canonical_title_only")]
    bare = [r for r in rows if not r["has_primary_skill"]
            and not r["family_concepts"] and not r["neighbour_exclusions"]
            and r["objective_source"] != "canonical_title_only"]
    print("=" * 92)
    print(f"LEKCIJA UKUPNO: {total}")
    print(f"  primarna vještina (NPP/override): {len(have_primary):>3}  "
          f"({len(have_primary) / total:.1%})")
    print(f"  opseg (porodica/susjedi):         {len(family_only):>3}  "
          f"({len(family_only) / total:.1%})")
    print(f"  BEZ IJEDNOG MJERILA:              {len(bare):>3}  ({len(bare) / total:.1%})")
    measurable = total - len(bare)
    print(f"  MJERLJIVO UKUPNO:                 {measurable:>3}  ({measurable / total:.1%})")
    print("\nizvor objektiva:", dict(Counter(r["objective_source"] for r in rows)))
    print("pouzdanost:     ", dict(Counter(r["objective_confidence"] for r in rows)))
    print(f"susjedne zabrane: {sum(1 for r in rows if r['neighbour_exclusions'])}")
    print(f"granice nivoa:    {sum(1 for r in rows if r['level_bounds'])}")
    print(f"ugovor lekcije:   {sum(1 for r in rows if r['has_lesson_contract'])}")
    print(f"practice ugovor:  {sum(1 for r in rows if r['has_practice_contract'])}")
    print("\n=== BEZ MJERILA, PO RAZREDU ===")
    for grade, count in sorted(Counter(r["grade"] for r in bare).items()):
        print(f"  r{grade}: {count}")
    print("\n=== BEZ MJERILA, PO OBLASTI (top 15) ===")
    for oblast, count in Counter(r["oblast"] for r in bare).most_common(15):
        print(f"  {count:>3}  {oblast}")
    return {"total": total, "primary": len(have_primary), "family_only": len(family_only),
            "bare": len(bare), "measurable": measurable}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    rows = coverage()
    summary = report(rows)
    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary, "lessons": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"\nzapisano {args.json}")


if __name__ == "__main__":
    main()
