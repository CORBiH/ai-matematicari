"""Generator ciljanog talasa F4D — granica dokaza orakla djeljivosti (Faza 4D).

    python tools/practice_eval/scenarios/family/wave_f4d.jsonl.py

Zašto postoji: živi gate na 93ad85c pao je na ISPRAVNOM paketu jer je orakl
djeljivosti tražio „sa N“ ODMAH iza riječi „djeljiv“, pa je jedan prilog
(„djeljiv ISTOVREMENO sa 25 i sa 6“) dao prazan skup djelilaca i cio turn je
odbijen kao `divisibility_condition_ambiguous`. Commit e07f267 je to popravio
uskim, ZATVORENIM skupom priloga.

Talas cilja tačno tu promijenjenu granicu, na lekciji i ulozi koje je gate
koristio, plus okolinu koja NE SMIJE postati dokaziva:

  • replikacija gate uloge `harder_level2` (isti prelaz nivoa 1 → 2);
  • izričit zahtjev za istovremenu djeljivost (prilog i običan veznik);
  • negacija i disjunkcija — smiju samo sigurno pasti, nikad lažno dokazati;
  • zadatak s cifrom-mjestodržačem — orakl mora PRESKOČITI, ne pogađati;
  • saglasnost označene opcije, očuvanje stanja i granica od dva poziva.

Negacija/disjunkcija namjerno NEMAJU `published` u provjerama: i objavljen
preformulisan pozitivan zadatak i sigurno odbijanje su ispravni ishodi, pa se
ti redovi klasifikuju ručno. Provjerava se samo da ništa nije procurilo, da
stanje nije mutirano i da nema trećeg poziva.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f4d.jsonl"

# Lekcija i razred koje koristi CORE_DIVISIBILITY u živom release gate-u.
TOPIC, GRADE, OBLAST = "6-03-004", 6, "Djeljivost brojeva"

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "calls_at_most:2",
]
# Za formulacije koje SMIJU ostati nedokazive (negacija, disjunkcija):
# tražimo samo sigurnost, ne objavu.
SAFETY_CHECKS = [
    "response_schema", "no_leak", "no_control_chars", "math_safe",
    "bosnian", "calls_at_most:2",
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


def task_step(message, extra=(), checks=None):
    return {"kind": "text", "message": message, "expect_calls": 2,
            "checks": list(checks if checks is not None else TASK_CHECKS) + list(extra),
            "rubrics": list(RUBRICS)}


def scenario(sid, reason, tags, steps, importance="critical"):
    return {"id": sid, "wave": "F4D", "importance": importance, "grade": GRADE,
            "oblast": OBLAST, "topic_id": TOPIC, "reason": reason,
            "tags": list(tags), "steps": list(steps)}


def build():
    rows = []

    # --- 1) REPLIKACIJA GATE ULOGE `harder_level2` --------------------------
    # Pad je bio uzorak, ne determinizam: isti put se ponavlja tri puta da se
    # vidi drži li popravka na više nezavisnih generisanja.
    for n in (1, 2, 3):
        rows.append(scenario(
            f"D{n:02d}",
            "Replikacija uloge harder_level2 iz živog gate-a (nivo 1 → 2) na "
            "lekciji na kojoj je gate pao. Teži zadatak često uvodi DVA "
            "djelioca, što je tačno formulacija koja je rušila orakl.",
            ["gate_replica", "harder_level2", "divisibility"],
            [task_step("Daj mi zadatak."),
             task_step("Daj mi teži zadatak.", ["task_differs"])]))

    # --- 2) IZRIČITA ISTOVREMENA DJELJIVOST — promijenjena granica ----------
    rows.append(scenario(
        "D04",
        "Učenik izričito traži istovremenu djeljivost s prilogom "
        "„istovremeno“ — formulacija koja je prije e07f267 obarala turn.",
        ["adverb_istovremeno", "conjunction", "changed_boundary"],
        [task_step("Daj mi zadatak u kojem broj mora biti djeljiv "
                   "istovremeno sa 25 i sa 6.")]))
    rows.append(scenario(
        "D05",
        "Isti matematički uslov bez priloga — običan veznik. Mora dati "
        "jednako valjan ishod kao D04 (dokaz da prilog ne mijenja semantiku).",
        ["plain_conjunction", "changed_boundary"],
        [task_step("Daj mi zadatak u kojem broj mora biti djeljiv sa 25 i sa 6.")]))
    rows.append(scenario(
        "D06",
        "Drugi prilog iz zatvorenog skupa („ujedno“) i drugi par djelilaca.",
        ["adverb_ujedno", "conjunction", "changed_boundary"],
        [task_step("Daj mi zadatak u kojem broj mora biti djeljiv "
                   "ujedno sa 4 i sa 9.")]))
    rows.append(scenario(
        "D07",
        "Istovremena djeljivost u SMJERU TEŽE — spaja promijenjenu granicu s "
        "prelazom nivoa na kojem je gate pao.",
        ["adverb_istovremeno", "harder_level2", "changed_boundary"],
        [task_step("Daj mi zadatak."),
         task_step("Daj mi teži zadatak u kojem broj mora biti djeljiv "
                   "istovremeno sa 25 i sa 6.", ["task_differs"])]))

    # --- 3) NEGACIJA I DISJUNKCIJA — ne smiju postati dokazive --------------
    rows.append(scenario(
        "D08",
        "Negirana djeljivost. Orakl mora ostati nedokaziv (fail-closed) ili "
        "model mora preformulisati u pozitivan oblik. Nikad lažan dokaz.",
        ["negation", "must_not_prove"],
        [task_step("Daj mi zadatak u kojem treba naći broj koji NIJE "
                   "djeljiv sa 6.", checks=SAFETY_CHECKS)]))
    rows.append(scenario(
        "D09",
        "Disjunkcija („ili“). Prilog „istovremeno“ ne smije disjunkciju "
        "pretvoriti u konjunkciju.",
        ["disjunction", "must_not_prove"],
        [task_step("Daj mi zadatak u kojem je broj djeljiv sa 25 ili sa 6.",
                   checks=SAFETY_CHECKS)]))

    # --- 4) CIFRA-MJESTODRŽAČ — orakl mora preskočiti -----------------------
    rows.append(scenario(
        "D10",
        "Zadatak s cifrom koja nedostaje. Orakl nema pravo izvoditi istinu "
        "bez uvrštavanja — mora preskočiti, a paket i dalje mora biti valjan.",
        ["digit_placeholder", "proof_boundary"],
        [task_step("Daj mi zadatak u kojem treba pronaći cifru koja "
                   "nedostaje da bi broj bio djeljiv sa 9.")]))

    # --- 5) SAGLASNOST OZNAČENE OPCIJE, CURENJE I STANJE --------------------
    rows.append(scenario(
        "D11",
        "Pogrešan klik: verdikt mora biti netačan, tačna opcija stabilna, bez "
        "otkrivanja odgovora i bez mutacije aktivnog zadatka.",
        ["marked_option", "wrong_answer", "no_leak"],
        [task_step("Daj mi zadatak."),
         {"kind": "choice", "select": "wrong", "expect_calls": 1,
          "checks": list(CHOICE_WRONG), "rubrics": ["clarity"]}]))
    rows.append(scenario(
        "D12",
        "Pomoć ne smije otkriti odgovor niti promijeniti aktivni zadatak.",
        ["hint", "no_leak", "state"],
        [task_step("Daj mi zadatak."),
         {"kind": "text", "message": "Ne znam.", "expect_calls": 1,
          "checks": list(HINT_CHECKS), "rubrics": ["clarity"]}]))

    # --- 6) OSTATAK MAŠINE STANJA NA ISTOJ LEKCIJI --------------------------
    rows.append(scenario(
        "D13",
        "Novi zadatak istog nivoa — potpis se mora razlikovati, nivo ostati 1.",
        ["same_level_new", "signature"],
        [task_step("Daj mi zadatak."),
         task_step("Daj mi novi zadatak.", ["task_differs"])]))
    rows.append(scenario(
        "D14",
        "Svjež zadatak nivoa 1 na lekciji djeljivosti — osnovna objava.",
        ["fresh_level1"],
        [task_step("Daj mi zadatak.")]))

    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    return rows


if __name__ == "__main__":
    rows = build()
    steps = sum(len(r["steps"]) for r in rows)
    calls = sum(s["expect_calls"] for r in rows for s in r["steps"])
    print(f"OK: {OUT} — {len(rows)} scenarija, {steps} koraka, ~{calls} poziva")
