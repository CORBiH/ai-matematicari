"""Generator ciljanog talasa F5H — lekcijski-relativna kalibracija težine.

    python tools/practice_eval/scenarios/family/wave_f5h.jsonl.py

Poslije uvođenja lekcijski-relativnih profila težine
(data/difficulty_profiles.json + matbot/difficulty_profiles.py) ovaj talas
živo provjerava da PRIRODAN svjež zahtjev nivoa 1 na lekcijama čiji
najjednostavniji legitiman zadatak iskreno prelazi globalne pragove sada
objavljuje, a da lake lekcije NISU popuštene:

  • 8-05-007 — zapremina pravilne trostrane piramide (pad završne kapije);
  • 8-04-016 — praktična Pitagora (F5F pad);
  • 9-05-013 — tekstualni zadatak sa sistemom (dva F5F pada);
  • 8-04-013 — udaljenost tačaka (ista porodica, drugi oblik) — kontrola
    profila na lekciji koja NIJE bila u padovima;
  • 6-03-010 — tekstualni zadaci iz djeljivosti — kontrola da laka lekcija
    zadržava strogu globalnu rubriku;
  • 6-04-015 — tekstualni zadaci s razlomcima — druga nepovezana kontrola;
  • 8-05-007 svjež pa „teži“ — dokaz da progresija nivoa i dalje radi.

Modelu se NIŠTA ne sugeriše o težini — testira se prirodno ponašanje.
Najviše 16 poziva; nijedan scenario ne smije preći 2 poziva po turnu.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f5h.jsonl"

PYRAMID = ("8-05-007", 8, "Prizme, piramide, površina i zapremina")
PYTHAGORAS = ("8-04-016", 8, "Pitagorina teorema i primjene u ravni")
SYSTEM_WORDS = ("9-05-013", 9, "Sistemi linearnih jednačina")
DISTANCE = ("8-04-013", 8, "Pitagorina teorema i primjene u ravni")
EASY_DIVISIBILITY = ("6-03-010", 6, "Djeljivost brojeva")
EASY_FRACTION_WORDS = ("6-04-015", 6, "Razlomci")

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "calls_at_most:2",
]
TASK_RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def fresh_step():
    return {"kind": "text", "message": "Daj mi zadatak.",
            "expect_calls": 2, "checks": list(TASK_CHECKS),
            "rubrics": list(TASK_RUBRICS)}


def harder_step():
    return {"kind": "text", "message": "Daj mi teži zadatak.",
            "expect_calls": 2, "checks": list(TASK_CHECKS) + ["task_differs"],
            "rubrics": list(TASK_RUBRICS), "difficulty_request": "harder"}


def scenario(lesson, reason, tags, steps):
    topic, grade, oblast = lesson
    return {"grade": grade, "oblast": oblast, "topic_id": topic,
            "reason": reason, "tags": list(tags), "steps": steps}


rows = []
for index, row in enumerate([
    scenario(PYRAMID,
             "Težina: svjež nivo 1 na lekciji pada završne kapije — direktna "
             "primjena formule zapremine (iskreno ops=3) sada mora biti "
             "validan lekcijski-relativan nivo 1.",
             ["model_control", "difficulty_profile", "regression"],
             [fresh_step()]),
    scenario(PYTHAGORAS,
             "Težina: svjež nivo 1 na praktičnoj Pitagori (F5F pad) — "
             "direktna primjena teoreme mora objaviti.",
             ["model_control", "difficulty_profile", "regression"],
             [fresh_step()]),
    scenario(SYSTEM_WORDS,
             "Težina: svjež nivo 1 na tekstualnom zadatku sa sistemom (dva "
             "F5F pada) — dva uslova i prevod teksta u sistem jesu vještina "
             "lekcije, dakle validan lekcijski-relativan nivo 1.",
             ["model_control", "difficulty_profile", "regression"],
             [fresh_step()]),
    scenario(DISTANCE,
             "Težina: kontrola profila na lekciji ISTE porodice koja nije "
             "bila u padovima (udaljenost tačaka — formula s više veličina).",
             ["model_control", "difficulty_profile", "control"],
             [fresh_step()]),
    scenario(EASY_DIVISIBILITY,
             "Kontrola: laka lekcija BEZ profila — nivo 1 mora ostati "
             "istinski jednostavan i uredno objaviti (globalna rubrika "
             "netaknuta).",
             ["model_control", "global_rubric", "control"],
             [fresh_step()]),
    scenario(EASY_FRACTION_WORDS,
             "Kontrola: druga nepovezana laka model-lekcija bez profila — "
             "prirodno ponašanje bez ikakve sugestije o težini.",
             ["model_control", "global_rubric", "control"],
             [fresh_step()]),
    scenario(PYRAMID,
             "Progresija: svjež nivo 1 pa izričito teži — profil ne smije "
             "srušiti smislenu tranziciju na nivo 2.",
             ["model_control", "difficulty_profile", "progression"],
             [fresh_step(), harder_step()]),
], start=1):
    row.update({"id": f"H{index:02d}", "wave": "F5H", "importance": "critical"})
    rows.append(row)

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
               encoding="utf-8")
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, at most {maximum} model calls")
