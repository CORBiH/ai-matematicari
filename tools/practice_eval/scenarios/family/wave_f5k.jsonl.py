"""Generator ciljanog talasa F5K — živa semantička vjernost lekciji.

    python tools/practice_eval/scenarios/family/wave_f5k.jsonl.py

Poslije uvođenja semantičkih ugovora vježbe (27 blokirajućih lekcija), ovaj
talas živo mjeri SVIH SEDAM istorijski palih lekcija (svjež + po jedan
nastavak) i šest susjednih kontrola.

PRAVILO PRIHVATANJA ISTORIJSKIH TURNOVA: prihvatljivo je ILI objavljivanje
vjernog zadatka ILI serversko zatvaranje nevjernog paketa — NIKAD objava
susjedne/lakše vještine. Zato istorijski turnovi NEMAJU tvrdu provjeru
„published“: presudu daje semantički validator (package_clean je od F5K
semantički svjestan) + ručni pregled uhvaćenih paketa (Korak 22).

ROUTE PREFLIGHT: istorijske i kontrolne model-lekcije bez semantičkog
ugovora porodice; deterministički kontrolni parovi moraju biti deterministički.
Budžet: najviše 32 poziva (plafon talasa 40).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from matbot import deterministic as det_registry              # noqa: E402
from matbot import semantic_practice                          # noqa: E402
from matbot.semantics import contracts as semantic_contracts  # noqa: E402

OUT = Path(__file__).resolve().parent / "wave_f5k.jsonl"

MODEL_LESSONS = {
    "8-02-007": (8, "Koordinatni sistem i linearna funkcija"),
    "9-03-004": (9, "Linearna funkcija i prava"),
    "8-05-010": (8, "Prizme, piramide, površina i zapremina"),
    "9-07-009": (9, "Geometrijska tijela"),
    "9-01-015": (9, "Razlomljeni racionalni izrazi"),
    "9-01-017": (9, "Razlomljeni racionalni izrazi"),
    "7-04-016": (7, "Ugao i trougao"),
    "7-04-013": (7, "Ugao i trougao"),
    "9-05-006": (9, "Sistemi linearnih jednačina"),
    "6-10-007": (6, "Relacije, preslikavanja i koordinatni sistem"),
    "9-04-010": (9, "Linearne jednačine i nejednačine"),
}
DET_LESSONS = {
    "9-01-003": (9, "Razlomljeni racionalni izrazi"),
    "8-05-015": (8, "Prizme, piramide, površina i zapremina"),
}
HISTORICAL = ("8-02-007", "9-03-004", "8-05-010", "9-07-009", "9-01-015",
              "9-01-017", "7-04-016")

for lesson_id in MODEL_LESSONS:
    if semantic_contracts.contract_for(lesson_id) is not None:
        raise SystemExit(f"ROUTE PREFLIGHT: {lesson_id} nije model-lekcija")
for lesson_id in HISTORICAL:
    if semantic_practice.contract_for(lesson_id) is None:
        raise SystemExit(f"PREFLIGHT: {lesson_id} nema semantički ugovor")
for lesson_id in DET_LESSONS:
    contract = semantic_contracts.contract_for(lesson_id)
    module = contract and det_registry.GENERATORS.get(contract.family_id)
    if not (contract and contract.blocking and module
            and module.supports(dict(contract.parameters))):
        raise SystemExit(f"ROUTE PREFLIGHT: {lesson_id} nije deterministička")

# Bez tvrdog `published`: fail-closed je prihvatljiv ishod. Semantiku mjeri
# package_clean (F5K svjestan) nad UHVAĆENIM paketom kad paket postoji.
RISK_CHECKS = [
    "response_schema", "no_leak", "no_control_chars", "math_safe",
    "terminology_clean", "bosnian", "stays_in_lesson", "package_clean",
    "no_verdict", "calls_at_most:2",
]
CONTROL_CHECKS = RISK_CHECKS + ["not_safe_error", "no_fallback_text",
                                "published", "task_published", "options_ok",
                                "lesson_matches", "task_not_completed"]
DET_CHECKS = [c for c in CONTROL_CHECKS if c != "calls_at_most:2"] + ["zero_calls"]
RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def fresh(checks, calls=2):
    return {"kind": "text", "message": "Daj mi zadatak.",
            "expect_calls": calls, "checks": list(checks),
            "rubrics": list(RUBRICS)}


def harder(checks):
    return {"kind": "text", "message": "Daj mi teži zadatak.",
            "difficulty_request": "harder", "expect_calls": 2,
            "checks": list(checks), "rubrics": list(RUBRICS),
            "requires_active_task": True}


def hint():
    return {"kind": "text", "message": "Ne znam.", "intent": "hint_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "expect_calls": 1,
            "checks": ["response_schema", "no_leak", "no_control_chars",
                       "math_safe", "bosnian", "no_new_task", "task_preserved",
                       "calls_at_most:1", "help_nonempty", "no_answer_leak"],
            "rubrics": []}


def solution():
    return {"kind": "text", "message": "Uradi ga ti.",
            "intent": "solution_request", "interaction_phase": "practice_help",
            "send_last_task": True, "requires_active_task": True,
            "expect_calls": 1,
            "checks": ["response_schema", "no_leak", "no_control_chars",
                       "math_safe", "bosnian", "no_new_task", "task_preserved",
                       "calls_at_most:1", "solution_complete"],
            "rubrics": []}


def scenario(sid, lesson_id, reason, tags, steps, lessons=MODEL_LESSONS):
    grade, oblast = lessons[lesson_id]
    return {"id": sid, "wave": "F5K", "importance": "critical",
            "grade": grade, "oblast": oblast, "topic_id": lesson_id,
            "reason": reason, "tags": list(tags), "steps": steps}


rows = [
    scenario("K01", "8-02-007",
             "Istorijski P1 (grafik→uvrštavanje): vjerna objava ILI zatvaranje.",
             ["historical", "graph"], [fresh(RISK_CHECKS), harder(RISK_CHECKS)]),
    scenario("K02", "9-03-004",
             "Istorijski P1 (grafik→uvrštavanje): svjež pa hint na istom zadatku.",
             ["historical", "graph"], [fresh(RISK_CHECKS), hint()]),
    scenario("K03", "8-05-010",
             "Istorijski P1 (mreža→zapremina): svjež pa potpuno rješenje.",
             ["historical", "net"], [fresh(RISK_CHECKS), solution()]),
    scenario("K04", "9-07-009",
             "Istorijski P1 (mreža→površina bez mreže): svjež pa teži.",
             ["historical", "net"], [fresh(RISK_CHECKS), harder(RISK_CHECKS)]),
    scenario("K05", "9-01-015",
             "Istorijski P1 (tekstualni→goli izraz): svjež pa hint.",
             ["historical", "word_problem"], [fresh(RISK_CHECKS), hint()]),
    scenario("K06", "9-01-017",
             "Istorijski P1 (dokaz→brojevni razlomak): svjež pa rješenje.",
             ["historical", "proof"], [fresh(RISK_CHECKS), solution()]),
    scenario("K07", "7-04-016",
             "Istorijski P1 (SSU→zahvaćeni ugao): svjež pa teži.",
             ["historical", "congruence"], [fresh(RISK_CHECKS), harder(RISK_CHECKS)]),
    scenario("K08", "9-01-003",
             "Susjed: lekcija BROJNE VRIJEDNOSTI smije golo uvrštavati — "
             "deterministička, nula poziva.",
             ["control", "neighbour"], [fresh(DET_CHECKS, calls=0)],
             lessons=DET_LESSONS),
    scenario("K09", "8-05-015",
             "Susjed: lekcija ZAPREMINE smije računati zapreminu — "
             "deterministička, nula poziva.",
             ["control", "neighbour"], [fresh(DET_CHECKS, calls=0)],
             lessons=DET_LESSONS),
    scenario("K10", "7-04-013",
             "Susjed s ugovorom: SUS legitimno koristi ZAHVAĆENI ugao.",
             ["control", "congruence"], [fresh(CONTROL_CHECKS)]),
    scenario("K11", "9-05-006",
             "Kontrola grafičkog ugovora: grafička metoda sistema.",
             ["control", "graph"], [fresh(RISK_CHECKS)]),
    scenario("K12", "6-10-007",
             "Kontrola grafičkog ugovora: tabela/grafik funkcije, 6. razred.",
             ["control", "graph"], [fresh(RISK_CHECKS)]),
    scenario("K13", "9-04-010",
             "Kontrola grafičkog ugovora: grafičko rješavanje jednačine "
             "(bivše model-jezgro kapije).",
             ["control", "graph"], [fresh(RISK_CHECKS)]),
]

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                       for row in rows), encoding="utf-8")
turns = sum(len(row["steps"]) for row in rows)
calls = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, {turns} turns, at most {calls} model calls")
