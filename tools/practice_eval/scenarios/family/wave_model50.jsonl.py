"""Generator MODEL50 talasa — svjež 50-turn model-only audit (F5K, Korak 23).

    python tools/practice_eval/scenarios/family/wave_model50.jsonl.py

Stratifikovan uzorak s TAČNO sjemenom 20260809: 25 različitih
ne-determinističkih lekcija × 2 prirodna turna (svjež + naizmjenično
teži/hint/rješenje). Stratumi: semantički ugovorene lekcije (svih šest
tipova, uključujući istorijski rizik), pa INSUFFICIENT / VISUAL / NEEDS-NEW
korpe, izbalansirano po razredima — namjerno NE testira samo lake lekcije.

Fail-closed je prihvatljiv ishod (bolji od nevjerne objave), pa scenariji
nemaju tvrdu provjeru „published“; semantiku mjeri package_clean (svjestan
ugovora) + naknadno bodovanje + ručni pregled. Budžet: plafon 100 poziva.
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from matbot import semantic_practice                          # noqa: E402
from matbot.semantics import contracts as semantic_contracts  # noqa: E402

OUT = Path(__file__).resolve().parent / "wave_model50.jsonl"
SEED = 20260809

report = json.loads((ROOT / "reference" / "curriculum" / "semantics" /
                     "deterministic_coverage_report.json").read_text(
                         encoding="utf-8"))
non_det = [entry for entry in report["lessons"]
           if entry["class"] != "DETERMINISTIC_READY"]
assert all(semantic_contracts.contract_for(e["lesson_id"]) is None
           for e in non_det)

rng = random.Random(SEED)
contracted = [e for e in non_det
              if semantic_practice.contract_for(e["lesson_id"])]
uncontracted = [e for e in non_det
                if not semantic_practice.contract_for(e["lesson_id"])]

# 11 ugovorenih: pokrij svih šest tipova, uključujući istorijske lekcije.
by_type = {}
for entry in contracted:
    contract = semantic_practice.contract_for(entry["lesson_id"])
    by_type.setdefault(contract.requirement_type, []).append(entry)
selected = []
for type_id in sorted(by_type):
    pool = sorted(by_type[type_id], key=lambda e: e["lesson_id"])
    take = {"graph_semantics": 4, "net_semantics": 3}.get(type_id, 1)
    selected.extend(rng.sample(pool, min(take, len(pool))))

# 14 neugovorenih: 6 INSUFFICIENT, 4 VISUAL, 2 CONCEPTUAL/PROOF, 2 NEEDS-NEW.
def bucket_of(entry):
    analysis = entry.get("analysis", "")
    if entry["class"] == "DETERMINISTIC_NEEDS_NEW_CAPABILITY":
        return "NEEDS_NEW"
    if entry["class"] == "VISUAL_OR_CONSTRUCTION_REQUIRED":
        return "VISUAL"
    if analysis in ("CONCEPTUAL_ONLY", "PROOF_REQUIRED"):
        return "CONCEPT_PROOF"
    return "INSUFFICIENT"


pools = {}
for entry in uncontracted:
    pools.setdefault(bucket_of(entry), []).append(entry)
for name, take in (("INSUFFICIENT", 6), ("VISUAL", 4), ("CONCEPT_PROOF", 2),
                   ("NEEDS_NEW", 2)):
    pool = sorted(pools.get(name, []), key=lambda e: e["lesson_id"])
    selected.extend(rng.sample(pool, min(take, len(pool))))

# Kad je neki stratum manji od kvote (nakon F5K je npr. skoro cio NEEDS_NEW
# graf-klaster ugovoren), dopuni iz najveće korpe do tačno 25 lekcija.
chosen_ids = {e["lesson_id"] for e in selected}
filler = [e for e in sorted(pools.get("INSUFFICIENT", []),
                            key=lambda e: e["lesson_id"])
          if e["lesson_id"] not in chosen_ids]
while len(selected) < 25 and filler:
    selected.append(filler.pop(rng.randrange(len(filler))))

assert len({e["lesson_id"] for e in selected}) == len(selected) == 25, \
    len(selected)

RISK_CHECKS = [
    "response_schema", "no_leak", "no_control_chars", "math_safe",
    "terminology_clean", "bosnian", "stays_in_lesson", "package_clean",
    "no_verdict", "calls_at_most:2",
]
RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]
FOLLOW_UPS = ("harder", "hint", "solution")

rows = []
for index, entry in enumerate(sorted(selected, key=lambda e: e["lesson_id"])):
    lesson_id = entry["lesson_id"]
    follow = FOLLOW_UPS[index % 3]
    steps = [{"kind": "text", "message": "Daj mi zadatak.", "expect_calls": 2,
              "checks": list(RISK_CHECKS), "rubrics": list(RUBRICS)}]
    if follow == "harder":
        steps.append({"kind": "text", "message": "Daj mi teži zadatak.",
                      "difficulty_request": "harder", "expect_calls": 2,
                      "checks": list(RISK_CHECKS), "rubrics": list(RUBRICS),
                      "requires_active_task": True})
    elif follow == "hint":
        steps.append({"kind": "text", "message": "Ne znam.",
                      "intent": "hint_request",
                      "interaction_phase": "practice_help",
                      "send_last_task": True, "requires_active_task": True,
                      "expect_calls": 1,
                      "checks": ["response_schema", "no_leak",
                                 "no_control_chars", "math_safe", "bosnian",
                                 "no_new_task", "task_preserved",
                                 "calls_at_most:1", "help_nonempty",
                                 "no_answer_leak"], "rubrics": []})
    else:
        steps.append({"kind": "text", "message": "Uradi ga ti.",
                      "intent": "solution_request",
                      "interaction_phase": "practice_help",
                      "send_last_task": True, "requires_active_task": True,
                      "expect_calls": 1,
                      "checks": ["response_schema", "no_leak",
                                 "no_control_chars", "math_safe", "bosnian",
                                 "no_new_task", "task_preserved",
                                 "calls_at_most:1", "solution_complete"],
                      "rubrics": []})
    contract = semantic_practice.contract_for(lesson_id)
    rows.append({"id": f"M{index + 1:02d}", "wave": "F5K-M50",
                 "importance": "critical", "grade": int(entry["grade"]),
                 "oblast": entry["oblast"], "topic_id": lesson_id,
                 "reason": (f"MODEL50 stratum: "
                            f"{contract.requirement_type if contract else bucket_of(entry)}"
                            f" — {entry['title'][:60]}"),
                 "tags": ["model50",
                          contract.requirement_type if contract
                          else bucket_of(entry)],
                 "steps": steps})

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                       for row in rows), encoding="utf-8")
turns = sum(len(row["steps"]) for row in rows)
calls = sum(step["expect_calls"] for row in rows for step in row["steps"])
grades = {}
for row in rows:
    grades[row["grade"]] = grades.get(row["grade"], 0) + 1
print(f"{OUT}: {len(rows)} lessons, {turns} turns, at most {calls} calls; "
      f"grades={grades}")
