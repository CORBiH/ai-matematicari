"""Generator ciljanog talasa F5D — Batch #2 živa validacija (154 lekcije).

    python tools/practice_eval/scenarios/family/wave_f5d.jsonl.py

Deterministička matematika Batch #2 je dokazana OFFLINE fuzz kampanjom od
~46.000 paketa; ovaj talas živo dokazuje GRANICE RUTIRANJA i visokorizične
rubove novih kapaciteta:

  • po jedna reprezentativna lekcija svake važne nove/proširene porodice,
    raspoređena po sva četiri razreda — svaka strukturisana akcija TAČNO
    NULA SDK poziva;
  • visokorizični slučajevi: promjena smjera nejednakosti uz negativan
    koeficijent, kvadrirani/kubirani faktori površine i zapremine, brzina
    18/5, zaokruživanje, razdvojeni zapisi razlomak/decimala, nepoznati
    član proporcije, višekoračna jednačina s provjerom uvrštavanjem;
  • dva prelaza deterministički zadatak → slobodno konceptualno pitanje
    (model, najviše 2 poziva, aktivni zadatak sačuvan);
  • tri model-kontrole na reprezentativno TEŠKIM model-lekcijama.

Većina scenarija troši nula poziva; gornja granica talasa je ispisana pri
generisanju i mora ostati ≤ 12.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f5d.jsonl"

# (topic_id, razred, oblast) — deterministički reprezentanti Batch #2.
EQ_QPLUS = ("6-07-002", 6, "Jednačine, nejednačine i izrazi u Q+")
SIGN_FLIP = ("9-04-016", 9, "Linearne jednačine i nejednačine")
FUNCTION = ("8-02-005", 8, "Koordinatni sistem i linearna funkcija")
POLYNOMIAL = ("8-07-008", 8, "Cijeli racionalni izrazi i polinomi")
PROPORTION = ("8-03-003", 8, "Proporcionalnost, Talesova teorema i sličnost")
UNIT_LENGTH = ("6-13-001", 6, "Mjerenje, mjerne jedinice i podaci")
UNIT_AREA = ("6-13-004", 6, "Mjerenje, mjerne jedinice i podaci")
UNIT_LAV = ("9-08-011", 9, "Podaci, vjerovatnoća, finansije i mjerne jedinice")
SPEED = ("9-08-013", 9, "Podaci, vjerovatnoća, finansije i mjerne jedinice")
CONVERSION = ("6-05-003", 6, "Razlomci u decimalnom obliku i decimalni brojevi")
ROUNDING = ("6-05-007", 6, "Razlomci u decimalnom obliku i decimalni brojevi")
SCINOT = ("8-01-017", 8, "Realni brojevi, korijeni i stepeni")
ROOT_LAW = ("8-01-010", 8, "Realni brojevi, korijeni i stepeni")
PERCENT_ABR = ("8-03-017", 8, "Proporcionalnost, Talesova teorema i sličnost")
COMPLEMENT = ("8-06-013", 8, "Podaci i vjerovatnoća")
FREQUENCY = ("8-06-002", 8, "Podaci i vjerovatnoća")
Q_EXPRESSIONS = ("7-03-014", 7, "Racionalni brojevi")
DECADE = ("6-03-003", 6, "Djeljivost brojeva")
QUADRATIC = ("9-06-013", 9, "Polinomi, faktorizacija i jednostavne kvadratne jednačine")
ABS_EQUATION = ("7-02-018", 7, "Cijeli brojevi")
COMBINE_EQ = ("7-03-018", 7, "Racionalni brojevi")

# Model-kontrole: reprezentativno TEŠKE lekcije koje su OSTALE na modelu.
MODEL_FRACTION_WORDS = ("6-04-015", 6, "Razlomci")
MODEL_INTEREST = ("8-03-019", 8, "Proporcionalnost, Talesova teorema i sličnost")
MODEL_RATIONAL_EXPR = ("9-01-013", 9, "Razlomljeni racionalni izrazi")

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
    rows.append({"id": f"D{n:02d}", "wave": "F5D", "importance": importance,
                 "grade": grade, "oblast": oblast, "topic_id": topic,
                 "reason": reason, "tags": list(tags), "steps": steps})


# --- DETERMINISTIČKA JEZGRA BATCH #2: NULA POZIVA -----------------------------

add("Proširena jednačina u Q+ (6. razred): svjež, hint, potpuno rješenje — "
    "pozitivna rješenja, provjera uvrštavanjem.",
    ["deterministic", "equation", "grade6"],
    [fresh(), det_hint(), det_solution()], EQ_QPLUS)

add("VISOKORIZIČNO A — promjena smjera nejednakosti uz negativan "
    "koeficijent: svjež, nov, teži; opcije su skupovi rješenja.",
    ["deterministic", "inequality", "sign_flip", "high_risk"],
    [fresh(), new_task(), harder()], SIGN_FLIP)

add("Linearna funkcija: svjež pa TAČAN klik — vrijednost/koeficijent, "
    "deterministička ocjena.",
    ["deterministic", "function", "grading"],
    [fresh(), det_choice("correct", ["verdict_correct", "task_completed"])],
    FUNCTION)

add("Množenje polinoma: svjež pa POGREŠAN klik — pravilo-nagovještaj bez "
    "otkrivanja.",
    ["deterministic", "polynomial", "grading"],
    [fresh(), det_choice("wrong", ["verdict_incorrect", "reveal_absent",
                                   "no_answer_leak"])], POLYNOMIAL)

add("VISOKORIZIČNO F — nepoznati član proporcije: svjež pa hint; egzaktno "
    "racionalno rješenje, bez ekvivalentnih duplikata.",
    ["deterministic", "proportion", "high_risk"],
    [fresh(), det_hint()], PROPORTION)

add("Jedinice dužine (6. razred): svjež zadatak.",
    ["deterministic", "units", "grade6"], [fresh()], UNIT_LENGTH)

add("VISOKORIZIČNO B — jedinice POVRŠINE: kvadrirani faktori (100 po "
    "koraku), svjež pa teži.",
    ["deterministic", "units", "area", "high_risk"],
    [fresh(), harder()], UNIT_AREA)

add("Dužina/površina/ZAPREMINA (9. razred): tri uzastopna zadatka — "
    "kubirani faktori u opticaju, kanonska različitost.",
    ["deterministic", "units", "volume", "high_risk"],
    [fresh(), new_task(), new_task()], UNIT_LAV)

add("VISOKORIZIČNO C — brzina m/s ↔ km/h: egzaktan faktor 3,6 (18/5); "
    "svjež pa teži.",
    ["deterministic", "units", "speed", "high_risk"],
    [fresh(), harder()], SPEED)

add("VISOKORIZIČNO E — pretvaranje razlomak ↔ decimala: opcije nikad ne "
    "miješaju zapise (1/2 i 0,5 ne mogu biti dvije opcije); svjež, nov, "
    "rješenje.",
    ["deterministic", "conversion", "high_risk"],
    [fresh(), new_task(), det_solution()], CONVERSION)

add("VISOKORIZIČNO D — zaokruživanje decimalnih brojeva (školsko half-up): "
    "svjež, nov, hint.",
    ["deterministic", "rounding", "high_risk"],
    [fresh(), new_task(), det_hint()], ROUNDING)

add("Naučni zapis broja: svjež pa teži — samo pozitivni izložioci.",
    ["deterministic", "powers", "scientific"],
    [fresh(), harder()], SCINOT)

add("Korijen proizvoda i količnika: svjež pa potpuno rješenje.",
    ["deterministic", "roots"], [fresh(), det_solution()], ROOT_LAW)

add("Procentni iznos/osnovica/stopa: svjež pa teži.",
    ["deterministic", "percent"], [fresh(), harder()], PERCENT_ABR)

add("Komplementaran događaj: svjež pa TAČAN klik.",
    ["deterministic", "probability", "grading"],
    [fresh(), det_choice("correct", ["verdict_correct", "task_completed"])],
    COMPLEMENT)

add("Frekvencija malog niza podataka: svjež zadatak.",
    ["deterministic", "frequency"], [fresh()], FREQUENCY)

add("Brojevni izrazi sa zagradama u Q (7. razred): svjež, lakši na granici "
    "(istinit uvod), pa teži.",
    ["deterministic", "rational", "level_boundary"],
    [fresh(), easier(), harder()], Q_EXPRESSIONS)

add("Najveća dekadska jedinica (teorija brojeva, 6. razred): svjež pa "
    "TAČAN klik.",
    ["deterministic", "numbertheory", "grading"],
    [fresh(), det_choice("correct", ["verdict_correct", "task_completed"])],
    DECADE)

add("Kvadratna jednačina izlučivanjem (9. razred): svjež pa potpuno "
    "rješenje — potpun skup rješenja.",
    ["deterministic", "quadratic"], [fresh(), det_solution()], QUADRATIC)

add("Jednačina s apsolutnom vrijednošću (7. razred): svjež pa POGREŠAN "
    "klik — bez otkrivanja.",
    ["deterministic", "absolute_value", "grading"],
    [fresh(), det_choice("wrong", ["verdict_incorrect", "reveal_absent",
                                   "no_answer_leak"])], ABS_EQUATION)

add("VISOKORIZIČNO G — višekoračna jednačina (zagrada + svođenje sličnih "
    "članova): svjež pa rješenje s provjerom uvrštavanjem u POLAZNU "
    "jednačinu.",
    ["deterministic", "equation", "multi_step", "high_risk"],
    [fresh(), det_solution()], COMBINE_EQ)

# --- PRELAZ DETERMINISTIČKI ZADATAK → SLOBODNO PITANJE (MODEL) ----------------

add("Prelaz: aktivan deterministički zadatak nejednačine → konceptualno "
    "pitanje o promjeni smjera ide MODELU, zadatak ostaje aktivan, pa "
    "deterministički TAČAN klik.",
    ["routing", "free_form", "transition"],
    [fresh(),
     free_form("Zašto se znak nejednačine okrene kada dijelimo negativnim "
               "brojem?"),
     det_choice("correct", ["verdict_correct", "task_completed"])],
    SIGN_FLIP)

add("Prelaz: aktivan deterministički zadatak površine → konceptualno "
    "pitanje o kvadriranju faktora ide MODELU, zadatak sačuvan.",
    ["routing", "free_form", "transition"],
    [fresh(),
     free_form("Zašto se kod pretvaranja kvadratnih jedinica faktor "
               "kvadrira?")],
    UNIT_AREA)

# --- MODEL-KONTROLE NA REPREZENTATIVNO TEŠKIM LEKCIJAMA -----------------------

add("Model-kontrola (tekstualni zadaci s razlomcima, 6. razred): svjež "
    "zadatak — Tutor+Recenzent, najviše 2 poziva.",
    ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)],
    MODEL_FRACTION_WORDS)

add("Model-kontrola (jednostavni kamatni račun, 8. razred): svjež zadatak.",
    ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)], MODEL_INTEREST)

add("Model-kontrola (sređivanje razlomljenog racionalnog izraza, 9. "
    "razred): svjež zadatak — namjerno zahtjevan model-sadržaj.",
    ["model_control"],
    [task_step("Daj mi zadatak.", MODEL_TASK_CHECKS, 2)],
    MODEL_RATIONAL_EXPR)

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
               encoding="utf-8")
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
deterministic = sum(1 for row in rows
                    if all(step["expect_calls"] == 0 for step in row["steps"]))
print(f"{OUT}: {len(rows)} scenarios ({deterministic} deterministic), "
      f"at most {maximum} model calls")
