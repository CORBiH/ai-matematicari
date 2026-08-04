"""Pure tests for the permanent live-release plan and offline verifier."""
import copy
from datetime import datetime, timedelta, timezone

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
