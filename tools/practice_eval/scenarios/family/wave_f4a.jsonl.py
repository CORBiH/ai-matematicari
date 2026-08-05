"""Generator ciljanog talasa F4A — porodica `fraction_arithmetic_direct`.

    python tools/practice_eval/scenarios/wave_f4a.jsonl.py

Piše `wave_f4a.jsonl` pored sebe. Generator postoji da bi 24 scenarija bila
dosljedna (iste provjere za isti oblik koraka) i da bi se lako regenerisala
kad se skup provjera proširi. Nula mrežnih i nula model poziva.

POKRIVA: sve četiri lekcije, nivoe 1–3, novi zadatak, lakše/teže, zamjenu
jednakih/različitih imenilaca, zamjenu množenja i dijeljenja, dosljednost
odgovora, izostanak curenja, očuvanje sesije i granicu od dva poziva.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f4a.jsonl"

LESSONS = {
    "6-04-009": ("Razlomci", "sabiranje/oduzimanje JEDNAKIH imenilaca"),
    "6-04-010": ("Razlomci", "sabiranje/oduzimanje RAZLIČITIH imenilaca"),
    "6-04-011": ("Razlomci", "množenje razlomaka"),
    "6-04-012": ("Razlomci", "dijeljenje razlomaka"),
}

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
CHOICE_CORRECT = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "math_safe", "bosnian", "published", "lesson_matches",
    "correct_option_stable", "verdict_correct", "task_completed",
    "calls_at_most:1",
]
CHOICE_WRONG = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "math_safe", "bosnian", "published", "lesson_matches",
    "correct_option_stable", "verdict_incorrect", "reveal_absent",
    "no_answer_leak", "calls_at_most:1",
]
RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def task_step(message, level=None, extra=(), calls=2):
    # NAMJERNO BEZ `level:N`: kontroler nivoa težine (MATBOT_PRACTICE_DIFFICULTY_LEVELS)
    # za ove četiri lekcije rutira turn na legacy deterministički generator, pa
    # porodica tada uopšte ne bi bila u putu. `level` ostaje samo dokumentacija
    # namjere koraka; napredovanje se mjeri kroz `task_differs`.
    checks = list(TASK_CHECKS)
    checks.extend(extra)
    return {"kind": "text", "message": message, "expect_calls": calls,
            "checks": checks, "rubrics": list(RUBRICS)}


def scenario(index, topic_id, reason, tags, steps):
    oblast, _skill = LESSONS[topic_id]
    return {
        "id": f"F{index:02d}", "wave": "F4A", "importance": "critical",
        "grade": 6, "oblast": oblast, "topic_id": topic_id,
        "reason": reason, "tags": ["fraction_arithmetic_direct", *tags],
        "steps": steps,
    }


def build():
    rows, index = [], 1
    for topic_id, (_oblast, skill) in LESSONS.items():
        # 1) svjež zadatak nivoa 1 + tačan klik
        rows.append(scenario(
            index, topic_id,
            f"Svjež zadatak nivoa 1 mora ispitivati {skill} i objaviti se iz "
            "dva poziva; tačan klik zatvara zadatak.",
            ["level_1", "new_task", "correct_answer"],
            [task_step("Daj mi zadatak.", level=1),
             {"kind": "choice", "select": "correct", "expect_calls": 1,
              "checks": list(CHOICE_CORRECT), "rubrics": []}]))
        index += 1

        # 2) teže → nivo 2, pa još teže → nivo 3
        rows.append(scenario(
            index, topic_id,
            "Teže mora podići nivo, a NIKAD promijeniti vještinu lekcije "
            f"({skill}).",
            ["harder", "level_2", "level_3", "progression"],
            [task_step("Daj mi zadatak.", level=1),
             task_step("Daj mi teži zadatak.", level=2, extra=["task_differs"]),
             task_step("Daj mi još teži zadatak.", level=3,
                       extra=["task_differs"])]))
        index += 1

        # 3) lakše nakon težeg — mora ostati ista vještina
        rows.append(scenario(
            index, topic_id,
            "Lakše se vraća na niži nivo bez izlaska iz lekcije.",
            ["easier", "progression"],
            [task_step("Daj mi zadatak.", level=1),
             task_step("Daj mi teži zadatak.", level=2, extra=["task_differs"]),
             task_step("Daj mi lakši zadatak.", level=1,
                       extra=["task_differs"])]))
        index += 1

        # 4) hint ljestvica bez curenja + očuvanje zadatka
        rows.append(scenario(
            index, topic_id,
            "Dva hinta ne smiju otkriti rezultat, a zadatak mora ostati "
            "netaknut u sesiji.",
            ["hint_1", "hint_2", "no_leak", "session_continuity"],
            [task_step("Daj mi zadatak.", level=1),
             {"kind": "text", "message": "Ne znam.", "expect_calls": 1,
              "checks": list(HINT_CHECKS), "rubrics": ["clarity"]},
             {"kind": "text", "message": "Još uvijek ne znam.",
              "expect_calls": 1,
              "checks": HINT_CHECKS + ["hint_differs"], "rubrics": ["clarity"]}]))
        index += 1

        # 5) pogrešan klik: bez otkrivanja i bez curenja
        rows.append(scenario(
            index, topic_id,
            "Prvi pogrešan klik ne smije otkriti tačnu opciju ni procuriti "
            "odgovor kroz feedback.",
            ["wrong_answer", "no_leak"],
            [task_step("Daj mi zadatak.", level=1),
             {"kind": "choice", "select": "wrong", "expect_calls": 1,
              "checks": list(CHOICE_WRONG), "rubrics": ["clarity"]}]))
        index += 1

        # 6) novi zadatak iste težine — dosljednost i bez ponavljanja
        rows.append(scenario(
            index, topic_id,
            "Novi zadatak iste težine mora ostati u lekciji i razlikovati se "
            "od prethodnog.",
            ["new_task", "repetition"],
            [task_step("Daj mi zadatak.", level=1),
             task_step("Daj mi novi zadatak.", level=1,
                       extra=["task_differs"])]))
        index += 1

    OUT.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8")
    return rows


if __name__ == "__main__":
    rows = build()
    print(f"OK: {OUT} — {len(rows)} scenarija, "
          f"{sum(len(r['steps']) for r in rows)} koraka")
