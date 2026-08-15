"""Pure tests for the permanent live-release plan and offline verifier."""
import copy
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from matbot import config, release_config
from matbot.contracts import registry as contract_registry
from tools import check_live_release_gate as checker
from tools import run_live_release_gate as runner

ROOT = Path(__file__).resolve().parent.parent


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



SHA = "a" * 40
TREE = "b" * 40


def _passing_document():
    roles = [
        "fresh_level1", "correct_choice", "harder_level2", "first_hint", "full_solution",
        "easier_level1", "same_level_new", "contract_fresh", "contract_harder",
        "semantic_fresh", "semantic_harder", "migrated_deterministic",
        "grade7", "grade8", "grade9",
    ]
    return {
        "campaign": "release-gate",
        "verdict": "PASS",
        "tested_commit_sha": SHA,
        "tested_tree_hash": TREE,
        "clean_worktree": True,
        "practice_pipeline": "universal_two_call",
        "difficulty_levels_enabled": True,
        # ARTEFAKT MORA DOKAZATI ČIME JE MJERENO. Zvanična kampanja je jednom
        # prošla s rokom od 30 s dok produkcija radi na 45 s, jer je kapija od
        # deklarisanih vrijednosti provjeravala samo dvije. Provjera artefakta
        # zato traži i rok i cijelu primijenjenu konfiguraciju.
        "timeout_seconds": float(
            release_config.REQUIRED_RELEASE_ENV["AI_TUTOR_TIMEOUT"]),
        "release_configuration": dict(release_config.REQUIRED_RELEASE_ENV),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count": 15,
        "required_scenario_count": 15,
        "sdk_call_ceiling": 23,
        "planned_sdk_calls": 17,
        "escalated_sdk_calls": 0,
        "actual_sdk_calls": 17,
        "call_above_ceiling_refused": True,
        "twentieth_call_refused_before_sdk": True,
        "validation_failures": [],
        "infrastructure_failures": [],
        "scenarios": [
            {"role": role, "errors": [], "result": {
                "attempted": True, "published": True, "failure_is_infrastructure": False,
            }}
            for role in roles
        ],
    }


def test_release_gate_plan_covers_every_route_class():
    """Faza 4B: plan pokriva OBA puta — deterministicki K1/K3 (6-04-005, 1
    poziv) i semanticki put (6-04-009). Faza 4H: semanticka lekcija ima potpun
    deterministicki generator, pa njeni scenariji IZRICITO dokazuju NULA
    poziva; plafon je tada pao 23 → 19.

    SERVER-VLASNICKA POMOC: `full_solution` je uvijek serverski (0 poziva), a
    `first_hint` se IZVODI iz politike prije turna.

    BRZA RUTA: modelski podrzana lekcija trosi TACNO 1 poziv (ne 2), pa je
    staticki dio plana 10. Recenzentski popravak je USLOVAN i dodaje najvise
    jos jedan poziv po modelskom scenariju, sto plafon mora pokriti — zato je
    maksimum 21. Tacnost i dalje cuva ugovor po scenariju, koji trazi da drugi
    poziv bude RECENZENTSKI, nikad ponovljeni tutorski."""
    plan = runner.build_release_gate_plan("0123456789abcdef" * 4)
    assert len(plan) == 15
    # „Sutra imam kontrolni“ (v1): plafon = Practice plan (23) + najgori
    # kontrolni ishod (2 testa × 2 poziva). Practice dio plana je nepromijenjen.
    assert runner.max_planned_calls(plan) == 23
    assert runner.KONTROLNI_MAX_CALLS == 4
    assert runner.SDK_CALL_CEILING == 23 + runner.KONTROLNI_MAX_CALLS
    assert sum(item.expected_calls or 0 for item in plan) == 11
    # Pomoc nikad ne eskalira, pa joj plafon ne priznaje dodatni poziv.
    assert runner._MAX_CALLS_PER_TURN == 2
    by_role = {item.role: item for item in plan}
    # MIGRACIJA K1/K3: ugovorna lekcija vise nema vlastitu rutu. Scenario ostaje
    # i dalje trosi TACNO jedan poziv, ali ga trosi na zajednickoj brzoj ruti —
    # ugovor sada zivi kao ogranicenje u promptu i serverska provjera objave.
    assert by_role["contract_fresh"].scenario.path == "non_contract"
    assert by_role["contract_fresh"].expected_calls == 1
    assert by_role["contract_harder"].expected_calls == 1
    # MJERENO SLABA DETERMINISTICKA PORODICA sada trosi tacno jedan poziv.
    assert by_role["migrated_deterministic"].expected_calls == 1
    # JAKA PORODICA ostaje na nula poziva — selidba je selektivna, ne opsta.
    assert by_role["semantic_fresh"].expected_calls == 0
    assert contract_registry.contract_for(
        by_role["contract_fresh"].scenario.lesson_id) is not None
    assert by_role["semantic_fresh"].scenario.path == "non_contract"
    assert by_role["semantic_fresh"].expected_calls == 0
    assert by_role["semantic_harder"].expected_calls == 0
    # C) puno rjesenje je serversko — tacno 0, nikad raspon.
    assert by_role["full_solution"].expected_calls == 0
    # Prvi hint NEMA statickog broja: izvodi se iz politike prije turna.
    assert by_role["first_hint"].expected_calls is runner.DERIVED_FROM_HELP_POLICY
    assert [item.role for item in plan][:7] == [
        "fresh_level1", "correct_choice", "harder_level2", "first_hint", "full_solution",
        "easier_level1", "same_level_new",
    ]


