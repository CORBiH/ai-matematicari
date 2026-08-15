"""UGOVOR O BROJU POZIVA ZA POMOĆ — kapija mora mjeriti STVARNU arhitekturu.

ŽIVI NALAZ (zvanična kapija): kampanja je pala na scenariju `first_hint` s
„expected 1 SDK call, actual 0“, a prva tri scenarija — uključujući lekciju
koja je ranije imala problem s oznakom opcije — prošla su. Aplikacija NIJE bila
u regresiji: kapija je mjerila ugovor stariji od SERVER-VLASNIČKE POMOĆI.

Sadašnja arhitektura (matbot/hint_policy.py + pipeline._help_author):
  • `full_solution_request` → UVIJEK server → 0 poziva;
  • hint na vrhu ljestvice → server → 0 poziva;
  • hint 1–2 za klasu TVRDNJE → server → 0 poziva;
  • hint 1–2 za RAČUNSKU klasu → model → 1 poziv.

Sve četiri grane su poznate PRIJE ijednog poziva, pa kapija svoje očekivanje
IZVODI iz iste politike i poredi ga STROGOM JEDNAKOŠĆU. Raspon („0 ili 1, svejedno“)
bi kapiju prestao činiti kapijom: prestala bi hvatati i višak i manjak poziva.
"""
import pytest

from matbot import config, hint_policy, release_config
from tools import check_live_release_gate as checker
from tools import run_live_release_gate as runner


@pytest.fixture(autouse=True)
def _production_routing(monkeypatch):
    """Plan kapije opisuje PRODUKCIJU, pa se gradi u produkcijskoj konfiguraciji.

    Bez ove zastavice mjereno slaba porodica i dalje ide determinističkom rutom,
    pa bi scenario `migrated_deterministic` bio nemoguć — i kapija to izričito
    odbija umjesto da tiho izmjeri drugu arhitekturu."""
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    from matbot import deterministic_variety
    deterministic_variety._payload.cache_clear()
    deterministic_variety._migrated_families.cache_clear()
    yield
    deterministic_variety._payload.cache_clear()
    deterministic_variety._migrated_families.cache_clear()




def _gate(role):
    return next(item for item in runner.build_release_gate_plan("0123456789abcdef" * 4)
                if item.role == role)


def _session(task_text, options, marked_index, hint_level=0):
    return {
        "current_task": task_text,
        "current_options": [{"id": chr(ord("a") + i), "text": t}
                            for i, t in enumerate(options)],
        "correct_option_id": chr(ord("a") + marked_index),
        "hint_level": hint_level,
    }


# Klasa TVRDNJE: opcije su rečenice, nijedna nije gola vrijednost.
PROPOSITIONAL = _session(
    "Koja tvrdnja o razlomcima je tačna?",
    ("Svaki razlomak manji od jedan ima brojnik manji od nazivnika.",
     "Svaki razlomak veći od jedan ima brojnik manji od nazivnika.",
     "Razlomak je jednak jedan kada je brojnik dvostruko veći od nazivnika.",
     "Razlomak s nazivnikom jedan uvijek je manji od svog brojnika."),
    0)
# RAČUNSKA klasa: opcije su gole vrijednosti.
COMPUTATIONAL = _session(
    "Koliko je $\\frac{3}{4}$ od $48$?",
    ("$36$", "$12$", "$64$", "$24$"), 0)


# ---------------------------------------------------------------------------
# A/B) PRVI HINT — obje grane, svaka s TAČNIM očekivanjem
# ---------------------------------------------------------------------------

def test_a_first_hint_on_a_propositional_task_expects_exactly_zero_calls():
    expected, basis = runner.resolve_expected_calls(_gate("first_hint"), PROPOSITIONAL)
    assert expected == 0
    assert basis == f"help_policy:{hint_policy.SERVER}"


def test_b_first_hint_on_a_computational_task_expects_exactly_one_call():
    """Model-put za prvi hint I DALJE postoji — dokazano iz same politike."""
    assert hint_policy.session_task_class(COMPUTATIONAL) == hint_policy.COMPUTATIONAL
    expected, basis = runner.resolve_expected_calls(_gate("first_hint"), COMPUTATIONAL)
    assert expected == 1
    assert basis == f"help_policy:{hint_policy.MODEL}"


def test_b_ladder_top_is_server_owned_even_for_a_computational_task():
    top = dict(COMPUTATIONAL, hint_level=config.MAX_HINT_LEVEL)
    expected, _ = runner.resolve_expected_calls(_gate("first_hint"), top)
    assert expected == 0


# ---------------------------------------------------------------------------
# C) PUNO RJEŠENJE — uvijek serversko
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("session", [PROPOSITIONAL, COMPUTATIONAL])
def test_c_full_solution_expects_exactly_zero_calls(session):
    gate = _gate("full_solution")
    assert gate.expected_calls == 0
    expected, basis = runner.resolve_expected_calls(gate, session)
    assert (expected, basis) == (0, "static_plan")


# ---------------------------------------------------------------------------
# D/E) POGREŠAN BROJ POZIVA MORA OBORITI SCENARIO
# ---------------------------------------------------------------------------

