"""Trijaža preostalih UNKNOWN semantičkih pravila + izvještaj o STVARNOM autoritetu.

Dvije stvari koje moraju stajati odvojeno:

  • ŠTA UGOVOR TRAŽI      — `enforcement_mode` u podacima (uvijek `blocking`)
  • ŠTA SERVER MOŽE       — postoji li detektor i može li ijedan paket odbiti

Prethodna faza je pokazala da prvo bez drugog obmanjuje. Ovaj skript zato
generiše i `authority_status.json`: po lekciji, `requested_enforcement` naspram
`detector_status`. Podaci ugovora se NE migriraju — izvedeni artefakt rješava
isti problem bez rizika po postojeću šemu.

Pokretanje:
    python scripts/build_semantic_triage.py
Rezultat:
    scratchpad/semantic_triage/{unknown_detectors,affected_lessons,ranking}.json
    scratchpad/semantic_triage/audit.md
    data/semantic_authority_status.json
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

OUT = ROOT / "scratchpad" / "semantic_triage"
STATUS_ARTIFACT = ROOT / "data" / "semantic_authority_status.json"

# ROI ocjene (0–5) iz trijaže ove faze. Zapisane kao PODATAK i obrazložene u
# audit.md; runtime ih ne čita — služe odluci šta se sljedeće gradi.
#   drift = šteta ako Luna prekrši; cover = lekcije; prov = dokazivost;
#   evid = postoji li strukturisan serverski dokaz; fp = RIZIK lažne blokade
#   (5 = velik rizik); reuse = koliko porodica dijeli isti primitiv.
RANKING = {
    "angle_relationships_direct": dict(drift=4, cover=5, prov=2, evid=2, fp=4, reuse=4,
        klasa="NOT_RELIABLY_PROVABLE",
        why="Klasa odgovora (naziv vrste vs izračunata mjera) je autorski izbor; "
            "mjereno je da model legitimno bira drugi oblik od generatora."),
    "pythagoras_direct": dict(drift=3, cover=4, prov=2, evid=2, fp=4, reuse=4,
        klasa="NOT_RELIABLY_PROVABLE",
        why="`verify_triple` vs računanje stranice razlikuje se samo klasom "
            "odgovora — ista odbijena metoda. Nema živih uzoraka."),
    "rational_expression": dict(drift=4, cover=4, prov=2, evid=1, fp=3, reuse=2,
        klasa="NOT_RELIABLY_PROVABLE",
        why="`domain_condition`/`reduce`/`expand` traže tumačenje ALGEBARSKE "
            "namjere; server nema strukturisanu činjenicu koja to razlikuje."),
    "ratio_proportion": dict(drift=3, cover=3, prov=1, evid=1, fp=3, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE",
        why="Pojmovi su pedagoške oznake bez strukturnog potpisa u paketu."),
    "structured_word_problem": dict(drift=4, cover=3, prov=1, evid=1, fp=4, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE",
        why="Tip priče se ne da dokazati iz teksta bez tumačenja prirodnog jezika."),
    "power_arithmetic": dict(drift=3, cover=3, prov=3, evid=2, fp=2, reuse=1,
        klasa="EXACT_PARSED_MATH",
        why="`scientific_notation` JESTE parsivo ($1 \\le |a| < 10$, cijeli "
            "eksponent), ali pogađa 1 lekciju i nema nijedan živi uzorak za "
            "dokaz nula lažnih blokada. Najbolji kandidat za sljedeći ciklus."),
    "simple_quadratic": dict(drift=3, cover=2, prov=2, evid=2, fp=3, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE",
        why="Oblik jednačine je u tekstu, ali traženi POSTUPAK nije serverska činjenica."),
    "fraction_decimal": dict(drift=2, cover=2, prov=2, evid=1, fp=3, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE",
        why="Smjer pretvaranja se ne da dokazati bez čitanja namjere zadatka."),
    "number_set_membership": dict(drift=3, cover=2, prov=2, evid=2, fp=4, reuse=2,
        klasa="NOT_RELIABLY_PROVABLE",
        why="Mjereno: 3 lažne blokade na 6 živih uzoraka (simbolička tvrdnja `$A=N$`)."),
    "similarity": dict(drift=3, cover=2, prov=1, evid=1, fp=3, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE",
        why="`similarity_coefficient` je pedagoška oznaka; nema strukturnog potpisa."),
    "square_root": dict(drift=2, cover=2, prov=2, evid=1, fp=3, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE", why="Isti razlog kao ostale pojmovne porodice."),
    "financial_arithmetic": dict(drift=2, cover=2, prov=1, evid=1, fp=3, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE", why="Vrsta finansijskog računa nije serverska činjenica."),
    "common_divisors_multiples": dict(drift=2, cover=1, prov=2, evid=2, fp=2, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE", why="Mali obuhvat; nema živih uzoraka."),
    "prime_structure": dict(drift=2, cover=1, prov=2, evid=2, fp=2, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE", why="Mali obuhvat; nema živih uzoraka."),
    "fraction_concept": dict(drift=2, cover=1, prov=1, evid=1, fp=3, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE", why="Pojmovna porodica bez strukturnog potpisa."),
    "rational_equation": dict(drift=3, cover=1, prov=1, evid=1, fp=3, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE", why="Mali obuhvat; nema živih uzoraka."),
    "divisibility_value": dict(drift=3, cover=1, prov=2, evid=2, fp=5, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE",
        why="Mjereno: 4 lažne blokade na 4 živa uzorka (odluka s obrazloženjem)."),
    "finite_set": dict(drift=2, cover=1, prov=1, evid=1, fp=3, reuse=1,
        klasa="NOT_RELIABLY_PROVABLE", why="Bez upotrebljivog generatora i bez živih uzoraka."),
}


def _route(lesson_id, contract):
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
    compiled = json.loads((ROOT / "data" / "lesson_semantics.compiled.json")
                          .read_text(encoding="utf-8"))
    lessons = compiled["lessons"]
    topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    title, grade = {}, {}
    for grade_key, payload in topics.get("grades", {}).items():
        for row in payload.get("lessons", []):
            title[row["id"]] = row.get("title", "")
            grade[row["id"]] = int(grade_key)

    routes, status_by_lesson = {}, {}
    for lesson_id, entry in lessons.items():
        contract = semantic_contracts.contract_for(lesson_id)
        route = _route(lesson_id, contract)
        implemented = entry["detector"] in detectors.DETECTORS
        routes[lesson_id] = route
        if implemented and route == "model":
            detector_status = "IMPLEMENTED"
        elif route == "deterministic":
            detector_status = "REDUNDANT"
        elif implemented:
            detector_status = "REDUNDANT"
        else:
            detector_status = "UNKNOWN"
        status_by_lesson[lesson_id] = {
            "detector": entry["detector"],
            "requested_enforcement": entry["enforcement_mode"],
            "detector_status": detector_status,
            "production_route": route,
            "server_can_refuse_publication": detector_status == "IMPLEMENTED",
        }

    # --- artefakt STVARNOG autoriteta (uz podatke ugovora, ne umjesto njih) ---
    counts = collections.Counter(v["detector_status"] for v in status_by_lesson.values())
    STATUS_ARTIFACT.write_text(json.dumps({
        "_readme": [
            "STVARNI autoritet po lekciji, IZVEDEN iz koda i rute.",
            "`requested_enforcement` je ono sto ugovor TRAZI (uvijek `blocking`).",
            "`detector_status` je ono sto server MOZE:",
            "  IMPLEMENTED = postoji detektor i lekcija ide model-rutom -> objava se moze odbiti",
            "  REDUNDANT   = paket gradi server (0-pozivna ruta) -> drift nije moguc",
            "  UNKNOWN     = ugovor trazi blokadu, a dokaza nema -> NISTA se ne blokira",
            "Gradi ga scripts/build_semantic_triage.py; ne cita ga runtime.",
        ],
        "contract_version": compiled.get("contract_version"),
        "counts": dict(counts),
        "lessons": status_by_lesson,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # --- trijaža UNKNOWN ---
    unknown_names = sorted({v["detector"] for v in status_by_lesson.values()
                            if v["detector_status"] == "UNKNOWN"})
    unknown, affected, ranking = {}, {}, []
    for name in unknown_names:
        lids = sorted(l for l, v in status_by_lesson.items()
                      if v["detector"] == name and v["detector_status"] == "UNKNOWN")
        params = collections.Counter()
        vocab = collections.Counter()
        for lesson_id in lids:
            for key, value in (lessons[lesson_id].get("parameters") or {}).items():
                params[key] += 1
                if isinstance(value, (list, tuple)):
                    for item in value:
                        vocab[f"{key}:{item}"] += 1
        score = RANKING.get(name, {})
        unknown[name] = {
            "model_route_lessons": len(lids),
            "grades": sorted({grade.get(l) for l in lids if grade.get(l)}),
            "contract_fields": dict(params),
            "vocabulary": [k for k, _ in vocab.most_common()],
            "titles": [title.get(l, "") for l in lids[:5]],
            "current_behavior": "detect() -> UNSUPPORTED (never blocks)",
            "reviewer_recipe": None,
            "provability_class": score.get("klasa", "NOT_RELIABLY_PROVABLE"),
            "decision": "STAY_UNKNOWN",
            "why": score.get("why", ""),
        }
        for lesson_id in lids:
            affected[lesson_id] = {"detector": name, "grade": grade.get(lesson_id),
                                   "title": title.get(lesson_id, "")}
        if score:
            total = (score["drift"] + score["cover"] + score["prov"]
                     + score["evid"] + score["reuse"] - score["fp"])
            ranking.append({"detector": name, "lessons": len(lids), **{
                k: score[k] for k in ("drift", "cover", "prov", "evid", "fp", "reuse")},
                "roi": total, "class": score["klasa"], "decision": "STAY_UNKNOWN"})
    ranking.sort(key=lambda r: -r["roi"])

    (OUT / "unknown_detectors.json").write_text(
        json.dumps(unknown, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "affected_lessons.json").write_text(
        json.dumps(affected, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "ranking.json").write_text(
        json.dumps(ranking, ensure_ascii=False, indent=1), encoding="utf-8")

    model_total = sum(1 for v in status_by_lesson.values()
                      if v["production_route"] == "model")
    implemented = sum(1 for v in status_by_lesson.values()
                      if v["detector_status"] == "IMPLEMENTED")
    lines = [
        "# UNKNOWN semantic rules — triage",
        "",
        f"Contract version `{compiled.get('contract_version')}`.",
        "",
        f"- contract lessons: **{len(status_by_lesson)}** "
        f"(model route **{model_total}**, deterministic **{len(status_by_lesson) - model_total}**)",
        f"- lessons the server can actually refuse to publish: **{implemented}**",
        f"- UNKNOWN detector names: **{len(unknown_names)}** covering "
        f"**{sum(v['model_route_lessons'] for v in unknown.values())}** model-route lessons",
        "",
        "## Why nothing was promoted to BLOCKING this cycle",
        "",
        "The strongest candidate was a generic **answer-class** primitive "
        "(recognition vs computed result), reusing the live-proven "
        "`hint_policy.value_shaped` classifier, with the token→class map derived "
        "from the deterministic generators exactly as the accepted "
        "measure-dimension map was.",
        "",
        "It was measured and **rejected**:",
        "",
        "| corpus | packages | false blocks |",
        "|---|---|---|",
        "| deterministic known-good | 21,120 | **0** |",
        "| live, model-authored | 3,287 | **48 (1.46 %)** |",
        "",
        "All 48 were inspected and all are false: a system of equations as the "
        "answer on an equivalent-systems lesson, a decision plus justification "
        "(\"Da, jer svaki činilac…\"), a symbolic assertion (`$A=N$`), and "
        "\"Tačno\" on a verification task.",
        "",
        "**The transferable lesson:** deriving a rule from the deterministic "
        "generator works for a *unit* (the dimension of the quantity asked is a "
        "physical property of the question) but not for an *answer class* (the "
        "shape of the answer is an authoring choice, and the model legitimately "
        "chooses differently). The deterministic corpus is therefore not a valid "
        "stand-in for model-authored content when proving an author-chosen property.",
        "",
        "## The blocking constraint for every remaining candidate",
        "",
        "Across all 18 UNKNOWN detectors and 111 model-route lessons the entire "
        "known-good corpus holds **13 model-authored packages**, and **13 of the "
        "18 detectors have none at all**. The acceptance bar for a new blocker is "
        "zero false blocks on known-good *model* output; with no such corpus that "
        "bar cannot be met for those families, and the answer-class result shows "
        "the deterministic corpus cannot substitute for it.",
        "",
        "## Ranking (ROI = drift + coverage + provability + evidence + reuse − false-positive risk)",
        "",
        "| detector | lessons | drift | cover | prov | evid | FP risk | reuse | ROI | class | decision |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in ranking:
        lines.append(
            f"| `{row['detector']}` | {row['lessons']} | {row['drift']} | {row['cover']} "
            f"| {row['prov']} | {row['evid']} | {row['fp']} | {row['reuse']} | {row['roi']} "
            f"| {row['class']} | {row['decision']} |")
    lines += [
        "",
        "## Best next candidate",
        "",
        "`power_arithmetic` / `scientific_notation` is the only remaining rule in "
        "class `EXACT_PARSED_MATH`: canonical form (1 ≤ |a| < 10 with an integer "
        "power of ten) is objectively parseable. It is not implemented here "
        "because it reaches a single lesson and has zero model-authored samples "
        "to prove a false-positive rate against. Collect that corpus first.",
        "",
    ]
    (OUT / "audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"contract lessons        : {len(status_by_lesson)}")
    print(f"authority counts        : {dict(counts)}")
    print(f"UNKNOWN detector names  : {len(unknown_names)}")
    print(f"UNKNOWN model lessons   : {sum(v['model_route_lessons'] for v in unknown.values())}")
    print(f"promoted to BLOCKING    : 0 (see audit.md)")
    print(f"written: {OUT.relative_to(ROOT)}/ and {STATUS_ARTIFACT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
