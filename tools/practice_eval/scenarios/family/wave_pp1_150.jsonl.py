"""Generator talasa PP1-150 — post-release ugovorna evaluacija Vježbe.

    python tools/practice_eval/scenarios/family/wave_pp1_150.jsonl.py

Piše DVA artefakta pored sebe:
  • `wave_pp1_150.jsonl`      — scenariji za runner (isti oblik kao ostali talasi)
  • `wave_pp1_150.plan.json`  — plan/očekivanja po scenariju (adjudikacija)

ZAŠTO JE OVDJE (a ne u scratchpadu): prva verzija ovog plana živjela je uz
artefakte završenog runa, u `scratchpad/practice_eval/`, koji je git-ignorisan.
Ispravke ugovora izvedene iz forenzike LIVE-150 tako NISU bile vezane za commit,
pa se talas nije mogao reprodukovati iz praćenog izvora. Logika sada živi ovdje,
po istoj konvenciji kao svaki drugi `wave_*.jsonl.py`.

UGOVORNE ISPRAVKE koje ovaj plan nosi (sve izvedene iz forenzike LIVE-150):
  • F001–F010 su MODEL rute: njihove poruke nose dodatna ograničenja ili su
    konceptualne, a `pipeline._deterministic_task_intent` determinističku
    namjeru čita SAMO iz UI polja i zatvorenog skupa poruka;
  • F008 ostaje sonda za porodicu nejednačina/MCQ — samo s ispravnom rutom;
  • C-grupa PRVO objavi zadatak, pa tek onda šalje odgovoroliku poruku;
  • strict zero-call dokaz nosi ISKLJUČIVO H-grupa (20 scenarija);
  • nijedan scenario ne počinje korakom koji traži aktivan zadatak.

Ukupno: 150 scenarija = 130 real_model + 20 deterministic.
"""
from __future__ import annotations

import json
import os
import random
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
# Podrazumijevano piše PORED SEBE (konvencija svih talasa). Override postoji
# samo da regresioni test smije regenerisati talas u privremeni direktorij i
# uporediti ga s commitovanim — bez diranja praćenih fajlova.
OUT_DIR = Path(os.environ.get("MATBOT_WAVE_OUT_DIR")
               or Path(__file__).resolve().parent)
SEED = 20260809
EXPECTED_HEAD = "01864da4acc220e5ec31ab0553d34d8ab8af7831"
EXPECTED_TREE = "7d72d4cfff393e3a0738294f73fe655029bf0175"
GROUP_COUNTS = {"A": 35, "B": 18, "C": 18, "D": 14, "E": 15,
                "F": 10, "G": 10, "H": 20, "I": 10}

sys.path.insert(0, str(ROOT))

from matbot import deterministic as deterministic_registry  # noqa: E402
from matbot.semantics import contracts as semantic_contracts  # noqa: E402


def routes_deterministically(lesson_id: str) -> bool:
    contract = semantic_contracts.contract_for(lesson_id)
    if contract is None or not contract.blocking:
        return False
    module = deterministic_registry.GENERATORS.get(contract.family_id)
    return module is not None and module.supports(dict(contract.parameters))


coverage = json.loads((ROOT / "reference" / "curriculum" / "semantics" /
                       "deterministic_coverage_report.json").read_text(encoding="utf-8"))
rows_by_id = {row["lesson_id"]: row for row in coverage["lessons"]}
assert len(rows_by_id) == 534

for row in coverage["lessons"]:
    actual = routes_deterministically(row["lesson_id"])
    declared = row["class"] == "DETERMINISTIC_READY"
    if actual != declared:
        raise SystemExit(f"route preflight mismatch: {row['lesson_id']} declared={declared} actual={actual}")


