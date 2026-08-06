"""Generator kombinovanog ciljanog talasa F4F — stabilizacija Practice puta.

    python tools/practice_eval/scenarios/family/wave_f4f.jsonl.py

Zašto postoji: Faza 4F je u jednom paketu popravila pet potvrđenih defekata
Practice toka. Ovaj talas ih pokriva ZAJEDNO, prije skupe pune A+B kampanje.

  A  presjecanje recenzentovog izlaza (živi gate 458d12a, harder_level2):
     zajednički budžet od 2500 i pogrešna klasifikacija presjeka;
  B  „Daj mi novi zadatak.“ je vraćao ISTI zadatak i iste opcije, jer je
     zaštita poredila potpis koji MODEL deklariše, a ne vidljivi paket;
  C  crvena oznaka prethodnog klika je preživljavala u novi zadatak;
  D  sigurna greška je brisala kartice iako je server zadatak sačuvao;
  F  produkcija je radila bez obje release zastavice.

Težište je na scenarijima koji tjeraju recenzenta na KOMPLETNU ispravku (tu je
izlaz najveći i tu je presjecanje pucalo), i na eksplicitnim zahtjevima za nov
zadatak (tu se duplikat vidi).

Redoslijed odgovora i UI stanje se dokazuju frontend testovima
(tests/frontend/), ne ovdje — runner nema pregledač i ne izmišljamo mu podršku.

Oblici koji SMIJU ostati nedokazivi (negacija, disjunkcija) nemaju `published`
u provjerama: i preformulisan objavljen zadatak i sigurno odbijanje su ispravni.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f4f.jsonl"

TOPIC, GRADE, OBLAST = "6-03-004", 6, "Djeljivost brojeva"
OTHER_TOPIC, OTHER_OBLAST = "6-03-003", "Djeljivost brojeva"
# Druge oblasti/razredi — popravke su globalne, ne vezane za djeljivost.
FRACTIONS = ("6-04-009", 6, "Razlomci")
EQUATIONS = ("9-04-003", 9, "Linearne jednačine i nejednačine")

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "calls_at_most:2",
]
# Nov zadatak MORA biti drugačiji od aktivnog — srž nalaza B.
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


def scenario(sid, reason, tags, steps, importance="critical",
             topic=TOPIC, oblast=OBLAST, grade=GRADE):
    return {"id": sid, "wave": "F4F", "importance": importance, "grade": grade,
            "oblast": oblast, "topic_id": topic, "reason": reason,
            "tags": list(tags), "steps": steps}


rows = []
n = 0


def add(*args, **kwargs):
    global n
    n += 1
    rows.append(scenario(f"F{n:02d}", *args, **kwargs))


# --- B: DUPLIKAT NAKON EKSPLICITNOG „NOVI ZADATAK“ (produkcijski niz) ------
for index in range(3):
    add("Doslovan produkcijski niz: zadatak → pogrešan klik → „Daj mi novi zadatak.“ "
        "U produkciji je posljednji korak vratio ISTI zadatak s ISTE četiri opcije. "
        f"Ponavlja se radi mjerenja stope (ponavljanje {index + 1} od 3).",
        ["p0b", "duplicate_task", "production_replica", f"repeat_{index + 1}_of_3"],
        [task_step("Daj mi jedan zadatak za vježbu iz ove teme."),
         choice_step("wrong", ["verdict_incorrect", "reveal_absent", "no_answer_leak"]),
         task_step("Daj mi novi zadatak.", NEW_TASK_CHECKS)])

add("Dva uzastopna zahtjeva za nov zadatak bez ijednog klika: svaki mora biti "
    "kanonski drugačiji od prethodnog.",
    ["duplicate_task", "repeated_new_task"],
    [task_step("Daj mi zadatak."),
     task_step("Daj mi novi zadatak.", NEW_TASK_CHECKS),
     task_step("Daj mi još jedan zadatak.", NEW_TASK_CHECKS)])

add("Nov zadatak nakon hinta — hint ne smije ni sačuvati ni pokvariti identitet.",
    ["duplicate_task", "hint_then_new"],
    [task_step("Daj mi zadatak."), hint_step(),
     task_step("Daj mi novi zadatak.", NEW_TASK_CHECKS)])

add("Nov zadatak nakon punog rješenja.",
    ["duplicate_task", "solution_then_new"],
    [task_step("Daj mi zadatak."), solution_step(),
     task_step("Daj mi novi zadatak.", NEW_TASK_CHECKS)])

add("Nov zadatak na DRUGOJ lekciji: kanonska jedinstvenost je globalno pravilo.",
    ["duplicate_task", "other_lesson"],
    [task_step("Daj mi zadatak."),
     task_step("Daj mi novi zadatak.", NEW_TASK_CHECKS)],
    topic=OTHER_TOPIC, oblast=OTHER_OBLAST)

add("Nov zadatak u drugoj oblasti i razredu — popravka nije vezana za djeljivost.",
    ["duplicate_task", "other_oblast"],
    [task_step("Daj mi zadatak."),
     task_step("Daj mi novi zadatak.", NEW_TASK_CHECKS)],
    topic=EQUATIONS[0], grade=EQUATIONS[1], oblast=EQUATIONS[2])

# --- Intent/težina: uvod ne smije protivrječiti stvarnoj akciji -----------
add("Lakši zadatak na nivou 1: nema nižeg nivoa. Zadatak svejedno mora biti "
    "kanonski drugačiji, a nijedna provjera ne smije pasti.",
    ["difficulty", "easier_at_level_1"],
    [task_step("Daj mi zadatak."),
     task_step("Daj mi lakši zadatak.", NEW_TASK_CHECKS,
               difficulty_request="easier")])

add("Teži pa lakši: prelaz 1 → 2 → 1 uz kanonski različite zadatke.",
    ["difficulty", "harder_then_easier"],
    [task_step("Daj mi zadatak."),
     task_step("Daj mi teži zadatak.", NEW_TASK_CHECKS, difficulty_request="harder"),
     task_step("Daj mi lakši zadatak.", NEW_TASK_CHECKS, difficulty_request="easier")])

add("Teži zadatak dva puta zaredom — replika uloge harder_level2 iz živog gate-a, "
    "gdje je recenzentov izlaz bio presječen.",
    ["reviewer_budget", "gate_replica", "harder"],
    [task_step("Daj mi zadatak."),
     task_step("Daj mi teži zadatak.", NEW_TASK_CHECKS, difficulty_request="harder"),
     task_step("Daj mi još teži zadatak.", NEW_TASK_CHECKS, difficulty_request="harder")])

# --- A: SCENARIJI KOJI TJERAJU KOMPLETNU ISPRAVKU RECENZENTA --------------
for index, message in enumerate((
    "Daj mi zadatak u kojem broj mora biti djeljiv i sa 6 i sa 25.",
    "Daj mi teži zadatak s dva pravila djeljivosti odjednom.",
    "Daj mi zadatak u kojem broj mora biti djeljiv i sa 4 i sa 9.",
    "Daj mi zadatak u kojem broj mora biti djeljiv i sa 3 i sa 10.",
), start=1):
    add("Uslov s dva djelioca često natjera recenzenta na KOMPLETNU ispravku — "
        "tu je njegov izlaz najveći i tu je presjecanje pucalo. Objavljen MCQ mora "
        "imati tačno jednu tačnu opciju.",
        ["reviewer_budget", "correction_heavy", "divisibility_pair"],
        [task_step(message)])

add("Zadatak s tri pravila djeljivosti — najduži očekivan recenzentov paket.",
    ["reviewer_budget", "correction_heavy"],
    [task_step("Daj mi zadatak s tri pravila djeljivosti: sa 2, sa 3 i sa 5.")],
    importance="high")

add("Zadatak s cifrom-mjestodržačem: orakl mora preskočiti, a paket biti ispravan.",
    ["mcq_integrity", "digit_placeholder"],
    [task_step("Daj mi zadatak u kojem treba dopuniti cifru tako da broj bude "
               "djeljiv i sa 6 i sa 25.")])

add("Jedno pravilo djeljivosti — kontrola da stroža pravila ne ruše prost zadatak.",
    ["mcq_integrity", "control"],
    [task_step("Daj mi jednostavan zadatak o djeljivosti sa 6.")])

for index, message in enumerate((
    "Daj mi zadatak u kojem se traži broj koji NIJE djeljiv sa 9.",
    "Daj mi zadatak u kojem broj treba biti djeljiv sa 4 ili sa 6.",
), start=1):
    add("Negacija i disjunkcija ostaju nedokazive za orakl: ispravna su OBA ishoda "
        "(preformulisan objavljen zadatak ili sigurno odbijanje). Mjeri se samo da "
        "ništa nije procurilo i da nema trećeg poziva.",
        ["mcq_integrity", "unprovable_condition", "safety_only"],
        [{"kind": "text", "message": message, "expect_calls": 2,
          "checks": list(SAFETY_CHECKS), "rubrics": []}],
        importance="high")

# --- Recenzentska ispravka u drugoj oblasti (dužina paketa) ---------------
add("Razlomci: aktivan semantički ugovor tjera recenzenta na precizan paket.",
    ["reviewer_budget", "other_oblast"],
    [task_step("Daj mi zadatak."), task_step("Daj mi teži zadatak.", NEW_TASK_CHECKS,
                                             difficulty_request="harder")],
    topic=FRACTIONS[0], grade=FRACTIONS[1], oblast=FRACTIONS[2])

add("Linearne jednačine, 9. razred: najduži tekstovi zadataka u kurikulumu.",
    ["reviewer_budget", "other_oblast"],
    [task_step("Daj mi zadatak."), task_step("Daj mi teži zadatak.", NEW_TASK_CHECKS,
                                             difficulty_request="harder")],
    topic=EQUATIONS[0], grade=EQUATIONS[1], oblast=EQUATIONS[2])

# --- Kontinuitet aktivnog zadatka -----------------------------------------
add("Hint pa rješenje: aktivan zadatak mora preživjeti oba koraka.",
    ["continuity", "hint_solution"],
    [task_step("Daj mi zadatak."), hint_step(), solution_step()])

add("Pogrešan pa tačan klik: označena opcija je serverska činjenica i ne mijenja se.",
    ["continuity", "choice_grading"],
    [task_step("Daj mi zadatak."),
     choice_step("wrong", ["verdict_incorrect", "reveal_absent",
                           "task_not_completed", "no_answer_leak"]),
     choice_step("correct", ["verdict_correct", "task_completed"])])

add("Klik nakon rješenja mora biti blokiran bez ijednog poziva i bez mutacije.",
    ["continuity", "solution_then_click"],
    [task_step("Daj mi zadatak."), solution_step(),
     {"kind": "choice", "select": "a", "expect_calls": 0, "requires_active_task": True,
      "checks": ["not_published", "zero_calls", "state_unchanged", "response_schema"],
      "rubrics": []}])

add("Idempotentan retry klika: identičan odgovor, nula poziva, bez mutacije.",
    ["continuity", "idempotency"],
    [task_step("Daj mi zadatak."),
     choice_step("correct", ["verdict_correct", "task_completed"]),
     {"kind": "repeat_choice", "select": "correct", "expect_calls": 0,
      "requires_active_task": True,
      "checks": ["zero_calls", "identical_response", "response_schema"], "rubrics": []}])

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
               encoding="utf-8")
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, at most {maximum} model calls")