def test_static_release_gate_checks_make_zero_sdk_calls():
    runner._run_static_checks()


def test_offline_result_checker_accepts_only_the_exact_current_commit_and_tree():
    document = _passing_document()
    assert checker.validate_result(document, expected_commit=SHA, expected_tree=TREE) == []
    assert "commit_sha_mismatch" in checker.validate_result(
        document, expected_commit="c" * 40, expected_tree=TREE,
    )
    assert "tree_hash_mismatch" in checker.validate_result(
        document, expected_commit=SHA, expected_tree="d" * 40,
    )


def test_offline_result_checker_fails_closed_for_age_count_skip_and_infrastructure():
    stale = _passing_document()
    stale["finished_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert "result_expired_or_invalid_time" in checker.validate_result(
        stale, expected_commit=SHA, expected_tree=TREE,
    )

    skipped = _passing_document()
    skipped["scenarios"][0]["result"]["attempted"] = False
    skipped["actual_sdk_calls"] = 16
    skipped["infrastructure_failures"] = ["timeout"]
    errors = checker.validate_result(skipped, expected_commit=SHA, expected_tree=TREE)
    assert {"scenario_failed_or_skipped", "wrong_sdk_call_count", "infrastructure_failure"} <= set(errors)


@pytest.mark.parametrize("pipeline", [None, "legacy_single_call", "universal", "typo"])
def test_gate_preconditions_accept_only_the_structured_pipeline(monkeypatch, pipeline):
    monkeypatch.setattr(runner, "_git", lambda *args: "" if args[0] == "status" else SHA)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-offline-test-key")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    if pipeline is None:
        monkeypatch.delenv("MATBOT_PRACTICE_PIPELINE", raising=False)
    else:
        monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", pipeline)
    with pytest.raises(runner.GateRefusal):
        runner._require_live_preconditions()


@pytest.mark.parametrize("difficulty", [None, "disabled", "enable", "true", "ENABLED", " enabled "])
def test_gate_preconditions_require_enabled_difficulty_controller(monkeypatch, difficulty):
    monkeypatch.setattr(runner, "_git", lambda *args: "" if args[0] == "status" else SHA)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-offline-test-key")
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    if difficulty is None:
        monkeypatch.delenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", raising=False)
    else:
        monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", difficulty)
    with pytest.raises(runner.GateRefusal):
        runner._require_live_preconditions()


def _apply_declaration(monkeypatch):
    """Postavi CIJELU auditiranu konfiguraciju, kao skriptni put kapije."""
    for name, value in release_config.REQUIRED_RELEASE_ENV.items():
        monkeypatch.setenv(name, value)
    # `AI_TIMEOUT_S` se u `matbot.config` čita PRI UVOZU, a u testnoj sviti je
    # modul odavno uvezen. Skriptni put kapije postavlja okruženje prije tog
    # uvoza; ovdje se isti ishod dobija izričitom zamjenom.
    monkeypatch.setattr(config, "AI_TIMEOUT_S",
                        float(release_config.REQUIRED_RELEASE_ENV["AI_TUTOR_TIMEOUT"]))


def test_gate_preconditions_accept_the_whole_audited_configuration(monkeypatch):
    monkeypatch.setattr(runner, "_git", lambda *args: "" if args[0] == "status" else SHA)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-offline-test-key")
    _apply_declaration(monkeypatch)
    assert runner._require_live_preconditions() == (SHA, SHA)


@pytest.mark.parametrize("name,wrong", [
    ("MATBOT_FAST_SINGLE_CALL_SCOPE", "lessons"),
    ("MATBOT_DETERMINISTIC_VARIETY_GATE", "disabled"),
    ("MATBOT_PRACTICE_SINGLE_HINT", "disabled"),
    ("MATBOT_ARCHETYPE_ROTATION", "disabled"),
    ("MATBOT_FORM_ROTATION", "disabled"),
])
def test_gate_preconditions_reject_a_route_changing_flag(monkeypatch, name, wrong):
    """Ranije je kapija provjeravala SAMO rutu i nivoe težine, pa je mogla
    izmjeriti drugu arhitekturu nego što produkcija izvršava."""
    monkeypatch.setattr(runner, "_git", lambda *args: "" if args[0] == "status" else SHA)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-offline-test-key")
    _apply_declaration(monkeypatch)
    monkeypatch.setenv(name, wrong)
    with pytest.raises(runner.GateRefusal):
        runner._require_live_preconditions()


