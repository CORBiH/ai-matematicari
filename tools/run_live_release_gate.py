"""Manual, commit-bound real-model release gate for MAT-BOT behavior.

This program is deliberately never invoked by the application or the hook.
It uses the real Practice entrypoint and the existing ``CountingLLM`` wrapper,
but only after a human starts it in a clean, committed worktree with an API key
already inherited by that shell.  It never loads ``.env`` and never prints a
secret.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT / "scratchpad" / "live_release_gate"
CORE_DIVISIBILITY = ("6-03-004", 6)
CORE_CONTRACT = ("6-04-009", 6)
REQUIRED_SCENARIO_COUNT = 12
SDK_CALL_CEILING = 19

sys.path.insert(0, str(ROOT))

from matbot import config, feedback, mathsafe, mcq_integrity, practice  # noqa: E402
from matbot.contracts import registry as contract_registry  # noqa: E402
from matbot.llm import OpenAIPracticeLLM  # noqa: E402
from matbot.session_store import SessionStore  # noqa: E402
from matbot.topics import lesson_info  # noqa: E402
from scratchpad.run_difficulty_canary import (  # noqa: E402
    CanaryReport, CountingLLM, SDKCallBudgetExceeded, Scenario, _LogCapture,
    _has_disallowed_control_character, _run_one_turn,
)


class GateRefusal(RuntimeError):
    """A precondition failed before any SDK delegation was possible."""


@dataclass(frozen=True)
class GateScenario:
    role: str
    scenario: Scenario
    expected_calls: int


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    if completed.returncode:
        raise GateRefusal("Git metadata cannot be resolved for the live release gate.")
    return completed.stdout.strip()


def _head_metadata() -> tuple[str, str]:
    sha = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    if not sha or not tree:
        raise GateRefusal("HEAD or its tree hash is unavailable.")
    return sha, tree


def _require_live_preconditions() -> tuple[str, str]:
    if _git("status", "--porcelain"):
        raise GateRefusal("Worktree is dirty; commit or stash changes before the live release gate.")
    if not bool((os.environ.get("OPENAI_API_KEY", "") or "").strip()):
        raise GateRefusal("OPENAI_API_KEY is not present in this shell environment.")
    if (os.environ.get("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "") or "").strip().lower() != "enabled":
        raise GateRefusal("MATBOT_PRACTICE_DIFFICULTY_LEVELS must be exactly enabled.")
    if (os.environ.get("MATBOT_PRACTICE_PIPELINE", "") or "").strip().lower() == "universal_two_call":
        raise GateRefusal("MATBOT_PRACTICE_PIPELINE=universal_two_call is not a valid release-gate runtime.")
    if not isinstance(config.AI_TIMEOUT_S, (int, float)) or config.AI_TIMEOUT_S <= 0:
        raise GateRefusal("AI_TUTOR_TIMEOUT runtime configuration is invalid.")
    return _head_metadata()


def _all_lessons_for_grade(grade: int) -> list[dict]:
    data_path = ROOT / "data" / "topics.json"
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateRefusal("Canonical curriculum data is unavailable.") from exc
    rows = payload.get("grades", {}).get(str(grade), {}).get("lessons", [])
    return [row for row in rows if isinstance(row, dict) and row.get("id") and row.get("title")]


def _select_rotating_lesson(grade: int, commit_sha: str) -> tuple[str, int]:
    """Choose a reproducible eligible non-contract lesson from the tested SHA."""
    candidates = []
    for row in _all_lessons_for_grade(grade):
        lesson_id = str(row["id"])
        title = str(row["title"])
        if lesson_id in {CORE_DIVISIBILITY[0], CORE_CONTRACT[0]}:
            continue
        if contract_registry.contract_for(lesson_id) is not None:
            continue
        if grade == 9 and not any(word in title.lower() for word in (
                "jednačin", "jednacin", "sistem", "tekstual", "algebr")):
            continue
        candidates.append(lesson_id)
    if not candidates:
        raise GateRefusal(f"No eligible canonical non-contract Grade-{grade} lesson exists.")
    candidates.sort()
    offset = int(commit_sha[:16], 16) % len(candidates)
    return candidates[offset], grade


def build_release_gate_plan(commit_sha: str) -> tuple[GateScenario, ...]:
    """The exact 12-scenario / 19-call plan, pure except curriculum lookup."""
    grade7 = _select_rotating_lesson(7, commit_sha)
    grade8 = _select_rotating_lesson(8, commit_sha)
    grade9 = _select_rotating_lesson(9, commit_sha)

    def scenario(name, lesson, request, session, message, calls, *, path="non_contract",
                 requires=None, interaction="task_generation", intent="", role):
        return GateScenario(
            role,
            Scenario(name, lesson[0], lesson[1], path, request, session, message,
                     requires, interaction, intent),
            calls,
        )

    return (
        scenario("release_gate_divisibility_fresh_level1", CORE_DIVISIBILITY, "", "release-core",
                 "Daj mi zadatak.", 2, role="fresh_level1"),
        scenario("release_gate_correct_committed_choice", CORE_DIVISIBILITY, "", "release-core", "", 1,
                 requires=1, interaction="correct_choice", role="correct_choice"),
        scenario("release_gate_divisibility_harder_level1_to_2", CORE_DIVISIBILITY, "harder",
                 "release-core", "Daj mi teži zadatak.", 2, requires=1, role="harder_level2"),
        scenario("release_gate_first_hint", CORE_DIVISIBILITY, "", "release-core", "Ne znam.", 1,
                 requires=2, interaction="hint", intent="hint_request", role="first_hint"),
        scenario("release_gate_full_solution", CORE_DIVISIBILITY, "", "release-core", "Uradi ga ti.", 1,
                 requires=2, interaction="full_solution", intent="solution_request", role="full_solution"),
        scenario("release_gate_divisibility_easier_level2_to_1", CORE_DIVISIBILITY, "easier",
                 "release-core", "Daj mi lakši zadatak.", 2, requires=2, role="easier_level1"),
        scenario("release_gate_divisibility_new_same_level", CORE_DIVISIBILITY, "", "release-core",
                 "Daj mi novi zadatak.", 2, requires=1, role="same_level_new"),
        scenario("release_gate_contract_fresh", CORE_CONTRACT, "", "release-contract", "Daj mi zadatak.", 1,
                 path="contract", role="contract_fresh"),
        scenario("release_gate_contract_harder", CORE_CONTRACT, "harder", "release-contract",
                 "Daj mi teži zadatak.", 1, path="contract", requires=1, role="contract_harder"),
        scenario("release_gate_grade7_rotating", grade7, "", "release-grade7", "Daj mi zadatak.", 2,
                 role="grade7"),
        scenario("release_gate_grade8_rotating", grade8, "", "release-grade8", "Daj mi zadatak.", 2,
                 role="grade8"),
        scenario("release_gate_grade9_rotating", grade9, "", "release-grade9", "Daj mi zadatak.", 2,
                 role="grade9"),
    )


def _task_output_errors(result) -> list[str]:
    errors = []
    if not result.published_task_text:
        return ["missing_published_task"]
    if result.answer_text == practice.SAFE_ERROR_MESSAGE:
        errors.append("generic_failure_response")
    if _has_disallowed_control_character(result.answer_text) or _has_disallowed_control_character(result.published_task_text):
        errors.append("control_character_in_output")
    if (result.answer_text or "").rstrip().endswith("\\"):
        errors.append("dangling_terminal_backslash")
    if mathsafe.find_unsafe_math_issues(result.published_task_text or ""):
        errors.append("malformed_task_math")
    if len(result.next_state_options) != 4:
        errors.append("missing_options")
        return errors
    options = result.next_state_options
    ids = [option.get("id") for option in options if isinstance(option, dict)]
    texts = [option.get("text", "") for option in options if isinstance(option, dict)]
    if len(ids) != 4 or len(set(ids)) != 4 or len(set(texts)) != 4:
        errors.append("ambiguous_option_structure")
        return errors
    if result.next_state_options_match_session is not True:
        errors.append("next_state_options_not_committed_options")
    if result.internal_correct_option_id_after not in ids:
        errors.append("marked_option_missing")
        return errors
    marked_index = ids.index(result.internal_correct_option_id_after)
    failure, evaluation = mcq_integrity.publication_failure(
        result.published_task_text, texts, marked_index, result.expected_answer or "",
    )
    if failure:
        errors.append(failure)
    if evaluation.applicable and result.model_marked_option_value != result.visible_correct_option_value:
        errors.append("explanation_answer_mismatch")
    return errors


def _transition_errors(role: str, result) -> list[str]:
    expected = {
        "fresh_level1": (1, 1, 1, 1),
        "harder_level2": (1, 2, 1, 2),
        "easier_level1": (2, 1, 2, 1),
        "same_level_new": (1, 1, 1, 1),
        "contract_fresh": (1, 1, 1, 1),
        "contract_harder": (1, 2, 1, 2),
        "grade7": (1, 1, 1, 1),
        "grade8": (1, 1, 1, 1),
        "grade9": (1, 1, 1, 1),
    }.get(role)
    if expected is None:
        return []
    actual = (result.previous_level, result.target_level,
              result.session_level_before, result.session_level_after)
    return [] if actual == expected else [f"incorrect_transition:{actual!r}"]


def _scenario_errors(gate: GateScenario, result, prior_task: str, prior_options: Iterable[dict]) -> list[str]:
    errors = []
    if not result.published:
        errors.append(result.failure_class or "turn_not_published")
        if result.session_unchanged_after_rejection is not True:
            errors.append("state_mutation_after_rejection")
        return errors
    if result.sdk_calls_this_turn != gate.expected_calls:
        errors.append(f"expected_{gate.expected_calls}_sdk_calls_got_{result.sdk_calls_this_turn}")
    if result.lesson_id != gate.scenario.lesson_id or result.effective_topic != gate.scenario.lesson_id \
            or result.session_lesson_id_after != gate.scenario.lesson_id:
        errors.append("wrong_lesson")
    errors.extend(_transition_errors(gate.role, result))
    if gate.role in {"fresh_level1", "harder_level2", "easier_level1", "same_level_new",
                     "contract_fresh", "contract_harder", "grade7", "grade8", "grade9"}:
        errors.extend(_task_output_errors(result))
        if result.intro_actual != result.intro_expected:
            errors.append("untruthful_intro")
    if gate.role == "harder_level2":
        before = mcq_integrity.difficulty_profile(
            prior_task, [option.get("text", "") for option in prior_options]
        )
        after = mcq_integrity.difficulty_profile(
            result.published_task_text or "", [option.get("text", "") for option in result.next_state_options]
        )
        if not before.measurable or not after.measurable or after.level <= before.level:
            errors.append("difficulty_direction_not_measurable")
    if gate.role == "easier_level1":
        before = mcq_integrity.difficulty_profile(
            prior_task, [option.get("text", "") for option in prior_options]
        )
        after = mcq_integrity.difficulty_profile(
            result.published_task_text or "", [option.get("text", "") for option in result.next_state_options]
        )
        if not before.measurable or not after.measurable or after.level >= before.level:
            errors.append("difficulty_direction_not_measurable")
    if gate.role == "correct_choice":
        if result.answer_verdict != "correct" or not result.task_completed_after:
            errors.append("correct_committed_answer_not_recognized")
        if result.internal_correct_option_id_before != result.internal_correct_option_id_after:
            errors.append("correct_option_changed_during_click")
    if gate.role == "first_hint":
        text = result.answer_text or ""
        if not text or text == practice.SAFE_ERROR_MESSAGE:
            errors.append("useless_hint")
        if feedback.ensure_hint_makes_progress(result.published_task_text or "", text) != text:
            errors.append("hint_does_not_make_valid_progress")
        if feedback.leaks_answer(text, result.internal_correct_option_value or "", result.expected_answer or ""):
            errors.append("first_hint_reveals_answer")
    if gate.role == "full_solution":
        text = result.answer_text or ""
        if not text or text == practice.SAFE_ERROR_MESSAGE or mathsafe.find_unsafe_math_issues(text):
            errors.append("incomplete_or_malformed_full_solution")
        if not result.task_completed_after or result.revealed_correct_option_id != result.internal_correct_option_id_after:
            errors.append("full_solution_did_not_complete_committed_task")
    return errors


def _selected_lessons(plan: Iterable[GateScenario]) -> list[dict]:
    rows = []
    for gate in plan:
        if gate.role not in {"grade7", "grade8", "grade9"}:
            continue
        lesson = lesson_info(gate.scenario.grade, gate.scenario.lesson_id) or {}
        rows.append({"role": gate.role, "grade": gate.scenario.grade,
                     "id": gate.scenario.lesson_id, "title": lesson.get("title", "")})
    return rows


def _failure_console_lines(document: dict, result_path: Path) -> list[str]:
    """Return concise, safe diagnostics for a persisted failed gate result."""
    scenarios = document.get("scenarios") or []
    failed = next((row for row in scenarios if row.get("errors")), None)
    if failed is None and scenarios:
        failed = scenarios[-1]
    failed = failed or {}
    result = failed.get("result") if isinstance(failed.get("result"), dict) else {}
    reasons = failed.get("errors") or document.get("validation_failures") or ["unknown_failure"]
    relative_path = result_path.relative_to(ROOT).as_posix()
    return [
        f"FAILED SCENARIO: {failed.get('role', 'unknown')}",
        f"COMPLETED SCENARIOS: {document.get('scenario_count', 0)}/{REQUIRED_SCENARIO_COUNT}",
        f"REASON: {reasons[0]}",
        "LEVELS: previous={previous} target={target} committed={committed}".format(
            previous=result.get("previous_level", "-"), target=result.get("target_level", "-"),
            committed=result.get("session_level_after", "-"),
        ),
        f"SDK CALLS: {document.get('actual_sdk_calls', 0)}/{SDK_CALL_CEILING}",
        "STATE PRESERVED: " + str(
            result.get("session_unchanged_after_rejection") is True
        ).lower(),
        f"RESULT: {relative_path}",
    ]


def run_live_release_gate() -> int:
    commit_sha, tree_hash = _require_live_preconditions()
    plan = build_release_gate_plan(commit_sha)
    if len(plan) != REQUIRED_SCENARIO_COUNT or sum(item.expected_calls for item in plan) != SDK_CALL_CEILING:
        raise GateRefusal("The committed release-gate plan is not the required 12-scenario/19-call plan.")

    started = datetime.now(timezone.utc)
    store = SessionStore()
    llm = CountingLLM(OpenAIPracticeLLM(), SDK_CALL_CEILING)
    capture = _LogCapture()
    report = CanaryReport(campaign="release-gate", started_at=started.isoformat(),
                          model=config.OPENAI_MODEL_TEXT,
                          reasoning_effort=config.REASONING_EFFORT,
                          timeout_seconds=config.AI_TIMEOUT_S,
                          sdk_call_ceiling=SDK_CALL_CEILING)
    import logging
    matbot_logger = logging.getLogger("matbot")
    previous_level = matbot_logger.level
    matbot_logger.setLevel(logging.INFO)
    matbot_logger.addHandler(capture)
    scenario_rows = []
    failures: list[str] = []
    infrastructure_failures: list[str] = []
    try:
        for gate in plan:
            before = store.peek(gate.scenario.session_id) or {}
            result, stop = _run_one_turn(store, llm, capture, report, gate.scenario, "release-gate")
            errors = _scenario_errors(gate, result, before.get("current_task", ""),
                                      before.get("current_options", []))
            scenario_rows.append({"role": gate.role, "expected_sdk_calls": gate.expected_calls,
                                  "errors": errors, "result": asdict(result)})
            if result.failure_is_infrastructure:
                infrastructure_failures.append(gate.role)
            if errors or stop:
                failures.extend(f"{gate.role}:{error}" for error in (errors or [result.stop_triggered or "stopped"]))
                break
        cap_probe_refused = False
        if not failures and llm.call_count == SDK_CALL_CEILING:
            try:
                llm._count("release_gate_twentieth_call_probe")
            except SDKCallBudgetExceeded:
                cap_probe_refused = True
            else:  # pragma: no cover - defensive; _count must never allow this
                failures.append("twentieth_sdk_call_was_not_refused")
        else:
            cap_probe_refused = False
    finally:
        matbot_logger.removeHandler(capture)
        matbot_logger.setLevel(previous_level)

    finished = datetime.now(timezone.utc)
    all_required_completed = len(scenario_rows) == REQUIRED_SCENARIO_COUNT and not failures
    passed = bool(all_required_completed and llm.call_count == SDK_CALL_CEILING
                  and cap_probe_refused and not infrastructure_failures)
    document = {
        "campaign": "release-gate",
        "verdict": "PASS" if passed else "FAIL",
        "tested_commit_sha": commit_sha,
        "tested_tree_hash": tree_hash,
        "clean_worktree": True,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "result_age_basis": "finished_at",
        "model": config.OPENAI_MODEL_TEXT,
        "reasoning_effort": config.REASONING_EFFORT,
        "timeout_seconds": config.AI_TIMEOUT_S,
        "selected_lessons": _selected_lessons(plan),
        "scenario_count": len(scenario_rows),
        "required_scenario_count": REQUIRED_SCENARIO_COUNT,
        "sdk_call_ceiling": SDK_CALL_CEILING,
        "actual_sdk_calls": llm.call_count,
        "twentieth_call_refused_before_sdk": cap_probe_refused,
        "scenarios": scenario_rows,
        "validation_failures": failures,
        "infrastructure_failures": infrastructure_failures,
        "final_verdict": "LIVE RELEASE GATE PASS — PUSH ALLOWED" if passed
                         else "LIVE RELEASE GATE FAILED — PUSH BLOCKED",
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, ensure_ascii=False, indent=2)
    result_path = RESULT_DIR / f"{commit_sha}.json"
    result_path.write_text(encoded, encoding="utf-8")
    (RESULT_DIR / "latest.json").write_text(encoded, encoding="utf-8")
    if not passed:
        for line in _failure_console_lines(document, result_path):
            print(line)
    print(document["final_verdict"])
    return 0 if passed else 1


def _run_static_checks() -> None:
    """Inert plan/budget checks.  No SDK client is created or invoked."""
    plan = build_release_gate_plan("0123456789abcdef" * 4)
    assert len(plan) == REQUIRED_SCENARIO_COUNT
    assert sum(item.expected_calls for item in plan) == SDK_CALL_CEILING
    assert [item.role for item in plan] == [
        "fresh_level1", "correct_choice", "harder_level2", "first_hint", "full_solution",
        "easier_level1", "same_level_new", "contract_fresh", "contract_harder",
        "grade7", "grade8", "grade9",
    ]
    assert _selected_lessons(plan) == _selected_lessons(build_release_gate_plan("0123456789abcdef" * 4))
    counter = CountingLLM(object(), SDK_CALL_CEILING)
    for _ in range(SDK_CALL_CEILING):
        counter._count("static")
    try:
        counter._count("static-twentieth")
    except SDKCallBudgetExceeded:
        pass
    else:
        raise AssertionError("20th SDK call was not refused")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manual commit-bound MAT-BOT live release gate")
    parser.add_argument("--static-checks", action="store_true", help="run inert plan/budget checks only")
    args = parser.parse_args(argv)
    if args.static_checks:
        _run_static_checks()
        print("LIVE RELEASE GATE STATIC CHECKS PASS — ZERO SDK CALLS")
        return 0
    try:
        return run_live_release_gate()
    except GateRefusal as exc:
        print("LIVE RELEASE GATE FAILED — PUSH BLOCKED")
        print(f"REFUSING TO RUN: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
