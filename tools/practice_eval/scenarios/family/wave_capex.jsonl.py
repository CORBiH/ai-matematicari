"""Generator ciljanog talasa F5C (kapacitetna ekspanzija).

Ime talasa je F5C (Faza 5, Capability) jer šema scenarija prihvata samo
A/B/F* imena talasa — vidi tools/practice_eval/scenario.py.

    python tools/practice_eval/scenarios/family/wave_capex.jsonl.py

Talas dokazuje ŽIVO granice rutiranja poslije masovnog uključivanja (57
lekcija / 16 porodica), NE determinističku matematiku samu — nju je dokazala
offline fuzz kampanja od ~10.000 paketa (tests/test_deterministic_bulk_properties.py):

  • po jedna reprezentativna lekcija SVAKOG novog kapaciteta, sva četiri
    razreda: svjež/nov/lakši/teži, klik (tačan i pogrešan), hint, rješenje —
    tačno NULA SDK poziva po akciji;
  • slobodno konceptualno pitanje iznad determinističkog zadatka ide MODELU;
  • model-kontrola na lekcijama koje su OSTALE na model-putu (tekstualni
    zadaci, brojevni izrazi s razlomcima) — najviše 2 poziva, bez regresije.

Većina scenarija troši nula poziva; gornja granica cijelog talasa je mala i
ispisana pri generisanju.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_capex.jsonl"

# (topic_id, razred, oblast) — reprezentativna lekcija svake NOVE porodice.
ORDER_OPS = ("6-02-007", 6, "Prirodni brojevi i skupovi N/N0")
DIVISIBILITY = ("6-03-004", 6, "Djeljivost brojeva")
GCD = ("6-03-008", 6, "Djeljivost brojeva")
PRIMES = ("6-03-005", 6, "Djeljivost brojeva")
COMPARE_FRACTIONS = ("6-04-008", 6, "Razlomci")
DECIMAL_MULTIPLY = ("6-05-009", 6, "Razlomci u decimalnom obliku i decimalni brojevi")
PERCENT = ("6-06-002", 6, "Postotak, razmjera i aritmetička sredina")
MEAN6 = ("6-06-004", 6, "Postotak, razmjera i aritmetička sredina")
INTEGER_MULTIPLY = ("7-02-011", 7, "Cijeli brojevi")
EQUATION_Z = ("7-02-016", 7, "Cijeli brojevi")
RATIONAL_ADD = ("7-03-009", 7, "Racionalni brojevi")
ABS_RATIONAL = ("7-03-005", 7, "Racionalni brojevi")
POWER_LAW = ("8-01-015", 8, "Realni brojevi, korijeni i stepeni")
SQUARE_ROOT = ("8-01-008", 8, "Realni brojevi, korijeni i stepeni")
PROBABILITY = ("8-06-012", 8, "Podaci i vjerovatnoća")
EQUATION_PAREN = ("9-04-003", 9, "Linearne jednačine i nejednačine")
MEAN9 = ("9-08-010", 9, "Podaci, vjerovatnoća, finansije i mjerne jedinice")

# Model-kontrole: lekcije koje su NAMJERNO ostale na model-putu.
MODEL_WORD_PROBLEMS = ("6-03-010", 6, "Djeljivost brojeva")
MODEL_FRACTION_EXPR = ("6-04-014", 6, "Razlomci")
MODEL_WORD_EQUATION = ("9-04-011", 9, "Linearne jednačine i nejednačine")

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


def harder():
    return task_step("Daj mi teži zadatak.", DET_TASK_CHECKS, 0,
                     difficulty_request="harder")


def easier():
    return task_step("Daj mi lakši zadatak.", DET_TASK_CHECKS, 0,
                     difficulty_request="easier")


rows = []
n = 0


def add(reason, tags, steps, lesson, importance="critical"):
    global n
    n += 1
    topic, grade, oblast = lesson
    rows.append({"id": f"X{n:02d}", "wave": "F5C", "importance": importance,
                 "grade": grade, "oblast": oblast, "topic_id": topic,
                 "reason": reason, "tags": list(tags), "steps": steps})


# --- DETERMINISTIČKA JEZGRA: po jedan kapacitet, nula poziva -----------------

add("Redoslijed operacija (prirodni brojevi): svjež pa teži — prioritet i "
    "zagrade, nula poziva.", ["deterministic", "natural_arithmetic"],
    [fresh(), harder()], ORDER_OPS)

add("Pravila djeljivosti — puni tok: svjež, pogrešan klik (pravilo-hint), "
    "hint, rješenje, nov zadatak; sve nula poziva, orakl djeljivosti "
    "nezavisno dokazuje opcije.", ["deterministic", "divisibility", "full_flow"],
    [fresh(),
     det_choice("wrong", ["verdict_incorrect", "reveal_absent", "no_answer_leak"]),
     det_hint(), det_solution(),
     task_step("Daj mi novi zadatak.", DET_TASK_CHECKS, 0)],
    DIVISIBILITY)

add("NZD: svjež pa hint iz pohranjene ljestvice.", ["deterministic", "gcd"],
    [fresh(), det_hint()], GCD)

add("Prosti brojevi: svjež pa TAČAN klik — deterministička ocjena.",
    ["deterministic", "primes", "grading"],
    [fresh(), det_choice("correct", ["verdict_correct", "task_completed"])],
    PRIMES)

add("Upoređivanje razlomaka: svjež pa teži — superlativni MCQ, orakl "
    "poređenja nezavisno dokazuje ekstrem.", ["deterministic", "comparison"],
    [fresh(), harder()], COMPARE_FRACTIONS)

add("Množenje decimalnih brojeva: svjež pa teži pa lakši — tranzicije nivoa.",
    ["deterministic", "decimal", "difficulty"],
    [fresh(), harder(), easier()], DECIMAL_MULTIPLY)

add("Procenat broja: svjež pa dvaput teži — do traženja cjeline na nivou 3.",
    ["deterministic", "percent", "level_boundary"],
    [fresh(), harder(), harder()], PERCENT)

add("Aritmetička sredina (6. razred): svjež zadatak.",
    ["deterministic", "mean"], [fresh()], MEAN6)

add("Množenje cijelih brojeva: svjež pa TAČAN klik — pravilo znakova.",
    ["deterministic", "integer", "grading"],
    [fresh(), det_choice("correct", ["verdict_correct", "task_completed"])],
    INTEGER_MULTIPLY)

add("Jednačine u Z: svjež, hint pa potpuno rješenje s provjerom uvrštavanjem.",
    ["deterministic", "equation", "solution"],
    [fresh(), det_hint(), det_solution()], EQUATION_Z)

add("Sabiranje racionalnih: svjež pa teži — predznaci i nejednaki imenioci.",
    ["deterministic", "rational"], [fresh(), harder()], RATIONAL_ADD)

add("Apsolutna vrijednost racionalnog broja: svjež zadatak.",
    ["deterministic", "absolute_value"], [fresh()], ABS_RATIONAL)

add("Zakoni stepena jednakih osnova: svjež pa teži — opcije su stepeni "
    "različitih vrijednosti.", ["deterministic", "powers"],
    [fresh(), harder()], POWER_LAW)

add("Kvadratni korijen: svjež pa pogrešan klik — hint bez otkrivanja.",
    ["deterministic", "roots", "grading"],
    [fresh(), det_choice("wrong", ["verdict_incorrect", "reveal_absent",
                                   "no_answer_leak"])],
    SQUARE_ROOT)

add("Klasična vjerovatnoća: svjež pa dvaput teži — do komplementa na nivou 3.",
    ["deterministic", "probability", "level_boundary"],
    [fresh(), harder(), harder()], PROBABILITY)

add("Jednačina sa zagradama (9. razred): svjež, hint, rješenje.",
    ["deterministic", "equation", "solution"],
    [fresh(), det_hint(), det_solution()], EQUATION_PAREN)

add("Aritmetička sredina (9. razred): svjež pa nov zadatak — kanonska "
    "jedinstvenost.", ["deterministic", "mean", "uniqueness"],
    [fresh(), task_step("Daj mi novi zadatak.", DET_TASK_CHECKS, 0)], MEAN9)

# --- SLOBODNA PORUKA NA DETERMINISTIČKOJ LEKCIJI → MODEL ----------------------

add("Slobodno konceptualno pitanje iznad aktivnog determinističkog zadatka "
    "ide MODELU — ruta se nikad ne bira iz proze.", ["routing", "free_form"],
    [fresh(),
     {"kind": "text", "message": "Zašto pravilo za djeljivost sa 3 gleda zbir cifara?",
      "send_last_task": True, "requires_active_task": True, "expect_calls": 2,
      "checks": list(MODEL_HELP_CHECKS), "rubrics": ["pedagogy"]}],
    DIVISIBILITY)

add("Prelaz deterministički zadatak → slobodno objašnjenje → povratak na "
    "deterministički klik: model odgovara, a zadatak i ocjena ostaju serverski.",
    ["routing", "free_form", "transition"],
    [fresh(),
     {"kind": "text", "message": "Objasni mi drugim riječima kako se rješava ovakva jednačina.",
      "send_last_task": True, "requires_active_task": True, "expect_calls": 2,
      "checks": list(MODEL_HELP_CHECKS), "rubrics": ["pedagogy"]},
     det_choice("correct", ["verdict_correct", "task_completed"])],
    EQUATION_Z)

# --- MODEL-KONTROLA NA LEKCIJAMA KOJE SU OSTALE NA MODEL-PUTU -----------------

add("Model-kontrola (tekstualni zadaci iz djeljivosti): svjež zadatak — "
    "2 poziva, nepromijenjena validacija.", ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)], MODEL_WORD_PROBLEMS)

add("Model-kontrola (brojevni izrazi s razlomcima): svjež zadatak — lekcija "
    "izvan ugovora ostaje Tutor+Recenzent.", ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)], MODEL_FRACTION_EXPR)

add("Model-kontrola (tekstualni zadatak → linearna jednačina, 9. razred): "
    "modelovanje teksta ostaje na modelu.", ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)], MODEL_WORD_EQUATION)

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
               encoding="utf-8")
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, at most {maximum} model calls")