F_IDS = (
    "6-07-003",  # exact x-1/2 > -3/14 historical regression
    "6-07-002",  # unknown addend/minuend/subtrahend
    "6-07-004",  # unknown factor/dividend/divisor
    "6-07-005",  # multiplicative inequality
    "6-07-006",  # decimal equation/inequality under Grade-6 policy
    "7-02-016",  # higher-grade additive equation positive control
    "7-02-017",  # higher-grade multiplicative equation positive control
    "7-02-019",  # negative integer/additive inequality positive control
    "7-02-020",  # sign direction positive control
    "9-04-016",  # explicit sign-flip pedagogy positive control
)

G_MESSAGES = {
    "8-02-007": "Daj zadatak gdje samo izračunam y za x=3, ne mora tačka pripadati grafiku.",
    "9-03-004": "Može samo uvrštavanje u formulu funkcije, bez crtanja i čitanja grafika?",
    "8-05-010": "Daj mi računanje zapremine prizme, nemoj mrežu.",
    "9-07-009": "Daj površinu valjka direktno, bez mreže tijela.",
    "9-01-015": "Daj samo goli algebarski razlomak da ga sredim, bez tekstualnog zadatka.",
    "9-01-017": "Daj brojčani primjer, ne moram dokazivati identitet.",
    "7-04-016": "Daj podudarnost po SSS umjesto SSU, lakše mi je.",
    "7-04-013": "Daj ispravan SUS zadatak sa zahvaćenim uglom između dvije stranice.",
    "9-05-006": "Daj sistem jednačina koji moram riješiti grafičkom metodom.",
    "6-10-007": "Daj zadatak da iz tabele nacrtam grafik preslikavanja.",
}
G_IDS = tuple(G_MESSAGES)

for lesson_id in F_IDS:
    assert routes_deterministically(lesson_id), lesson_id
for lesson_id in G_IDS:
    assert not routes_deterministically(lesson_id), lesson_id
assert len(set(F_IDS) | set(G_IDS)) == 20


COMMON = ["response_schema", "no_leak", "no_control_chars", "math_safe",
          "terminology_clean", "bosnian", "stays_in_lesson"]
FRESH_MODEL = COMMON + ["not_safe_error", "no_fallback_text", "published",
                        "task_published", "task_self_contained", "options_ok",
                        "lesson_matches", "package_clean", "no_verdict",
                        "task_not_completed", "calls_at_most:2"]
FRESH_RISK = COMMON + ["lesson_matches", "package_clean", "no_verdict",
                       "calls_at_most:2"]
FRESH_DET = [c for c in FRESH_MODEL if c != "calls_at_most:2"] + ["zero_calls"]
HELP_MODEL = COMMON + ["no_new_task", "task_preserved", "help_nonempty",
                       "no_answer_leak", "calls_at_most:1"]
HELP_DET = [c for c in HELP_MODEL if c != "calls_at_most:1"] + ["zero_calls"]
SOLUTION_MODEL = COMMON + ["no_new_task", "task_preserved", "solution_complete",
                           "calls_at_most:1"]
SOLUTION_DET = [c for c in SOLUTION_MODEL if c != "calls_at_most:1"] + ["zero_calls"]
CORRECT_MODEL = COMMON + ["verdict_correct", "task_completed", "no_new_task",
                          "task_preserved", "correct_option_stable", "calls_at_most:1"]
WRONG_MODEL = COMMON + ["verdict_incorrect", "task_not_completed", "no_new_task",
                        "task_preserved", "correct_option_stable", "reveal_absent",
                        "calls_at_most:1"]


def text(message: str, calls: int, checks, **extra):
    result = {"kind": "text", "message": message, "expect_calls": calls,
              "checks": list(checks), "rubrics": []}
    result.update(extra)
    return result


def fresh(message="Daj mi zadatak.", deterministic=False, risk=False):
    checks = FRESH_DET if deterministic else (FRESH_RISK if risk else FRESH_MODEL)
    return text(message, 0 if deterministic else 2, checks)


def harder(deterministic=False, expected_level=None, message="može teži"):
    checks = list(FRESH_DET if deterministic else FRESH_MODEL) + ["task_differs"]
    if expected_level:
        checks.append(f"level:{expected_level}")
    return text(message, 0 if deterministic else 2, checks,
                difficulty_request="harder", requires_active_task=True)


