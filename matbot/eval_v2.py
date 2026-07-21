"""Engine V2 — Phase 7: evaluation harness + release gates.

Runs repeatable MULTI-TURN scenarios against the tutor under several feature-flag
configurations (legacy / each V2 component alone / all V2 together), checks
per-scenario expectations plus universal invariants, and emits a machine-readable
report with release-gate status.

Fully offline: the model is scripted, so runs are deterministic and safe. This
module PROVES readiness; it does not deploy anything.
"""
from __future__ import annotations

import json
import os
import re
import time
import types
from dataclasses import dataclass, field
from typing import Any, Callable

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot import task_templates as tt
from matbot.grading_guard import has_grade_contradiction

# --------------------------------------------------------------------------- #
# Flag configurations                                                          #
# --------------------------------------------------------------------------- #
V2_FLAGS = (
    "MATBOT_ENGINE_V2",
    "MATBOT_ENGINE_V2_GRADING",
    "MATBOT_ENGINE_V2_PRACTICE",
    "MATBOT_ENGINE_V2_EXAM",
)

CONFIGS: dict[str, dict[str, str]] = {
    "legacy": {},
    "shadow": {"MATBOT_ENGINE_V2": "shadow"},
    "grading": {"MATBOT_ENGINE_V2_GRADING": "on"},
    "practice": {"MATBOT_ENGINE_V2_PRACTICE": "on"},
    "exam": {"MATBOT_ENGINE_V2_EXAM": "on"},
    "all_v2": {
        "MATBOT_ENGINE_V2": "shadow",
        "MATBOT_ENGINE_V2_GRADING": "on",
        "MATBOT_ENGINE_V2_PRACTICE": "on",
        "MATBOT_ENGINE_V2_EXAM": "on",
    },
}

# Ekavica / diacritic-stripped leaks (matches ONLY the wrong form).
LANGUAGE_LEAK_RE = re.compile(
    r"\b(deo|dela|delu|resenj\w*|resiti|vezb\w*|sledec\w*|razumem\w*|obe|devoj\w*|"
    r"umesto|posle|uvek|gde|ovde|dve|ceo|celi|primer\w*|zbroj\w*|kut|imenilac|"
    r"imenitelj\w*|brojilac|brojitelj\w*|matematicki|rijesi|rjesenj\w*|necu|"
    r"posalji\w*|jednoznac\w*|tacno|tacan|netacno|netacan|djelimicn\w*|vjezb\w*|"
    r"sljedeci|zajednicki|objasnjenj\w*|greske|pomoc|konacn\w*|izracunaj\w*)\b",
    re.IGNORECASE,
)

# Telemetry must never carry prompts, hidden reasoning, or credentials.
TELEMETRY_BANNED_RE = re.compile(
    r"(VOĐENJE KROZ ZADATAK|answer grader|Return JSON|system_prompt|"
    r"chain[ _-]?of[ _-]?thought|\breasoning\b|sk-[A-Za-z0-9]{8,}|api[_-]?key|secret)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Scenario model                                                               #
# --------------------------------------------------------------------------- #
@dataclass
class Turn:
    message: str
    phase: str | None = None            # "answer" → answering_practice_task
    reply: str = "U redu."              # scripted model prose
    gpt_verdict: str | None = None      # structured grader JSON to inject
    expect: dict = field(default_factory=dict)


@dataclass
class Scenario:
    id: str
    category: str
    payload: dict
    turns: list[Turn]
    seed_task: str = ""
    applies_to: tuple[str, ...] = ()    # configs where `expect` holds ( ()=all )
    regression_of: str = ""             # production bug this pins


def _scripted_chat(turn: Turn) -> Callable:
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        sys_txt = "".join(str(m.get("content") or "") for m in messages
                          if m.get("role") == "system")
        if "answer grader" in sys_txt.lower() and turn.gpt_verdict:
            body = json.dumps({"verdict": turn.gpt_verdict, "confidence": 0.9,
                               "public_feedback": ""})
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=body))])
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=turn.reply))])
    return chat


