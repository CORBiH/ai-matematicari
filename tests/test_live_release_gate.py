"""Pure tests for the permanent live-release plan and offline verifier."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tools import check_live_release_gate as checker
from tools import run_live_release_gate as runner


SHA = "a" * 40
TREE = "b" * 40


def _passing_document():
    roles = [
        "fresh_level1", "correct_choice", "harder_level2", "first_hint", "full_solution",
        "easier_level1", "same_level_new", "contract_fresh", "contract_harder",
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
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count": 12,
        "required_scenario_count": 12,
        "sdk_call_ceiling": 19,
        "actual_sdk_calls": 19,
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


def test_release_gate_plan_is_exactly_twelve_scenarios_and_nineteen_calls():
    plan = runner.build_release_gate_plan("0123456789abcdef" * 4)
    assert len(plan) == 12
    assert sum(item.expected_calls for item in plan) == 19
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
    skipped["actual_sdk_calls"] = 18
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


def test_gate_preconditions_accept_the_two_exact_runtime_flags(monkeypatch):
    monkeypatch.setattr(runner, "_git", lambda *args: "" if args[0] == "status" else SHA)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-offline-test-key")
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    assert runner._require_live_preconditions() == (SHA, SHA)


def test_offline_checker_rejects_legacy_or_missing_structured_runtime_identity():
    legacy = _passing_document()
    legacy["practice_pipeline"] = "legacy_single_call"
    assert "wrong_practice_pipeline" in checker.validate_result(
        legacy, expected_commit=SHA, expected_tree=TREE)
    missing = _passing_document()
    missing.pop("practice_pipeline")
    missing["difficulty_levels_enabled"] = False
    errors = checker.validate_result(missing, expected_commit=SHA, expected_tree=TREE)
    assert {"wrong_practice_pipeline", "difficulty_levels_not_enabled"} <= set(errors)


def test_public_router_reaches_universal_pipeline_and_not_legacy(monkeypatch):
    """Gate scenarios use the public Practice entrypoint; no private bypass."""
    from matbot import practice
    from matbot.session_store import SessionStore

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    called = []
    monkeypatch.setattr(practice.tutor_pipeline, "run_turn",
                        lambda store, llm, turn: called.append(turn) or {"status": "ready"})
    monkeypatch.setattr(practice, "_run_legacy_single_call_turn",
                        lambda *args: (_ for _ in ()).throw(AssertionError("legacy path invoked")))
    response = practice.run_practice_turn(SessionStore(), object(), {
        "session_id": "gate-route", "grade": 6, "selected_topic": "6-03-001",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "", "client_turn_id": "",
    })
    assert response == {"status": "ready"}
    assert len(called) == 1


def test_structured_release_runtime_preserves_deterministic_contract_call_budget(monkeypatch):
    from matbot import practice
    from matbot.session_store import SessionStore
    from tests.conftest import FakeLLM, make_output

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka."))
    response = practice.run_practice_turn(store, fake, {
        "session_id": "gate-contract", "grade": 6, "selected_topic": "6-04-009",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "", "client_turn_id": "",
    })
    assert response["status"] == "ready"
    assert fake.call_count == 1


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
        ceiling = 19
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
    assert "SDK CALLS: 9/19" in report
    assert "STATE PRESERVED: true" in report
    assert "SECRET" not in report


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

        def reviewer_turn(self, instructions, input_text):
            raise AssertionError("Reviewer must not run after first Tutor failure")

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    inner = FirstCallFailure()
    counter = canary.CountingLLM(inner, ceiling=19)
    report = canary.CanaryReport(campaign="release-gate", started_at="now", sdk_call_ceiling=19)
    scenario = canary.Scenario("first-failure", "6-03-001", 6, "non_contract", "",
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
        "difficulty_evidence": task.difficulty_evidence.model_copy(update={"operation_count": 2}),
    })
    draft = make_tutor_draft(new_task=task)
    reviewer = make_reviewer_final(final=draft)
    result = canary.TurnResult("difficulty", "6-03-004",
                               "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
                               "non_contract", 6, "")
    canary._record_rejected_generation_diagnostics(
        result, SimpleNamespace(last_tutor_output=draft, last_reviewer_output=reviewer))

    assert result.final_difficulty_target_level == 1
    assert result.final_difficulty_evidence["operation_count"] == 2
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
