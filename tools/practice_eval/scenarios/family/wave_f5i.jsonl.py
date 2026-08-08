"""Generator ciljanog talasa F5I — živa validacija Batch #4 ekspanzije.

    python tools/practice_eval/scenarios/family/wave_f5i.jsonl.py

Svrha (poslije offline kampanje od >220.000 paketa): dokazati da su nove
deterministicke rute STVARNO nula poziva pod produkcijskim zastavicama, da
mathkernel autoritet preživljava stvarni orkestrator (domen, IR-prije-proze,
skupovna jednakost, egzaktan novac, potpuna parametarska podjela), da
slobodna konverzacija i dalje ide model-putem uz očuvan aktivni zadatak, te
da je novo model-jezgro kapije (6-04-001) živo održivo prije trošenja
gate budžeta.

ROUTE PREFLIGHT PRI GENERISANJU: svaka lekcija se provjerava prema TRENUTNO
kompajliranim ugovorima — deterministički scenario na model-lekciji (ili
obrnuto) OBARA generisanje ovog fajla, prije ijednog SDK poziva.

Budžet: najviše 8 model poziva (plafon talasa 12); deterministički koraci
nose izričitu provjeru `zero_calls`.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from matbot import deterministic as det_registry              # noqa: E402
from matbot.semantics import contracts as semantic_contracts  # noqa: E402

OUT = Path(__file__).resolve().parent / "wave_f5i.jsonl"

DETERMINISTIC_LESSONS = {
    "9-01-005": (9, "Razlomljeni racionalni izrazi"),
    "9-01-014": (9, "Razlomljeni racionalni izrazi"),
    "6-03-010": (6, "Djeljivost brojeva"),
    "8-04-016": (8, "Pitagorina teorema i primjene u ravni"),
    "9-05-013": (9, "Sistemi linearnih jednačina"),
    "6-01-007": (6, "Skupovi i skupovne operacije"),
    "9-08-005": (9, "Podaci, vjerovatnoća, finansije i mjerne jedinice"),
    "8-02-004": (8, "Koordinatni sistem i linearna funkcija"),
    "9-04-022": (9, "Linearne jednačine i nejednačine"),
    "9-01-008": (9, "Razlomljeni racionalni izrazi"),
}
MODEL_LESSONS = {
    "6-04-001": (6, "Razlomci"),
    "9-01-016": (9, "Razlomljeni racionalni izrazi"),
}


def _routes_deterministically(lesson_id):
    contract = semantic_contracts.contract_for(lesson_id)
    if contract is None or not contract.blocking:
        return False
    module = det_registry.GENERATORS.get(contract.family_id)
    return module is not None and module.supports(dict(contract.parameters))


for lesson_id in DETERMINISTIC_LESSONS:
    if not _routes_deterministically(lesson_id):
        raise SystemExit(f"ROUTE PREFLIGHT: {lesson_id} nije deterministička")
for lesson_id in MODEL_LESSONS:
    if semantic_contracts.contract_for(lesson_id) is not None:
        raise SystemExit(f"ROUTE PREFLIGHT: {lesson_id} nije model-lekcija")

DET_TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "zero_calls",
]
MODEL_TASK_CHECKS = [
    "response_schema", "not_safe_error", "no_fallback_text", "no_leak",
    "no_control_chars", "math_safe", "terminology_clean", "bosnian",
    "published", "task_published", "task_self_contained", "lesson_matches",
    "stays_in_lesson", "options_ok", "numeric_consistent", "package_clean",
    "no_verdict", "task_not_completed", "calls_at_most:2",
]
RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]


def det_fresh(message="Daj mi zadatak."):
    return {"kind": "text", "message": message, "expect_calls": 0,
            "checks": list(DET_TASK_CHECKS), "rubrics": list(RUBRICS)}


def det_task(message, difficulty_request="", extra=()):
    step = {"kind": "text", "message": message, "expect_calls": 0,
            "checks": list(DET_TASK_CHECKS) + list(extra),
            "rubrics": list(RUBRICS)}
    if difficulty_request:
        step["difficulty_request"] = difficulty_request
    return step


def det_hint():
    return {"kind": "text", "message": "Ne znam.", "intent": "hint_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "expect_calls": 0,
            "checks": ["response_schema", "not_safe_error", "no_fallback_text",
                       "no_leak", "no_control_chars", "math_safe",
                       "terminology_clean", "bosnian", "no_new_task",
                       "task_preserved", "zero_calls", "help_nonempty",
                       "hint_no_leak", "no_answer_leak", "reveal_absent",
                       "task_not_completed"], "rubrics": []}


def det_solution():
    return {"kind": "text", "message": "Uradi ga ti.",
            "intent": "solution_request", "interaction_phase": "practice_help",
            "send_last_task": True, "requires_active_task": True,
            "expect_calls": 0,
            "checks": ["response_schema", "not_safe_error", "no_fallback_text",
                       "no_leak", "no_control_chars", "math_safe",
                       "terminology_clean", "bosnian", "no_new_task",
                       "task_preserved", "zero_calls", "solution_complete",
                       "reveal_present", "task_completed"], "rubrics": []}


def det_click(select):
    checks = ["response_schema", "not_safe_error", "no_fallback_text",
              "no_leak", "no_control_chars", "math_safe", "bosnian",
              "correct_option_stable", "zero_calls"]
    if select == "correct":
        checks += ["verdict_correct", "task_completed"]
    else:
        checks += ["verdict_incorrect", "reveal_absent", "no_answer_leak"]
    return {"kind": "choice", "select": select, "expect_calls": 0,
            "requires_active_task": True, "checks": checks, "rubrics": []}


def scenario(sid, lesson_id, reason, tags, steps, lessons=DETERMINISTIC_LESSONS):
    grade, oblast = lessons[lesson_id]
    return {"id": sid, "wave": "F5I", "importance": "critical",
            "grade": grade, "oblast": oblast, "topic_id": lesson_id,
            "reason": reason, "tags": list(tags), "steps": steps}


rows = [
    scenario("I01", "9-01-005",
             "Skraćivanje algebarskog razlomka: kraćenje faktora mora "
             "ZADRŽATI isključenu vrijednost u uslovu; svjež → teži → hint "
             "→ rješenje, sve nula poziva.",
             ["deterministic", "rational", "domain"],
             [det_fresh(), det_task("Daj mi teži zadatak.", "harder",
                                    extra=["task_differs"]),
              det_hint(), det_solution()]),
    scenario("I02", "9-01-014",
             "Razlomljena jednačina: zabranjene vrijednosti imenioca "
             "poštovane, egzaktno rješenje; svjež → pogrešan klik → hint.",
             ["deterministic", "rational_equation", "domain"],
             [det_fresh(), det_click("wrong"), det_hint()]),
    scenario("I03", "6-03-010",
             "Strukturisani tekstualni zadatak (djeljivost): činjenice prije "
             "proze, hint ostaje na ISTOM zadatku, tačan klik zatvara.",
             ["deterministic", "word_problem"],
             [det_fresh(), det_hint(), det_click("correct")]),
    scenario("I04", "8-04-016",
             "Praktična Pitagora kao strukturisana priča: egzaktan rezultat "
             "s jedinicama; svjež → teži → lakši (obje tranzicije stvarne).",
             ["deterministic", "word_problem", "pythagoras"],
             [det_fresh(), det_task("Daj mi teži zadatak.", "harder",
                                    extra=["task_differs"]),
              det_task("Daj mi lakši zadatak.", "easier",
                       extra=["task_differs"])]),
    scenario("I05", "9-05-013",
             "Sistemska priča (nekadašnji L1 sudar): dvije nezavisne "
             "relacije, rješenje zadovoljava OBJE; svjež → nov zadatak "
             "mijenja identitet.",
             ["deterministic", "word_problem", "system", "regression"],
             [det_fresh(), det_task("Daj mi novi zadatak.",
                                    extra=["task_differs"])]),
    scenario("I06", "6-01-007",
             "Presjek skupova: jednakost je SKUPOVNA (poredak nebitan), "
             "duplikatne opcije nemoguće; svjež → pogrešan klik → rješenje.",
             ["deterministic", "sets"],
             [det_fresh(), det_click("wrong"), det_solution()]),
    scenario("I07", "9-08-005",
             "Kamata na štednju: sve stope navedene U zadatku, egzaktan "
             "decimalni račun; svjež → teži.",
             ["deterministic", "finance"],
             [det_fresh(), det_task("Daj mi teži zadatak.", "harder",
                                    extra=["task_differs"])]),
    scenario("I08", "8-02-004",
             "Udaljenost dvije tačke (nekadašnji profil-sudar): egzaktna "
             "Pitagorina udaljenost, lekcijski-relativan nivo; svjež → teži.",
             ["deterministic", "coordinate", "regression"],
             [det_fresh(), det_task("Daj mi teži zadatak.", "harder",
                                    extra=["task_differs"])]),
    scenario("I09", "9-04-022",
             "Parametarska diskusija: potpuna i međusobno isključiva "
             "podjela slučajeva u rješenju; svjež → hint → rješenje.",
             ["deterministic", "parametric"],
             [det_fresh(), det_hint(), det_solution()]),
    scenario("I10", "9-01-008",
             "Granica slobodne konverzacije: strukturisan zadatak (0 "
             "poziva) → pojmovno pitanje ide MODEL-putem uz očuvan aktivni "
             "zadatak → sljedeća strukturisana akcija opet 0 poziva.",
             ["boundary", "free_form", "rational"],
             [det_fresh(),
              {"kind": "text",
               "message": "Zašto se kod skraćivanja algebarskog razlomka i "
                          "dalje mora paziti na vrijednosti za koje je "
                          "nazivnik nula?",
               "send_last_task": True, "requires_active_task": True,
               "expect_calls": 2,
               "checks": ["response_schema", "not_safe_error",
                          "no_fallback_text", "no_leak", "no_control_chars",
                          "math_safe", "terminology_clean", "bosnian",
                          "no_new_task", "task_preserved", "calls_at_most:2",
                          "help_nonempty"], "rubrics": []},
              det_task("Daj mi novi zadatak.", extra=["task_differs"])]),
    scenario("I11", "6-04-001",
             "NOVO model-jezgro kapije: prirodan svjež nivo 1 pa teži — "
             "Tutor+Recenzent, najviše 2 poziva po paketu, server ostaje "
             "autoritet težine.",
             ["model_core", "gate_prerequisite"],
             [{"kind": "text", "message": "Daj mi zadatak.",
               "expect_calls": 2, "checks": list(MODEL_TASK_CHECKS),
               "rubrics": list(RUBRICS)},
              {"kind": "text", "message": "Daj mi teži zadatak.",
               "difficulty_request": "harder", "expect_calls": 2,
               "checks": list(MODEL_TASK_CHECKS) + ["task_differs"],
               "rubrics": list(RUBRICS)}],
             lessons=MODEL_LESSONS),
    scenario("I12", "9-01-016",
             "Model-kontrola 9. razreda: namjerno NEPODRŽAN simbolički domen "
             "(diskusija racionalnog izraza s parametrom) ostaje model-put — "
             "ekspanzija nije preuzela više nego što dokazuje.",
             ["model_control", "grade9"],
             [{"kind": "text", "message": "Daj mi zadatak.",
               "expect_calls": 2, "checks": list(MODEL_TASK_CHECKS),
               "rubrics": list(RUBRICS)}],
             lessons=MODEL_LESSONS),
]

OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                       for row in rows), encoding="utf-8")
model_calls = sum(step["expect_calls"] for row in rows for step in row["steps"])
det_steps = sum(1 for row in rows for step in row["steps"]
                if step["expect_calls"] == 0)
print(f"{OUT}: {len(rows)} scenarios, {det_steps} deterministic steps, "
      f"at most {model_calls} model calls")
