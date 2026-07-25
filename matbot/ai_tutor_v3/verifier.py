# -*- coding: utf-8 -*-
"""The verification boundary — model-only in this stage.

The architecture reserves a place for deterministic, typed verification of
``VerificationRequest`` values (rational equality, divisibility, equation
substitution, …). NONE of those handlers are built yet. This module provides
only the boundary and an honest model-only result, so that:

  * every assessment is recorded with ``verification_status="model_only"``;
  * a model-only result is NEVER labelled deterministically verified,
    mathematically proven, or verifier-confirmed;
  * ``MATBOT_V3_VERIFICATION=required`` refuses safely, because no deterministic
    handler exists to satisfy it — it does not fabricate a pass.

When deterministic handlers arrive, they slot in behind ``verify_batch`` without
changing its callers or the reducer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from matbot.ai_tutor_v3.schemas import VerificationBatchResult

_FLAG = "MATBOT_V3_VERIFICATION"
_VALID = {"off", "shadow", "required"}


def verification_mode() -> str:
    """``off`` (default) | ``shadow`` | ``required``. Invalid values → ``off``."""
    value = (os.getenv(_FLAG) or "off").strip().lower()
    return value if value in _VALID else "off"


@dataclass(frozen=True)
class VerificationDecision:
    """What the verifier boundary decided for a turn.

    ``can_proceed`` is False only under ``required`` mode, where the turn must
    NOT mutate session state because no deterministic handler can satisfy the
    requirement yet.
    """
    result: VerificationBatchResult
    can_proceed: bool
    refusal_reason: str = ""


def verify_batch(claims_present: bool) -> VerificationDecision:
    """Produce the verification result for a turn.

    ``claims_present`` only affects the human-readable note; in every mode the
    status is ``model_only`` because there are no deterministic handlers. Under
    ``required`` the boundary refuses (``can_proceed=False``) rather than
    pretend a claim was verified.
    """
    mode = verification_mode()
    note = ("Model-only interpretation; no deterministic verifier is available "
            "in this stage.")

    if mode == "required":
        return VerificationDecision(
            result=VerificationBatchResult(status="unavailable", note=(
                "Required deterministic verification is unavailable: no typed "
                "verifier handlers are implemented yet.")),
            can_proceed=False,
            refusal_reason="verification_required_unavailable",
        )

    # off and shadow both record model_only. shadow deliberately does NOT
    # fabricate a comparison result — it reserves the fields, and until real
    # handlers exist there is nothing to compare against.
    shadow_note = note
    if mode == "shadow":
        shadow_note = (note + " (shadow mode: comparison fields reserved for "
                       "future deterministic handlers; none ran).")
    return VerificationDecision(
        result=VerificationBatchResult(status="model_only", note=shadow_note),
        can_proceed=True,
    )


def is_model_only(result: VerificationBatchResult) -> bool:
    """Guard for callers/audit: this stage's results are never 'verified'."""
    return result.status in ("model_only", "unavailable")
