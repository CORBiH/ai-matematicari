r"""Evaluator mora mjeriti STVARAN proizvodni ugovor (PP-1 LIVE-150 ispravke).

Živi talas od 150 scenarija prijavio je tri klase nalaza koje forenzika NIJE
potvrdila kao kvarove proizvoda, nego kao pogrešna očekivanja evaluatora:

  1. F001–F010  planirani kao strict zero-call, iako njihove poruke nose dodatna
                ograničenja ili su konceptualna pitanja. Ugovor
                (`pipeline._deterministic_task_intent`) determinističku namjeru
                izvodi SAMO iz UI polja `difficulty_request` i ZATVORENOG skupa
                jednostavnih poruka — „ruta se NIKAD ne bira iz modelove proze“.
                → 10 lažnih UNEXPECTED_MODEL_CALL nalaza.

  2. D004       treći nagovještaj je dao konačan rezultat i prijavljen je kao
                ANSWER_LEAK, iako ljestvica to izričito traži i produkcijski gate
                vrh ljestvice izuzima (`is_hint_ladder_top`).

  3. C-grupa    odgovorolika poruka („valjda B“) bila je PRVI korak, prije nego
                što je ijedan zadatak objavljen. Server je to ispravno odbio
                („namjera 'answer_attempt' bez aktivnog zadatka“), pa 7 scenarija
                nikad nije izmjerilo ponašanje koje su trebali ispitati.

Ovaj modul zaključava sve tri ispravke. Nula poziva modela — mjeri se čista
logika provjera i konstrukcija plana.
"""
import importlib.util
import json
import pathlib
import tempfile

import pytest

from matbot import config
from matbot.tutor import pipeline as tutor_pipeline
from tools.practice_eval import checks as check_lib
from tools.practice_eval.scenario import load_scenarios, validate_scenarios

LESSON = "6-04-009"
TASK = r"Izračunaj: $\frac{2}{7}+\frac{3}{7}$"
COMMITTED = r"$\frac{5}{7}$"
# Doslovan oblik trećeg nagovještaja koji harmonizovana ruta sada služi.
TOP_HINT_REPLY = (r"Dakle: $\frac{2}{7}+\frac{3}{7}=\frac{2+3}{7}$ — još samo sredi "
                  r"rezultat. Konačan rezultat je $\frac{5}{7}$.")


def observation(answer, *, hint_level, intent="hint_request", student_message="Ne znam."):
    session = {"current_task": TASK,
               "current_options": [{"id": "a", "text": COMMITTED}],
               "correct_option_id": "a", "expected_answer_summary": COMMITTED,
               "hint_level": hint_level}
    after = dict(session, hint_level=hint_level + 1)
    return check_lib.TurnObservation(
        scenario_id="PP1-D004", step_index=hint_level + 1, step_kind="text",
        topic_id=LESSON, grade=6,
        request_payload={"student_message": student_message, "intent": intent},
        http_status=200,
        response={"status": "ready", "answer": answer, "answer_verdict": None,
                  "last_tutor_task": TASK, "next_state": {"v": 1},
                  "session_mode": "practice", "effective_topic": LESSON},
        session_before=session, session_after=after, sdk_calls=1)


# ---------------------------------------------------------------------------
# 1. D004 — CURENJE JE SVJESNO O NIVOU LJESTVICE
# ---------------------------------------------------------------------------

def test_third_hint_stating_the_answer_is_not_a_leak():
    """C iz zadatka: nivo 3 + konačan odgovor → NIJE answer leak."""
    result = check_lib.check_no_answer_leak(
        observation(TOP_HINT_REPLY, hint_level=config.MAX_HINT_LEVEL - 1))
    assert result.outcome == check_lib.SKIP
    assert "treći" in result.detail or "third" in result.detail.lower()


def test_second_hint_stating_the_answer_is_still_a_leak():
    """D iz zadatka: nivo 2 + konačan odgovor → I DALJE answer leak."""
    result = check_lib.check_no_answer_leak(observation(TOP_HINT_REPLY, hint_level=1))
    assert result.outcome == check_lib.FAIL


def test_first_hint_stating_the_answer_is_still_a_leak():
    assert check_lib.check_no_answer_leak(
        observation(TOP_HINT_REPLY, hint_level=0)).outcome == check_lib.FAIL


def test_the_hint_variant_of_the_check_follows_the_same_rule():
    assert check_lib.check_hint_no_leak(
        observation(TOP_HINT_REPLY, hint_level=config.MAX_HINT_LEVEL - 1)
    ).outcome == check_lib.SKIP
    assert check_lib.check_hint_no_leak(
        observation(TOP_HINT_REPLY, hint_level=1)).outcome == check_lib.FAIL


def test_the_exemption_requires_an_actual_hint_intent():
    """Izuzetak je vezan za VRH LJESTVICE, ne za visok `hint_level`: curenje u
    odgovoru na pokušaj/objašnjenje i dalje pada bez obzira na brojač."""
    leaked = check_lib.check_no_answer_leak(observation(
        TOP_HINT_REPLY, hint_level=config.MAX_HINT_LEVEL - 1,
        intent="", student_message="mislim da je nesto drugo"))
    assert leaked.outcome == check_lib.FAIL


