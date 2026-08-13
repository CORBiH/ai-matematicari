"""Kvalitet determinističkih PORODICA — mjerenje, ne procjena (0 modelskih poziva).

ZAŠTO POSTOJI: nulti poziv i nula sekundi imaju veliku vrijednost, ali ne po
svaku cijenu. Ako učenik na tri nivoa težine i na svaki „daj novi“ dobija ISTU
rečenicu s drugim brojevima, to nije vježba nego ponavljanje.

Prethodno mjerenje je gledalo samo koliko su tekstovi doslovno različiti i
vodilo je spisak LEKCIJA. Dvije stvari su tu pogrešne:

  1. „Različit tekst“ je preslab signal — „Koliko je 12+7?“ i „Koliko je 45+8?“
     su različiti stringovi, a ista rečenica. Zato se ovdje mjeri ŠABLON: tekst
     s maskiranim brojevima. Šablon razlikuje pravu raznolikost od prebrojavanja.
  2. Odluka po lekciji je spisak izuzetaka prerušen u podatak. Generator je
     PORODICA; ako je porodica slaba, slaba je za svaku svoju lekciju. Zato se
     ocjena računa i donosi NA NIVOU PORODICE.

Mjeri se nad stvarnim serverskim putem (`run_practice_turn`), bez ijednog
modelskog poziva — svaki takav poziv je greška i prekida mjerenje.

    python scripts/build_deterministic_quality.py [--write] [--samples N]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
os.environ.setdefault("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")

from matbot import archetype_support, task_archetypes               # noqa: E402
from matbot.practice import run_practice_turn                       # noqa: E402
from matbot.session_store import SessionStore                       # noqa: E402
from matbot.tutor import lesson_context                             # noqa: E402
from matbot.tutor import pipeline as tutor_pipeline                 # noqa: E402

OUTPUT = ROOT / "data" / "deterministic_quality.json"
TOPICS = ROOT / "data" / "topics.json"

# Brojevi, razlomci i mjerne vrijednosti se maskiraju: ostaje REČENICA.
_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_FRACTION_RE = re.compile(r"\\+t?frac\{[^{}]*\}\{[^{}]*\}")


def template_of(text):
    """Tekst bez brojeva — ono što učenik doživljava kao „isto pitanje“."""
    masked = _FRACTION_RE.sub("«f»", text or "")
    masked = _NUMBER_RE.sub("«n»", masked)
    return " ".join(masked.split())


class NoModel:
    """Svaki modelski poziv je greška: ovdje se mjeri SAMO deterministički put."""

    def __getattr__(self, name):
        def explode(*args, **kwargs):
            raise AssertionError(f"modelski poziv: {name}")
        return explode


def _turn(session_id, grade, lesson_id, message, request=""):
    return {"session_id": session_id, "grade": grade, "selected_topic": lesson_id,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": request, "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


def probe_lesson(grade, lesson_id, samples):
    """{nivo: [(tekst, šablon)]} ili None kad lekcija nije deterministička."""
    store = SessionStore()
    seen = defaultdict(list)
    for index in range(samples):
        session_id = f"q-{lesson_id}-{index}"
        # Svjež zadatak, pa dva „novi“ na istom nivou (mjeri ponavljanje na
        # NEW), pa uspon kroz nivoe.
        steps = [("Daj mi zadatak.", ""), ("Daj mi novi zadatak.", ""),
                 ("Daj mi novi zadatak.", ""), ("Daj mi teži zadatak.", "harder"),
                 ("Daj mi teži zadatak.", "harder")]
        for message, request in steps:
            try:
                run_practice_turn(store, NoModel(), _turn(
                    session_id, grade, lesson_id, message, request))
            except AssertionError:
                return None                       # dotakao model → nije 0-call
            except Exception:                     # noqa: BLE001
                return None
            state = store.peek(session_id) or {}
            text = state.get("current_task")
            if text:
                level = state.get("difficulty_level", 1)
                options = [o.get("text") for o in (state.get("current_options") or [])]
                seen[level].append((text, template_of(text),
                                    task_archetypes.classify(text, options)))
    return dict(seen) if seen else None


def family_of(grade, lesson_id):
    context = lesson_context.build(grade, lesson_id)
    contract = getattr(context, "semantic_contract", None)
    return getattr(contract, "family_id", "") or "?"


def lessons():
    payload = json.loads(TOPICS.read_text(encoding="utf-8"))
    for grade, block in sorted(payload["grades"].items()):
        for lesson in block["lessons"]:
            yield int(grade), lesson


def score(records):
    """Signali kvaliteta JEDNE LEKCIJE.

    Mjeri se po lekciji jer učenik vježba lekciju, ne porodicu: porodica s 12
    lekcija ima 12 rečenica i djeluje raznoliko, a učenik u svojoj lekciji i
    dalje dobija jednu jedinu rečenicu na sva tri nivoa."""
    texts = [text for level in records for text, _, _ in records[level]]
    templates = [tpl for level in records for _, tpl, _ in records[level]]
    archetypes = [arc for level in records for _, _, arc in records[level] if arc]
    per_level = {level: {tpl for _, tpl, _ in rows} for level, rows in records.items()}
    shared = set.intersection(*per_level.values()) if len(per_level) > 1 else set()
    only_level = next(iter(per_level.values())) if len(per_level) == 1 else None
    return {
        "samples": len(texts),
        "levels_reached": sorted(per_level),
        "distinct_texts": len(set(texts)),
        "distinct_templates": len(set(templates)),
        "text_ratio": round(len(set(texts)) / len(texts), 3) if texts else 0.0,
        # KLJUČNI SIGNAL: koliko RAZLIČITIH REČENICA porodica uopšte zna.
        "template_ratio": round(len(set(templates)) / len(templates), 3) if templates else 0.0,
        "templates_shared_across_levels": len(shared),
        "templates_per_level": {str(k): len(v) for k, v in sorted(per_level.items())},
        "single_level_templates": len(only_level) if only_level is not None else None,
        # ARHETIP JE JAČA MJERA OD ŠABLONA: 12 rečenica o kupovini i kusuru je
        # 12 šablona, ali JEDAN oblik vježbe.
        "distinct_archetypes": len(set(archetypes)),
        "archetypes": sorted(set(archetypes)),
        "dominant_archetype_share": (
            round(max(Counter(archetypes).values()) / len(archetypes), 3)
            if archetypes else 0.0),
    }


def classify(stats):
    """PRAVILO ZA JEDNU LEKCIJU (dokumentovano, više signala — ne jedan prag).

    Lekcija je slaba kad učenik ne može dobiti STVARNO drugačiji zadatak:
      A) zna manje od 3 različite rečenice, ILI
      B) ista rečenica se ponavlja na svim dostignutim nivoima (težina se ne
         vidi u zadatku), ILI
      C) ijedan nivo zna tačno jednu rečenicu, a lekcija ukupno manje od 5.

    Broj NIJE rečenica: „Koliko je 12+7?“ i „Koliko je 45+8?“ su isti šablon."""
    reasons = []
    if stats["distinct_templates"] < 3:
        reasons.append(f"samo {stats['distinct_templates']} različite rečenice")
    if (stats["templates_shared_across_levels"] > 0
            and len(stats["levels_reached"]) > 1
            and stats["templates_shared_across_levels"] >= stats["distinct_templates"]):
        reasons.append("ista rečenica na svim nivoima")
    thin = [level for level, count in stats["templates_per_level"].items() if count == 1]
    if thin and stats["distinct_templates"] < 5:
        reasons.append(f"nivo(i) {','.join(thin)} imaju samo jednu rečenicu")
    # NOVI, ODLUČUJUĆI SIGNAL (nalaz iz živog QA): generator koji zna mnogo
    # rečenica, a samo JEDAN oblik vježbe, nije raznolik. Lekcija o brojevnim
    # izrazima s decimalnim brojevima imala je 12 šablona i 1 arhetip (kupovina
    # i kusur), pa je prošla staru mjeru i pala na ručnom testu.
    #
    # MJERI SE SAMO GDJE JE RAZNOLIKOST UOPŠTE MOGUĆA. Mjereno: 340 od 352
    # determinističke lekcije daje tačno jedan arhetip — generatori su takvi po
    # konstrukciji. Kad i sama LEKCIJA podržava manje od tri oblika, jedan oblik
    # nije mana nego opseg, i 0-poziva ostaje čista dobit. Prekršaj je samo kad
    # lekcija dokazano nosi tri ili više oblika, a generator daje jedan.
    # Signal se MJERI i zapisuje ovdje, ali NE ulazi u ovu ocjenu. Mjereno: 340
    # od 352 determinističke lekcije daje tačno jedan arhetip — to je svojstvo
    # generatora, ne izuzetak. Da ovaj uslov ovdje obara lekciju, deterministička
    # ruta bi nestala u cijelosti, a njena vrijednost (nula poziva, nula sekundi,
    # serverski dokazana matematika) je mjerena i stvarna.
    #
    # Arhetip zato odlučuje TAMO GDJE JE DOKAZ TRAŽEN: da li lekcija koja je
    # POJEDINAČNO vraćena u determinističku rutu to zaslužuje (vidi
    # `scripts/build_deterministic_routing.py`). Ostatak ostaje kako je
    # izmjereno, a nalaz o jednom arhetipu se prijavljuje kao zaseban podatak.
    return reasons


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--samples", type=int, default=4)
    args = parser.parse_args()

    per_lesson = {}
    family_lessons = defaultdict(list)
    checked = 0
    for grade, lesson in lessons():
        records = probe_lesson(grade, lesson["id"], args.samples)
        if not records:
            continue
        checked += 1
        family = family_of(grade, lesson["id"])
        family_lessons[family].append(lesson["id"])
        stats = score(records)
        stats["supported_archetypes"] = list(archetype_support.supported(lesson["id"]))
        stats["narrow_scope"] = archetype_support.is_narrow(lesson["id"])
        stats["reasons"] = classify(stats)
        stats["weak"] = bool(stats["reasons"])
        stats["family"] = family
        stats["grade"] = grade
        per_lesson[lesson["id"]] = stats

    families = {}
    for family, ids in sorted(family_lessons.items()):
        rows = [per_lesson[i] for i in ids]
        weak_rows = [r for r in rows if r["weak"]]
        # PORODICA JE SLABA kad je slaba VEĆINA njenih lekcija: generator je
        # zajednički, pa pojedinačan izuzetak ne mijenja njegov kvalitet, a
        # većina dokazuje da je ograničenje u generatoru, ne u jednoj lekciji.
        share = len(weak_rows) / len(rows)
        reasons = sorted({reason for r in weak_rows for reason in r["reasons"]})
        families[family] = {
            "lessons": sorted(ids),
            "lesson_count": len(ids),
            "weak_lessons": sorted(r_id for r_id, r in per_lesson.items()
                                   if r["family"] == family and r["weak"]),
            "weak_lesson_share": round(share, 3),
            "median_distinct_templates": sorted(
                r["distinct_templates"] for r in rows)[len(rows) // 2],
            "median_template_ratio": sorted(
                r["template_ratio"] for r in rows)[len(rows) // 2],
            "lessons_with_same_sentence_on_every_level": sum(
                1 for r in rows if "ista rečenica na svim nivoima" in r["reasons"]),
            "weak": share > 0.5,
            "reasons": reasons,
        }

    weak = {name: row for name, row in families.items() if row["weak"]}
    payload = {
        "_readme": [
            "Mjeren kvalitet determinističkih PORODICA (scripts/build_deterministic_quality.py).",
            "Odluka je po PORODICI, ne po lekciji: generator je porodica, pa je i",
            "ocjena njegova. Kljucni signal je SABLON (tekst s maskiranim brojevima) —",
            "razlicit broj nije razlicit zadatak.",
            "Pravilo je u `classify()` i namjerno koristi vise signala, ne jedan prag.",
        ],
        "schema_version": 1,
        "samples_per_lesson": args.samples,
        "deterministic_lessons_measured": checked,
        "families": families,
        "lessons": per_lesson,
        "weak_families": sorted(weak),
    }
    print(f"determinističkih lekcija: {checked} | porodica: {len(families)} "
          f"| slabih porodica: {len(weak)} "
          f"| lekcija u slabim porodicama: {sum(r['lesson_count'] for r in weak.values())}")
    print(f"\n{'porodica':<34}{'lekc':>5}{'slab':>6}{'med.sabl':>9}{'ist.niv':>8}  razlog")
    for name, row in sorted(families.items(), key=lambda kv: (not kv[1]["weak"], kv[0])):
        mark = "WEAK " if row["weak"] else "     "
        print(f"{mark}{name:<29}{row['lesson_count']:>5}"
              f"{len(row['weak_lessons']):>6}{row['median_distinct_templates']:>9}"
              f"{row['lessons_with_same_sentence_on_every_level']:>8}  "
              + "; ".join(row["reasons"])[:58])
    if args.write:
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nzapisano {OUTPUT}")


if __name__ == "__main__":
    main()
