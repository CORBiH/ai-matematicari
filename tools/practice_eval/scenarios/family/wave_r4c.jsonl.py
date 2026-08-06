"""Generator ciljanog talasa R4C — autoritet recenzentovih provjera (Faza 4C).

    python tools/practice_eval/scenarios/family/wave_r4c.jsonl.py

Cilj talasa je da uhvati TAČNO ono što je Faza 4C promijenila:
  • paket koji je deterministički ispravan više ne smije pasti zbog jedne
    savjetodavne samoprijavljene zastavice;
  • sigurnosno kritična tvrdnja i dalje mora obarati turn (fail-closed);
  • rubrika nivoa 1 mora prihvatiti minimalan zadatak POREĐENJA;
  • ništa od toga ne smije objaviti neispravan paket, promijeniti lekciju,
    procuriti odgovor, mutirati stanje pri odbijanju ni napraviti treći poziv.

Pokrivenost lekcija je namjerno šira od pilota: lekcija iz živog gate pada,
istorijska lekcija o djeljivosti, sve četiri semantičke lekcije razlomaka i
nekoliko nepilot oblasti i razreda.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_r4c.jsonl"

# (topic_id, grade, oblast, kratak opis vještine)
LESSONS = [
    ("7-03-006", 7, "Racionalni brojevi", "upoređivanje racionalnih brojeva (živi gate pad)"),
    ("6-03-004", 6, "Djeljivost brojeva", "pravila djeljivosti (istorijski gate pad)"),
    ("6-04-009", 6, "Razlomci", "sabiranje/oduzimanje jednakih imenilaca"),
    ("6-04-010", 6, "Razlomci", "sabiranje/oduzimanje različitih imenilaca"),
    ("6-04-011", 6, "Razlomci", "množenje razlomaka"),
    ("6-04-012", 6, "Razlomci", "dijeljenje razlomaka"),
    ("6-04-008", 6, "Razlomci", "upoređivanje razlomaka (nepilot, poređenje)"),
    ("8-01-008", 8, "Realni brojevi, korijeni i stepeni", "kvadratni korijen (nepilot)"),
    ("9-04-003", 9, "Linearne jednačine i nejednačine", "jednačina sa zagradama (nepilot)"),
    ("9-05-007", 9, "Sistemi linearnih jednačina", "metoda supstitucije (nepilot)"),
]

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "calls_at_most:2",
]
HINT_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "math_safe", "bosnian", "help_nonempty", "hint_no_leak",
    "task_preserved", "calls_at_most:2",
]
CHOICE_WRONG = [
    "response_schema", "not_safe_error", "no_leak", "math_safe", "bosnian",
    "published", "lesson_matches", "correct_option_stable",
    "verdict_incorrect", "reveal_absent", "no_answer_leak", "calls_at_most:1",
]
RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def task_step(message, extra=()):
    return {"kind": "text", "message": message, "expect_calls": 2,
            "checks": list(TASK_CHECKS) + list(extra), "rubrics": list(RUBRICS)}


def build():
    rows, index = [], 1
    for topic_id, grade, oblast, skill in LESSONS:
        # 1) svjež nivo 1 — pogađa rubriku nivoa 1 (uključujući poređenje)
        rows.append({
            "id": f"R{index:02d}", "wave": "F4C", "importance": "critical",
            "grade": grade, "oblast": oblast, "topic_id": topic_id,
            "reason": (f"Svjež zadatak nivoa 1 za: {skill}. Mora se objaviti iz "
                       "dva poziva, bez pada na samoprijavljenoj zastavici."),
            "tags": ["reviewer_authority", "level_1", "new_task"],
            "steps": [task_step("Daj mi zadatak.")],
        })
        index += 1

        # 2) teže → recenzentova ispravka i dokaz težine
        rows.append({
            "id": f"R{index:02d}", "wave": "F4C", "importance": "critical",
            "grade": grade, "oblast": oblast, "topic_id": topic_id,
            "reason": (f"Teži zadatak za: {skill}. Provjerava ispravku recenzenta i "
                       "dokaz težine, uz očuvanje vještine lekcije."),
            "tags": ["reviewer_authority", "harder", "correction"],
            "steps": [task_step("Daj mi zadatak."),
                      task_step("Daj mi teži zadatak.", ["task_differs"])],
        })
        index += 1

    # 3) pomoć i pogrešan klik na dvije lekcije — curenje i stanje
    for topic_id, grade, oblast, skill in (LESSONS[0], LESSONS[2]):
        rows.append({
            "id": f"R{index:02d}", "wave": "F4C", "importance": "critical",
            "grade": grade, "oblast": oblast, "topic_id": topic_id,
            "reason": (f"Pomoć i pogrešan klik za: {skill}. Bez otkrivanja odgovora "
                       "i bez mutacije aktivnog zadatka."),
            "tags": ["reviewer_authority", "hint", "wrong_answer", "no_leak"],
            "steps": [task_step("Daj mi zadatak."),
                      {"kind": "text", "message": "Ne znam.", "expect_calls": 1,
                       "checks": list(HINT_CHECKS), "rubrics": ["clarity"]},
                      {"kind": "choice", "select": "wrong", "expect_calls": 1,
                       "checks": list(CHOICE_WRONG), "rubrics": ["clarity"]}],
        })
        index += 1

    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    return rows


if __name__ == "__main__":
    rows = build()
    steps = sum(len(r["steps"]) for r in rows)
    calls = sum(s["expect_calls"] for r in rows for s in r["steps"])
    print(f"OK: {OUT} — {len(rows)} scenarija, {steps} koraka, ~{calls} poziva")
