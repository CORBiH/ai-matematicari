"""Generator ciljanog talasa F5F — Batch #3 živa validacija (272 lekcije).

    python tools/practice_eval/scenarios/family/wave_f5f.jsonl.py

Deterministička matematika Batch #3 dokazana je OFFLINE fuzz kampanjom od
~81.600 paketa; ovaj talas živo dokazuje NOVU arhitekturu u produkcijskom
runtime-u, ne statistiku svih 272 lekcije:

  • po jedan reprezentant svake nove porodice/proširenja: formula-geometrija
    ravni, Pitagora s egzaktnim radikalom, tijela, uglovi (DMS s prenosom),
    sistemi (rješavanje + klasifikacija), faktorizacija razlike kvadrata —
    svaka strukturisana akcija TAČNO NULA SDK poziva;
  • visokorizične tačke: π autoritet (površina kruga, zapremina kupe),
    egzaktan radikal $a\\sqrt{2}$, provjera rješenja sistema uvrštavanjem,
    proširenje faktorizacije nazad u polazni polinom, DMS prenos minuta;
  • dva prelaza deterministički zadatak → slobodno konceptualno pitanje
    (model, najviše 2 poziva, aktivni zadatak sačuvan, povratak na 0-call);
  • dvije model-kontrole na lekcijama koje su OSTALE model-only u
    kompajliranom klasifikatoru (tekstualni sistem, praktični problemi).

Gornja granica talasa je ispisana pri generisanju i mora ostati ≤ 14.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f5f.jsonl"

# (topic_id, razred, oblast) — deterministički reprezentanti Batch #3,
# provjereni u kompajliranom data/lesson_semantic_assignments.json (5E.1).
GEO2D_TRAPEZOID = ("7-05-022", 7, "Četverougao, obim i površina")
CIRCLE_AREA = ("8-08-011", 8, "Mnogougao, kružnica i krug")
SQUARE_DIAGONAL = ("8-04-004", 8, "Pitagorina teorema i primjene u ravni")
PRISM4 = ("8-05-006", 8, "Prizme, piramide, površina i zapremina")
CONE_VOLUME = ("9-07-023", 9, "Geometrijska tijela")
DMS_ADD_SUB = ("6-09-011", 6, "Uglovi")
SYSTEM_SOLVE = ("9-05-007", 9, "Sistemi linearnih jednačina")
SYSTEM_CLASSIFY = ("9-05-012", 9, "Sistemi linearnih jednačina")
FACTOR_SQUARES = ("9-06-006", 9,
                  "Polinomi, faktorizacija i jednostavne kvadratne jednačine")

# Model-kontrole: klasifikatorski dokazano MODEL_ONLY_FOR_NOW, sadržajno teške.
MODEL_SYSTEM_WORDS = ("9-05-013", 9, "Sistemi linearnih jednačina")
MODEL_PRACTICAL = ("8-04-016", 8, "Pitagorina teorema i primjene u ravni")

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed",
]
DET_TASK_CHECKS = TASK_CHECKS + ["zero_calls", "task_differs"]
DET_FRESH_CHECKS = TASK_CHECKS + ["zero_calls"]
MODEL_TASK_CHECKS = TASK_CHECKS + ["calls_at_most:2"]

HELP_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "no_new_task", "task_preserved",
]
DET_HINT_CHECKS = HELP_CHECKS + ["zero_calls", "help_nonempty", "hint_no_leak",
                                 "no_answer_leak", "reveal_absent",
                                 "task_not_completed"]
DET_SOLUTION_CHECKS = HELP_CHECKS + ["zero_calls", "solution_complete",
                                     "reveal_present", "task_completed"]
MODEL_HELP_CHECKS = HELP_CHECKS + ["help_nonempty", "calls_at_most:2"]

TASK_RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def task_step(message, checks, calls, rubrics=TASK_RUBRICS, **extra):
    step = {"kind": "text", "message": message, "expect_calls": calls,
            "checks": list(checks), "rubrics": list(rubrics)}
    step.update(extra)
    return step


def det_choice(select, extra):
    return {"kind": "choice", "select": select, "expect_calls": 0,
            "requires_active_task": True,
            "checks": ["response_schema", "not_safe_error", "no_fallback_text",
                       "no_leak", "no_control_chars", "math_safe", "bosnian",
                       "correct_option_stable", "zero_calls"] + list(extra),
            "rubrics": []}


def det_hint():
    return {"kind": "text", "message": "Ne znam.", "intent": "hint_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "expect_calls": 0,
            "checks": list(DET_HINT_CHECKS), "rubrics": []}


def det_solution():
    return {"kind": "text", "message": "Uradi ga ti.", "intent": "solution_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "expect_calls": 0,
            "checks": list(DET_SOLUTION_CHECKS), "rubrics": []}


def fresh():
    return task_step("Daj mi jedan zadatak za vježbu iz ove teme.",
                     DET_FRESH_CHECKS, 0)


def new_task():
    return task_step("Daj mi novi zadatak.", DET_TASK_CHECKS, 0)


def harder():
    return task_step("Daj mi teži zadatak.", DET_TASK_CHECKS, 0,
                     difficulty_request="harder")


def easier():
    return task_step("Daj mi lakši zadatak.", DET_TASK_CHECKS, 0,
                     difficulty_request="easier")


def free_form(message):
    return {"kind": "text", "message": message, "send_last_task": True,
            "requires_active_task": True, "expect_calls": 2,
            "checks": list(MODEL_HELP_CHECKS), "rubrics": ["pedagogy"]}


rows = []
n = 0


def add(reason, tags, steps, lesson, importance="critical"):
    global n
    n += 1
    topic, grade, oblast = lesson
    rows.append({"id": f"F{n:02d}", "wave": "F5F", "importance": importance,
                 "grade": grade, "oblast": oblast, "topic_id": topic,
                 "reason": reason, "tags": list(tags), "steps": steps})


# --- DETERMINISTIČKA JEZGRA BATCH #3: NULA POZIVA -----------------------------

add("Formula-geometrija ravni — površina trapeza (7. razred): svjež, teži, "
    "hint; formula/uvrštavanje/lanac egzaktni.",
    ["deterministic", "geometry2d", "high_risk"],
    [fresh(), harder(), det_hint()], GEO2D_TRAPEZOID)

add("VISOKORIZIČNO D — π autoritet: površina kruga, odgovor je racionalni "
    "višekratnik broja π bez decimalne zamjene; svjež pa potpuno rješenje.",
    ["deterministic", "pi_authority", "circle", "high_risk"],
    [fresh(), det_solution()], CIRCLE_AREA)

add("VISOKORIZIČNO C — egzaktan radikal: dijagonala kvadrata $d = a\\sqrt{2}$ "
    "ostaje simbolička; svjež pa potpuno rješenje.",
    ["deterministic", "pythagoras", "radical", "high_risk"],
    [fresh(), det_solution()], SQUARE_DIAGONAL)

add("Prelaz: aktivan deterministički Pitagorin zadatak → konceptualno "
    "pitanje zašto teorema važi samo za pravougli trougao ide MODELU "
    "(≤2 poziva), zadatak sačuvan, pa deterministički TAČAN klik.",
    ["routing", "free_form", "transition", "pythagoras"],
    [fresh(),
     free_form("Zašto Pitagorina teorema vrijedi samo za pravougli trougao?"),
     det_choice("correct", ["verdict_correct", "task_completed"])],
    SQUARE_DIAGONAL)

add("VISOKORIZIČNO E — tijela: pravilna četverostrana prizma (omotač/"
    "površina/zapremina), pogrešan eksponent ili faktor bio bi očigledan; "
    "svjež, nov, potpuno rješenje.",
    ["deterministic", "solid", "high_risk"],
    [fresh(), new_task(), det_solution()], PRISM4)

add("π autoritet u ZAPREMINI: zapremina kupe s racionalnim višekratnikom π "
    "i faktorom 1/3; svjež pa lakši.",
    ["deterministic", "solid", "pi_authority", "high_risk"],
    [fresh(), easier()], CONE_VOLUME)

add("VISOKORIZIČNO F — DMS aritmetika (6. razred): sabiranje/oduzimanje "
    "stepeni i minuta s prenosom/pozajmicom; svjež, teži, pa POGREŠAN klik "
    "bez otkrivanja.",
    ["deterministic", "angles", "dms", "high_risk"],
    [fresh(), harder(),
     det_choice("wrong", ["verdict_incorrect", "reveal_absent",
                          "no_answer_leak"])], DMS_ADD_SUB)

add("VISOKORIZIČNO A — sistem s jedinstvenim rješenjem (supstitucija): "
    "svjež pa potpuno rješenje s provjerom uvrštavanjem u OBJE jednačine.",
    ["deterministic", "system", "high_risk"],
    [fresh(), det_solution()], SYSTEM_SOLVE)

add("VISOKORIZIČNO B — faktorizacija razlike kvadrata: označena "
    "faktorizacija množenjem se vraća u polazni polinom; svjež, TAČAN "
    "klik, pa lakši.",
    ["deterministic", "polynomial", "factorization", "high_risk"],
    [fresh(), det_choice("correct", ["verdict_correct", "task_completed"]),
     easier()], FACTOR_SQUARES)

add("VISOKORIZIČNO A2 + prelaz: klasifikacija broja rješenja sistema "
    "(određena i determinantom/rangom na serveru); konceptualno pitanje o "
    "odnosu koeficijenata ide MODELU, zadatak sačuvan, pa deterministički "
    "nov zadatak.",
    ["deterministic", "system", "classification", "routing", "free_form"],
    [fresh(),
     free_form("Zašto nam odnos koeficijenata sistema govori koliko "
               "rješenja sistem ima?"),
     new_task()], SYSTEM_CLASSIFY)

# --- MODEL-KONTROLE NA KLASIFIKATORSKI MODEL-ONLY LEKCIJAMA -------------------

add("Model-kontrola (tekstualni zadatak sa sistemom, 9. razred): svjež "
    "zadatak — Tutor+Recenzent, najviše 2 poziva; dokaz da aktivacija "
    "sistema NIJE progutala tekstualne zadatke.",
    ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)], MODEL_SYSTEM_WORDS)

add("Model-kontrola (praktični problemski zadaci uz Pitagorinu teoremu, "
    "8. razred): svjež zadatak — namjerno otvoren model-sadržaj.",
    ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)], MODEL_PRACTICAL)

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
               encoding="utf-8")
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
deterministic = sum(1 for row in rows
                    if all(step["expect_calls"] == 0 for step in row["steps"]))
print(f"{OUT}: {len(rows)} scenarios ({deterministic} deterministic), "
      f"at most {maximum} model calls")
