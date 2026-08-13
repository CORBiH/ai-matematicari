"""Kompajlira PRIMARNU VJEŠTINU lekcije iz kanonske Faze-2 mapiranja.

    python scripts/build_lesson_objectives.py            # provjeri (ne piše)
    python scripts/build_lesson_objectives.py --write    # upiši artefakt

ZAŠTO POSTOJI (živi nalaz: semantički zanos susjedne vještine):
Lekcija „Skup racionalnih brojeva Q“ dobijala je zadatke čija je STVARNA
vještina sabiranje razlomaka. Zadatak jeste sadržavao racionalne brojeve, ali
nije ispitivao ono što lekcija predaje. Mjerena je bila samo pripadnost sesije
lekciji, a to nije isto što i semantička vjernost.

Uzrok NIJE bio nedostatak podataka. Kanonsko mapiranje NPP stavki na lekcije
(reference/curriculum/semantics/MATBOT_Faza2_Mapiranje.xlsx) već nosi, po
lekciji, ishode učenja i njihovu RELACIJU prema lekciji:

    exact        — ishod TE lekcije (primarna vještina)
    supporting   — pomoćni/preduslovni pojam (smije se pojaviti, ne smije biti cilj)
    prerequisite — isto, preduslov
    neighbour    — ishod SUSJEDNE lekcije (upravo ono u šta zadatak zanosi)

Mjereno nad kurikulumom: 152 od 184 model-podržanih lekcija dobijalo je u
promptu SAMO naslov. Za 89 njih ova evidencija postoji i nikad nije bila
korištena. Ovaj skript je izlaže — nijedan podatak se ne izmišlja.

FILTER PROTIV POGREŠNOG MAPIRANJA: sirova evidencija nije savršena (ima
`exact` redova očito prenesenih s druge lekcije). Zato `exact` ishod ulazi u
primarnu vještinu SAMO ako dijeli sadržajnu riječ s naslovom ili oblašću
lekcije. Filter je opšti i deterministički — nema izuzetka po lekciji.
"""
import argparse
import io
import json
import re
import sys
import unicodedata
from collections import OrderedDict, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
XLSX = ROOT / "reference" / "curriculum" / "semantics" / "MATBOT_Faza2_Mapiranje.xlsx"
TOPICS = ROOT / "data" / "topics.json"
OUTPUT = ROOT / "data" / "lesson_objectives.compiled.json"

MAX_PRIMARY = 4
MAX_SUPPORTING = 3
MAX_EXCLUSIONS = 4
MAX_CHARS = 190

PRIMARY_RELATIONS = {"exact"}
SUPPORTING_RELATIONS = {"supporting", "prerequisite"}
NEIGHBOUR_RELATIONS = {"neighbour"}

# Riječi bez sadržaja — ne dokazuju vezu ishoda s lekcijom.
_STOPWORDS = frozenset("""
i ili te a u na za od do sa se je su bez kao što sto koji koja koje kojih
znati razumjeti moći umjeti uspješno pravilno datih date dati dato naći
primjenom pomoću prema pri kroz iz o s
""".split())
_WORD_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)
# Rečenice u izvoru ponekad nose zalijepljen pojmovnik iza tačke:
# „… u skupu racionalnim postupkom. Pozitivni racionalni brojevi Negativni …“
_GLOSSARY_TAIL_RE = re.compile(r"\.\s+(?=[A-ZČĆŽŠĐ])")


def _fold(word):
    return "".join(
        c for c in unicodedata.normalize("NFKD", word.lower())
        if not unicodedata.combining(c))


def _content_words(text):
    return {_fold(w) for w in _WORD_RE.findall(text or "")
            if _fold(w) not in {_fold(s) for s in _STOPWORDS}}