def easier(deterministic=False, expected_level=None, message="daj laksi"):
    checks = list(FRESH_DET if deterministic else FRESH_MODEL) + ["task_differs"]
    if expected_level:
        checks.append(f"level:{expected_level}")
    return text(message, 0 if deterministic else 2, checks,
                difficulty_request="easier", requires_active_task=True)


def another(deterministic=False, expected_level=None, message="daj novi"):
    checks = list(FRESH_DET if deterministic else FRESH_MODEL) + ["task_differs"]
    if expected_level:
        checks.append(f"level:{expected_level}")
    return text(message, 0 if deterministic else 2, checks,
                difficulty_request="same", requires_active_task=True)


def hint(message="daj mi hint", deterministic=False, differs=False):
    checks = list(HELP_DET if deterministic else HELP_MODEL)
    if differs:
        checks.append("hint_differs")
    return text(message, 0 if deterministic else 1, checks,
                intent="hint_request", interaction_phase="practice_help",
                send_last_task=True, requires_active_task=True, collect_help=True)


def solution(message="uradi ga ti", deterministic=False):
    return text(message, 0 if deterministic else 1,
                SOLUTION_DET if deterministic else SOLUTION_MODEL,
                intent="solution_request", interaction_phase="practice_help",
                send_last_task=True, requires_active_task=True)


def attempt_wording(message):
    """Realističan odgovorolik/nesiguran tekst NAD VEĆ OBJAVLJENIM zadatkom.

    Namjerno BEZ `intent`: server sam klasifikuje namjeru, i to je upravo ono
    što se mjeri. Zahtijeva aktivan zadatak (`requires_active_task`), pa se u
    planu više ne može naći kao prvi korak — to je bio uzrok 7 INFRA_ERROR
    scenarija u LIVE-150. Zadatak mora PREŽIVJETI ovaj potez: takva poruka nije
    zahtjev za novim zadatkom."""
    return text(message, 1, COMMON + ["no_new_task", "task_preserved",
                                      "task_not_completed", "calls_at_most:1"],
                send_last_task=True, requires_active_task=True)


def choice(select: str, correct: bool, deterministic=False):
    checks = CORRECT_MODEL if correct else WRONG_MODEL
    if deterministic:
        checks = [c for c in checks if c != "calls_at_most:1"] + ["zero_calls"]
    return {"kind": "choice", "select": select,
            "expect_calls": 0 if deterministic else 1,
            "checks": list(checks), "rubrics": [], "requires_active_task": True}


rng = random.Random(SEED)
model_candidates = [row for row in coverage["lessons"]
                    if not routes_deterministically(row["lesson_id"])]
fixed_model = set(G_IDS)
selected_model = list(G_IDS)
for grade in (6, 7, 8, 9):
    existing = sum(1 for lesson_id in selected_model
                   if int(rows_by_id[lesson_id]["grade"]) == grade)
    pool = [row for row in model_candidates
            if int(row["grade"]) == grade and row["lesson_id"] not in fixed_model]
    pool.sort(key=lambda row: row["lesson_id"])
    selected_model.extend(row["lesson_id"] for row in rng.sample(pool, 30 - existing))

assert len(selected_model) == len(set(selected_model)) == 120
assert Counter(int(rows_by_id[x]["grade"]) for x in selected_model) == Counter({6: 30, 7: 30, 8: 30, 9: 30})

remaining_model = [x for x in selected_model if x not in fixed_model]
rng.shuffle(remaining_model)
allocations = {}
cursor = 0
for group, count in (("A", 35), ("B", 18), ("C", 18), ("D", 14),
                     ("E", 15), ("I", 10)):
    allocations[group] = remaining_model[cursor:cursor + count]
    cursor += count
assert cursor == len(remaining_model) == 110
allocations["G"] = list(G_IDS)

det_pool = [row for row in coverage["lessons"]
            if routes_deterministically(row["lesson_id"])
            and row["lesson_id"] not in set(F_IDS)]
by_family = {}
for row in det_pool:
    by_family.setdefault(row.get("family") or "unknown", []).append(row)
