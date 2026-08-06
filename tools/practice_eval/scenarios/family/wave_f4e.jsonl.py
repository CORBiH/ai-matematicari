"""Generator ciljanog talasa F4E — dva P0 nalaza iz produkcije (Faza 4E).

    python tools/practice_eval/scenarios/family/wave_f4e.jsonl.py

Zašto postoji: ručni produkcijski smoke test je dokazao DVA P0 defekta koje
uzorkovani release gate nije uhvatio.

P0-A — objavljen MCQ bez ijednog tačnog odgovora:
    „Primijeni pravila djeljivosti: koji od sljedećih brojeva je djeljiv i sa
     6 i sa 25?“   opcije 8 · 6 · 7 · 9
    Broj djeljiv i sa 6 i sa 25 djeljiv je sa NZS(6,25)=150 — nijedna opcija.
    Uzrok: parser liste djelilaca je krnje čitanje uzimao kao potpun uslov, pa
    je orakl AKTIVNO potvrdio pogrešnu opciju kao jedinu tačnu.

P0-B — „Uradi ga ti“ je zamijenio aktivan zadatak novim:
    zadatak → hint → „Uradi ga ti“ → objavljen POTPUNO NOV zadatak.
    Uzrok: univerzalni put nije čitao eksplicitan `intent=solution_request`
    koji frontend šalje, pa je namjeru birao model iz slobodnog teksta.

Talas cilja tačno te dvije granice, na lekciji i u nizu iz produkcije:

  • TAČAN produkcijski niz (zadatak → hint → „Uradi ga ti“), tri puta, jer je
    Talas A dokazao da isti zahtjev jednom padne a jednom prođe;
  • istovremena djeljivost sa 6 i 25, i drugi parovi djelilaca;
  • formulacije koje su parser skraćivale (prilog, „i sa brojem N“, zarez);
  • zadatak s cifrom-mjestodržačem — orakl mora PRESKOČITI, ne pogađati;
  • pogrešan pa tačan klik, kontinuitet sesije, granica od dva poziva.

Koraci s „Uradi ga ti“ šalju TAČAN produkcijski payload: `intent`,
`interaction_phase` i `last_tutor_task`, ne samo tekst dugmeta — jer se upravo
oslanjanje na tekst pokazalo kao uzrok P0-B.

Formulacije koje smiju ostati nedokazive (negacija, disjunkcija) namjerno NEMAJU
`published` u provjerama: i preformulisan objavljen zadatak i sigurno odbijanje
su ispravni ishodi. Provjerava se samo da ništa nije procurilo, da stanje nije
mutirano i da nema trećeg poziva.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "wave_f4e.jsonl"

# Lekcija na kojoj je produkcijski smoke test proizveo oba nalaza.
TOPIC, GRADE, OBLAST = "6-03-004", 6, "Djeljivost brojeva"
# Druga lekcija istog razreda — dokazuje da popravke nisu vezane za jednu lekciju.
OTHER_TOPIC, OTHER_OBLAST = "6-03-003", "Djeljivost brojeva"

TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "calls_at_most:2",
]

# „Uradi ga ti“: rješenje POSTOJEĆEG zadatka. `no_new_task` i `task_preserved`
# su srž P0-B — zadatak se rješava, ne zamjenjuje.
SOLUTION_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "no_new_task", "task_preserved", "solution_complete", "reveal_present",
    "task_completed", "calls_at_most:2",
]

HINT_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "no_new_task", "task_preserved", "help_nonempty", "hint_no_leak",
    "no_answer_leak", "reveal_absent", "task_not_completed", "calls_at_most:2",
]

# Za oblike koji SMIJU ostati nedokazivi: tražimo samo sigurnost, ne objavu.
SAFETY_CHECKS = [
    "response_schema", "no_leak", "no_control_chars", "math_safe",
    "bosnian", "calls_at_most:2",
]

TASK_RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def task_step(message, extra=(), rubrics=TASK_RUBRICS, calls=2):
    return {"kind": "text", "message": message, "expect_calls": calls,
            "checks": TASK_CHECKS + list(extra), "rubrics": list(rubrics)}


def hint_step():
    """Isti payload koji šalje chip „Ne znam — daj mi hint“."""
    return {"kind": "text", "message": "Ne znam.", "intent": "hint_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "expect_calls": 2,
            "checks": list(HINT_CHECKS), "rubrics": ["hint_usefulness"]}


def solution_step():
    """Isti payload koji šalje chip „Uradi ga ti“ — eksplicitan ugovor."""
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
    return {"id": sid, "wave": "F4E", "importance": importance, "grade": grade,
            "oblast": oblast, "topic_id": topic, "reason": reason,
            "tags": list(tags), "steps": steps}


rows = []

# --- P0-B: TAČAN PRODUKCIJSKI NIZ, tri puta -------------------------------
for index in range(1, 4):
    rows.append(scenario(
        f"E{index:02d}",
        "Doslovan produkcijski niz: zadatak → „Ne znam — daj mi hint“ → „Uradi "
        "ga ti“. U produkciji je posljednji korak objavio POTPUNO NOV zadatak "
        f"umjesto rješenja postojećeg. Ponavlja se 3x radi mjerenja STOPE "
        f"(ponavljanje {index} od 3).",
        ["p0b", "production_replica", "solution_request", f"repeat_{index}_of_3"],
        [task_step("Daj mi jedan zadatak za vježbu iz ove teme."),
         hint_step(), solution_step()],
    ))

# --- P0-B: rješenje bez prethodnog hinta ----------------------------------
rows.append(scenario(
    "E04",
    "„Uradi ga ti“ odmah nakon objave zadatka, bez hinta: kontinuitet aktivnog "
    "zadatka ne smije ovisiti o tome da li je hint prethodio.",
    ["p0b", "solution_request", "no_hint_first"],
    [task_step("Daj mi zadatak."), solution_step()],
))

# --- P0-B: rješenje pa tek onda nov zadatak -------------------------------
rows.append(scenario(
    "E05",
    "Nakon rješenja učenik izričito traži NOV zadatak. Zabrana objave važi samo "
    "za turn eksplicitne UI akcije — sljedeći, izričit zahtjev mora proći.",
    ["p0b", "solution_request", "new_task_after_solution"],
    [task_step("Daj mi zadatak."), solution_step(),
     task_step("Daj mi novi zadatak.", extra=["task_differs"])],
))

# --- P0-B: druga lekcija — popravka nije vezana za jednu lekciju ----------
rows.append(scenario(
    "E06",
    "Isti niz na DRUGOJ lekciji: kontinuitet aktivnog zadatka je univerzalno "
    "pravilo puta, ne posebnost lekcije na kojoj je nalaz otkriven.",
    ["p0b", "solution_request", "other_lesson"],
    [task_step("Daj mi zadatak."), hint_step(), solution_step()],
    topic=OTHER_TOPIC, oblast=OTHER_OBLAST,
))

# --- P0-A: istovremena djeljivost sa 6 i 25 -------------------------------
for index, message in enumerate((
    "Daj mi zadatak u kojem broj mora biti djeljiv i sa 6 i sa 25.",
    "Daj mi teži zadatak u kojem se traži djeljivost sa 6 i sa 25 istovremeno.",
    "Daj mi zadatak s dva pravila djeljivosti odjednom.",
), start=7):
    rows.append(scenario(
        f"E{index:02d}",
        "Tačan par djelilaca iz produkcijskog nalaza (6 i 25). Objavljen MCQ mora "
        "imati TAČNO JEDNU tačnu opciju; `package_clean` pokreće isti orakl koji "
        "je u produkciji potvrdio pogrešnu opciju kao jedinu tačnu.",
        ["p0a", "divisibility_pair", "6_and_25"],
        [task_step(message)],
    ))

# --- P0-A: drugi parovi djelilaca ----------------------------------------
for index, message in enumerate((
    "Daj mi zadatak u kojem broj mora biti djeljiv i sa 4 i sa 9.",
    "Daj mi zadatak u kojem broj mora biti djeljiv i sa 3 i sa 10.",
    "Daj mi zadatak s tri pravila djeljivosti: sa 2, sa 3 i sa 5.",
), start=10):
    rows.append(scenario(
        f"E{index:02d}",
        "Drugi parovi/trojke djelilaca: granica se popravlja u parseru uslova, ne "
        "za konkretne brojeve 6 i 25 — pa mora vrijediti i ovdje.",
        ["p0a", "divisibility_pair", "other_divisors"],
        [task_step(message)],
        importance="high",
    ))

# --- P0-A: jedno pravilo — kontrola da se uslov ne proširuje --------------
rows.append(scenario(
    "E13",
    "Jedan djelilac: uslov se ne smije proširiti drugim brojem iz teksta. Kontrola "
    "da popravka nije pretvorila ispravan jednostavan zadatak u odbijanje.",
    ["p0a", "single_divisor", "control"],
    [task_step("Daj mi jednostavan zadatak o djeljivosti sa 6.", extra=["level:1"])],
))

# --- P0-A: cifra-mjestodržač — orakl mora preskočiti ---------------------
rows.append(scenario(
    "E14",
    "Zadatak tipa „Dopuni cifru tako da broj bude djeljiv…“: opcije su kandidati "
    "za CIFRU, ne brojevi čija se djeljivost tvrdi. Orakl mora preskočiti, a "
    "zadatak se svejedno mora objaviti ispravan.",
    ["p0a", "digit_placeholder", "oracle_boundary"],
    [task_step("Daj mi zadatak u kojem treba dopuniti cifru tako da broj bude "
               "djeljiv i sa 6 i sa 25.")],
))

# --- P0-A: negacija i disjunkcija — smiju samo sigurno pasti -------------
for index, message in enumerate((
    "Daj mi zadatak u kojem se traži broj koji NIJE djeljiv sa 9.",
    "Daj mi zadatak u kojem broj treba biti djeljiv sa 4 ili sa 6.",
), start=15):
    rows.append(scenario(
        f"E{index:02d}",
        "Negacija i disjunkcija ostaju nedokazive za orakl. Ispravna su OBA ishoda "
        "(preformulisan objavljen zadatak ili sigurno odbijanje) — mjeri se samo "
        "da ništa nije procurilo, da stanje nije mutirano i da nema trećeg poziva.",
        ["p0a", "unprovable_condition", "safety_only"],
        [{"kind": "text", "message": message, "expect_calls": 2,
          "checks": list(SAFETY_CHECKS), "rubrics": []}],
        importance="high",
    ))

# --- Klik: pogrešan pa tačan, uz očuvanje označene opcije ----------------
rows.append(scenario(
    "E17",
    "Pogrešan pa tačan klik nad zadatkom iz ove lekcije: označena tačna opcija je "
    "serverska činjenica i mora ostati ista kroz cio zadatak. Prvi pogrešan klik "
    "ne smije otkriti odgovor.",
    ["p0a", "choice_grading", "correct_option_stable"],
    [task_step("Daj mi zadatak."),
     choice_step("wrong", ["verdict_incorrect", "reveal_absent",
                           "task_not_completed", "no_answer_leak"]),
     choice_step("correct", ["verdict_correct", "task_completed"])],
))

# --- Klik nakon rješenja: zadatak je završen, klik se blokira ------------
rows.append(scenario(
    "E18",
    "Nakon „Uradi ga ti“ zadatak je završen. Klik na opciju mora biti blokiran bez "
    "ijednog poziva i bez mutacije stanja — dokaz da rješenje ISPRAVNO zatvara "
    "postojeći zadatak umjesto da otvori novi.",
    ["p0b", "solution_request", "session_continuity"],
    [task_step("Daj mi zadatak."), solution_step(),
     {"kind": "choice", "select": "a", "expect_calls": 0,
      "requires_active_task": True,
      "checks": ["not_published", "zero_calls", "state_unchanged", "response_schema"],
      "rubrics": []}],
))

OUT.write_text(
    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
    encoding="utf-8",
)
maximum = sum(step["expect_calls"] for row in rows for step in row["steps"])
print(f"{OUT}: {len(rows)} scenarios, at most {maximum} model calls")