def _result(sdk_calls):
    """STVARNI `TurnResult` iz kapije — ne dvojnik, da ovaj test ne bi mogao
    ostati zelen dok se stvarni oblik rezultata mijenja."""
    from scratchpad.run_difficulty_canary import TurnResult

    gate = _gate("first_hint").scenario
    return TurnResult(
        scenario=gate.name, lesson_id=gate.lesson_id, lesson_title="Razlomci",
        path=gate.path, grade=gate.grade, request_type=gate.request_type,
        attempted=True, published=True, sdk_calls_this_turn=sdk_calls,
        effective_topic=gate.lesson_id, session_lesson_id_after=gate.lesson_id,
        published_task_text="Zadatak.")


def test_d_extra_call_when_policy_predicted_zero_fails_the_scenario():
    errors = runner._scenario_errors(_gate("first_hint"), _result(1), "", [],
                                     expected_calls=0)
    assert "expected_0_sdk_calls_got_1" in errors


def test_e_missing_call_when_policy_predicted_one_fails_the_scenario():
    errors = runner._scenario_errors(_gate("first_hint"), _result(0), "", [],
                                     expected_calls=1)
    assert "expected_1_sdk_calls_got_0" in errors


def test_e_scoring_without_a_frozen_expectation_is_refused():
    """Izvedeni scenario se NE SMIJE ocijeniti bez zamrznute vrijednosti."""
    with pytest.raises(runner.GateRefusal):
        runner._scenario_errors(_gate("first_hint"), _result(0), "", [])


# ---------------------------------------------------------------------------
# F/G) PLAN I PLAFON
# ---------------------------------------------------------------------------

def test_f_plan_total_is_static_eleven_plus_derived_hint():
    """Brza ruta: modelski scenario trosi 1 poziv, pa je staticki zbir 10.

    Invarijanta je nepromijenjena: jedina razlika izmedju dva DOSTIZNA
    staticka plana je izvedeni prvi hint (0 ili 1)."""
    plan = runner.build_release_gate_plan("0123456789abcdef" * 4)
    static_total = sum(item.expected_calls or 0 for item in plan)
    assert static_total == 11
    assert {static_total, static_total + 1} == {11, 12}
    # Plafon pokriva i uslovni recenzentski popravak svakog modelskog scenarija.
    assert runner.max_planned_calls(plan) == 23


# Kontrolni v1: stvarni zbir = planirano + eskalirano + kontrolni (fixture
# nosi kontrolni_sdk_calls=4), pa prolazne vrijednosti uključuju taj dodatak.
@pytest.mark.parametrize("planned,actual,expected_error", [
    (17, 21, None), (17, 17, "wrong_sdk_call_count"),
    (17, 22, "wrong_sdk_call_count"), (18, 22, None),
])
def test_f_offline_validator_requires_actual_to_equal_planned(
        planned, actual, expected_error, passing_document):
    document = dict(passing_document, planned_sdk_calls=planned, actual_sdk_calls=actual)
    errors = checker.validate_result(document)
    if expected_error is None:
        assert "wrong_sdk_call_count" not in errors
    else:
        assert expected_error in errors


@pytest.mark.parametrize("planned,escalated,actual,expected_error", [
    # Dokazana eskalacija se PRIZNAJE, ali samo tacno onoliko koliko je brojana
    # (fixture nosi i kontrolni_sdk_calls=4 u stvarnom zbiru).
    (10, 2, 16, None),
    (10, 0, 16, "wrong_sdk_call_count"),
    (10, 2, 17, "wrong_sdk_call_count"),
    (10, 2, 12, "wrong_sdk_call_count"),
])
def test_f_escalation_calls_must_be_counted_not_forgiven(
        planned, escalated, actual, expected_error, passing_document):
    """Uslovni recenzentski popravak se ne moze zamrznuti prije turna, ali se ne
    smije ni tiho progutati: artefakt mora nositi TACAN broj eskalacija."""
    document = dict(passing_document, planned_sdk_calls=planned,
                    escalated_sdk_calls=escalated, actual_sdk_calls=actual)
    errors = checker.validate_result(document)
    if expected_error is None:
        assert "wrong_sdk_call_count" not in errors
    else:
        assert expected_error in errors


def test_f_artifact_without_escalation_count_is_refused(passing_document):
    """Zatecen (stari) artefakt nema polje — i ne smije proci."""
    document = dict(passing_document)
    document.pop("escalated_sdk_calls")
    assert "missing_escalated_sdk_calls" in checker.validate_result(document)


def test_g_plan_may_never_exceed_the_ceiling(passing_document):
    assert runner.max_planned_calls(
        runner.build_release_gate_plan("0123456789abcdef" * 4)) <= runner.SDK_CALL_CEILING
    over = dict(passing_document, planned_sdk_calls=24, actual_sdk_calls=24)
    assert "planned_sdk_calls_above_ceiling" in checker.validate_result(over)


# ---------------------------------------------------------------------------
# H) GRANICA ODBIJANJA — prvi poziv IZNAD plafona
# ---------------------------------------------------------------------------