for pool in by_family.values():
    pool.sort(key=lambda row: row["lesson_id"])

# Broad deterministic selection: one per family first, then seeded fill.
selected_h = []
for family in sorted(by_family):
    if len(selected_h) >= 20:
        break
    candidates = [row for row in by_family[family]
                  if row["lesson_id"] not in {x["lesson_id"] for x in selected_h}]
    if candidates:
        selected_h.append(rng.choice(candidates))
left = [row for row in det_pool if row["lesson_id"] not in {x["lesson_id"] for x in selected_h}]
rng.shuffle(left)
selected_h.extend(left[:20 - len(selected_h)])
H_IDS = tuple(row["lesson_id"] for row in selected_h)
assert len(H_IDS) == len(set(H_IDS)) == 20
assert not (set(H_IDS) & set(F_IDS))


def scenario(sid, group, lesson_id, steps, reason, interaction_phase,
             expected_route, deterministic, chain_id=None):
    row = rows_by_id[lesson_id]
    return {
        "runner": {
            "id": sid, "wave": "FPP1", "importance": "critical",
            "grade": int(row["grade"]), "oblast": row["oblast"],
            "topic_id": lesson_id, "reason": reason,
            "tags": [f"group_{group}", "deterministic" if deterministic else "real_model",
                     row.get("family") or row.get("analysis") or row["class"]],
            "steps": steps,
        },
        "plan": {
            "scenario_id": sid, "group": group,
            "grade": int(row["grade"]), "oblast": row["oblast"],
            "lesson_id": lesson_id, "lesson_title": row["title"],
            "family_module": row.get("family") or row.get("analysis") or row["class"],
            "coverage_class": row["class"], "interaction_phase": interaction_phase,
            "expected_route": expected_route,
            "execution_kind": "deterministic" if deterministic else "real_model",
            "expected_sdk_calls": sum(step["expect_calls"] for step in steps),
            "chain_id": chain_id or sid,
            "step_count": len(steps),
            "user_inputs": [step.get("message", f"choice:{step.get('select')}") for step in steps],
        },
    }


records = []

# A: fresh tasks, with real Level-1/2/3 coverage through the actual controller.
fresh_wordings = ["daj novi", "Moze zadatak?", "daj mi jedan zadatak", "haj neki zadatak",
                  "daj zadatak za vjezbu", "Može drugi zadatak", "daj samo novi"]
for i, lesson_id in enumerate(allocations["A"], 1):
    steps = [fresh(fresh_wordings[(i - 1) % len(fresh_wordings)])]
    if i > 25:
        steps.append(harder(expected_level=2))
    if i > 30:
        steps.append(harder(expected_level=3, message="jos tezi"))
    records.append(scenario(f"PP1-A{i:03d}", "A", lesson_id, steps,
                            "Fresh task quality and requested difficulty coverage.",
                            "fresh_task", "universal_two_call", False))

# B: fresh -> correct click -> next task, full chain preserved.
for i, lesson_id in enumerate(allocations["B"], 1):
    steps = [fresh("daj mi zadatak"), choice("correct", True),
             another(expected_level=1, message="moze drugi zadatak")]
    records.append(scenario(f"PP1-B{i:03d}", "B", lesson_id, steps,
                            "Correct answer grading, state progression and same-lesson next task.",
                            "correct_answer_followup", "model_generation_and_grading", False,
                            chain_id=f"CHAIN-B-{i:03d}"))

