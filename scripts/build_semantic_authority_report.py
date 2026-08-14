"""Iskrena inventura SEMANTIČKOG AUTORITETA: šta je stvarno blokirajuće.

ZAŠTO POSTOJI: revizija ovlašćenja pravila je prijavila da je „43/44 semantička
detektora neimplementirano“, dok su svi ugovori u podacima označeni kao
`blocking`. To je problem ISTINITOSTI: konfiguracija tvrdi da server pravilo
provodi, a detektora nema — pa generisanje smije prekršiti pravilo, a objavu
niko ne zaustavlja.

Ovaj izvještaj mjeri stvarno stanje na TRENUTNOM HEAD-u i razvrstava svako
pravilo po DOKAZIVOSTI, ne po želji. Dokazivo blokirajuće je samo ono što
server može sam dokazati nad objavljenim paketom.

Pokretanje:
    python scripts/build_semantic_authority_report.py
Rezultat:
    scratchpad/semantic_authority/detector_matrix.json
    scratchpad/semantic_authority/lesson_coverage.json
    scratchpad/semantic_authority/audit.md
"""
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")

from matbot import deterministic as registry                          # noqa: E402
from matbot import deterministic_variety                              # noqa: E402
from matbot.semantics import contracts as semantic_contracts          # noqa: E402
from matbot.semantics import detectors                                # noqa: E402

OUT = ROOT / "scratchpad" / "semantic_authority"

# RAZVRSTAVANJE PO DOKAZIVOSTI (Faza A/E). Vrijednosti su odluka ove faze i
# zapisane su ovdje, a ne u runtime kodu — detektor se bira po imenu porodice.
#   EXACT_WITH_METADATA          dokazivo iz strukture + kanonskog parametra
#   REDUNDANT                    već u potpunosti čuva drugi validator
#   NOT_RELIABLY_MACHINE_CHECKABLE  ne može se sigurno tvrdo blokirati
CLASSIFICATION = {
    "geometry_formula_2d": "EXACT_WITH_METADATA",
    "solid_geometry_direct": "EXACT_WITH_METADATA",
    "fraction_arithmetic": "EXACT_WITH_METADATA",
    "polynomial_basic": "EXACT_WITH_METADATA",
}
# Porodice koje NE mogu drifovati jer paket gradi server (deterministička ruta)
# su REDUNDANT po ruti, ne po detektoru — računa se dinamički niže.


def _discriminating(contract):
    """Da li detektor za OVU lekciju uopšte može išta odbiti.

    Pita se sam detektor (preko njegove izvedene mape), pa se odgovor ne može
    raziću s onim što produkcija stvarno radi."""
    if contract is None:
        return False
    by_kind, unitless = detectors._measure_dimensions()
    kinds = tuple(contract.parameters.get("kinds") or ())
    if not kinds or not by_kind:
        return False
    if any(kind in unitless for kind in kinds):
        return False
    allowed = {by_kind.get(kind) for kind in kinds}
    return None not in allowed and allowed != {1, 2, 3}


