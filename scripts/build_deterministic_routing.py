"""Klasifikacija determinističkih porodica → produkcijska ruta.

PRAVILO (dokazano A/B mjerenjem, ne pretpostavkom):

  Porodica ide na Lunu kad je MJERENO slaba (`build_deterministic_quality.py`)
  i kad njena slabost NIJE posljedica prikaza koji model ne može vjerno dati.

  „Slaba“ je definisano u `build_deterministic_quality.classify()` i koristi
  VIŠE signala nad ŠABLONOM (tekst s maskiranim brojevima), ne jedan prag:
    • manje od 3 različite rečenice po lekciji, ILI
    • ista rečenica na svim dostignutim nivoima, ILI
    • nivo koji zna tačno jednu rečenicu uz manje od 5 ukupno.

  A/B nad 12 reprezentativnih porodica (60 determinističkih + 60 Luna turnova)
  pokazao je: medijan različitih rečenica po lekciji 1 → 4, objava 0.80 → 0.98,
  a nula pogrešno označenih odgovora, nula zanosa i nula trećih poziva na obje
  strane. Dakle pravilo pouzdano razdvaja dobre od loših porodica.

KLASE:
  MIGRATE_TO_LUNA                        — mjereno slaba, prikaz nije prepreka
  KEEP_DETERMINISTIC                     — mjereno dobra: 0 poziva, 0 s, dokazana matematika
  KEEP_DETERMINISTIC_FOR_REPRESENTATION  — slaba, ali koncept traži prikaz koji
                                           tekstualni MCQ od modela ne bi vjerno dao
  NEEDS_MORE_EVIDENCE                    — signal na granici; ostaje deterministička

Rezultat je PODATAK (`data/deterministic_routing.json`) koji čita birač
strategije. Popravak generatora mijenja mjerenje, ne Python.

    python scripts/build_deterministic_routing.py [--write]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

QUALITY = ROOT / "data" / "deterministic_quality.json"
OUTPUT = ROOT / "data" / "deterministic_routing.json"

# Porodice čiji koncept ŽIVI od prikaza (brojevna prava, dijagram, figura):
# tamo ponavljanje rečenice nije dovoljan razlog za selidbu, jer bi model morao
# opisati ono što se u ovom UI-ju ne može nacrtati. Popis je KONCEPTUALAN i
# odnosi se na porodice (generatore), ne na pojedine lekcije.
REPRESENTATION_FAMILIES = frozenset({
    "coordinate_line_direct",      # tačka na brojevnoj pravoj / koordinatnoj osi
    "finite_set_direct",           # Venn/dijagramski prikaz skupa
})

# Porodice koje je A/B mjerenje izričito pokrilo (vidi scratchpad/det_quality).
AB_PROVEN = frozenset({
    "ratio_proportion_direct", "geometry_formula_2d", "pythagoras_direct",
    "solid_geometry_direct", "similarity_direct", "number_set_membership",
    "percent_basic", "power_arithmetic_direct", "square_root_direct",
    "simple_quadratic_equation", "rational_expression_direct",
    "fraction_decimal_conversion",
})

# Prag „ista rečenica, drugi brojevi“: porodica čiji je medijan različitih
# rečenica po lekciji <= 2 ponavlja se očito. Porodice s medijanom 3 selimo
# samo kad ih je A/B izričito izmjerio — inače ostaju uz oznaku granice.
CLEAR_REPETITION_MEDIAN = 2


def classify(name, row):
    if not row["weak"]:
        return "KEEP_DETERMINISTIC", "mjereno dobra raznolikost"
    if name in REPRESENTATION_FAMILIES:
        return ("KEEP_DETERMINISTIC_FOR_REPRESENTATION",
                "koncept traži prikaz koji tekstualni MCQ ne bi vjerno dao")
    median = row["median_distinct_templates"]
    if name in AB_PROVEN:
        return "MIGRATE_TO_LUNA", f"A/B dokaz; medijan {median} rečenica po lekciji"
    if median <= CLEAR_REPETITION_MEDIAN:
        return ("MIGRATE_TO_LUNA",
                f"isti obrazac kao dokazane porodice; medijan {median} rečenica")
    return ("NEEDS_MORE_EVIDENCE",
            f"medijan {median} rečenica — na granici, ostaje deterministička")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    quality = json.loads(QUALITY.read_text(encoding="utf-8"))
    families = quality["families"]
    decisions = {}
    for name, row in sorted(families.items()):
        verdict, reason = classify(name, row)
        decisions[name] = {
            "route": verdict,
            "reason": reason,
            "lesson_count": row["lesson_count"],
            "weak": row["weak"],
            "median_distinct_templates": row["median_distinct_templates"],
            "lessons_with_same_sentence_on_every_level":
                row["lessons_with_same_sentence_on_every_level"],
        }

    migrate = sorted(n for n, d in decisions.items() if d["route"] == "MIGRATE_TO_LUNA")

    # PRECIZNOST PO LEKCIJI (nalaz: odluka po porodici je bila pregruba).
    # Lekcija „Brojevni izrazi i tekstualni zadaci s decimalnim brojevima“ ima
    # 12 različitih rečenica i uredan raspored po nivoima, a otišla je na model
    # samo zato što joj je PORODICA mjerena kao slaba. Generator je zajednički,
    # ali njegova pokrivenost po lekciji nije — pa se mjerenje po lekciji
    # poštuje: dokazano jaka lekcija ostaje na nula poziva i unutar slabe
    # porodice. Spisak je IZVEDEN iz mjerenja, nikad ručno pisan.
    lessons = quality.get("lessons") or {}
    # ARHETIP ODLUČUJE O IZUZETKU (nalaz iz ručnog QA). Lekcija se vraća u
    # determinističku rutu SAMO ako je i po šablonu dobra I nudi više od jednog
    # OBLIKA vježbe — ili joj je opseg dokazano uzak, pa jedan oblik nije mana.
    # Lekcija s 11 rečenica o kupovini i kusuru ima jedan oblik i 8 mogućih:
    # takva se ne vraća, jer je učenik doživljava kao istu vježbu.
    def keeps_deterministic(row):
        if row.get("weak"):
            return False
        if row.get("narrow_scope"):
            return True                     # uzak opseg: jedan oblik JE opseg
        if len(row.get("supported_archetypes") or ()) < 3:
            return True
        return row.get("distinct_archetypes", 0) >= 2

    strong_in_weak, weak_in_weak = [], []
    for lesson_id, row in lessons.items():
        if row.get("family") not in set(migrate):
            continue
        (strong_in_weak if keeps_deterministic(row) else weak_in_weak).append(lesson_id)
    strong_in_weak.sort()
    weak_in_weak.sort()
    archetype_demoted = sorted(
        lesson_id for lesson_id, row in lessons.items()
        if row.get("family") in set(migrate) and not row.get("weak")
        and not keeps_deterministic(row))
    payload = {
        "_readme": [
            "Klasifikacija determinističkih PORODICA u produkcijsku rutu.",
            "Odluka je po porodici (generatoru), nikad po lekciji — vidi",
            "scripts/build_deterministic_routing.py za pravilo i dokaz.",
            "`migrate_to_luna_families` je jedini spisak koji birač strategije čita.",
        ],
        "schema_version": 1,
        "rule": {
            "weakness": "build_deterministic_quality.classify (šablon, više signala)",
            "clear_repetition_median": CLEAR_REPETITION_MEDIAN,
            "ab_proven_families": sorted(AB_PROVEN),
            "representation_families": sorted(REPRESENTATION_FAMILIES),
        },
        "families": decisions,
        "migrate_to_luna_families": migrate,
        # Lekcije iz migriranih porodica koje su POJEDINAČNO mjerene kao dobre —
        # ostaju determinističke uprkos porodici.
        "deterministic_lesson_exceptions": strong_in_weak,
        "migrated_lessons": weak_in_weak,
        # Lekcije koje su po ŠABLONU dobre, ali nude samo jedan OBLIK vježbe.
        "archetype_demoted_lessons": archetype_demoted,
    }
    counts = {}
    for decision in decisions.values():
        counts[decision["route"]] = counts.get(decision["route"], 0) + 1
    print("KLASIFIKACIJA PORODICA:", counts)
    print(f"lekcija u migriranim porodicama: "
          f"{sum(d['lesson_count'] for d in decisions.values() if d['route'] == 'MIGRATE_TO_LUNA')}")
    print(f"  pojedinacno slabe -> Luna      : {len(weak_in_weak)}")
    print(f"  pojedinacno jake  -> ostaju 0-call: {len(strong_in_weak)}")
    print(f"  sablonski dobre, ALI jedan arhetip -> Luna: {len(archetype_demoted)}")
    print(f"\n{'porodica':<34}{'lekc':>5}  ruta / razlog")
    for name, decision in sorted(decisions.items(), key=lambda kv: (kv[1]["route"], kv[0])):
        if decision["route"] == "KEEP_DETERMINISTIC":
            continue
        print(f"  {name:<32}{decision['lesson_count']:>5}  {decision['route']}"
              f" — {decision['reason']}")
    if args.write:
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nzapisano {OUTPUT}")


if __name__ == "__main__":
    main()