# C: fresh -> realistic wrong click.
# POSTAVKA C-GRUPE (ispravka poslije LIVE-150): realističan odgovorolik tekst
# („valjda B“, „je l 4“, „probao sam al ne kontam“) je RANIJE bio PRVI korak, tj.
# zahtjev za zadatkom. Bez aktivnog zadatka server takvu poruku ispravno odbija
# (`namjera 'answer_attempt' bez aktivnog zadatka`), pa zadatak nikad nije
# objavljen i korak s pogrešnim klikom se nije ni izvršio — 7 scenarija je palo
# kao INFRA_ERROR, bez ijednog mjerenja ponašanja koje su trebali ispitati.
# Ispravno je: PRVO se običnim zahtjevom objavi zadatak, pa TEK ONDA ide
# odgovorolika poruka i klik. Odgovorolik tekst se čuva kao zaseban korak, da
# se i dalje mjeri ponašanje nad realističnim učeničkim izrazom.
for i, lesson_id in enumerate(allocations["C"], 1):
    word = ["je l 4", "mislim da je 7", "valjda B", "nije valjda ovo",
            "probao sam al ne kontam"][(i - 1) % 5]
    steps = [fresh("daj mi zadatak"), attempt_wording(word),
             choice("wrong", False)]
    records.append(scenario(f"PP1-C{i:03d}", "C", lesson_id, steps,
                            "Wrong-option grading with a realistic student error.",
                            "wrong_answer_followup", "model_generation_and_grading", False,
                            chain_id=f"CHAIN-C-{i:03d}"))

# D: hint ladders and full-solution requests.
for i, lesson_id in enumerate(allocations["D"], 1):
    steps = [fresh("daj zadatak")]
    variant = (i - 1) % 7
    if variant == 0:
        steps += [hint("daj mi hint")]
    elif variant == 1:
        steps += [hint("pomozi mi malo")]
    elif variant == 2:
        steps += [hint("ne znam kako poceti"), hint("jos jedan hint", differs=True)]
    elif variant == 3:
        steps += [hint("ne kontam"), hint("kako ovo", differs=True),
                  hint("daj treci hint", differs=True)]
    elif variant == 4:
        steps += [solution("uradi ga ti")]
    elif variant == 5:
        steps += [solution("pokazi rjesenje")]
    else:
        steps += [hint("daj samo hint"), solution("sad uradi ti")]
    records.append(scenario(f"PP1-D{i:03d}", "D", lesson_id, steps,
                            "Hint ladder or full-solution policy on one immutable task.",
                            "practice_help", "model_help_on_model_task", False,
                            chain_id=f"CHAIN-D-{i:03d}"))

# E: explicit difficulty controller transitions.
for i, lesson_id in enumerate(allocations["E"], 1):
    variant = (i - 1) % 6
    steps = [fresh("daj zadatak")]
    if variant == 0:
        steps += [harder(expected_level=2)]
    elif variant == 1:
        steps += [harder(expected_level=2), harder(expected_level=3, message="jos tezi")]
    elif variant == 2:
        steps += [harder(expected_level=2), harder(expected_level=3), easier(expected_level=2)]
    elif variant == 3:
        steps += [harder(expected_level=2), easier(expected_level=1)]
    elif variant == 4:
        steps += [another(expected_level=1, message="daj isti nivo")]
    else:
        steps += [harder(expected_level=2), easier(expected_level=1),
                  easier(expected_level=1, message="moze jos laksi")]
    records.append(scenario(f"PP1-E{i:03d}", "E", lesson_id, steps,
                            "Structured harder/easier/same-level transition.",
                            "difficulty_transition", "universal_two_call", False,
                            chain_id=f"CHAIN-E-{i:03d}"))