def test_a_useful_third_hint_without_the_answer_still_passes():
    """Izuzetak je SKIP, ne slijepo propuštanje — hint bez odgovora i dalje
    prolazi normalnom provjerom na nižim nivoima."""
    safe = "Saberi brojnike, nazivnik ostaje isti."
    assert check_lib.check_no_answer_leak(
        observation(safe, hint_level=1)).outcome == check_lib.PASS


def test_the_evaluator_mirrors_the_production_gate_boundary():
    """Evaluatorov vrh ljestvice mora biti ISTI uslov koji produkcija koristi."""
    session = {"current_task": TASK, "task_completed": False,
               "correct_option_id": "a",
               "current_options": [{"id": "a", "text": COMMITTED}],
               "expected_answer_summary": COMMITTED, "hint_level": 0}

    class _Ctx:
        topic_id = LESSON

    for level in range(0, config.MAX_HINT_LEVEL + 1):
        session["hint_level"] = level
        top = level >= config.MAX_HINT_LEVEL - 1
        assert observation(TOP_HINT_REPLY, hint_level=level).serves_hint_ladder_top is top
        guarded = tutor_pipeline._guard_answer_leak(
            session, "req", _Ctx(), "hint_request", TOP_HINT_REPLY,
            is_hint_ladder_top=top)
        assert (guarded == TOP_HINT_REPLY) is top


# ---------------------------------------------------------------------------
# 2. RUTA — ŠTA UGOVOR STVARNO ČINI DETERMINISTIČKIM
# ---------------------------------------------------------------------------

