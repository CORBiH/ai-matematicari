"""Generator ciljanog talasa F4H — deterministička Practice jezgra.

    python tools/practice_eval/scenarios/family/wave_f4h.jsonl.py

Faza 4H: strukturisane akcije na lekcijama porodice fraction_arithmetic_direct
idu determinističkom strategijom (NULA SDK poziva), a model-put dobija
kompaktno recenzentsko odobrenje, keširabilan prefiks prompta i rok turna.

Talas dokazuje ŽIVO:
  • deterministic: svjež/nov/lakši/teži/klik/hint/rješenje = 0 poziva;
  • slobodno pitanje na determinističkoj lekciji i dalje ide modelu;
  • model-kontrola na nepokrivenim lekcijama: 2 poziva, kompaktno odobrenje,
    keširani prefiks (cached_input_tokens u logu), bez regresije validacije.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f4h.jsonl"

LIKE = ("6-04-009", 6, "Razlomci")
UNLIKE = ("6-04-010", 6, "Razlomci")
MULTIPLY = ("6-04-011", 6, "Razlomci")
DIVIDE = ("6-04-012", 6, "Razlomci")
DIVISIBILITY = ("6-03-004", 6, "Djeljivost brojeva")
COMPARE = ("7-03-006", 7, "Racionalni brojevi")
EQUATIONS = ("9-04-003", 9, "Linearne jednačine i nejednačine")

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
DET_HINT_TOP_CHECKS = HELP_CHECKS + ["zero_calls", "help_nonempty",
                                     "reveal_absent"]
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


def det_hint(checks=DET_HINT_CHECKS):
    return {"kind": "text", "message": "Ne znam.", "intent": "hint_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "expect_calls": 0,
            "checks": list(checks), "rubrics": []}


def det_solution():
    return {"kind": "text", "message": "Uradi ga ti.", "intent": "solution_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "expect_calls": 0,
            "checks": list(DET_SOLUTION_CHECKS), "rubrics": []}


rows = []
n = 0


def add(reason, tags, steps, lesson, importance="critical"):
    global n
    n += 1
    topic, grade, oblast = lesson
    rows.append({"id": f"H{n:02d}", "wave": "F4H", "importance": importance,
                 "grade": grade, "oblast": oblast, "topic_id": topic,
                 "reason": reason, "tags": list(tags), "steps": steps})


# --- DETERMINISTIČKA JEZGRA: NULA POZIVA --------------------------------------

add("Puni deterministički tok jednakih imenilaca: svjež zadatak, pogrešan klik "
    "(pravilo-nagovještaj, bez otkrivanja), hint, rješenje, pa nov zadatak — "
    "SVE bez ijednog SDK poziva.",
    ["deterministic", "full_flow"],
    [task_step("Daj mi jedan zadatak za vježbu iz ove teme.", DET_FRESH_CHECKS, 0),
     det_choice("wrong", ["verdict_incorrect", "reveal_absent", "no_answer_leak"]),
     det_hint(),
     det_solution(),
     task_step("Daj mi novi zadatak.", DET_TASK_CHECKS, 0)],
    LIKE)

add("Različiti imenioci: svjež pa teži pa lakši — serverske tranzicije nivoa, "
    "nula poziva, istinit uvod.",
    ["deterministic", "difficulty"],
    [task_step("Daj mi zadatak.", DET_FRESH_CHECKS, 0),
     task_step("Daj mi teži zadatak.", DET_TASK_CHECKS, 0,
               difficulty_request="harder"),
     task_step("Daj mi lakši zadatak.", DET_TASK_CHECKS, 0,
               difficulty_request="easier")],
    UNLIKE)

add("Množenje: svjež zadatak pa TAČAN klik — deterministička ocjena s "
    "potvrdom rezultata, nula poziva.",
    ["deterministic", "grading"],
    [task_step("Daj mi zadatak.", DET_FRESH_CHECKS, 0),
     det_choice("correct", ["verdict_correct", "task_completed"])],
    MULTIPLY)

add("Dijeljenje: svjež zadatak pa potpuno rješenje iz pohranjenih koraka.",
    ["deterministic", "solution"],
    [task_step("Daj mi zadatak.", DET_FRESH_CHECKS, 0),
     det_solution()],
    DIVIDE)

add("Ljestvica nagovještaja do vrha: tri hinta redom, nula poziva; treći "
    "smije pokazati većinu postupka (vrh ljestvice).",
    ["deterministic", "hint_ladder"],
    [task_step("Daj mi zadatak.", DET_FRESH_CHECKS, 0),
     det_hint(),
     det_hint(),
     det_hint(DET_HINT_TOP_CHECKS)],
    UNLIKE)

add("Teži do nivoa 3 pa još jednom teži: istinit uvod na granici, kanonski "
    "različiti zadaci, nula poziva.",
    ["deterministic", "level_boundary"],
    [task_step("Daj mi zadatak.", DET_FRESH_CHECKS, 0),
     task_step("Daj mi teži zadatak.", DET_TASK_CHECKS, 0,
               difficulty_request="harder"),
     task_step("Daj mi teži zadatak.", DET_TASK_CHECKS, 0,
               difficulty_request="harder"),
     task_step("Daj mi teži zadatak.", DET_TASK_CHECKS, 0,
               difficulty_request="harder")],
    MULTIPLY)

add("Lakši na nivou 1: istinita poruka o najlakšem nivou, nov zadatak, "
    "nula poziva.",
    ["deterministic", "level_boundary"],
    [task_step("Daj mi zadatak.", DET_FRESH_CHECKS, 0),
     task_step("Daj mi lakši zadatak.", DET_TASK_CHECKS, 0,
               difficulty_request="easier")],
    DIVIDE)

add("Dva uzastopna nova zadatka: kanonska jedinstvenost bez modela.",
    ["deterministic", "uniqueness"],
    [task_step("Daj mi zadatak.", DET_FRESH_CHECKS, 0),
     task_step("Daj mi novi zadatak.", DET_TASK_CHECKS, 0),
     task_step("Daj mi još jedan zadatak.", DET_TASK_CHECKS, 0)],
    LIKE)

# --- SLOBODNA PORUKA NA DETERMINISTIČKOJ LEKCIJI → MODEL ----------------------

add("Slobodno konceptualno pitanje iznad aktivnog determinističkog zadatka "
    "ide MODELU (pomoćni turn, najviše 2 poziva) — ruta se ne bira iz proze.",
    ["routing", "free_form"],
    [task_step("Daj mi zadatak.", DET_FRESH_CHECKS, 0),
     {"kind": "text",
      "message": "Zašto se imenioci ne sabiraju kad sabiram razlomke?",
      "send_last_task": True, "requires_active_task": True, "expect_calls": 2,
      "checks": list(MODEL_HELP_CHECKS), "rubrics": ["pedagogy"]}],
    LIKE)

# --- MODEL-KONTROLA NA NEPOKRIVENIM LEKCIJAMA ---------------------------------

add("Model-kontrola (djeljivost): svjež zadatak — 2 poziva, kompaktno "
    "odobrenje i keširabilan prefiks ne smiju promijeniti validaciju.",
    ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)],
    DIVISIBILITY)

add("Model-kontrola (djeljivost): teži zadatak — recenzent i dalje obavezan.",
    ["model_control", "difficulty"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2),
     task_step("Daj mi teži zadatak.", MODEL_TASK_CHECKS + ["task_differs"], 2,
               difficulty_request="harder")],
    DIVISIBILITY)

add("Model-kontrola (upoređivanje, 7. razred): svjež zadatak uz orakl "
    "poređenja.",
    ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)],
    COMPARE)

add("Model-kontrola (jednačine, 9. razred): najduži prompt/paket u kurikulumu "
    "— mjeri kompaktno odobrenje i keš na velikom ulazu.",
    ["model_control", "reviewer_budget"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)],
    EQUATIONS)

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
               encoding="utf-8")
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, at most {maximum} model calls")