# F: PP-1-sensitive deterministic publication and positive controls.
f_messages = [
    r"Hoću baš zadatak: x - 1/2 > -3/14. Pokaži prebacivanje na drugu stranu.",
    "Daj jednačinu s nepoznatim sabirkom, umanjenikom ili umanjiocem.",
    "Daj jednačinu s nepoznatim činiocem, djeljenikom ili djeliocem.",
    "Daj nejednačinu s množenjem ili dijeljenjem razlomaka.",
    "Daj jednačinu s decimalnim brojevima, bez prebacivanja članova.",
    "Daj jednačinu u Z i objasni transpozicijom ako je dozvoljeno.",
    "Daj multiplicativnu jednačinu u Z.",
    "Daj nejednačinu s negativnim cijelim brojevima.",
    "Daj nejednačinu gdje negativan množilac mijenja smjer.",
    "Pokaži zašto se znak nejednakosti obrće pri dijeljenju negativnim brojem.",
]
# RUTA F-GRUPE (ispravka poslije LIVE-150): ove poruke su SLOBODNE i nose
# dodatna ograničenja („s negativnim cijelim brojevima“, „bez prebacivanja
# članova“, „objasni transpozicijom“), a jedna je čisto konceptualno pitanje.
# Ugovor repozitorija (matbot/tutor/pipeline.py::_deterministic_task_intent)
# determinističku namjeru izvodi ISKLJUČIVO iz UI polja `difficulty_request` i
# ZATVORENOG skupa jednostavnih poruka — „ruta se NIKAD ne bira iz modelove
# proze“. Takve poruke zato PO UGOVORU idu model-putem; plan ih je pogrešno
# označio kao strict zero-call i proizveo 10 lažnih UNEXPECTED_MODEL_CALL
# nalaza. Lekcije ostaju determinističke (assert iznad) — mjeri se namjera
# poruke, ne pokrivenost lekcije. Zero-call dokaz nosi H-grupa, koja koristi
# stvarne poruke zatvorenog skupa.
#
# F010 je konceptualno pitanje („Pokaži zašto se znak obrće…“) i legitimno se
# odgovara objašnjenjem BEZ objave zadatka — zato dobija FRESH_RISK skup, koji
# ne zahtijeva `task_published`.
_F_CONCEPTUAL = {"9-04-016"}
for i, (lesson_id, message) in enumerate(zip(F_IDS, f_messages), 1):
    # F008 ostaje regresiona sonda za porodicu nejednačina/MCQ: novi serverski
    # orakl (matbot/mcq_integrity.evaluate_linear_solve_mcq) mora spriječiti
    # objavu paketa u kojem nijedna opcija nije CIO skup rješenja — to mjeri
    # `package_clean` + `options_ok` u FRESH_MODEL skupu.
    steps = [fresh(message, risk=lesson_id in _F_CONCEPTUAL)]
    records.append(scenario(f"PP1-F{i:03d}", "F", lesson_id, steps,
                            "PP-1 sensitive equation/inequality publication control.",
                            "pp1_sensitive", "universal_two_call", False))

# G: semantic/curriculum boundary probes, including positive controls.
for i, lesson_id in enumerate(G_IDS, 1):
    records.append(scenario(f"PP1-G{i:03d}", "G", lesson_id,
                            [fresh(G_MESSAGES[lesson_id], risk=True)],
                            "Semantic boundary probe: adjacent skill, terminology, or in-scope control.",
                            "semantic_boundary", "universal_two_call", False))

# H: zero-call deterministic routes, varied over the lifecycle.
for i, lesson_id in enumerate(H_IDS, 1):
    variant = (i - 1) % 5
    steps = [fresh("daj mi zadatak", deterministic=True)]
    if variant == 0:
        steps += [harder(True, 2)]
    elif variant == 1:
        steps += [harder(True, 2), easier(True, 1)]
    elif variant == 2:
        steps += [hint("ne znam kako poceti", True), hint("jos jedan hint", True, True)]
    elif variant == 3:
        steps += [choice("wrong", False, True), choice("correct", True, True)]
    else:
        steps += [solution("uradi ga ti", True)]
    records.append(scenario(f"PP1-H{i:03d}", "H", lesson_id, steps,
                            "Deterministic lifecycle with strict per-turn zero-call proof.",
                            "deterministic_zero_call", "deterministic_contract", True,
                            chain_id=f"CHAIN-H-{i:03d}"))

