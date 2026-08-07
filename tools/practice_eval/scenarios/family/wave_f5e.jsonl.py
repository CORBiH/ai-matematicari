"""Generator ciljanog talasa F5E — pouzdanost recenzentske ispravke.

    python tools/practice_eval/scenarios/family/wave_f5e.jsonl.py

Poslije popravke zapisa dijeljenja s ostatkom (mathcheck) i izričitog
zatvaranja nalaza u recenzentskom bloku, talas živo ispituje MODEL-put:

  • lekcija živog pada (tekstualni zadaci iz djeljivosti): svjež i TEŽI
    zadatak — upravo oblik na kojem je kapija pala, uzorkovan više puta;
  • nepovezane model-lekcije (tekstualni zadaci s razlomcima, kamatni račun,
    razlomljeni racionalni izrazi, geometrija) kao čiste APPROVE kontrole i
    prirodni izvor recenzentskih ispravki;
  • svaki turn: najviše 2 poziva, bez objave nevaljanog paketa, stanje
    sačuvano pri odbijanju.

Deterministički put se ovdje NE ispituje (dokazan talasom F5D) — svi
scenariji su model-scenariji.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f5e.jsonl"

GATE_LESSON = ("6-03-010", 6, "Djeljivost brojeva")
FRACTION_WORDS = ("6-04-015", 6, "Razlomci")
INTEREST = ("8-03-019", 8, "Proporcionalnost, Talesova teorema i sličnost")
RATIONAL_EXPR = ("9-01-013", 9, "Razlomljeni racionalni izrazi")
WORD_EQUATION = ("9-04-011", 9, "Linearne jednačine i nejednačine")

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed",
]
MODEL_TASK_CHECKS = TASK_CHECKS + ["calls_at_most:2"]
TASK_RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def task_step(message, calls, extra_checks=(), **extra):
    step = {"kind": "text", "message": message, "expect_calls": calls,
            "checks": MODEL_TASK_CHECKS + list(extra_checks),
            "rubrics": list(TASK_RUBRICS)}
    step.update(extra)
    return step


rows = []
n = 0


def add(reason, tags, steps, lesson, importance="critical"):
    global n
    n += 1
    topic, grade, oblast = lesson
    rows.append({"id": f"E{n:02d}", "wave": "F5E", "importance": importance,
                 "grade": grade, "oblast": oblast, "topic_id": topic,
                 "reason": reason, "tags": list(tags), "steps": steps})


add("Lekcija živog pada kapije: svjež model-zadatak dijeljenja s ostatkom — "
    "recenzentski put s popravljenim validatorom zapisa ostatka.",
    ["model_route", "remainder", "gate_lesson"],
    [task_step("Daj mi zadatak.", 2)], GATE_LESSON)

add("TAČAN OBLIK PADA: svjež pa TEŽI zadatak na lekciji dijeljenja s "
    "ostatkom — teži tekstualni zadatak najčešće traži zapis količnika i "
    "ostatka.", ["model_route", "remainder", "harder", "gate_lesson"],
    [task_step("Daj mi zadatak.", 2),
     task_step("Daj mi teži zadatak.", 2, extra_checks=("task_differs",),
               difficulty_request="harder")], GATE_LESSON)

add("Ponovljen uzorak istog oblika (nezavisna sesija): svjež pa teži.",
    ["model_route", "remainder", "harder", "gate_lesson"],
    [task_step("Daj mi zadatak.", 2),
     task_step("Daj mi teži zadatak.", 2, extra_checks=("task_differs",),
               difficulty_request="harder")], GATE_LESSON)

add("APPROVE kontrola: tekstualni zadaci s razlomcima (6. razred).",
    ["model_route", "control"],
    [task_step("Daj mi zadatak.", 2)], FRACTION_WORDS)

add("APPROVE kontrola: jednostavni kamatni račun (8. razred).",
    ["model_route", "control"],
    [task_step("Daj mi zadatak.", 2)], INTEREST)

add("Zahtjevan model-sadržaj: sređivanje razlomljenog racionalnog izraza "
    "(9. razred) — prirodan izvor recenzentskih ispravki.",
    ["model_route", "control", "hard_content"],
    [task_step("Daj mi zadatak.", 2)], RATIONAL_EXPR)

add("Tekstualni zadatak koji se svodi na linearnu jednačinu (9. razred).",
    ["model_route", "control"],
    [task_step("Daj mi zadatak.", 2)], WORD_EQUATION)

add("Još jedan svjež zadatak lekcije živog pada — dodatni uzorak zapisa "
    "ostatka.", ["model_route", "remainder", "gate_lesson"],
    [task_step("Daj mi zadatak.", 2)], GATE_LESSON)

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
               encoding="utf-8")
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, at most {maximum} model calls")