def _clean(text):
    """Prva rečenica bez zalijepljenog pojmovnika, dužinski ograničena.

    Tekst ide U PROMPT, dakle u projektnu prozu — zato prolazi kroz istu
    determinističku normalizaciju terminologije kao svaki drugi vidljivi tekst
    (`matbot/terminology.py`). Kurikularni izvor ostaje netaknut."""
    from matbot.terminology import normalize_terminology

    body = " ".join(normalize_terminology(text or "").split())
    if not body:
        return ""
    head = _GLOSSARY_TAIL_RE.split(body)[0].strip(" ,;:.")
    if len(head) > MAX_CHARS:
        head = head[:MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return head


def _shares_content(statement, lesson_words):
    """Deterministički filter pogrešnog mapiranja: bar jedna sadržajna riječ."""
    return bool(_content_words(statement) & lesson_words)


def load_mapping():
    import openpyxl

    workbook = openpyxl.load_workbook(XLSX, read_only=True)
    sheet = workbook["Mapiranje"]
    header = [c for c in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))]
    index = {name: position for position, name in enumerate(header)}
    grouped = defaultdict(list)
    for row in sheet.iter_rows(min_row=2, values_only=True):
        lesson_id = row[index["target_lesson_id"]]
        if not lesson_id:
            continue
        grouped[str(lesson_id).strip()].append({
            "text": _clean(row[index["source_text"]]),
            "relation": (row[index["relation"]] or "").strip().lower(),
            "confidence": (row[index["confidence"]] or "").strip().lower(),
            "item_id": row[index["item_id"]],
        })
    return grouped


def build():
    topics = json.loads(TOPICS.read_text(encoding="utf-8"))
    mapping = load_mapping()
    compiled = OrderedDict()
    stats = defaultdict(int)
    for grade, payload in sorted(topics["grades"].items()):
        for lesson in payload["lessons"]:
            lesson_id = lesson["id"]
            rows = mapping.get(lesson_id, [])
            if not rows:
                stats["no_evidence"] += 1
                continue
            lesson_words = _content_words(lesson["title"]) | _content_words(lesson["oblast"])
            primary, supporting, exclusions, evidence = [], [], [], []
            for row in rows:
                text = row["text"]
                if not text:
                    continue
                if row["relation"] in PRIMARY_RELATIONS:
                    if not _shares_content(text, lesson_words):
                        stats["dropped_unrelated_exact"] += 1
                        continue
                    if text not in primary:
                        primary.append(text)
                        evidence.append(row["item_id"])
                elif row["relation"] in SUPPORTING_RELATIONS:
                    if text not in supporting:
                        supporting.append(text)
                elif row["relation"] in NEIGHBOUR_RELATIONS:
                    if text not in exclusions:
                        exclusions.append(text)
            if not primary and not exclusions:
                stats["no_usable_signal"] += 1
                continue
            compiled[lesson_id] = OrderedDict([
                ("grade", int(grade)),
                ("primary_skills", primary[:MAX_PRIMARY]),
                ("supporting_concepts", supporting[:MAX_SUPPORTING]),
                ("neighbour_exclusions", exclusions[:MAX_EXCLUSIONS]),
                ("evidence_ids", [e for e in evidence[:MAX_PRIMARY] if e]),
            ])
            stats["compiled"] += 1
            if primary:
                stats["with_primary"] += 1
            if exclusions:
                stats["with_exclusions"] += 1
    return compiled, stats


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    compiled, stats = build()
    print(f"lekcija s kompajliranim ishodima: {stats['compiled']}")
    print(f"  s primarnom vještinom : {stats['with_primary']}")
    print(f"  sa susjednim zabranama: {stats['with_exclusions']}")
    print(f"  odbačeni nepovezani `exact` redovi: {stats['dropped_unrelated_exact']}")
    print(f"  bez ijedne evidencije : {stats['no_evidence']}")
    if args.write:
        document = OrderedDict([
            ("_readme", [
                "GENERISANO — ne uređivati ručno.",
                "Izvor: reference/curriculum/semantics/MATBOT_Faza2_Mapiranje.xlsx",
                "Generator: scripts/build_lesson_objectives.py",
                "primary_skills = ishodi TE lekcije (relation=exact, filtrirano)",
                "supporting_concepts = smiju se pojaviti, ne smiju biti CILJ",
                "neighbour_exclusions = ishodi SUSJEDNIH lekcija; nikad cilj zadatka",
            ]),
            ("schema_version", 1),
            ("lessons", compiled),
        ])
        OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        print(f"OK: {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
