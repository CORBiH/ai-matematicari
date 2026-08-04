"""Offline verifier for a commit-bound live release-gate result.

This checker imports no LLM adapter and cannot make an SDK call.  It is used
by both humans and the pre-push hook to reject stale, incomplete, or
wrong-commit results.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_SCENARIOS = 12
REQUIRED_CALLS = 19
MAX_AGE = timedelta(hours=24)
REQUIRED_PIPELINE = "universal_two_call"


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    if completed.returncode:
        raise ValueError("Git metadata is unavailable.")
    return completed.stdout.strip()


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def validate_result(document: dict, *, expected_commit: str | None = None,
                    expected_tree: str | None = None, now: datetime | None = None) -> list[str]:
    """Return every fail-closed reason; performs no network or SDK work."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["result_not_an_object"]
    if document.get("campaign") != "release-gate":
        errors.append("wrong_campaign")
    if document.get("verdict") != "PASS":
        errors.append("verdict_is_not_pass")
    if expected_commit and document.get("tested_commit_sha") != expected_commit:
        errors.append("commit_sha_mismatch")
    if expected_tree and document.get("tested_tree_hash") != expected_tree:
        errors.append("tree_hash_mismatch")
    if document.get("clean_worktree") is not True:
        errors.append("clean_worktree_not_confirmed")
    if document.get("practice_pipeline") != REQUIRED_PIPELINE:
        errors.append("wrong_practice_pipeline")
    if document.get("difficulty_levels_enabled") is not True:
        errors.append("difficulty_levels_not_enabled")
    if document.get("scenario_count") != REQUIRED_SCENARIOS:
        errors.append("wrong_scenario_count")
    if document.get("required_scenario_count") != REQUIRED_SCENARIOS:
        errors.append("missing_required_scenario_count")
    if document.get("actual_sdk_calls") != REQUIRED_CALLS:
        errors.append("wrong_sdk_call_count")
    if document.get("sdk_call_ceiling") != REQUIRED_CALLS:
        errors.append("wrong_sdk_call_ceiling")
    if document.get("twentieth_call_refused_before_sdk") is not True:
        errors.append("twentieth_call_not_refused")
    if document.get("validation_failures"):
        errors.append("hidden_validation_failures")
    if document.get("infrastructure_failures"):
        errors.append("infrastructure_failure")
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != REQUIRED_SCENARIOS:
        errors.append("required_scenarios_missing")
    else:
        required_roles = {
            "fresh_level1", "correct_choice", "harder_level2", "first_hint", "full_solution",
            "easier_level1", "same_level_new", "contract_fresh", "contract_harder",
            "grade7", "grade8", "grade9",
        }
        actual_roles = {row.get("role") for row in scenarios if isinstance(row, dict)}
        if actual_roles != required_roles:
            errors.append("required_scenario_roles_missing")
        for row in scenarios:
            result = row.get("result") if isinstance(row, dict) else None
            if not isinstance(row, dict) or row.get("errors") or not isinstance(result, dict):
                errors.append("scenario_failed_or_skipped")
                break
            if result.get("attempted") is not True or result.get("published") is not True:
                errors.append("scenario_failed_or_skipped")
                break
            if result.get("failure_is_infrastructure") is True:
                errors.append("scenario_infrastructure_failure")
                break
    finished = _parse_time(document.get("finished_at"))
    reference = now or datetime.now(timezone.utc)
    if finished is None:
        errors.append("missing_finished_at")
    elif reference - finished > MAX_AGE or finished > reference + timedelta(minutes=5):
        errors.append("result_expired_or_invalid_time")
    return errors


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Release-gate result cannot be read as UTF-8 JSON.") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline MAT-BOT live release-gate verifier")
    parser.add_argument("result", type=Path, help="release-gate JSON result")
    parser.add_argument("--expected-commit", help="pushed local commit SHA; defaults to current HEAD")
    parser.add_argument("--expected-tree", help="expected Git tree hash; defaults to current HEAD tree")
    args = parser.parse_args(argv)
    try:
        document = _load(args.result)
        commit = args.expected_commit or _git("rev-parse", "HEAD")
        tree = args.expected_tree or _git("rev-parse", "HEAD^{tree}")
        errors = validate_result(document, expected_commit=commit, expected_tree=tree)
    except ValueError as exc:
        print("LIVE RELEASE GATE FAILED — PUSH BLOCKED")
        print(f"Offline result check failed: {exc}")
        return 1
    if errors:
        print("LIVE RELEASE GATE FAILED — PUSH BLOCKED")
        print("Offline result check failures: " + ", ".join(sorted(set(errors))))
        return 1
    print("LIVE RELEASE GATE PASS — PUSH ALLOWED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