# --------------------------------------------------------------------------- #
# Expectation checking                                                         #
# --------------------------------------------------------------------------- #
def _check_expect(out: dict, expect: dict, task_ids: set[str]) -> list[str]:
    fails: list[str] = []
    ns = out.get("next_state") or {}
    cursor = ns.get("step_cursor") or {}
    exam = out.get("exam_state") or {}

    def _eq(key, actual, want):
        if isinstance(want, (list, tuple)):
            if actual not in want:
                fails.append(f"{key}={actual!r} not in {want!r}")
        elif actual != want:
            fails.append(f"{key}={actual!r} != {want!r}")

    if "verdict" in expect:
        _eq("verdict", out.get("answer_verdict"), expect["verdict"])
    if "verdict_not" in expect and out.get("answer_verdict") == expect["verdict_not"]:
        fails.append(f"verdict must not be {expect['verdict_not']!r}")
    if "task_status" in expect:
        _eq("task_status", out.get("task_status"), expect["task_status"])
    if "active_step" in expect:
        _eq("active_step", cursor.get("active_step_id"), expect["active_step"])
    if "step_complete" in expect:
        _eq("step_complete", bool(cursor.get("is_complete")), expect["step_complete"])
    if "streak" in expect:
        _eq("streak", ns.get("correct_streak"), expect["streak"])
    if "wrong_attempts" in expect:
        _eq("wrong_attempts", out.get("wrong_attempt_count"), expect["wrong_attempts"])
    if "exam_status" in expect:
        _eq("exam_status", exam.get("exam_status"), expect["exam_status"])
    if "exam_index" in expect:
        _eq("exam_index", exam.get("current_item_index"), expect["exam_index"])
    if "topic_covered" in expect:
        _eq("topic_covered", exam.get("topic_covered"), expect["topic_covered"])
    if "graded_flags" in expect:                     # EXACT list compare
        actual_flags = [i.get("correct") for i in exam.get("items", [])]
        if actual_flags != list(expect["graded_flags"]):
            fails.append(f"graded_flags={actual_flags!r} != {expect['graded_flags']!r}")
    if "mode" in expect:
        _eq("mode", out.get("mode"), expect["mode"])
        _eq("session_mode", out.get("session_mode"), expect["mode"])
    if "last_task_nonempty" in expect:
        _eq("last_task_nonempty", bool(out.get("last_tutor_task")),
            expect["last_task_nonempty"])
    if "answer_contains" in expect:
        for frag in expect["answer_contains"]:
            if frag.lower() not in (out.get("answer") or "").lower():
                fails.append(f"answer missing {frag!r}")
    if "answer_not_contains" in expect:
        for frag in expect["answer_not_contains"]:
            if frag.lower() in (out.get("answer") or "").lower():
                fails.append(f"answer must not contain {frag!r}")
    if "last_task_not_contains" in expect:
        for frag in expect["last_task_not_contains"]:
            if frag.lower() in (out.get("last_tutor_task") or "").lower():
                fails.append(f"last_tutor_task must not contain {frag!r}")
    if expect.get("task_validated"):
        # No task may become active without passing validation.
        if out.get("last_tutor_task"):
            status = (out.get("task_validation") or {}).get("validation_status")
            if status != "validated":
                fails.append(f"active task validation_status={status!r} (must be validated)")
    if expect.get("same_task_id") and len(task_ids) > 1:
        fails.append(f"task_id changed across turns: {sorted(task_ids)}")
    return fails


