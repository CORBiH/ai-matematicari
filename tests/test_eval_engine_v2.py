# -*- coding: utf-8 -*-
"""Phase 7 — evaluation harness self-tests + release-gate enforcement.

The harness itself must be trustworthy: it must run every scenario across every
flag config, restore env flags, cover the required categories, pin a permanent
regression fixture for each known production bug, and report machine-readable
results. The release gates are asserted here so CI fails if readiness regresses.
"""
import os

import pytest

from matbot import eval_v2
from matbot.eval_v2 import CONFIGS, V2_FLAGS, run_all
from matbot.eval_scenarios import SCENARIOS


@pytest.fixture(scope="module")
def report():
    return run_all()


# --------------------------------------------------------------------------- #
# Corpus shape / coverage                                                     #
# --------------------------------------------------------------------------- #
REQUIRED_CATEGORIES = {
    "exact_correct", "equivalent_form", "correct_intermediate", "partial_reasoning",
    "wrong_intermediate", "ambiguous", "ne_znam_hint", "item_attribution",
    "short_followup", "mode_preservation", "unsupported_fallback",
    "ungradeable_rejection", "language", "practice_step_completion",
    "exam_lifecycle", "post_exam", "task_id_persistence", "prose_not_evidence",
}

# Every previously observed production bug must have a permanent fixture.
REQUIRED_REGRESSIONS = {
    "240 divisibility-by-6 guided flow",
    "correct intermediate must not end task or bump streak",
    "15 vs 15 cm",
    "40 vs 40°",
    "3/2 vs 1 1/2",
    "common denominator statement as partial step",
    "incomplete inequality x>3 submitted as 4",
    "correct set union",
    "one exam answer grades exactly one item",
    "selected tema not collapsing to broader oblast",
    "completed exam never reopening",
    "unsupported topic never silently gets unrelated exam",
    "prose 'Tačno' never grading evidence",
    "prose 'Netačno' never grading evidence",
}


def test_all_required_categories_covered():
    present = {s.category for s in SCENARIOS}
    assert REQUIRED_CATEGORIES <= present, REQUIRED_CATEGORIES - present


def test_all_known_production_bugs_have_fixtures():
    present = {s.regression_of for s in SCENARIOS if s.regression_of}
    assert REQUIRED_REGRESSIONS <= present, REQUIRED_REGRESSIONS - present


def test_result_mode_equation_regression_is_deterministic():
    """12 - 23x = 4x historically returned 2/5; the checker must yield 4/9."""
    from matbot.answer_checker import derive_expected, _fmt_expected, check_practice_answer
    from matbot.grading_guard import authoritative_verdict
    assert _fmt_expected(derive_expected("12 - 23x = 4x")) == "4/9"
    assert authoritative_verdict(check_practice_answer("12 - 23x = 4x", "x=4/9")) == "correct"
    assert authoritative_verdict(check_practice_answer("12 - 23x = 4x", "2/5")) != "correct"


def test_configs_cover_legacy_each_component_and_all():
    assert CONFIGS["legacy"] == {}
    assert set(CONFIGS["all_v2"]) == set(V2_FLAGS)
    for single in ("shadow", "grading", "practice", "exam"):
        assert len(CONFIGS[single]) == 1


# --------------------------------------------------------------------------- #
# Harness hygiene                                                             #
# --------------------------------------------------------------------------- #
def test_flags_restored_after_run():
    """The harness must RESTORE the ambient environment exactly (not force it
    off) — otherwise a run would leak flags into the rest of the process."""
    before = {f: os.environ.get(f) for f in V2_FLAGS}
    eval_v2.run_all(scenarios=SCENARIOS[:1], configs=["all_v2", "legacy"])
    after = {f: os.environ.get(f) for f in V2_FLAGS}
    assert after == before


def test_report_is_machine_readable(report):
    for key in ("summary", "pass_rate_by_category", "by_config",
                "legacy_vs_v2_divergences", "grader_source_distribution",
                "shadow_conflicts", "template_validation_failures",
                "state_machine_failures", "language_failures",
                "consistency_failures", "telemetry_failures", "sheets_failures",
                "latency", "release_gates", "release_gates_passed", "failures"):
        assert key in report, key
    import json
    json.dumps(report)                      # must serialize cleanly


def test_every_scenario_ran_in_every_config(report):
    s = report["summary"]
    assert s["total_cases"] == len(SCENARIOS) * len(s["configs"])


# --------------------------------------------------------------------------- #
# Release gates (these fail CI if readiness regresses)                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("gate", [
    "no_false_incorrect_on_verified_correct",
    "multi_step_completion_100",
    "no_exam_answer_bleed",
    "no_exam_mode_drift",
    "no_completed_exam_reopen",
    "no_verdict_prose_counter_contradiction",
    "no_language_leaks",
    "template_validation_100",
    "unsupported_explicitly_handled",
    "no_duplicate_sheets_rows",
    "no_secrets_or_prompts_in_telemetry",
    "no_material_latency_regression",
])
def test_release_gate(report, gate):
    assert report["release_gates"][gate], (gate, report["failures"])


def test_all_release_gates_pass(report):
    assert report["release_gates_passed"], report["failures"]


def test_no_scenario_crashed(report):
    crashes = [f for f in report["failures"]
               if any("EXCEPTION" in x for x in f["failures"])]
    assert not crashes, crashes


def test_zero_template_validation_failures(report):
    assert report["template_validation_failures"] == []


def test_latency_not_materially_worse(report):
    assert report["latency"]["all_v2_vs_legacy_pct"] <= 25.0, report["latency"]
