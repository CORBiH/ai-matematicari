"""Izvedi mapu VRSTA ZADATKA → DIMENZIJA TRAŽENE VELIČINE iz dokaza, ne iz glave.

ZAŠTO POSTOJI: semantički ugovor lekcije deklariše `kinds` (npr. `prism4_surface`,
`pyramid4_volume`, `rectangle_perimeter`). Koliko je dimenzionalna tražena
veličina — dužina (1), površina (2) ili zapremina (3) — nije stvar mišljenja
nego MJERLJIVA činjenica: vidi se po eksponentu mjerne jedinice u objavljenim
opcijama ($12$ cm, $12$ cm$^2$, $12$ cm$^3$).

Mapa se zato ne kuca ručno. Izvodi se iz DETERMINISTIČKOG generatora tih istih
porodica, koji je za svaku vrstu već proizveo hiljade paketa kroz sve
validatore. Vrsta kod koje se eksponent NE slaže kroz cijeli uzorak se NE
upisuje — takva se ne smije blokirati.

Pokretanje:
    python scripts/build_measure_dimensions.py
Rezultat:
    data/semantic_measure_dimensions.json
"""
import collections
import json
import os
import random

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")

from matbot import deterministic as registry                         # noqa: E402
from matbot.deterministic.core import DeterministicGenerationError    # noqa: E402
from matbot.semantics import contracts as semantic_contracts          # noqa: E402
# JEDNO ČITANJE JEDINICE ZA SVE: mapa se izvodi ISTIM čitačem koji je poslije
# provjerava u produkciji. Vlastita kopija regexa ovdje bi značila da se mapa
# gradi po jednom pravilu, a primjenjuje po drugom.
from matbot.semantics.detectors import unit_exponents                 # noqa: E402

COMPILED = ROOT / "data" / "lesson_semantics.compiled.json"
OUTPUT = ROOT / "data" / "semantic_measure_dimensions.json"

# Porodice čija je tražena veličina mjerna (a ne broj, ugao ili odnos).
FAMILIES = ("geometry_formula_2d", "solid_geometry_direct")
SAMPLES_PER_LEVEL = int(os.environ.get("DIM_SAMPLES", "14"))



def main():
    lessons = json.loads(COMPILED.read_text(encoding="utf-8"))["lessons"]
    observed = collections.defaultdict(collections.Counter)
    for family in FAMILIES:
        module = registry.GENERATORS.get(family)
        if module is None:
            continue
        for lesson_id, entry in lessons.items():
            if entry["detector"] != family:
                continue
            contract = semantic_contracts.contract_for(lesson_id)
            if contract is None or not module.supports(dict(contract.parameters)):
                continue
            for level in (1, 2, 3):
                for seed in range(SAMPLES_PER_LEVEL):
                    try:
                        package = module.generate_package(
                            lesson_id, "", dict(contract.parameters), level,
                            rng=random.Random(f"{lesson_id}|{level}|{seed}"))
                    except DeterministicGenerationError:
                        continue
                    exponents = set()
                    for option in package.option_texts:
                        exponents |= unit_exponents(option)
                    key = tuple(sorted(exponents)) if exponents else ()
                    observed[package.operation][key] += 1

    dimensions, unitless, rejected = {}, [], {}
    for kind, counter in sorted(observed.items()):
        keys = set(counter)
        if keys == {()}:
            unitless.append(kind)                 # broj/ugao — nema dimenzije
        elif len(keys) == 1 and len(next(iter(keys))) == 1:
            dimensions[kind] = next(iter(keys))[0]
        else:
            # Nedosljedan uzorak → vrsta se NE smije blokirati.
            rejected[kind] = {str(k): n for k, n in counter.items()}

    payload = {
        "_readme": [
            "VRSTA ZADATKA -> DIMENZIJA TRAZENE VELICINE (1=duzina, 2=povrsina,",
            "3=zapremina). IZVEDENO MJERENJEM, ne rucno: vidi",
            "scripts/build_measure_dimensions.py. `unitless_kinds` su vrste bez",
            "mjerne jedinice (broj ivica, zbir uglova) — one se NIKAD ne blokiraju.",
            "`rejected_kinds` su vrste kod kojih uzorak nije dao jedinstven",
            "eksponent; i one ostaju nedokazive.",
        ],
        "schema_version": "1",
        "samples_per_level": SAMPLES_PER_LEVEL,
        "families": list(FAMILIES),
        "dimension_by_kind": dimensions,
        "unitless_kinds": sorted(unitless),
        "rejected_kinds": rejected,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    print(f"kinds with a proven dimension : {len(dimensions)}")
    print(f"unitless kinds (never blocked): {len(unitless)}")
    print(f"rejected (inconsistent)       : {len(rejected)}")
    print(f"written: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
