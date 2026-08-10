"""Auditable environment overrides for named practice-evaluation campaigns.

The product process environment remains authoritative for every variable that
the campaign did not declare.  In particular FINAL40 never changes the
deterministic-practice feature flag: its product default/current value must
remain visible to the runner and to the zero-call control.
"""
from __future__ import annotations

import json
import os
from collections.abc import MutableMapping, Sequence


CAMPAIGN_OVERRIDE_MARKER = "MATBOT_EVAL_CAMPAIGN_OVERRIDES"
DETERMINISTIC_FLAG = "MATBOT_DETERMINISTIC_PRACTICE"

ALLOWED_CAMPAIGN_OVERRIDES = frozenset({
    "MATBOT_PRACTICE_PIPELINE",
    "MATBOT_PRACTICE_DIFFICULTY_LEVELS",
    "OPENAI_MODEL_TEXT",
    "MATBOT_TUTOR_MODEL",
    "MATBOT_REVIEWER_MODEL",
    "MATBOT_REASONING_EFFORT",
    "MATBOT_MAX_OUTPUT_TOKENS_PRACTICE",
    "AI_TUTOR_TIMEOUT",
})

FINAL40_OVERRIDES = {
    "MATBOT_PRACTICE_PIPELINE": "universal_two_call",
    "MATBOT_PRACTICE_DIFFICULTY_LEVELS": "enabled",
    "OPENAI_MODEL_TEXT": "gpt-5-mini",
    "MATBOT_TUTOR_MODEL": "gpt-5-mini",
    "MATBOT_REVIEWER_MODEL": "gpt-5-mini",
    "MATBOT_REASONING_EFFORT": "low",
    "MATBOT_MAX_OUTPUT_TOKENS_PRACTICE": "4000",
    "AI_TUTOR_TIMEOUT": "45",
}

# F6H (Phase-2 help architecture) deliberately reuses the FINAL40 runtime, the
# canonical live-validation candidate configuration — same models, same
# reasoning effort, same output ceiling and the same **45 second** per-call
# timeout.  Without a registered campaign the runner would fall back to the
# unconfigured product default `AI_TUTOR_TIMEOUT=30`
# (`matbot/config.py::AI_TIMEOUT_S`), which is the product's own default and was
# never the authoritative live-validation value.  Nothing in the product's
# timeout code changes; this is launcher configuration only.
F6H_OVERRIDES = dict(FINAL40_OVERRIDES)

CAMPAIGNS = {"final40": FINAL40_OVERRIDES, "f6h": F6H_OVERRIDES}

# Per-call timeout every registered campaign must run with, in seconds. Frozen
# so a future campaign cannot silently inherit the 30 s product default.
CANONICAL_CAMPAIGN_TIMEOUT_S = "45"


class CampaignConfigurationError(ValueError):
    """A launcher requested an undeclared or malformed environment override."""


def _parse_override(spec: str) -> tuple[str, str]:
    name, separator, value = str(spec or "").partition("=")
    name = name.strip()
    if not separator or not name or not value:
        raise CampaignConfigurationError(
            f"campaign override must be NAME=VALUE, got {spec!r}")
    if name not in ALLOWED_CAMPAIGN_OVERRIDES:
        raise CampaignConfigurationError(
            f"campaign may not override undeclared environment variable {name!r}")
    return name, value


def apply_campaign_environment(
        campaign: str, extra_overrides: Sequence[str] = (),
        environ: MutableMapping[str, str] | None = None) -> dict:
    """Apply only declared overrides and record exactly what the launcher set.

    Validation happens before the first mutation, so an unauthorized request
    cannot leave a partially modified environment behind.
    """
    if campaign not in CAMPAIGNS:
        raise CampaignConfigurationError(f"unknown evaluation campaign {campaign!r}")
    requested = dict(CAMPAIGNS[campaign])
    parsed = [_parse_override(spec) for spec in extra_overrides]
    requested.update(parsed)
    forbidden = sorted(set(requested) - ALLOWED_CAMPAIGN_OVERRIDES)
    if forbidden:
        raise CampaignConfigurationError(
            "campaign contains undeclared overrides: " + ", ".join(forbidden))

    target = os.environ if environ is None else environ
    for name, value in requested.items():
        target[name] = value
    target[CAMPAIGN_OVERRIDE_MARKER] = json.dumps(
        requested, ensure_ascii=True, sort_keys=True)
    return requested


def recorded_campaign_overrides(environ=None) -> tuple[dict, list]:
    """Return the launcher's self-declared overrides and audit problems."""
    source = os.environ if environ is None else environ
    raw = source.get(CAMPAIGN_OVERRIDE_MARKER, "") or ""
    if not raw:
        return {}, []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}, ["campaign override marker is not valid JSON"]
    if not isinstance(payload, dict):
        return {}, ["campaign override marker must contain an object"]
    overrides = {str(name): str(value) for name, value in payload.items()}
    forbidden = sorted(set(overrides) - ALLOWED_CAMPAIGN_OVERRIDES)
    problems = []
    if forbidden:
        problems.append("evaluator-origin unauthorized overrides: "
                        + ", ".join(forbidden))
    for name, value in overrides.items():
        if source.get(name) != value:
            problems.append(
                f"campaign override marker disagrees with effective {name}")
    return overrides, problems


def environment_snapshot(deterministic_enabled: bool, environ=None) -> dict:
    source = os.environ if environ is None else environ
    overrides, problems = recorded_campaign_overrides(source)
    raw = source.get(DETERMINISTIC_FLAG)
    deterministic_source = (
        "campaign_override" if DETERMINISTIC_FLAG in overrides
        else "process_environment" if raw is not None
        else "product_default")
    return {
        "campaign_overrides": overrides,
        "campaign_override_problems": problems,
        "deterministic_practice_enabled": bool(deterministic_enabled),
        "deterministic_practice_value": raw if raw is not None else "(unset)",
        "deterministic_practice_source": deterministic_source,
    }


def preflight_environment_problems(
        scenarios, deterministic_enabled: bool, environ=None) -> list:
    """Narrow behavior-env gate, evaluated before a live SDK can be created."""
    snapshot = environment_snapshot(deterministic_enabled, environ)
    problems = list(snapshot["campaign_override_problems"])
    has_zero_call_control = any(
        "zero_call_control" in tuple(getattr(scenario, "tags", ()) or ())
        for scenario in scenarios)
    if has_zero_call_control and not deterministic_enabled:
        problems.append(
            "zero-call control requires deterministic practice, but effective "
            f"{DETERMINISTIC_FLAG}={snapshot['deterministic_practice_value']!r} "
            f"from {snapshot['deterministic_practice_source']}")
    return problems
