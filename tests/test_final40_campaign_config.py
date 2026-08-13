"""FINAL40 environment/route regression tests. Zero model and zero network."""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from tests.conftest import queue_two_call
from tools.practice_eval import campaign_config, classify, report, runner
from tools.practice_eval.scenario import load_scenarios


FINAL_WAVE = (runner.ROOT / "tools" / "practice_eval" / "scenarios" /
              "family" / "wave_final40.jsonl")


def _install(monkeypatch, values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _final_scenarios():
    return load_scenarios(FINAL_WAVE)


def _x02():
    return next(scenario for scenario in _final_scenarios()
                if scenario.id == "FW-X02")


def _ordinary_scenario(tmp_path):
    row = {
        "id": "CFG-U01", "wave": "FFINAL40", "importance": "critical",
        "grade": 6, "oblast": "Razlomci", "topic_id": "6-04-001",
        "reason": "ordinary non-contract lesson stays on universal two-call",
        "tags": ["unit"], "steps": [{
            "kind": "text", "message": "Daj mi zadatak.", "expect_calls": 2,
            "checks": ["published", "calls_at_most:2"], "rubrics": [],
        }],
    }
    path = tmp_path / "ordinary.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return load_scenarios(path)[0]


def test_final40_launcher_never_injects_deterministic_rollback():
    environ = {}
    applied = campaign_config.apply_campaign_environment(
        "final40", environ=environ)

    assert campaign_config.DETERMINISTIC_FLAG not in applied
    assert campaign_config.DETERMINISTIC_FLAG not in environ
    assert json.loads(environ[campaign_config.CAMPAIGN_OVERRIDE_MARKER]) == applied


def test_explicit_allowlisted_campaign_override_still_works():
    environ = {}
    applied = campaign_config.apply_campaign_environment(
        "final40", ["AI_TUTOR_TIMEOUT=12"], environ=environ)

    assert applied["AI_TUTOR_TIMEOUT"] == "12"
    assert environ["AI_TUTOR_TIMEOUT"] == "12"
    assert environ["MATBOT_PRACTICE_PIPELINE"] == "universal_two_call"


def test_unauthorized_behavior_override_is_rejected_before_any_mutation():
    environ = {}
    with pytest.raises(campaign_config.CampaignConfigurationError,
                       match="may not override"):
        campaign_config.apply_campaign_environment(
            "final40", ["MATBOT_DETERMINISTIC_PRACTICE=disabled"],
            environ=environ)
    assert environ == {}


def test_forged_evaluator_override_is_an_explicit_preflight_problem():
    environ = {
        campaign_config.DETERMINISTIC_FLAG: "disabled",
        campaign_config.CAMPAIGN_OVERRIDE_MARKER: json.dumps({
            campaign_config.DETERMINISTIC_FLAG: "disabled"}),
    }
    problems = campaign_config.preflight_environment_problems(
        [_x02()], deterministic_enabled=False, environ=environ)

    assert any("evaluator-origin unauthorized" in problem for problem in problems)
    assert any("zero-call control requires" in problem for problem in problems)
    snapshot = campaign_config.environment_snapshot(False, environ)
    assert snapshot["deterministic_practice_source"] == "campaign_override"


def test_final40_dry_run_records_effective_default_and_zero_calls(
        tmp_path, monkeypatch):
    monkeypatch.delenv(campaign_config.DETERMINISTIC_FLAG, raising=False)
    environ = {}
    campaign_config.apply_campaign_environment("final40", environ=environ)
    _install(monkeypatch, environ)

    result = runner.dry_run(_final_scenarios(), tmp_path / "dry")

    assert result["problems"] == []
    assert result["scenarios"] == 40
    assert result["sdk_calls_made"] == 0
    runtime = result["runtime"]
    assert runtime["deterministic_practice_enabled"] is True
    assert runtime["deterministic_practice_value"] == "(unset)"
    assert runtime["deterministic_practice_source"] == "product_default"
    assert campaign_config.DETERMINISTIC_FLAG not in runtime["campaign_overrides"]


def test_fw_x02_uses_deterministic_route_with_no_fabricated_calls(
        tmp_path, monkeypatch):
    class NeverCalled:
        def tutor_turn(self, instructions, input_text):
            raise AssertionError("FW-X02 must not call Tutor")

        def reviewer_turn(self, instructions, input_text, timeout_s=None,
                          model=None, reasoning_effort=None):
            raise AssertionError("FW-X02 must not call Reviewer")

    monkeypatch.delenv(campaign_config.DETERMINISTIC_FLAG, raising=False)
    environ = {}
    campaign_config.apply_campaign_environment("final40", environ=environ)
    _install(monkeypatch, environ)
    monkeypatch.setattr(runner, "_real_llm", lambda: NeverCalled())

    out = tmp_path / "x02"
    meta, records = runner.run_campaign([_x02()], out, 0, 1, 0, False)
    turn = records[0].turns[0]

    assert turn["route"] == classify.ROUTE_DETERMINISTIC
    assert turn["sdk_calls"] == 0
    assert turn["sdk_call_kinds"] == ()
    assert meta["actual_sdk_calls"] == 0
    assert meta["deterministic_practice_enabled"] is True

    summary = report.build_summary(meta, [asdict(records[0])], 534)
    summary["examples"] = {}
    markdown = report.render_markdown(summary)
    assert "deterministic practice: enabled=`True`" in markdown
    assert "source=`product_default`" in markdown


def test_ordinary_final40_model_scenario_still_uses_two_call_route(
        tmp_path, monkeypatch, fake_llm):
    monkeypatch.delenv(campaign_config.DETERMINISTIC_FLAG, raising=False)
    environ = {}
    campaign_config.apply_campaign_environment("final40", environ=environ)
    _install(monkeypatch, environ)
    queue_two_call(fake_llm)
    monkeypatch.setattr(runner, "_real_llm", lambda: fake_llm)

    meta, records = runner.run_campaign(
        [_ordinary_scenario(tmp_path)], tmp_path / "ordinary-out", 2, 1, 0, False)
    turn = records[0].turns[0]

    assert turn["route"] == classify.ROUTE_UNIVERSAL_TWO_CALL
    assert turn["sdk_calls"] == 2
    assert turn["sdk_call_kinds"] == ("tutor_turn", "reviewer_turn")
    assert meta["actual_sdk_calls"] == 2


def test_direct_live_runner_rejects_disabled_zero_call_control_before_llm(
        tmp_path, monkeypatch):
    monkeypatch.setenv(campaign_config.DETERMINISTIC_FLAG, "disabled")
    monkeypatch.delenv(campaign_config.CAMPAIGN_OVERRIDE_MARKER, raising=False)
    monkeypatch.setattr(
        runner, "_real_llm",
        lambda: (_ for _ in ()).throw(AssertionError("LLM constructed")))

    result = runner.main([
        "--scenarios", str(FINAL_WAVE), "--scenario", "FW-X02",
        "--output-dir", str(tmp_path / "blocked-live"),
    ])
    assert result == 2


def test_third_call_accounting_is_unchanged():
    record = {"turns": [{
        "step_index": 0, "sdk_calls": 3,
        "sdk_call_kinds": ["tutor_turn", "reviewer_turn", "practice_turn"],
    }]}
    assert classify.third_call_violations(record) == [{
        "step": 0, "sdk_calls": 3,
        "kinds": ["tutor_turn", "reviewer_turn", "practice_turn"],
    }]
