"""Koje ARHETIPE zadatka svaka lekcija legitimno podržava.

PRAVILO JE OPŠTE I ČITA SE IZ DOKAZA O LEKCIJI (naslov, kanonski ishodi,
semantička porodica) — nikad se ne piše po ID-ju lekcije. Popravak podataka
lekcije mijenja rezultat, ne Python.

ZAŠTO: raznolikost šablona nije raznolikost vježbe. Da bi server mogao TRAŽITI
drugi oblik zadatka, mora prvo znati koji su oblici za tu lekciju uopšte
dozvoljeni — inače bi „budi drugačiji“ vodilo izvan lekcije.

OPREZ: arhetip je PREFERENCIJA, a semantičke kapije i dalje odlučuju o objavi.
Ipak se dodjeljuje suzdržano: bolje manje oblika nego oblik koji lekcija ne
podnosi. Lekcija s manje od tri oblika je `NARROW_SCOPE` i iz nje se ne
izmišlja raznolikost.

    python scripts/build_archetype_support.py [--write]
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from matbot import lesson_objectives                                 # noqa: E402
from matbot import task_archetypes as ta                             # noqa: E402

OUTPUT = ROOT / "data" / "task_archetype_support.json"
TOPICS = ROOT / "data" / "topics.json"
SEMANTICS = ROOT / "data" / "lesson_semantics.compiled.json"

NARROW_THRESHOLD = 3


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower())
                   if not unicodedata.combining(c))


def _has(text, *words):
    body = _fold(text)
    return any(re.search(rf"\b{word}", body) for word in words)


# Signali se čitaju iz NASLOVA + KANONSKIH ISHODA + imena porodice.
# POSTUPAK se prepoznaje i po GLAGOLU i po MATEMATIČKOM OBJEKTU: mnogi naslovi
# nemaju glagol („Jednačina x²=a“), a nesumnjivo nose postupak koji se može
# pogriješiti, prekinuti na pola ili obrnuti.
PROCEDURE = ("izracun", "rijesi", "rjesav", "skrati", "prosiri", "sabir", "oduzim",
             "mnoz", "dijel", "racun", "primijen", "primjen", "postup", "odredi",
             "konstru", "izvod", "transform", "sred", "faktor",
             "jednacin", "nejednacin", "izraz", "formul", "povrsin", "zapremin",
             "obim", "teorem", "stepen", "korijen", "funkcij", "sistem",
             "razlom", "procen", "postot", "kamat", "razmjer", "proporcij",
             "brojev", "decimal", "operacij", "srednja", "sredin")
REPRESENTATION = ("zapis", "oblik", "pretvar", "decimal", "razlom", "procen", "postot",
                  "jedinic", "mjern", "grafi", "prikaz", "koordinat", "tabel")
CLASSIFICATION = ("pojam", "vrst", "prepozn", "razlik", "pripada", "skup", "klasif",
                  "definic", "svojstv", "elementi", "oznacav")
WORD_PROBLEM = ("tekstual", "primjen", "problem", "praktic", "svakodnev", "zadaci")
COMPARISON = ("uporedj", "uporedi", "poredj", "uredj", "veci", "manji", "odnos")


def supported_for(lesson, objectives, family):
    """Skup arhetipa koje lekcija podržava — iz njenih vlastitih dokaza."""
    evidence = " ".join([lesson.get("title", ""), lesson.get("oblast", ""),
                         " ".join(objectives), family or ""])
    supported = {ta.DIRECT_COMPUTE, ta.CHOOSE_CORRECT_REASONING}
    procedural = _has(evidence, *PROCEDURE)
    if procedural:
        # Gdje postoji POSTUPAK, postoji i tuđa greška u njemu i korak koji
        # nedostaje, i obrnut smjer (iz rezultata natrag u nepoznatu).
        supported |= {ta.ERROR_ANALYSIS, ta.COMPLETE_MISSING_STEP,
                      ta.FIND_MISSING_VALUE}
    if _has(evidence, *REPRESENTATION):
        supported.add(ta.TRANSLATE_REPRESENTATION)
    if _has(evidence, *CLASSIFICATION):
        supported.add(ta.CLASSIFY_CASE)
    if _has(evidence, *WORD_PROBLEM):
        # Lekcija koja se ZOVE tekstualnim zadacima podržava višekoračnu primjenu
        # i bez glagola u naslovu.
        supported.add(ta.MULTI_STEP_APPLICATION)
    if _has(evidence, *COMPARISON) or procedural:
        supported.add(ta.COMPARE_RESULTS)
    return supported


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    grades = json.loads(TOPICS.read_text(encoding="utf-8"))["grades"]
    semantics = json.loads(SEMANTICS.read_text(encoding="utf-8"))["lessons"]

    lessons = {}
    counts = Counter()
    narrow = []
    for grade, block in sorted(grades.items()):
        for lesson in block["lessons"]:
            lesson_id = lesson["id"]
            objectives = list(lesson_objectives.primary_skills(lesson_id))
            family = (semantics.get(lesson_id) or {}).get("family_id", "")
            supported = sorted(supported_for(lesson, objectives, family))
            is_narrow = len(supported) < NARROW_THRESHOLD
            lessons[lesson_id] = {
                "grade": int(grade),
                "family": family,
                "supported": supported,
                "narrow_scope": is_narrow,
            }
            counts[len(supported)] += 1
            if is_narrow:
                narrow.append(lesson_id)

    payload = {
        "_readme": [
            "Arhetipi zadatka koje svaka lekcija legitimno podrzava.",
            "Izvedeno iz naslova, kanonskih ishoda i semanticke porodice —",
            "nikad rucno po ID-ju lekcije (scripts/build_archetype_support.py).",
            "Arhetip je PREFERENCIJA; semanticke kapije i dalje odlucuju o objavi.",
        ],
        "schema_version": 1,
        "narrow_threshold": NARROW_THRESHOLD,
        "archetypes": list(ta.ALL_ARCHETYPES),
        "lessons": lessons,
        "narrow_scope_lessons": sorted(narrow),
    }
    print(f"lekcija: {len(lessons)} | NARROW_SCOPE: {len(narrow)}")
    print("raspodjela broja podrzanih arhetipa:")
    for size in sorted(counts):
        print(f"  {size} arhetipa: {counts[size]} lekcija")
    spread = Counter()
    for row in lessons.values():
        for archetype in row["supported"]:
            spread[archetype] += 1
    print("\nkoliko lekcija podrzava koji arhetip:")
    for archetype, count in spread.most_common():
        print(f"  {archetype:<26}{count:>5}")
    if args.write:
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        print(f"\nzapisano {OUTPUT}")


if __name__ == "__main__":
    main()