# Doslovne F-poruke iz plana LIVE-150 (nose dodatna ograničenja / konceptualne).
F_MESSAGES = [
    "Hoću baš zadatak: x - 1/2 > -3/14. Pokaži prebacivanje na drugu stranu.",
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
# Poruke ZATVORENOG skupa — H-grupa ih koristi i one JESU determinističke.
CLOSED_SET_MESSAGES = [
    "Daj mi zadatak.", "daj mi zadatak", "Daj mi jedan zadatak za vježbu iz ove teme.",
    "Novi zadatak.", "Daj mi lakši zadatak.", "Daj mi teži zadatak.",
]


def intent_for(message, **changes):
    turn = {"intent": "", "difficulty_request": "", "student_message": message}
    turn.update(changes)
    return tutor_pipeline._deterministic_task_intent(turn, {"current_task": ""})


@pytest.mark.parametrize("message", F_MESSAGES)
def test_constrained_f_style_messages_are_not_deterministic_by_contract(message):
    """A iz zadatka: slobodna/ograničena poruka → model ruta, po ugovoru."""
    assert intent_for(message) == "", message


@pytest.mark.parametrize("message", CLOSED_SET_MESSAGES)
def test_closed_set_messages_stay_deterministic(message):
    """B iz zadatka: jednostavna poruka zatvorenog skupa → deterministička ruta."""
    assert intent_for(message) != "", message


def test_ui_difficulty_field_still_selects_the_deterministic_route():
    assert intent_for("bilo šta", difficulty_request="harder") == "harder_task"
    assert intent_for("bilo šta", difficulty_request="easier") == "easier_task"


def test_help_intents_are_never_a_task_request():
    assert intent_for("Ne znam.", intent="hint_request") == ""
    assert intent_for("Uradi ga ti.", intent="solution_request") == ""


# ---------------------------------------------------------------------------
# 3. KONSTRUKCIJA PLANA — postavka prije odgovorolikih/pomoćnih koraka
# ---------------------------------------------------------------------------

FAMILY_DIR = (pathlib.Path(__file__).resolve().parents[1] / "tools" /
              "practice_eval" / "scenarios" / "family")
WAVE_JSONL = FAMILY_DIR / "wave_pp1_150.jsonl"
WAVE_PLAN = FAMILY_DIR / "wave_pp1_150.plan.json"
WAVE_GENERATOR = FAMILY_DIR / "wave_pp1_150.jsonl.py"


@pytest.fixture(scope="module")
def plan():
    """Plan iz PRAĆENOG talasa — nikad iz git-ignorisanog scratchpada.

    Dok je generator živio uz artefakte završenog runa, ugovorne ispravke nisu
    bile vezane za commit; testovi koji bi ga tamo čitali ne bi ništa dokazivali
    o onome što se stvarno isporučuje."""
    return json.loads(WAVE_PLAN.read_text(encoding="utf-8"))


def _scenarios(plan, group):
    return [row for row in plan["scenarios"] if row["group"] == group]


def test_c_group_establishes_a_task_before_any_answer_wording(plan):
    """E iz zadatka: odgovorolika poruka nikad nije prvi korak."""
    rows = _scenarios(plan, "C")
    assert len(rows) == 18
    for row in rows:
        assert row["step_count"] >= 3, row["scenario_id"]
        assert row["user_inputs"][0] == "daj mi zadatak", row["scenario_id"]


def test_no_scenario_starts_with_a_step_that_needs_an_active_task(plan):
    """F iz zadatka: isto pravilo vrijedi za hint/solution/klik korake."""
    for row in plan["scenarios"]:
        first = row["user_inputs"][0]
        assert not first.startswith("choice:"), row["scenario_id"]


def test_f_group_now_expects_the_model_route(plan):
    """A iz zadatka, na nivou plana."""
    rows = _scenarios(plan, "F")
    assert len(rows) == 10
    for row in rows:
        assert row["execution_kind"] == "real_model", row["scenario_id"]
        assert row["expected_route"] == "universal_two_call", row["scenario_id"]
        assert row["expected_sdk_calls"] > 0, row["scenario_id"]


def test_f008_remains_an_inequality_mcq_regression_probe(plan):
    """F008 ostaje sonda za porodicu nejednačina — samo s ispravnom rutom."""
    row = next(r for r in _scenarios(plan, "F") if r["scenario_id"] == "PP1-F008")
    assert row["lesson_id"] == "7-02-019"
    assert "negativnim cijelim brojevima" in row["user_inputs"][0]
    assert row["execution_kind"] == "real_model"


def test_strict_zero_call_coverage_is_preserved_and_contractual(plan):
    """G iz zadatka: strict zero-call broji SAMO ugovorno determinističke turnove."""
    deterministic = [r for r in plan["scenarios"]
                     if r["execution_kind"] == "deterministic"]
    assert len(deterministic) == 20
    assert {r["group"] for r in deterministic} == {"H"}
    assert all(r["expected_sdk_calls"] == 0 for r in deterministic)
    assert plan["deterministic_scenarios"] == 20
    assert plan["real_model_scenarios"] == 130
    assert plan["planned_scenarios"] == 150


def test_the_wave_is_tracked_and_loads_through_the_canonical_loader():
    """Talas mora biti PRAĆEN podatak, čitljiv istim loaderom kao svaki drugi."""
    assert WAVE_JSONL.exists() and WAVE_PLAN.exists() and WAVE_GENERATOR.exists()
    scenarios = load_scenarios(WAVE_JSONL)
    assert len(scenarios) == 150
    assert validate_scenarios(scenarios) == []
    assert len({scenario.session_id for scenario in scenarios}) == len(scenarios)
    assert all(scenario.reason.strip() for scenario in scenarios)


def test_the_committed_wave_is_reproducible_from_the_tracked_generator(plan, monkeypatch):
    """REPRODUCIBILNOST: praćeni generator mora dati BAJT ZA BAJT ono što je
    commitovano. Bez ovoga bi se podatak i logika mogli tiho razići."""
    out = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setenv("MATBOT_WAVE_OUT_DIR", str(out))   # ne diraj commitovane fajlove
    spec = importlib.util.spec_from_file_location("wave_pp1_150_probe", WAVE_GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert (out / "wave_pp1_150.jsonl").read_text(encoding="utf-8") == \
        WAVE_JSONL.read_text(encoding="utf-8")
    assert json.loads((out / "wave_pp1_150.plan.json").read_text(encoding="utf-8")) == plan


def test_the_scratchpad_copy_is_only_a_wrapper_around_the_tracked_source():
    """Nema dvije nezavisno održavane kopije plana: istorijski fajl (ako još
    postoji u radnom stablu) smije samo pozivati kanonski generator."""
    legacy = (pathlib.Path(__file__).resolve().parents[1] / "scratchpad" /
              "practice_eval" / "pp1_post_release_live_150" / "generate_plan.py")
    if not legacy.exists():
        pytest.skip("istorijski scratchpad nije prisutan u ovom radnom stablu")
    text = legacy.read_text(encoding="utf-8")
    assert "wave_pp1_150.jsonl.py" in text, "omotač mora upućivati na kanonski izvor"
    for duplicated in ("GROUP_COUNTS", "def scenario(", "F_IDS ="):
        assert duplicated not in text, f"logika plana se ne smije duplirati: {duplicated}"


def test_deterministic_group_still_covers_the_whole_lifecycle(plan):
    """Zero-call dokaz ostaje bogat: svjež zadatak, teži/lakši, hint, klik, rješenje."""
    inputs = {value for row in _scenarios(plan, "H") for value in row["user_inputs"]}
    assert "daj mi zadatak" in inputs
    assert any("teži" in value for value in inputs)
    assert any("laksi" in value or "lakši" in value for value in inputs)
    assert any("hint" in value or "ne znam" in value for value in inputs)
    assert any(value.startswith("choice:") for value in inputs)
    assert any("uradi ga ti" in value for value in inputs)
    # Svaka H-poruka MORA biti ugovorno deterministička (UI polje ili zatvoren
    # skup) — inače bi zero-call dokaz opet mjerio pogrešno očekivanje.
    for value in inputs:
        if value.startswith("choice:"):
            continue
        assert (intent_for(value) != ""
                or intent_for(value, intent="hint_request") == ""), value
