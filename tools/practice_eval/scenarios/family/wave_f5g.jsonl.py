"""Generator ciljanog talasa F5G — provjera identiteta lekcije na model-putu.

    python tools/practice_eval/scenarios/family/wave_f5g.jsonl.py

Poslije uske normalizacije zapisa `selected_lesson_id` („Naslov (ID)“ →
goli kanonski ID, oba dijela moraju odgovarati server-vlasničkom
LessonContext-u), ovaj mali talas živo provjerava da PRIRODNO ponašanje
modela više ne izaziva lažno odbijanje identiteta, a da stvarni nesklad
identiteta ostaje nemoguć (invarijanta nije ni uklonjena ni oslabljena):

  • 8-09-004 — tačno ona lekcija na kojoj je završna kapija pala;
  • 6-04-015 — nepovezana model-lekcija (razlomci, 6. razred);
  • 8-04-016 — kontrola čistog identiteta (uredno objavljivala u F5F).

Modelu se NIŠTA ne sugeriše o formatu — testira se prirodno ponašanje.
Najviše 6 poziva; plafon talasa 8.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f5g.jsonl"

VECTOR_SCALAR = ("8-09-004", 8, "Vektori")
FRACTION_WORDS = ("6-04-015", 6, "Razlomci")
PRACTICAL = ("8-04-016", 8, "Pitagorina teorema i primjene u ravni")

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "calls_at_most:2",
]
TASK_RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def model_task(lesson, reason, tags):
    topic, grade, oblast = lesson
    return {"grade": grade, "oblast": oblast, "topic_id": topic,
            "reason": reason, "tags": list(tags),
            "steps": [{"kind": "text", "message": "Daj mi zadatak.",
                       "expect_calls": 2, "checks": list(TASK_CHECKS),
                       "rubrics": list(TASK_RUBRICS)}]}


rows = []
for index, row in enumerate([
    model_task(VECTOR_SCALAR,
               "Identitet: lekcija na kojoj je završna kapija pala zbog "
               "zapisa „Naslov (ID)“ — objava sada mora uspjeti uz tačan "
               "identitet.",
               ["model_control", "identity", "regression"]),
    model_task(FRACTION_WORDS,
               "Identitet: nepovezana model-lekcija — prirodno ponašanje bez "
               "ikakve sugestije o formatu.",
               ["model_control", "identity"]),
    model_task(PRACTICAL,
               "Identitet: kontrola čistog identiteta (uredno objavljivala u "
               "F5F).",
               ["model_control", "identity", "control"]),
], start=1):
    row.update({"id": f"G{index:02d}", "wave": "F5G", "importance": "critical"})
    rows.append(row)

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
               encoding="utf-8")
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, at most {maximum} model calls")