def _route(lesson_id, contract):
    """Da li produkcija ovu lekciju servira determinističkim generatorom."""
    if contract is None or not contract.blocking:
        return "model"
    if deterministic_variety.family_routes_to_model(contract.family_id, lesson_id):
        return "model"
    module = registry.GENERATORS.get(contract.family_id)
    if module is not None and module.supports(dict(contract.parameters)):
        return "deterministic"
    return "model"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(encoding="utf-8"))
    lessons = compiled["lessons"]

    rows = {}
    coverage = {}
    for lesson_id, entry in sorted(lessons.items()):
        contract = semantic_contracts.contract_for(lesson_id)
        route = _route(lesson_id, contract)
        detector = entry["detector"]
        implemented = detector in detectors.DETECTORS
        rows.setdefault(detector, {
            "detector": detector,
            "family_ids": set(),
            "lessons": [],
            "model_route_lessons": [],
            "deterministic_lessons": [],
            "declared_enforcement": set(),
            "implemented": implemented,
            "implementation": (detectors.DETECTORS[detector].__name__
                               if implemented else ""),
            "enforced_parameters": collections.Counter(),
        })
        row = rows[detector]
        row["family_ids"].add(entry["family_id"])
        row["lessons"].append(lesson_id)
        row["declared_enforcement"].add(entry["enforcement_mode"])
        (row["model_route_lessons"] if route == "model"
         else row["deterministic_lessons"]).append(lesson_id)
        for parameter in entry.get("enforced_parameters") or ():
            row["enforced_parameters"][parameter] += 1
        coverage[lesson_id] = {
            "detector": detector,
            "family_id": entry["family_id"],
            "declared_enforcement": entry["enforcement_mode"],
            "production_route": route,
            "detector_implemented": implemented,
            # STVARNI autoritet: pravilo može spriječiti objavu SAMO ako je
            # detektor implementiran I lekcija ide model-rutom (na
            # determinističkoj ruti paket gradi server, pa drifta nema).
            "effective_blocking": bool(implemented and route == "model"),
            # JOŠ UŽE I JOŠ ISKRENIJE: detektor koji se pokreće ne mora i moći
            # nešto odbiti. Lekcija čije vrste pokrivaju sve dimenzije, ili
            # koja smije tražiti veličinu bez mjerne jedinice, prolazi kroz
            # detektor bez ijednog dokazivog ograničenja.
            "discriminating": bool(implemented and route == "model"
                                   and _discriminating(contract)),
        }

    matrix = []
    for detector, row in sorted(rows.items()):
        model_lessons = row["model_route_lessons"]
        implemented = row["implemented"]
        if implemented and model_lessons:
            status = "BLOCKING_IMPLEMENTED"
        elif implemented and not model_lessons:
            status = "REDUNDANT"          # implementiran, ali samo 0-pozivne lekcije
        elif not model_lessons:
            status = "REDUNDANT"          # server sam gradi paket — drift nemoguć
        else:
            status = "UNKNOWN"            # deklarisan blocking, dokaza nema
        matrix.append({
            "detector": detector,
            "family_ids": sorted(row["family_ids"]),
            "lessons_total": len(row["lessons"]),
            "lessons_model_route": len(model_lessons),
            "lessons_deterministic": len(row["deterministic_lessons"]),
            "declared_enforcement": sorted(row["declared_enforcement"]),
            "implemented": implemented,
            "implementation": row["implementation"],
            "classification": CLASSIFICATION.get(
                detector,
                "REDUNDANT" if not model_lessons
                else "NOT_RELIABLY_MACHINE_CHECKABLE"),
            "authority_status": status,
            "can_prevent_publication": bool(implemented and model_lessons),
            "reviewer_repair_recipe": bool(implemented),
            "enforced_parameters": dict(row["enforced_parameters"]),
        })

    (OUT / "detector_matrix.json").write_text(
        json.dumps({"contract_version": compiled.get("contract_version"),
                    "detectors": matrix}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (OUT / "lesson_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=1), encoding="utf-8")

    status_counts = collections.Counter(item["authority_status"] for item in matrix)
    class_counts = collections.Counter(item["classification"] for item in matrix)
    effective = [lid for lid, item in coverage.items() if item["effective_blocking"]]
    discriminating = [lid for lid, item in coverage.items() if item["discriminating"]]
    model_total = [lid for lid, item in coverage.items()
                   if item["production_route"] == "model"]

    lines = [
        "# Semantic authority — what is actually enforced",
        "",
        f"Contract version: `{compiled.get('contract_version')}`.",
        "",
        "## The honesty problem",
        "",
        f"- lessons carrying a semantic contract: **{len(coverage)}**",
        f"- contracts labelled `blocking` in data: **{len(coverage)}** (all of them)",
        f"- distinct detector names: **{len(matrix)}**",
        f"- detector names with an implementation: "
        f"**{sum(1 for item in matrix if item['implemented'])}**",
        "",
        "A contract labelled `blocking` whose detector is missing returns "
        "`UNSUPPORTED`, which never rejects. That is not a failure of the "
        "engine — the three-state contract is deliberate — but it does mean "
        "the label promised more than the server could prove.",
        "",
        "## Route matters more than the label",
        "",
        f"- contract lessons served by a deterministic generator (0 calls): "
        f"**{len(coverage) - len(model_total)}**",
        f"- contract lessons served by the model route: **{len(model_total)}**",
        "",
        "On the deterministic route the server builds the package itself, so "
        "lesson drift is structurally impossible and a detector adds nothing. "
        "Only the model route can drift, so effective coverage is measured "
        "there.",
        "",
        f"- lessons whose semantic detector actually runs on the model route: "
        f"**{len(effective)}**",
        f"- of those, lessons where the contract genuinely constrains the "
        f"answer (the detector can reject something): **{len(discriminating)}**",
        "",
        "The remainder run the detector but declare a scope wide enough that "
        "nothing is provable — they return `UNSUPPORTED`, and the report says "
        "so rather than counting them as protection.",
        "",
        "## Detector status",
        "",
        "| status | detectors |",
        "|---|---|",
    ]
    for status, count in status_counts.most_common():
        lines.append(f"| {status} | {count} |")
    lines += ["", "## Provability classification", "",
              "| class | detectors |", "|---|---|"]
    for name, count in class_counts.most_common():
        lines.append(f"| {name} | {count} |")
    lines += ["", "## Detectors that can actually prevent publication", "",
              "| detector | model-route lessons | implementation |", "|---|---|---|"]
    for item in matrix:
        if item["can_prevent_publication"]:
            lines.append(f"| `{item['detector']}` | {item['lessons_model_route']} "
                         f"| `{item['implementation']}` |")
    lines += ["", "## Declared blocking with no proof available (UNKNOWN)", "",
              "These keep their prompt guidance and every generic validator "
              "(mathsafe, mathcheck, geometry notation, option uniqueness, "
              "solution/answer consistency), but the server cannot prove a "
              "lesson-semantic violation, so it does not pretend to.", "",
              "| detector | model-route lessons | enforced parameters |",
              "|---|---|---|"]
    for item in matrix:
        if item["authority_status"] == "UNKNOWN":
            lines.append(f"| `{item['detector']}` | {item['lessons_model_route']} "
                         f"| {', '.join(sorted(item['enforced_parameters'])) or '—'} |")
    (OUT / "audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"detectors                : {len(matrix)}")
    print(f"status                   : {dict(status_counts)}")
    print(f"classification           : {dict(class_counts)}")
    print(f"contract lessons         : {len(coverage)}")
    print(f"  model route            : {len(model_total)}")
    print(f"  deterministic route    : {len(coverage) - len(model_total)}")
    print(f"EFFECTIVE blocking lessons: {len(effective)}")
    print(f"  of which discriminating : {len(discriminating)}")
    print(f"written: {OUT.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