def test_h_first_call_above_the_ceiling_is_refused():
    from scratchpad.run_difficulty_canary import CountingLLM, SDKCallBudgetExceeded

    counter = CountingLLM(object(), runner.SDK_CALL_CEILING)
    for _ in range(runner.SDK_CALL_CEILING):
        counter._count("allowed")
    assert counter.call_count == runner.SDK_CALL_CEILING
    with pytest.raises(SDKCallBudgetExceeded):
        counter._count("above-ceiling")


def test_h_validator_requires_the_refusal_flag(passing_document):
    document = dict(passing_document)
    document["call_above_ceiling_refused"] = False
    assert "call_above_ceiling_not_refused" in checker.validate_result(document)


# ---------------------------------------------------------------------------
# I/J) POKRIVENOST I SIGURNOST OSTAJU
# ---------------------------------------------------------------------------

def test_i_every_required_scenario_role_is_still_demanded(passing_document):
    for dropped in ("first_hint", "full_solution", "semantic_fresh", "grade9"):
        document = dict(passing_document)
        document["scenarios"] = [row for row in passing_document["scenarios"]
                                 if row["role"] != dropped]
        errors = checker.validate_result(document)
        assert {"wrong_scenario_count", "required_scenarios_missing"} & set(errors), dropped


def test_j_correct_call_count_cannot_hide_a_failed_scenario(passing_document):
    """Tačan broj poziva NE SMIJE sam po sebi dati PASS."""
    for mutate, expected in (
            (lambda d: d["scenarios"][3].update({"errors": ["wrong_marked_answer"]}),
             "scenario_failed_or_skipped"),
            (lambda d: d["scenarios"][3]["result"].update({"published": False}),
             "scenario_failed_or_skipped"),
            (lambda d: d.update({"validation_failures": ["math_error"]}),
             "hidden_validation_failures"),
            (lambda d: d.update({"infrastructure_failures": ["timeout"]}),
             "infrastructure_failure"),
            (lambda d: d.update({"verdict": "FAIL"}), "verdict_is_not_pass"),
    ):
        import copy as _copy
        document = _copy.deepcopy(passing_document)
        mutate(document)
        assert expected in checker.validate_result(document)


# ---------------------------------------------------------------------------
# K/L) ARTEFAKT — stari pada, novi prolazi
# ---------------------------------------------------------------------------

def test_k_stale_nineteen_call_artifact_fails_offline_validation(passing_document):
    """Artefakt zatečene semantike (19/19, bez planiranog zbira) NE prolazi."""
    stale = dict(passing_document)
    stale.pop("planned_sdk_calls")
    stale.pop("call_above_ceiling_refused")
    stale.update({"sdk_call_ceiling": 19, "actual_sdk_calls": 19,
                  "twentieth_call_refused_before_sdk": True})
    errors = checker.validate_result(stale)
    assert {"missing_planned_sdk_calls", "wrong_sdk_call_ceiling",
            "call_above_ceiling_not_refused"} <= set(errors)


def test_l_current_format_artifact_validates(passing_document):
    assert checker.validate_result(passing_document) == []


@pytest.fixture
def passing_document():
    from datetime import datetime, timezone
    roles = ["fresh_level1", "correct_choice", "harder_level2", "first_hint",
             "full_solution", "easier_level1", "same_level_new", "contract_fresh",
             "contract_harder", "semantic_fresh", "semantic_harder", "migrated_deterministic",
             "grade7", "grade8", "grade9"]
    return {
        "campaign": "release-gate", "verdict": "PASS",
        "tested_commit_sha": "a" * 40, "tested_tree_hash": "b" * 40,
        "clean_worktree": True, "practice_pipeline": "universal_two_call",
        "difficulty_levels_enabled": True,
        "timeout_seconds": float(release_config.REQUIRED_RELEASE_ENV["AI_TUTOR_TIMEOUT"]),
        "release_configuration": dict(release_config.REQUIRED_RELEASE_ENV),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count": 15, "required_scenario_count": 15,
        # Kontrolni v1: plafon = Practice (23) + kontrolni (4); stvarni zbir
        # nosi i kontrolni pozive (17 + 0 + 4).
        "sdk_call_ceiling": 27, "planned_sdk_calls": 17, "actual_sdk_calls": 21,
        "escalated_sdk_calls": 0,
        "kontrolni_sdk_calls": 4, "kontrolni_max_calls": 4,
        "kontrolni_required_tests": 2,
        "kontrolni_tests": [
            {"oblast_id": "6-04", "grade": 6, "relative": "", "status": "ready",
             "sdk_calls": 2, "difficulty": "standard", "errors": []},
            {"oblast_id": "6-04", "grade": 6, "relative": "harder", "status": "ready",
             "sdk_calls": 2, "difficulty": "harder", "errors": []},
        ],
        "call_above_ceiling_refused": True,
        "twentieth_call_refused_before_sdk": True,
        "validation_failures": [], "infrastructure_failures": [],
        "scenarios": [{"role": role, "errors": [], "expected_sdk_calls": 0,
                       "result": {"attempted": True, "published": True,
                                  "failure_is_infrastructure": False}}
                      for role in roles],
    }
