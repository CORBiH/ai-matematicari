"""Generator ciljanog talasa F4G — spremnost sedam lekcija u opsegu Faze 4G.

    python tools/practice_eval/scenarios/family/wave_f4g.jsonl.py

Zašto postoji: Faza 4G je zatvorila četiri globalne klase defekata i talas ih
pokriva ZAJEDNO na sedam lekcija u opsegu, prije završnog release gatea:

  A  namjerno lažna jednakost (dokaz kontradikcije) — živi gate 5ac723e,
     scenario grade9: rješenje sistema bez rješenja padalo je kao
     `numeric_equality_mismatch` i lekcija je bila neobjavljiva;
  B  recenzent bez tačnog koda mathsafe defekta za `unsafe_..._notation`
     (A+B ab-5ac723e: 5 od 13 turnova s unchanged=True);
  C  vezničke varijante složenog uslova djeljivosti („i istovremeno sa“,
     „sa brojem“, „, ali i“, „te“) padale kao nečitljiv uslov;
  D  novi uski orakli: direktan račun (vrijednost vidljivog izraza mora biti
     tačno jedna opcija) i poređenje (znak / najveći / najmanji).

Oblici koji SMIJU ostati nedokazivi (negacija, disjunkcija) nemaju `published`
u provjerama: i preformulisan objavljen zadatak i sigurno odbijanje su ispravni.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f4g.jsonl"

DIVISIBILITY = ("6-03-004", 6, "Djeljivost brojeva")
LIKE = ("6-04-009", 6, "Razlomci")
UNLIKE = ("6-04-010", 6, "Razlomci")
MULTIPLY = ("6-04-011", 6, "Razlomci")
DIVIDE = ("6-04-012", 6, "Razlomci")
COMPARE = ("7-03-006", 7, "Racionalni brojevi")
NO_SOLUTION = ("9-05-010", 9, "Sistemi linearnih jednačina")

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "calls_at_most:2",
]
NEW_TASK_CHECKS = TASK_CHECKS + ["task_differs"]

HELP_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "no_new_task", "task_preserved", "calls_at_most:2",
]
HINT_CHECKS = HELP_CHECKS + ["help_nonempty", "hint_no_leak", "no_answer_leak",
                             "reveal_absent", "task_not_completed"]
SOLUTION_CHECKS = HELP_CHECKS + ["solution_complete", "reveal_present", "task_completed"]

SAFETY_CHECKS = ["response_schema", "no_leak", "no_control_chars", "math_safe",
                 "bosnian", "calls_at_most:2"]
TASK_RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def task_step(message, checks=None, rubrics=TASK_RUBRICS, calls=2, **extra):
    step = {"kind": "text", "message": message, "expect_calls": calls,
            "checks": list(checks or TASK_CHECKS), "rubrics": list(rubrics)}
    step.update(extra)
    return step


def hint_step():
    return {"kind": "text", "message": "Ne znam.", "intent": "hint_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "expect_calls": 2,
            "checks": list(HINT_CHECKS), "rubrics": ["hint_usefulness"]}


def solution_step():
    return {"kind": "text", "message": "Uradi ga ti.", "intent": "solution_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "expect_calls": 2,
            "checks": list(SOLUTION_CHECKS), "rubrics": ["pedagogy"]}


def choice_step(select, extra, calls=1):
    return {"kind": "choice", "select": select, "expect_calls": calls,
            "requires_active_task": True,
            "checks": ["response_schema", "not_safe_error", "no_fallback_text",
                       "no_leak", "no_control_chars", "math_safe", "bosnian",
                       "correct_option_stable", f"calls_at_most:{calls}"] + list(extra),
            "rubrics": []}


rows = []
n = 0


def add(reason, tags, steps, lesson, importance="critical"):
    global n
    n += 1
    topic, grade, oblast = lesson
    rows.append({"id": f"G{n:02d}", "wave": "F4G", "importance": importance,
                 "grade": grade, "oblast": oblast, "topic_id": topic,
                 "reason": reason, "tags": list(tags), "steps": steps})


# --- 6-03-004: DJELJIVOST --------------------------------------------------

add("Običan MCQ o djeljivosti: zadatak, pogrešan klik (bez otkrivanja), hint "
    "vezan za aktivni zadatak.",
    ["divisibility", "ordinary_flow"],
    [task_step("Daj mi jedan zadatak za vježbu iz ove teme."),
     choice_step("wrong", ["verdict_incorrect", "reveal_absent", "no_answer_leak"]),
     hint_step()],
    DIVISIBILITY)

add("Cijelo rješenje na zahtjev: potpun postupak, zadatak ostaje isti.",
    ["divisibility", "solution_flow"],
    [task_step("Daj mi zadatak."), solution_step()],
    DIVISIBILITY)

add("Doslovan produkcijski zahtjev sa složenim uslovom (i sa 6 i sa 25): "
    "objavljen MCQ mora imati tačno jednu tačnu opciju (djeljivu sa 150), pa "
    "pogrešan klik, pa kanonski različit nov zadatak.",
    ["divisibility", "compound_6_25", "production_replica"],
    [task_step("Daj mi MCQ zadatak gdje broj mora biti djeljiv i sa 6 i sa 25."),
     choice_step("wrong", ["verdict_incorrect", "reveal_absent", "no_answer_leak"]),
     task_step("Daj mi novi zadatak.", NEW_TASK_CHECKS)],
    DIVISIBILITY)

add("Veznička varijanta „i istovremeno sa“ — Faza 4G je čita kao potpun uslov.",
    ["divisibility", "compound_wording"],
    [task_step("Daj mi zadatak gdje broj mora biti djeljiv sa 6 i istovremeno sa 25.")],
    DIVISIBILITY)

add("Veznička varijanta „ali i sa“ — Faza 4G je čita kao potpun uslov.",
    ["divisibility", "compound_wording"],
    [task_step("Daj mi zadatak gdje broj mora biti djeljiv sa 4, ali i sa 9.")],
    DIVISIBILITY)

add("Negativna formulacija ostaje nedokaziva za orakl: ispravna su OBA ishoda "
    "(preformulisan objavljen zadatak ili sigurno odbijanje).",
    ["divisibility", "negation", "safety_only"],
    [{"kind": "text",
      "message": "Daj mi zadatak u kojem se traži broj koji NIJE djeljiv sa 9.",
      "expect_calls": 2, "checks": list(SAFETY_CHECKS), "rubrics": []}],
    DIVISIBILITY, importance="high")

add("Teži pa lakši: prelaz 1 → 2 → 1 uz kanonski različite zadatke i istinit uvod.",
    ["divisibility", "difficulty"],
    [task_step("Daj mi zadatak."),
     task_step("Daj mi teži zadatak.", NEW_TASK_CHECKS, difficulty_request="harder"),
     task_step("Daj mi lakši zadatak.", NEW_TASK_CHECKS, difficulty_request="easier")],
    DIVISIBILITY)

# --- 6-04-009: JEDNAKI IMENIOCI -------------------------------------------

add("Puni tok lekcije jednakih imenilaca: zadatak (jednaki imenioci po ugovoru), "
    "pogrešan klik, hint, cijelo rješenje. Orakl direktnog računa nadzire da je "
    "tačno jedna opcija vrijednost izraza.",
    ["fractions", "like_denominators", "full_flow"],
    [task_step("Daj mi zadatak."),
     choice_step("wrong", ["verdict_incorrect", "reveal_absent", "no_answer_leak"]),
     hint_step(), solution_step()],
    LIKE)

# --- 6-04-010: RAZLIČITI IMENIOCI -----------------------------------------

add("Različiti imenioci: ugovor blokira jednake, rješenje koristi zajednički "
    "imenilac, rezultat ispravno pojednostavljen.",
    ["fractions", "unlike_denominators"],
    [task_step("Daj mi zadatak.")],
    UNLIKE)

add("Teži zadatak različitih imenilaca (uzajamno prosti imenioci po ugovoru).",
    ["fractions", "unlike_denominators", "difficulty"],
    [task_step("Daj mi zadatak."),
     task_step("Daj mi teži zadatak.", NEW_TASK_CHECKS, difficulty_request="harder")],
    UNLIKE)

# --- 6-04-011: MNOŽENJE ----------------------------------------------------

add("Množenje razlomaka: operacija mora biti množenje, nikad zbir ili dijeljenje.",
    ["fractions", "multiplication", "operation_fidelity"],
    [task_step("Daj mi zadatak.")],
    MULTIPLY)

add("Množenje s rezultatom koji se može skratiti: skraćivanje mora biti tačno.",
    ["fractions", "multiplication", "reducible"],
    [task_step("Daj mi zadatak množenja dva razlomka gdje se rezultat može skratiti.")],
    MULTIPLY)

# --- 6-04-012: DIJELJENJE --------------------------------------------------

add("Dijeljenje razlomaka: djelilac nije nula, recipročan korak tačan.",
    ["fractions", "division", "operation_fidelity"],
    [task_step("Daj mi zadatak.")],
    DIVIDE)

add("Razlomak podijeljen razlomkom: rezultat mora odgovarati originalnom izrazu.",
    ["fractions", "division", "reciprocal"],
    [task_step("Daj mi zadatak gdje se razlomak dijeli razlomkom.")],
    DIVIDE)

# --- 7-03-006: UPOREĐIVANJE -----------------------------------------------

add("Upoređivanje racionalnih brojeva: običan zadatak lekcije; orakl poređenja "
    "nadzire znak-oblik i superlativ-oblik.",
    ["comparison", "ordinary_flow"],
    [task_step("Daj mi zadatak.")],
    COMPARE)

add("Izričito poređenje dva razlomka različitih imenilaca.",
    ["comparison", "unlike_denominators"],
    [task_step("Daj mi zadatak gdje treba uporediti dva razlomka različitih "
               "imenilaca.")],
    COMPARE)

# --- 9-05-010: SISTEM BEZ RJEŠENJA ----------------------------------------

add("TAČNA replika uloge koja je oborila prethodni gate (grade9, fresh "
    "generate_task): rješenje smije dokazati kontradikciju lažnom jednakošću "
    "uz izričitu izjavu da nije tačna — paket se sada objavljuje.",
    ["no_solution", "gate_replica"],
    [task_step("Daj mi zadatak.")],
    NO_SOLUTION)

add("Sistem bez rješenja + cijelo rješenje: kontradikcijski postupak mora "
    "proći numeričku provjeru i stići do učenika.",
    ["no_solution", "solution_flow"],
    [task_step("Daj mi zadatak."), solution_step()],
    NO_SOLUTION)

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
               encoding="utf-8")
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, at most {maximum} model calls")