@pytest.mark.parametrize("timeout", [30.0, 45.5, 60.0])
def test_gate_preconditions_require_the_production_timeout(monkeypatch, timeout):
    """ŽIVI NALAZ: kampanja je prošla s 30 s dok produkcija radi na 45 s, jer
    je rok provjeravan samo kao „pozitivan broj“."""
    monkeypatch.setattr(runner, "_git", lambda *args: "" if args[0] == "status" else SHA)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-offline-test-key")
    _apply_declaration(monkeypatch)
    monkeypatch.setattr(config, "AI_TIMEOUT_S", timeout)
    with pytest.raises(runner.GateRefusal):
        runner._require_live_preconditions()


def test_importing_the_gate_never_mutates_the_process_environment():
    """Bezuslovna primjena pri uvozu bi promijenila rutu Practice turnova
    svakom procesu koji ovaj modul samo uveze — uključujući testnu svitu."""
    import subprocess
    import sys as _sys
    clean = {key: value for key, value in os.environ.items()
             if key not in release_config.REQUIRED_RELEASE_ENV}
    clean["FLASK_SECRET_KEY"] = "test-only"
    result = subprocess.run(
        [_sys.executable, "-c",
         "import os, tools.run_live_release_gate;"
         "print('SCOPE', os.environ.get('MATBOT_FAST_SINGLE_CALL_SCOPE'))"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=clean, timeout=300)
    assert result.returncode == 0, result.stderr
    assert "SCOPE None" in result.stdout, result.stdout


def test_offline_checker_still_rejects_a_missing_runtime_identity():
    """POVLAČENJE (2026-08-14): polje `practice_pipeline` više ne postoji.

    Ranije je ovaj test dokazivao da artefakt s `legacy_single_call` ne smije
    autorizovati push. Ta vrijednost sada ne postoji ni kao izbor, pa se
    dokazuje ono što je i dalje istina: artefakt koji ne potvrđuje ostatak
    produkcijske konfiguracije i dalje pada."""
    missing = _passing_document()
    missing["difficulty_levels_enabled"] = False
    errors = checker.validate_result(missing, expected_commit=SHA, expected_tree=TREE)
    assert "difficulty_levels_not_enabled" in errors


def test_public_router_reaches_the_single_engine(monkeypatch):
    """Gate scenarios use the public Practice entrypoint; no private bypass.

    Poslije povlačenja starog motora nema više „druge rute“ koju bi trebalo
    isključiti — dokazuje se da javni ulaz vodi TAČNO u jedini motor."""
    from matbot import practice
    from matbot.session_store import SessionStore

    called = []
    monkeypatch.setattr(practice.tutor_pipeline, "run_turn",
                        lambda store, llm, turn: called.append(turn) or {"status": "ready"})
    response = practice.run_practice_turn(SessionStore(), object(), {
        "session_id": "gate-route", "grade": 6, "selected_topic": "6-03-001",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "", "client_turn_id": "",
    })
    assert response == {"status": "ready"}
    assert len(called) == 1


def test_contract_lesson_keeps_a_one_call_budget_on_the_fast_route(monkeypatch):
    """MIGRACIJA K1/K3: ugovorna lekcija vise NE trosi zaseban ugovorni poziv.

    Ranije je ovaj test dokazivao da 6-04-005 ide starim jednopozivnim
    ugovornim putem. Posto ugovor vise nije RUTA nego PODATAK, dokazuje se
    ono sto stvarno mora vrijediti: ista lekcija trosi TACNO jedan poziv i
    trosi ga na brzoj ruti, nikad na starom ugovornom pozivu."""
    from matbot import practice
    from matbot.session_store import SessionStore
    from tests.conftest import FakeLLM

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_SCOPE", "model_backed")

    class Recorder(FakeLLM):
        def __init__(self):
            super().__init__()
            self.stages = []

        def fast_turn(self, instructions, input_text, timeout_s=None):
            self.stages.append("fast_turn")
            raise RuntimeError("stop-after-route")

        def practice_turn(self, instructions, input_text):
            self.stages.append("practice_turn")
            raise RuntimeError("stop-after-route")

    store, fake = SessionStore(), Recorder()
    # Stub prekida turn odmah nakon izbora rute — mjeri se RUTA, ne objava.
    try:
        practice.run_practice_turn(store, fake, {
            "session_id": "gate-contract", "grade": 6, "selected_topic": "6-04-005",
            "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "student_question", "selected_option_id": "",
            "client_turn_id": "",
        })
    except RuntimeError:
        pass
    assert fake.stages == ["fast_turn"], fake.stages


def test_gate_harness_calls_the_public_practice_router(monkeypatch):
    from scratchpad import run_difficulty_canary as canary
    from matbot.session_store import SessionStore

    calls = []
    monkeypatch.setattr(canary.practice, "run_practice_turn", lambda store, llm, payload:
                        calls.append(payload) or {"status": "ready", "answer": "Evo zadatka.\n\nZadatak: x",
                                                    "effective_topic": payload["selected_topic"],
                                                    "answer_verdict": None, "next_state": {"task": {"options": []}}})

    class Capture:
        messages = []
        def reset(self): pass
        def safe_diagnostics(self): return []
    class LLM:
        ceiling = 21
        call_count = 0
        last_tutor_output = None
        last_reviewer_output = None
    scenario = canary.Scenario("public-route", "6-03-001", 6, "non_contract", "",
                                "public-route", "Daj mi zadatak.")
    report = canary.CanaryReport(campaign="release-gate", started_at="now", sdk_call_ceiling=19)
    canary._run_one_turn(SessionStore(), LLM(), Capture(), report, scenario, "release-gate")
    assert len(calls) == 1


def _gate(role):
    return next(item for item in runner.build_release_gate_plan("0123456789abcdef" * 4)
                if item.role == role)


def _structured_result(previous, target, *, final_target=None, valid=True, signature="new"):
    return SimpleNamespace(
        previous_level=previous, target_level=target, session_level_before=previous,
        session_level_after=target, reviewer_final_target_level=target if final_target is None else final_target,
        structured_package_validation_passed=valid,
        structured_package_validation_errors=[] if valid else ["difficulty evidence: insufficient"],
        reviewer_checks={"task_package_consistent": valid, "difficulty_evidence_valid": valid,
                         "task_signature_consistent": valid},
        committed_task_signature_matches_final=valid,
        final_task_signature_canonical=signature,
        final_structured_package_source="reviewer_final_task",
    )


def test_structured_harder_easier_and_new_task_gate_checks_use_final_package(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    assert runner._structured_transition_errors(_gate("harder_level2"),
                                                 _structured_result(1, 2)) == []
    assert runner._structured_transition_errors(_gate("easier_level1"),
                                                 _structured_result(2, 1)) == []
    assert runner._structured_transition_errors(_gate("same_level_new"),
                                                 _structured_result(1, 1, signature="new"),
                                                 {"structured_signature": "old"}) == []


def test_structured_gate_rejects_bad_evidence_target_or_reused_signature(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    assert "structured_package_validation_failed" in runner._structured_transition_errors(
        _gate("harder_level2"), _structured_result(1, 2, valid=False))
    assert "wrong_reviewer_final_target_level" in runner._structured_transition_errors(
        _gate("harder_level2"), _structured_result(1, 2, final_target=1))
    assert "same_level_task_reused_signature" in runner._structured_transition_errors(
        _gate("same_level_new"), _structured_result(1, 1, signature="same"),
        {"structured_signature": "same"})


def test_universal_gate_never_uses_prose_difficulty_parser(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setattr(runner.mcq_integrity, "difficulty_profile",
                        lambda *args: (_ for _ in ()).throw(AssertionError("prose parser called")))
    gate = _gate("harder_level2")
    result = _structured_result(1, 2)
    result.published = True
    result.sdk_calls_this_turn = 2
    result.lesson_id = gate.scenario.lesson_id
    result.effective_topic = gate.scenario.lesson_id
    result.session_lesson_id_after = gate.scenario.lesson_id
    result.published_task_text = "Izračunaj $2+2$."
    result.answer_text = "Evo težeg zadatka.\n\nZadatak: Izračunaj $2+2$."
    result.next_state_options = [{"id": key, "text": value}
                                 for key, value in zip("abcd", ("$4$", "$3$", "$5$", "$6$"))]
    result.next_state_options_match_session = True
    result.internal_correct_option_id_after = "a"
    result.expected_answer = "$4$"
    result.model_marked_option_value = "$4$"
    result.visible_correct_option_value = "$4$"
    result.intro_actual = result.intro_expected = "Evo težeg zadatka."
    assert "difficulty_direction_not_measurable" not in runner._scenario_errors(gate, result, "", [], {})


def test_canary_records_reviewer_final_task_as_the_final_package_source():
    from scratchpad import run_difficulty_canary as canary
    from tests.conftest import make_reviewer_final, make_task_payload, make_tutor_draft

    task = make_task_payload()
    draft = make_tutor_draft(new_task=task)
    reviewer = make_reviewer_final(final=draft)
    result = canary.TurnResult("source", "6-04-009", "lesson", "non_contract", 6, "",
                               target_level=1)
    options = [{"id": option.id, "text": option.text} for option in task.options]
    canary._record_answer_metadata(
        result,
        {"next_state": {"task": {"options": options}}},
        {"current_options": options, "correct_option_id": "a", "current_task_signature": {}},
        SimpleNamespace(last_tutor_output=draft, last_reviewer_output=reviewer),
    )
    assert result.final_task_answer_kind_source == "reviewer_final_task"
    assert result.final_structured_package_source == "reviewer_final_task"


def test_offline_checker_has_no_sdk_or_counting_llm_dependency():
    names = set(checker.__dict__)
    assert "CountingLLM" not in names
    assert "OpenAIPracticeLLM" not in names


def test_all_required_scenarios_are_required_for_pass():
    document = _passing_document()
    shortened = copy.deepcopy(document)
    shortened["scenarios"].pop()
    shortened["scenario_count"] = 11
    errors = checker.validate_result(shortened, expected_commit=SHA, expected_tree=TREE)
    assert "wrong_scenario_count" in errors
    assert "required_scenarios_missing" in errors


def test_failed_live_gate_console_summary_is_informative_and_does_not_echo_hidden_content():
    document = _passing_document()
    document.update({"verdict": "FAIL", "scenario_count": 6, "actual_sdk_calls": 9})
    document["scenarios"] = [{
        "role": "easier_level1", "errors": ["difficulty_direction_not_measurable"],
        "result": {
            "previous_level": 2, "target_level": 1, "session_level_after": 2,
            "session_unchanged_after_rejection": True,
            "expected_answer": "SECRET/hidden answer must never be printed",
        },
    }]
    lines = runner._failure_console_lines(
        document, runner.RESULT_DIR / f"{SHA}.json",
    )
    report = "\n".join(lines)
    assert "FAILED SCENARIO: easier_level1" in report
    assert "REASON: difficulty_direction_not_measurable" in report
    assert "LEVELS: previous=2 target=1 committed=2" in report
    assert "SDK CALLS: 9/17 (ceiling 23)" in report
    assert "STATE PRESERVED: true" in report
    assert "SECRET" not in report


def test_gate_harness_records_a_compact_approve_as_reviewer_owned(monkeypatch):
    """ŽIVI PAD PRVOG F4H GATEA (harder_level2, wrong_reviewer_final_target_level):
    kompaktno odobrenje ne vraća eho paketa, pa je harness čitao
    reviewer_final_target_level=None iako je objavljeni (odobreni) nacrt bio na
    tačnom nivou. Harness sada, kao i produkcija, na `approve` uzima NACRT kao
    recenzentov konačan paket."""
    from matbot.llm import LLMResult
    from matbot.session_store import SessionStore
    from scratchpad import run_difficulty_canary as canary
    from tests.conftest import (make_reviewer_final, make_task_payload,
                                make_tutor_draft)

    task = make_task_payload(
        text="Koji od ponuđenih brojeva je djeljiv sa 25?",
        options=("725", "714", "738", "741"), correct_option_index=0,
        expected="725")
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    compact = make_reviewer_final(final=draft).model_copy(update={"final": None})

    class CompactApprove:
        def __init__(self):
            self.queue = [draft, compact]

        def _result(self, output, input_text):
            bound = output
            try:
                from tests.conftest import FakeLLM
                FakeLLM._bind_universal_fixture_metadata(bound, input_text)
            except Exception:
                pass
            return LLMResult(output=bound, latency_ms=5,
                             usage={"input_tokens": 100, "output_tokens": 50})

        def tutor_turn(self, instructions, input_text):
            return self._result(self.queue.pop(0), input_text)

        def reviewer_turn(self, instructions, input_text, timeout_s=None,
                          model=None, reasoning_effort=None):
            return self._result(self.queue.pop(0), input_text)

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    counter = canary.CountingLLM(CompactApprove(), ceiling=19)
    report = canary.CanaryReport(campaign="release-gate", started_at="now",
                                 sdk_call_ceiling=19)
    scenario = canary.Scenario("compact-approve", "6-03-004", 6, "non_contract",
                               "", "compact-approve", "Daj mi zadatak.")
    result, stop = canary._run_one_turn(
        SessionStore(), counter, canary._LogCapture(), report, scenario,
        "release-gate")

    assert stop is False
    assert result.published is True
    assert result.sdk_calls_this_turn == 2
    assert result.reviewer_decision == "approve"
    assert result.reviewer_final_target_level == 1
    assert result.final_structured_package_source == "reviewer_final_task"
    assert result.structured_package_validation_passed is True


def test_gate_harness_records_a_deterministic_scenario_with_zero_calls(monkeypatch):
    """Faza 4H: semantic_fresh sada ide determinističkom strategijom — harness
    mora zabilježiti attempted/published i TAČNO nula SDK poziva, jer pre-push
    checker svaki red artefakta traži upravo u tom obliku."""
    from matbot.session_store import SessionStore
    from scratchpad import run_difficulty_canary as canary

    class NeverCalled:
        def tutor_turn(self, instructions, input_text):
            raise AssertionError("deterministic scenario must not call the model")

        def reviewer_turn(self, instructions, input_text, timeout_s=None,
                          model=None, reasoning_effort=None):
            raise AssertionError("deterministic scenario must not call the model")

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    counter = canary.CountingLLM(NeverCalled(), ceiling=19)
    report = canary.CanaryReport(campaign="release-gate", started_at="now",
                                 sdk_call_ceiling=19)
    scenario = canary.Scenario("semantic-det", "6-04-009", 6, "non_contract", "",
                               "semantic-det", "Daj mi zadatak.")
    result, stop = canary._run_one_turn(
        SessionStore(), counter, canary._LogCapture(), report, scenario,
        "release-gate")

    assert stop is False
    assert result.attempted is True
    assert result.published is True
    assert result.sdk_calls_this_turn == 0
    assert counter.call_count == 0
    assert result.published_task_text
    assert len(result.next_state_options) == 4


@pytest.mark.parametrize(
    ("error_type", "category", "infrastructure"),
    [
        ("LLMSchemaParseError", "llm_schema_parse_error", False),
        ("LLMTimeout", "llm_timeout", True),
        ("LLMUnavailable", "llm_sdk_error", True),
    ],
)
def test_gate_harness_preserves_safe_first_call_llm_failure_details(
        monkeypatch, error_type, category, infrastructure):
    """A first-call adapter failure is counted once and stays diagnosable offline."""
    from matbot.llm import LLMSchemaParseError, LLMTimeout, LLMUnavailable
    from matbot.session_store import SessionStore
    from scratchpad import run_difficulty_canary as canary

    errors = {
        "LLMSchemaParseError": LLMSchemaParseError,
        "LLMTimeout": LLMTimeout,
        "LLMUnavailable": LLMUnavailable,
    }

    class FirstCallFailure:
        def __init__(self):
            self.calls = []

        def tutor_turn(self, instructions, input_text):
            self.calls.append((instructions, input_text))
            raise errors[error_type](
                "adapter failure", diagnostics={
                    "status": "failed", "exception_summary": "Authorization: secret-value",
                    "unapproved_detail": "must not be persisted",
                })

        def reviewer_turn(self, instructions, input_text, timeout_s=None,
                          model=None, reasoning_effort=None):
            raise AssertionError("Reviewer must not run after first Tutor failure")

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    inner = FirstCallFailure()
    counter = canary.CountingLLM(inner, ceiling=19)
    report = canary.CanaryReport(campaign="release-gate", started_at="now", sdk_call_ceiling=19)
    # Kapacitetna ekspanzija: 6-03-001 sada ide determinističkom strategijom
    # (0 poziva), pa prvi poziv modela mora simulirati lekcija koja je OSTALA
    # na model-putu.
    scenario = canary.Scenario("first-failure", "6-04-001", 6, "non_contract", "",
                                "first-failure", "Daj mi zadatak.")
    result, stop = canary._run_one_turn(
        SessionStore(), counter, canary._LogCapture(), report, scenario, "release-gate")

    assert stop is False
    assert len(inner.calls) == counter.call_count == result.sdk_calls_this_turn == 1
    assert result.llm_failure_stage == "tutor"
    assert result.llm_failure_category == category
    assert result.failure_class == category
    assert result.failure_class != "unknown_rejection"
    assert result.failure_is_infrastructure is infrastructure
    assert result.session_unchanged_after_rejection is True
    assert result.llm_failure_diagnostics["status"] == "failed"
    assert result.llm_failure_diagnostics["exception_summary"] == "[REDACTED]"
    assert "unapproved_detail" not in result.llm_failure_diagnostics
    assert "secret-value" not in str(result.llm_failure_diagnostics)


def test_publication_rejection_is_never_classified_as_unknown():
    from scratchpad import run_difficulty_canary as canary

    message = (
        "tutor_rejected request_id=abc topic=6-03-004 stage=publication "
        "intent=generate_task detail=task lesson ID does not match selected lesson"
    )
    assert canary._classify_failure([message]) == "publication_validation_rejection"


def test_contradictory_reviewer_approval_is_classified_as_reviewer_payload_rejection():
    """Živi gate cb80b92 je ovu kontradikciju prijavio kao publication rejection.

    Recenzent je odobrio zadatak čiji je NJEGOV VLASTITI dokaz van traženog
    nivoa; server je to hvatao tek u objavi, pa je gate pogrešno optuživao
    završnu validaciju umjesto recenzentovog payloada. Sada se odbija na
    recenzentu i klasa mora biti `reviewer_payload_rejection`."""
    from scratchpad import run_difficulty_canary as canary
    from matbot.tutor.schema import REVIEWER_EVIDENCE_OUTSIDE_TARGET

    message = (
        "tutor_rejected request_id=abc topic=7-04-021 stage=reviewer_payload "
        f"intent=generate_task detail={REVIEWER_EVIDENCE_OUTSIDE_TARGET}: "
        "decision=approve target_level=1 "
        "errors=level_1_is_not_direct_introductory_application"
    )
    assert canary._classify_failure([message]) == "reviewer_payload_rejection"
    # Zatečena klasifikacija objave ostaje netaknuta.
    assert canary._classify_failure([
        "tutor_rejected request_id=abc topic=7-04-021 stage=publication "
        "intent=generate_task detail=difficulty evidence: "
        "level_1_is_not_direct_introductory_application"
    ]) == "publication_validation_rejection"


def test_gate_identity_diagnostics_record_only_safe_title_match_facts():
    from scratchpad import run_difficulty_canary as canary
    from tests.conftest import make_reviewer_final, make_task_payload, make_tutor_draft

    task = make_task_payload().model_copy(update={
        "selected_lesson_id": "6-03-004", "selected_lesson_title": "Drugi prikazni naslov",
    })
    draft = make_tutor_draft(new_task=task)
    reviewer = make_reviewer_final(final=draft)
    result = canary.TurnResult("identity", "6-03-004",
                               "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
                               "non_contract", 6, "")
    canary._record_rejected_generation_diagnostics(
        result, SimpleNamespace(last_tutor_output=draft, last_reviewer_output=reviewer))

    assert result.canonical_context_lesson_id == "6-03-004"
    assert result.canonical_context_lesson_title == result.lesson_title
    assert result.tutor_returned_lesson_id == "6-03-004"
    assert result.reviewer_final_lesson_id == "6-03-004"
    assert result.tutor_title_matched_canonical is False
    assert result.reviewer_final_title_matched_canonical is False
    assert result.title_canonicalized is True


def test_rejected_package_diagnostics_include_closed_difficulty_evidence_and_codes():
    from scratchpad import run_difficulty_canary as canary
    from tests.conftest import make_reviewer_final, make_task_payload, make_tutor_draft

    task = make_task_payload().model_copy(update={
        "selected_lesson_id": "6-03-004",
        "selected_lesson_title": "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
    })
    task = task.model_copy(update={
        "difficulty_evidence": task.difficulty_evidence.model_copy(update={"operation_count": 3}),
    })
    draft = make_tutor_draft(new_task=task)
    reviewer = make_reviewer_final(final=draft)
    result = canary.TurnResult("difficulty", "6-03-004",
                               "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
                               "non_contract", 6, "")
    canary._record_rejected_generation_diagnostics(
        result, SimpleNamespace(last_tutor_output=draft, last_reviewer_output=reviewer))

    assert result.final_difficulty_target_level == 1
    assert result.final_difficulty_evidence["operation_count"] == 3
    assert result.final_difficulty_validator_errors == [
        "level_1_is_not_direct_introductory_application"
    ]


def test_canary_diagnostics_distinguish_tutor_reviewer_and_authoritative_evidence():
    from scratchpad import run_difficulty_canary as canary
    from tests.conftest import make_reviewer_final, make_task_payload, make_tutor_draft

    tutor_task = make_task_payload().model_copy(update={
        "selected_lesson_id": "6-03-004",
        "selected_lesson_title": "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
    })
    tutor_task = tutor_task.model_copy(update={
        "difficulty_evidence": tutor_task.difficulty_evidence.model_copy(update={
            "condition_count": 2, "operation_count": 2, "combines_concepts": True,
        }),
    })
    draft = make_tutor_draft(new_task=tutor_task)
    reviewer = make_reviewer_final(
        final=draft,
        reviewed_difficulty_evidence=make_task_payload().difficulty_evidence,
    )
    result = canary.TurnResult("cross-evidence", "6-03-004",
                               "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
                               "non_contract", 6, "", target_level=1)
    canary._record_rejected_generation_diagnostics(
        result, SimpleNamespace(last_tutor_output=draft, last_reviewer_output=reviewer))

    assert result.tutor_difficulty_evidence == tutor_task.difficulty_evidence.model_dump()
    assert result.reviewer_difficulty_evidence == reviewer.reviewed_difficulty_evidence.model_dump()
    assert result.difficulty_evidence_matched is False
    assert result.difficulty_evidence_differing_fields == [
        "combines_concepts", "condition_count", "operation_count",
    ]
    assert result.difficulty_evidence_corrected is True
    assert result.final_difficulty_evidence_source == "reviewer"
    assert result.final_difficulty_evidence == reviewer.reviewed_difficulty_evidence.model_dump()
    assert result.final_difficulty_validator_errors == []


def test_package_ownership_is_proven_per_route():
    """Vlasnistvo nad objavljenim paketom dokazuje se po ruti koja je isla.

    Zivi nalaz (zvanicna kapija): trazilo se da paket UVIJEK bude recenzentski
    — invarijanta univerzalnog dvopozivnog puta. Na brzoj ruti recenzent
    legitimno ne radi, pa je ispravan turn padao s tri greske odjednom.
    Provjera nije ukinuta nego preslikana: svaka ruta dokazuje SVOG
    verifikatora i SVOJ ciljni nivo."""
    # HERMETICKI: scenario se sastavlja ovdje umjesto da se gradi cijeli plan
    # kapije. Gradnja plana dodiruje kurikulum i rute lekcija, a ovaj test
    # provjerava samo cistu funkciju bodovanja.
    from scratchpad.run_difficulty_canary import Scenario

    gate = runner.GateScenario(
        "harder_level2",
        Scenario("release_gate_core_harder_level1_to_2", "6-04-001", 6,
                 "non_contract", "harder", "release-core", "Daj mi tezi zadatak.",
                 1, "task_generation", ""),
        1)

    def result(source, *, reviewer_level=None, tutor_level=None, checks=None):
        return SimpleNamespace(
            previous_level=1, target_level=2, session_level_before=1,
            session_level_after=2, reviewer_final_target_level=reviewer_level,
            tutor_proposed_target_level=tutor_level,
            final_structured_package_source=source,
            structured_package_validation_passed=True,
            structured_package_validation_errors=[],
            reviewer_checks=checks or {},
            committed_task_signature_matches_final=True,
            committed_task_signature="sig", final_task_signature="sig")

    all_true = {"task_package_consistent": True, "difficulty_evidence_valid": True,
                "task_signature_consistent": True}
    # Brza ruta: paket je tutorski, a nivo mora biti serverski potvrdjen.
    assert runner._structured_transition_errors(
        gate, result("tutor_task", tutor_level=2), None) == []
    assert "wrong_fast_route_declared_target_level" in runner._structured_transition_errors(
        gate, result("tutor_task", tutor_level=3), None)
    # Eskalirani turn: puni recenzentski ugovor ostaje na snazi.
    assert runner._structured_transition_errors(
        gate, result("reviewer_final_task", reviewer_level=2, checks=all_true), None) == []
    assert "wrong_reviewer_final_target_level" in runner._structured_transition_errors(
        gate, result("reviewer_final_task", reviewer_level=3, checks=all_true), None)
    assert "reviewer_structured_checks_not_all_true" in runner._structured_transition_errors(
        gate, result("reviewer_final_task", reviewer_level=2,
                     checks={"task_package_consistent": True}), None)
    # Paket bez poznatog vlasnika i dalje pada zatvoreno.
    assert any(e.startswith("final_package_has_no_known_owner")
               for e in runner._structured_transition_errors(gate, result("mystery"), None))


def test_intro_must_be_server_owned_and_truthful():
    """Uvod se dokazuje kao SERVERSKI i ISTINIT, ne kao odredjeni string.

    Zivi nalaz (zvanicna kapija, scenario `same_level_new`): postoje DVIJE
    serverske tabele uvoda — legacy `matbot/practice.py` i aktivna
    `matbot/tutor/pipeline.py`. Kapija je poredila s legacy tabelom, pa je
    potpuno ispravan turn („Evo sljedeceg zadatka.“ na `next_task`) padao kao
    „untruthful_intro“."""
    from matbot.tutor import pipeline as tutor_pipeline

    def result(actual, expected, before=1, after=1):
        return SimpleNamespace(intro_actual=actual, intro_expected=expected,
                               session_level_before=before, session_level_after=after)

    server_next = tutor_pipeline._NEW_TASK_INTRO["next_task"]
    # Serverski uvod iz AKTIVNE tabele prolazi i kad se razlikuje od legacy.
    assert runner._intro_errors(result(server_next, "Evo zadatka.")) == []
    # Tacno poklapanje i dalje prolazi.
    assert runner._intro_errors(result("Evo zadatka.", "Evo zadatka.")) == []
    # Modelova proza NIJE serverski uvod.
    assert runner._intro_errors(
        result("Naravno, evo jednog zanimljivog zadatka!", "Evo zadatka.")) == [
        "intro_is_not_server_owned"]
    assert runner._intro_errors(result(None, "Evo zadatka.")) == ["intro_is_not_server_owned"]
    # Neistinita tvrdnja o promjeni tezine i dalje pada (nalaz F09/F10).
    assert runner._intro_errors(
        result(tutor_pipeline._NEW_TASK_INTRO["harder_task"], "Evo zadatka.",
               before=3, after=3)) == ["untruthful_intro_claims_harder"]
    assert runner._intro_errors(
        result(tutor_pipeline._NEW_TASK_INTRO["easier_task"], "Evo zadatka.",
               before=1, after=1)) == ["untruthful_intro_claims_easier"]
    # Istinita tvrdnja o promjeni prolazi.
    assert runner._intro_errors(
        result(tutor_pipeline._NEW_TASK_INTRO["harder_task"], "Evo zadatka.",
               before=1, after=2)) == []