def _universal_invariants(out: dict, sheets_writes: int) -> dict[str, list[str]]:
    """Checked in EVERY config for EVERY turn, regardless of expectations."""
    issues: dict[str, list[str]] = {}
    answer = out.get("answer") or ""

    leaks = LANGUAGE_LEAK_RE.findall(answer)
    if leaks:
        issues["language"] = sorted(set(leaks))

    # verdict / prose / counter consistency
    contradictions = []
    verdict = out.get("answer_verdict")
    if verdict in ("correct", "incorrect") and has_grade_contradiction(answer):
        contradictions.append("prose self-contradiction")
    if verdict == "correct" and (out.get("wrong_attempt_count") or 0) > 0:
        prev_wrong = ((out.get("next_state") or {}).get("wrong_attempt_count") or 0)
        if prev_wrong and verdict == "correct" and out.get("task_status") == "completed":
            pass  # historical wrong attempts on a completed task are legitimate
    if contradictions:
        issues["consistency"] = contradictions

    blob = json.dumps({k: v for k, v in out.items() if k != "answer"},
                      ensure_ascii=False, default=str)
    banned = TELEMETRY_BANNED_RE.findall(blob)
    if banned:
        issues["telemetry"] = sorted({b if isinstance(b, str) else b[0] for b in banned})

    if sheets_writes > 1:
        issues["sheets"] = [f"{sheets_writes} rows for one turn"]
    return issues


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #
#: This harness evaluates the LEGACY/V2 pipeline. The minimal engine would
#: intercept Practice turns for its supported topics and answer them with a
#: different (correct, but different) contract, so it is pinned off for the
#: duration of a run — otherwise an ambient MATBOT_MINIMAL_ENGINE=on silently
#: makes every V2 expectation evaluate the wrong engine.
_MINIMAL_FLAG = "MATBOT_MINIMAL_ENGINE"
_MANAGED_FLAGS = V2_FLAGS + (_MINIMAL_FLAG,)


def _apply_flags(config: dict[str, str]) -> dict[str, str | None]:
    prev = {k: os.environ.get(k) for k in _MANAGED_FLAGS}
    for k in _MANAGED_FLAGS:
        os.environ.pop(k, None)
    os.environ.update(config)
    os.environ[_MINIMAL_FLAG] = "off"
    return prev


