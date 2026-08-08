"""Generator ciljanog talasa F5J — pouzdanost recenzentovog dokaza težine.

    python tools/practice_eval/scenarios/family/wave_f5j.jsonl.py

Poslije F5J fiksa (oba prompta dobijaju server-vlasničke numeričke pragove
aktivnog cilja + kalibrisanu semantiku brojanja), mali prirodni model-talas
dokazuje da recenzentov `approve` više ne protivrječi vlastitom dokazu:

  • 9-04-010 — TAČNO lekcija živog pada kapije (svjež pa nov zadatak);
  • 6-04-001 — model-jezgro kapije;
  • 7-04-022 — lekcijski-relativan (formula) profil na model-putu;
  • 7-02-002 — nepovezana strogo-globalna L1 kontrola.

Sistemsko-profilna model-lekcija NE POSTOJI (jedina, 9-05-013, je
deterministička od Batch #4) — taj zahtjev je ispunjen prazno.

ROUTE PREFLIGHT: sve četiri lekcije moraju biti BEZ semantičkog ugovora.
Budžet: najviše 10 poziva (plafon talasa 12).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from matbot.semantics import contracts as semantic_contracts  # noqa: E402

OUT = Path(__file__).resolve().parent / "wave_f5j.jsonl"

MODEL_LESSONS = {
    "9-04-010": (9, "Linearne jednačine i nejednačine"),
    "6-04-001": (6, "Razlomci"),
    "7-04-022": (7, "Ugao i trougao"),
    "7-02-002": (7, "Cijeli brojevi"),
}
for lesson_id in MODEL_LESSONS:
    if semantic_contracts.contract_for(lesson_id) is not None:
        raise SystemExit(f"ROUTE PREFLIGHT: {lesson_id} nije model-lekcija")

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "calls_at_most:2",
]
RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def fresh(message="Daj mi zadatak."):
    return {"kind": "text", "message": message, "expect_calls": 2,
            "checks": list(TASK_CHECKS), "rubrics": list(RUBRICS)}


def scenario(sid, lesson_id, reason, tags, steps):
    grade, oblast = MODEL_LESSONS[lesson_id]
    return {"id": sid, "wave": "F5J", "importance": "critical",
            "grade": grade, "oblast": oblast, "topic_id": lesson_id,
            "reason": reason, "tags": list(tags), "steps": steps}


rows = [
    scenario("J01", "9-04-010",
             "TAČNO lekcija živog pada: svjež nivo 1 pa nov zadatak — "
             "recenzentov dokaz mora biti dosljedan njegovoj odluci prema "
             "isporučenim pragovima.",
             ["reviewer_reliability", "regression"],
             [fresh(), {"kind": "text", "message": "Daj mi novi zadatak.",
                        "expect_calls": 2,
                        "checks": list(TASK_CHECKS) + ["task_differs"],
                        "rubrics": list(RUBRICS)}]),
    scenario("J02", "6-04-001",
             "Model-jezgro kapije: svjež nivo 1 mora ostati stabilan i "
             "poslije novog zajedničkog bloka pragova.",
             ["reviewer_reliability", "gate_core"],
             [fresh()]),
    scenario("J03", "7-04-022",
             "Lekcijski-relativan (formula) profil na model-putu: recenzent "
             "dobija profil-pragove umjesto globalnih i mjeri po njima.",
             ["reviewer_reliability", "profile"],
             [fresh()]),
    scenario("J04", "7-02-002",
             "Nepovezana strogo-globalna L1 kontrola: laka pojmovna lekcija "
             "ostaje stroga.",
             ["reviewer_reliability", "control"],
             [fresh()]),
]

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                       for row in rows), encoding="utf-8")
total = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, at most {total} model calls")