# I: routing/lifecycle robustness on model-backed tasks.
for i, lesson_id in enumerate(allocations["I"], 1):
    variant = (i - 1) % 10
    steps = [fresh("daj novi" if i % 2 else "daj zadatak direktno")]
    if variant == 0:
        steps += [another(message="moze drugi zadatak")]
    elif variant == 1:
        steps += [harder(expected_level=2)]
    elif variant == 2:
        steps += [harder(expected_level=2), easier(expected_level=1)]
    elif variant == 3:
        steps += [choice("correct", True)]
    elif variant == 4:
        steps += [choice("wrong", False)]
    elif variant == 5:
        steps += [hint("kako ovo")]
    elif variant == 6:
        steps += [solution("uradi ti")]
    elif variant == 7:
        steps += [another(message="daj isti nivo")]
    elif variant == 8:
        steps += [choice("correct", True), {"kind": "repeat_choice", "select": "correct",
                  "expect_calls": 0, "checks": ["zero_calls", "identical_response", "state_unchanged"],
                  "rubrics": [], "requires_active_task": True}]
    else:
        steps += [hint("daj samo hint"), another(message="novi zadatak")]
    records.append(scenario(f"PP1-I{i:03d}", "I", lesson_id, steps,
                            "Routing, call-budget, lesson preservation and lifecycle robustness.",
                            "routing_lifecycle", "mixed_model_lifecycle", False,
                            chain_id=f"CHAIN-I-{i:03d}"))


runner_rows = [record["runner"] for record in records]
plan_rows = [record["plan"] for record in records]
assert len(runner_rows) == len(plan_rows) == 150
assert dict(Counter(row["id"][4] for row in runner_rows)) == GROUP_COUNTS
# F-grupa (10) je poslije LIVE-150 ispravno prekvalifikovana u real_model —
# njene poruke su slobodne/ograničene, a ugovor determinističku namjeru čita
# samo iz UI polja i zatvorenog skupa poruka. Strict zero-call dokaz nosi
# ISKLJUČIVO H-grupa, čije poruke JESU iz tog zatvorenog skupa.
assert Counter(row["execution_kind"] for row in plan_rows) == Counter({"real_model": 130, "deterministic": 20})
assert len({row["lesson_id"] for row in plan_rows}) == 150
assert all(all("zero_calls" in step["checks"] and step["expect_calls"] == 0
               for step in record["runner"]["steps"])
           for record in records if record["plan"]["execution_kind"] == "deterministic")
# Svaki deterministički scenario mora i dalje pokrivati STVARAN zatvoreni skup
# poruka, a ne samo determinističku lekciju.
assert {record["plan"]["scenario_id"][4] for record in records
        if record["plan"]["execution_kind"] == "deterministic"} == {"H"}
# Nijedan odgovorolik/pomoćni korak ne smije biti PRVI u lancu — bez aktivnog
# zadatka server ga ispravno odbija, a scenario mjeri postavku umjesto ponašanja
# (uzrok 7 INFRA_ERROR scenarija u LIVE-150).
assert all(not record["runner"]["steps"][0].get("requires_active_task")
           for record in records), "prvi korak ne smije zahtijevati aktivan zadatak"

(OUT_DIR / "wave_pp1_150.jsonl").write_text(
    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in runner_rows),
    encoding="utf-8")
(OUT_DIR / "wave_pp1_150.plan.json").write_text(json.dumps({
    "seed": SEED, "expected_head": EXPECTED_HEAD, "expected_tree": EXPECTED_TREE,
    "planned_scenarios": 150, "real_model_scenarios": 130,
    "deterministic_scenarios": 20, "group_counts": GROUP_COUNTS,
    "grade_5_note": "No grade-5 curriculum exists in topics.json; canonical grades are 6-9.",
    "unique_lessons": 150,
    "scenarios": plan_rows,
}, ensure_ascii=False, indent=2), encoding="utf-8")

print(json.dumps({
    "seed": SEED, "scenarios": len(runner_rows),
    "groups": Counter(row["id"][4] for row in runner_rows),
    "execution": Counter(row["execution_kind"] for row in plan_rows),
    "grades": Counter(row["grade"] for row in plan_rows),
    "unique_lessons": len({row["lesson_id"] for row in plan_rows}),
    "families_modules": len({row["family_module"] for row in plan_rows}),
    "expected_sdk_calls": sum(row["expected_sdk_calls"] for row in plan_rows),
}, ensure_ascii=False, default=dict, indent=2))