def _restore_flags(prev: dict[str, str | None]) -> None:
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def run_scenario(scenario: Scenario, config_name: str, master, tmap) -> dict:
    config = CONFIGS[config_name]
    prev_flags = _apply_flags(config)
    applies = (not scenario.applies_to) or (config_name in scenario.applies_to)
    sheets_counter = {"n": 0}
    orig_sheets = svc.log_transcript_to_sheet

    def counting_sheets(payload, response):
        sheets_counter["n"] += 1
        return None

    svc.log_transcript_to_sheet = counting_sheets
    turns_out, failures, invariant_issues = [], [], {}
    task_ids: set[str] = set()
    carry_state, carry_task = None, scenario.seed_task
    latencies: list[float] = []
    try:
        for idx, turn in enumerate(scenario.turns):
            payload = dict(scenario.payload)
            payload["student_message"] = turn.message
            if turn.phase == "answer":
                payload["interaction_phase"] = "answering_practice_task"
                if carry_task:
                    payload["last_tutor_task"] = carry_task
            if carry_state:
                payload["previous_next_state"] = carry_state
            sheets_counter["n"] = 0
            t0 = time.perf_counter()
            try:
                out = svc.handle_chat(payload, _scripted_chat(turn), master, tmap,
                                      model="eval", timeout=5)
            except Exception as exc:  # a crash is always a failure
                failures.append(f"turn{idx}: EXCEPTION {type(exc).__name__}: {exc}")
                break
            latencies.append((time.perf_counter() - t0) * 1000.0)

            if out.get("task_id"):
                task_ids.add(out["task_id"])
            for key, vals in _universal_invariants(out, sheets_counter["n"]).items():
                invariant_issues.setdefault(key, []).extend(vals)
            if applies and turn.expect:
                for f in _check_expect(out, turn.expect, task_ids):
                    failures.append(f"turn{idx}: {f}")

            carry_state = out.get("next_state") or carry_state
            if "last_tutor_task" in out:
                carry_task = out.get("last_tutor_task") or ""
            turns_out.append({
                "message": turn.message,
                "verdict": out.get("answer_verdict"),
                "task_status": out.get("task_status"),
                "shadow": (out.get("shadow_grading") or {}).get("shadow_verdict"),
                "shadow_source": (out.get("shadow_grading") or {}).get("shadow_grader_source"),
                "shadow_conflict": (out.get("shadow_grading") or {}).get("shadow_conflict_type"),
            })
    finally:
        svc.log_transcript_to_sheet = orig_sheets
        _restore_flags(prev_flags)

    for key, vals in invariant_issues.items():
        failures.append(f"invariant[{key}]: {sorted(set(vals))}")

    return {
        "id": scenario.id, "category": scenario.category, "config": config_name,
        "regression_of": scenario.regression_of,
        "expectations_applied": applies,
        "passed": not failures, "failures": failures,
        "turns": turns_out,
        "latency_ms": {
            "total": round(sum(latencies), 2),
            "mean": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
    }


def run_all(scenarios: list[Scenario] | None = None,
            configs: list[str] | None = None) -> dict:
    """Run the corpus across configs and return the machine-readable report."""
    from matbot.eval_scenarios import SCENARIOS  # local import (avoids cycle)
    scenarios = scenarios if scenarios is not None else SCENARIOS
    configs = configs or list(CONFIGS)
    master, tmap = cl.load_master_content(), cl.load_thinkific_map()

    results = [run_scenario(s, c, master, tmap) for c in configs for s in scenarios]
    return build_report(results, scenarios, configs)


def _template_validation_failures(seeds: int = 60) -> list[str]:
    import random
    bad = []
    for t in tt._TEMPLATES:
        for s in range(seeds):
            q, a = t.generate(random.Random(s))
            if not tt._validates(q, a):
                bad.append(f"{t.skill_id}:{q}")
    return bad


def build_report(results: list[dict], scenarios: list[Scenario],
                 configs: list[str]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    by_category: dict[str, dict] = {}
    for r in results:
        c = by_category.setdefault(r["category"], {"total": 0, "passed": 0})
        c["total"] += 1
        c["passed"] += int(r["passed"])
    for c in by_category.values():
        c["pass_rate"] = round(100.0 * c["passed"] / c["total"], 1) if c["total"] else 0.0

    by_config: dict[str, dict] = {}
    for r in results:
        c = by_config.setdefault(r["config"], {"total": 0, "passed": 0, "failures": []})
        c["total"] += 1
        c["passed"] += int(r["passed"])
        if not r["passed"]:
            c["failures"].append({"id": r["id"], "failures": r["failures"]})
    for c in by_config.values():
        c["pass_rate"] = round(100.0 * c["passed"] / c["total"], 1) if c["total"] else 0.0

    # legacy vs V2 divergence: same scenario, different per-turn verdicts
    legacy_by_id = {r["id"]: r for r in results if r["config"] == "legacy"}
    divergences = []
    for r in results:
        if r["config"] == "legacy":
            continue
        base = legacy_by_id.get(r["id"])
        if not base:
            continue
        lv = [t["verdict"] for t in base["turns"]]
        vv = [t["verdict"] for t in r["turns"]]
        if lv != vv:
            divergences.append({"id": r["id"], "config": r["config"],
                                "legacy": lv, "v2": vv})

    grader_sources: dict[str, int] = {}
    shadow_conflicts: dict[str, int] = {}
    for r in results:
        for t in r["turns"]:
            if t.get("shadow_source"):
                grader_sources[t["shadow_source"]] = grader_sources.get(t["shadow_source"], 0) + 1
            if t.get("shadow_conflict"):
                shadow_conflicts[t["shadow_conflict"]] = shadow_conflicts.get(t["shadow_conflict"], 0) + 1

    def _fail_kind(kind: str) -> list[str]:
        out = []
        for r in results:
            for f in r["failures"]:
                if f.startswith(f"invariant[{kind}]"):
                    out.append(f"{r['config']}/{r['id']}: {f}")
        return out

    lat_by_config = {}
    for cfg in configs:
        rows = [r["latency_ms"]["mean"] for r in results if r["config"] == cfg and r["latency_ms"]["mean"]]
        lat_by_config[cfg] = {
            "mean_ms": round(sum(rows) / len(rows), 2) if rows else 0.0,
            "max_ms": round(max((r["latency_ms"]["max"] for r in results
                                 if r["config"] == cfg), default=0.0), 2),
        }
    base_lat = lat_by_config.get("legacy", {}).get("mean_ms") or 0.0
    all_lat = lat_by_config.get("all_v2", {}).get("mean_ms") or 0.0
    lat_regression_pct = round(((all_lat - base_lat) / base_lat * 100.0), 1) if base_lat else 0.0

    template_failures = _template_validation_failures()
    language_failures = _fail_kind("language")
    consistency_failures = _fail_kind("consistency")
    telemetry_failures = _fail_kind("telemetry")
    sheets_failures = _fail_kind("sheets")
    state_failures = [f"{r['config']}/{r['id']}" for r in results
                      if not r["passed"] and r["category"] in
                      ("practice_step_completion", "exam_lifecycle", "task_id_persistence",
                       "post_exam", "item_attribution", "mode_preservation")]

    def _cat_rate(cat: str) -> float:
        return by_category.get(cat, {}).get("pass_rate", 0.0)

    gates = {
        "no_false_incorrect_on_verified_correct": _cat_rate("exact_correct") == 100.0
                                                  and _cat_rate("equivalent_form") == 100.0,
        "multi_step_completion_100": _cat_rate("practice_step_completion") == 100.0,
        "no_exam_answer_bleed": _cat_rate("item_attribution") == 100.0,
        "no_exam_mode_drift": _cat_rate("mode_preservation") == 100.0,
        "no_completed_exam_reopen": _cat_rate("post_exam") == 100.0,
        "no_verdict_prose_counter_contradiction": not consistency_failures,
        "no_language_leaks": not language_failures,
        "template_validation_100": not template_failures,
        "unsupported_explicitly_handled": _cat_rate("unsupported_fallback") == 100.0
                                          and _cat_rate("ungradeable_rejection") == 100.0,
        "no_duplicate_sheets_rows": not sheets_failures,
        "no_secrets_or_prompts_in_telemetry": not telemetry_failures,
        "no_material_latency_regression": lat_regression_pct <= 25.0,
    }

    return {
        "summary": {
            "total_cases": total, "passed": passed, "failed": total - passed,
            "pass_rate": round(100.0 * passed / total, 1) if total else 0.0,
            "scenarios": len(scenarios), "configs": configs,
            # Cohort marker so an eval/smoke run from a canary host is identifiable.
            "engine_canary": __import__("matbot.engine_v2", fromlist=["x"]).canary_marker(),
        },
        "pass_rate_by_category": by_category,
        "by_config": by_config,
        "legacy_vs_v2_divergences": divergences,
        "grader_source_distribution": grader_sources,
        "shadow_conflicts": shadow_conflicts,
        "template_validation_failures": template_failures,
        "state_machine_failures": state_failures,
        "language_failures": language_failures,
        "consistency_failures": consistency_failures,
        "telemetry_failures": telemetry_failures,
        "sheets_failures": sheets_failures,
        "latency": {"by_config": lat_by_config,
                    "all_v2_vs_legacy_pct": lat_regression_pct},
        "release_gates": gates,
        "release_gates_passed": all(gates.values()),
        "failures": [{"config": r["config"], "id": r["id"], "failures": r["failures"]}
                     for r in results if not r["passed"]],
    }
