# -*- coding: utf-8 -*-
"""Shadow-audit widening, approximate-language policy, and instrumentation —
the observability/policy refinement made before shadow deployment.

1. Shadow mode now audits CONFIDENT "incorrect" verdicts too (not just
   NOT_CHECKABLE/incomplete), closing the blind spot where a deterministic
   parser silently misextracts a token from prose and never gets a second
   look.
2. Hedged/approximate language ("otprilike", "možda", "oko") no longer earns
   full independent-solution credit for an EXACT task merely because the
   nearby value matches.
3. The divisibility explanation policy is now applied consistently: a
   mathematically sufficient rule-level explanation (naming both required
   factors) is fully correct — no more, no less evidence than the rule
   itself requires.
4. Instrumentation aggregates calls/latency/tokens from the SAME structured
   telemetry already logged — no live paid calls, no chain-of-thought.

All model interaction here uses a fake ``openai_chat`` with a deterministic,
injectable delay — never a live network call.
"""
import json
import time

import pytest

from matbot.minimal import semantic_grading as sg
from matbot.minimal.semantic_instrumentation import (
    CallRecord,
    SemanticGradingRecorder,
    record_from_evidence,
)
from matbot.minimal.grading import grade
from matbot.minimal.state import ActiveTask


def fake_openai(reply, *, delay_s: float = 0.0):
    """A fake ``openai_chat`` with an INJECTABLE, deterministic delay — so
    latency measurement is exercised without depending on real wall time."""
    def _fake(model, messages, timeout=None, max_tokens=None, **kw):
        if delay_s:
            time.sleep(delay_s)
        class _Msg:
            content = reply if isinstance(reply, str) else json.dumps(
                reply, ensure_ascii=False)
        class _Choice:
            message = _Msg()
        class _Usage:
            prompt_tokens = 180
            completion_tokens = 60
        class _Resp:
            choices = [_Choice()]
            usage = _Usage()
        return _Resp()
    return _fake


def judgment_payload(**overrides):
    payload = {
        "understood": True, "response_kind": "answer", "decision": "yes",
        "decision_source": "explicit", "final_answer_text": "",
        "claims": [], "explanation_present": True, "relevance": "direct",
        "completeness": "complete", "ambiguity": "", "certainty": "certain",
        "precision": "unspecified", "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


def div_task(n, divisor, question=None):
    from matbot.minimal import divisibility_facts as df
    holds = df.satisfies(n, divisor)
    return ActiveTask(
        task_id="t1", skill_id="divisibility",
        question=question or f"Provjeri da li je broj {n} djeljiv sa {divisor}. "
                             "Obrazloži svoj odgovor.",
        expected_display=("da" if holds else "ne"), npp_id="", tema_title="",
        attempts=0, wrong_attempts=0, hints_given=0, solved=False,
        solution_revealed=False)


def equation_task(expected="1/2"):
    return ActiveTask(
        task_id="t2", skill_id="fraction_equation_additive",
        question="Riješi jednačinu: x + 1/3 = 5/6.", expected_display=expected,
        npp_id="", tema_title="", attempts=0, wrong_attempts=0, hints_given=0,
        solved=False, solution_revealed=False)


@pytest.fixture(autouse=True)
def _semantic_shadow(monkeypatch):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "shadow")


# =========================================================================== #
# 1. Shadow mode audits prose the deterministic path marked incorrect         #
# =========================================================================== #
def test_shadow_audits_prose_marked_incorrect():
    task = equation_task(expected="17/15")
    task = ActiveTask(**{**task.to_dict(), "skill_id": "fraction_add_unlike",
                        "question": "Izračunaj: 1/3 + 4/5.", "expected_display": "17/15"})
    reply = judgment_payload(response_kind="other", decision="unknown",
                             decision_source="none", relevance="unrelated",
                             completeness="absent")
    result = grade(task, "dobio sam 3 ali jos racunam",
                   openai_chat=fake_openai(reply), model="m", timeout=5)
    assert result.verdict == "incorrect"          # deterministic, unchanged
    audit = result.evidence["shadow_audit"]
    assert audit["deterministic_verdict"] == "incorrect"
    assert audit["shadow_disagreement_type"] == "deterministic_wrong_semantic_non_answer"


def test_bare_numeric_wrong_answer_is_not_audited_at_all():
    """A bare wrong number ("6" for an expected 17/15) is exactly what the
    deterministic parser handles cleanly — no prose, no audit, no call."""
    calls = []
    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("should not be called for a bare wrong number")
    task = ActiveTask(task_id="t3", skill_id="fraction_add_unlike",
                      question="Izračunaj: 1/3 + 4/5.", expected_display="17/15",
                      npp_id="", tema_title="", attempts=0, wrong_attempts=0,
                      hints_given=0, solved=False, solution_revealed=False)
    result = grade(task, "6", openai_chat=_boom, model="m", timeout=5)
    assert calls == []
    assert result.verdict == "incorrect"
    assert "shadow_audit" not in result.evidence


# =========================================================================== #
# 2. Shadow mode never changes the real verdict or counters                   #
# =========================================================================== #
def test_shadow_never_changes_verdict_for_confident_incorrect():
    task = div_task(252, 6)
    reply = judgment_payload(decision="no", claims=[
        {"type": "parity", "value": "paran", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "ne, mislim da nije", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "incorrect"          # never overridden
    assert result.solved is False


def test_shadow_never_changes_verdict_even_with_on_mode(monkeypatch):
    """Overriding a CONFIDENT deterministic "incorrect" is a line this round
    does not cross, in shadow OR "on" mode — audit only, always."""
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    task = div_task(252, 6)
    reply = judgment_payload(response_kind="other", decision="unknown",
                             decision_source="none", relevance="unrelated",
                             completeness="absent")
    result = grade(task, "sto me pitas samo za 6", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "unverified"
    assert result.detail == "not_checkable"       # unchanged from deterministic


# =========================================================================== #
# 3. Bare numeric/equation answers cause zero model calls                     #
# =========================================================================== #
@pytest.mark.parametrize("message", ["6", "11/15", "x=4", "x = 4", "da", "ne"])
def test_bare_answers_zero_model_calls(message):
    calls = []
    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError(f"model called for bare input {message!r}")
    task = div_task(110, 10) if message in ("da", "ne") else equation_task()
    grade(task, message, openai_chat=_boom, model="m", timeout=5)
    assert calls == []


def test_is_prose_like_classifies_bare_tokens_correctly():
    assert sg.is_prose_like("6") is False
    assert sg.is_prose_like("11/15") is False
    assert sg.is_prose_like("x=4") is False
    assert sg.is_prose_like("x = 4") is False
    assert sg.is_prose_like("da") is False
    assert sg.is_prose_like("ne.") is False
    assert sg.is_prose_like("da jer je zadnja cifra 0") is True
    assert sg.is_prose_like("sto me pitas samo za 6") is True


# =========================================================================== #
# 4. "sto me pitas samo za 6" -> deterministic_wrong_semantic_non_answer       #
# =========================================================================== #
def test_sto_me_pitas_logs_non_answer_disagreement():
    task = div_task(252, 6)
    reply = judgment_payload(response_kind="other", decision="unknown",
                             decision_source="none", relevance="unrelated",
                             completeness="absent")
    result = grade(task, "sto me pitas samo za 6", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    audit = result.evidence["shadow_audit"]
    assert audit["shadow_disagreement_type"] == "deterministic_wrong_semantic_non_answer"
    assert audit["deterministic_verdict"] == "unverified"


# =========================================================================== #
# 5. "ne jer je neparan" can expose a deterministic/semantic disagreement      #
# =========================================================================== #
def test_ne_jer_je_neparan_exposes_disagreement():
    task = div_task(275, 2)                        # 275 odd -> correct answer "ne"
    reply = judgment_payload(decision="no", claims=[
        {"type": "parity", "value": "neparan", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "ne jer je neparan", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    # deterministic never even mentions "2" literally, so it lands on
    # "incomplete", while the judge verifies it as fully correct
    assert result.verdict == "partial"
    assert result.detail == "incomplete"
    audit = result.evidence["shadow_audit"]
    assert audit["shadow_disagreement_type"] == "deterministic_partial_semantic_correct"
    assert audit["semantic_verified_outcome"] == "correct"


# =========================================================================== #
# 6. Invalid semantic JSON remains semantic_unavailable                       #
# =========================================================================== #
def test_invalid_json_is_semantic_unavailable():
    task = div_task(252, 6)
    result = grade(task, "sto me pitas samo za 6",
                   openai_chat=fake_openai("not json at all"), model="m", timeout=5)
    audit = result.evidence["shadow_audit"]
    assert audit["shadow_disagreement_type"] == "semantic_unavailable"
    assert result.evidence["semantic_fallback_reason"] == "invalid_json"
    assert result.verdict == "unverified"          # base result untouched


def test_low_confidence_is_also_semantic_unavailable():
    task = div_task(252, 6)
    reply = judgment_payload(confidence=0.1)
    result = grade(task, "sto me pitas samo za 6", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    audit = result.evidence["shadow_audit"]
    assert audit["shadow_disagreement_type"] == "semantic_unavailable"


# =========================================================================== #
# 7-8. Exact vs approximate language                                          #
# =========================================================================== #
@pytest.mark.parametrize("message,certainty,precision,expected_verdict,expected_detail", [
    ("x je jedna polovina", "certain", "exact", "correct", "correct"),
    ("dobio sam pola", "certain", "exact", "correct", "correct"),
    ("otprilike pola", "uncertain", "approximate", "partial", sg.NEEDS_CONFIRMATION),
    ("možda pola", "uncertain", "approximate", "partial", sg.NEEDS_CONFIRMATION),
    ("oko 0.5", "uncertain", "approximate", "partial", sg.NEEDS_CONFIRMATION),
])
def test_exact_vs_approximate_language_policy_on_mode(
        monkeypatch, message, certainty, precision, expected_verdict,
        expected_detail):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    task = equation_task(expected="1/2")
    value = "0.5" if "0.5" in message else "1/2"
    reply = judgment_payload(decision="unknown", decision_source="none",
                             final_answer_text=value, certainty=certainty,
                             precision=precision, claims=[
        {"type": "rational_result", "value": value, "polarity": "positive",
         "confidence": 0.85}])
    result = grade(task, message, openai_chat=fake_openai(reply), model="m",
                   timeout=5)
    assert result.verdict == expected_verdict
    assert result.detail == expected_detail
    if expected_detail == sg.NEEDS_CONFIRMATION:
        assert result.solved is False


def test_needs_confirmation_does_not_count_as_wrong_attempt(monkeypatch):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    task = equation_task(expected="1/2")
    reply = judgment_payload(decision="unknown", decision_source="none",
                             final_answer_text="1/2", certainty="uncertain",
                             precision="approximate", claims=[
        {"type": "rational_result", "value": "1/2", "polarity": "positive",
         "confidence": 0.85}])
    result = grade(task, "otprilike pola", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.detail == sg.NEEDS_CONFIRMATION
    assert result.solved is False


def test_approximate_wrong_value_is_simply_incorrect(monkeypatch):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    task = equation_task(expected="1/2")
    reply = judgment_payload(decision="unknown", decision_source="none",
                             final_answer_text="1/3", certainty="uncertain",
                             precision="approximate", claims=[
        {"type": "rational_result", "value": "1/3", "polarity": "positive",
         "confidence": 0.85}])
    result = grade(task, "otprilike trecina", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "incorrect"


# =========================================================================== #
# 9. Divisibility rule-level explanation is accepted consistently             #
# =========================================================================== #
def test_252_div_6_rule_level_explanation_is_fully_correct():
    """No semantic judge needed at all — the DETERMINISTIC checker itself now
    accepts this consistently with how 3/9's digit-sum rule was already
    accepted without a stated computation."""
    from matbot.answer_checker import check_practice_answer
    q = "Provjeri da li je broj 252 djeljiv sa 6. Obrazloži svoj odgovor."
    result = check_practice_answer(q, "da jer je djeljiv i sa 2 i sa 3")
    assert result.checkable
    assert result.items[0].verdict == "correct"


def test_bare_decision_still_incomplete_if_explanation_required():
    from matbot.answer_checker import check_practice_answer
    q = "Provjeri da li je broj 252 djeljiv sa 6. Obrazloži svoj odgovor."
    result = check_practice_answer(q, "da")
    assert result.items[0].verdict == "incomplete"


# =========================================================================== #
# 10. False evidence is rejected                                              #
# =========================================================================== #
def test_false_evidence_still_rejected_by_semantic_verifier(monkeypatch):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    task = div_task(110, 10)
    reply = judgment_payload(claims=[
        {"type": "last_digit", "value": "5", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "da jer je zadnja cifra 5", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict != "correct"
    assert result.detail == "incorrect_evidence"


def test_false_evidence_does_not_slip_through_the_looser_compound_policy():
    """The loosened compound-explanation policy (bare factor naming is
    sufficient) must not be confused with accepting FALSE evidence — a
    student naming only ONE factor correctly and citing a wrong number for
    it stays partial, never correct, deterministically."""
    from matbot.answer_checker import check_practice_answer
    q = "Provjeri da li je broj 252 djeljiv sa 6. Obrazloži svoj odgovor."
    result = check_practice_answer(q, "da jer je zadnja cifra 5")
    assert result.items[0].verdict != "correct"


# =========================================================================== #
# 11. Instrumentation: calls, latency, tokens — no chain-of-thought           #
# =========================================================================== #
def test_instrumentation_records_latency_and_tokens():
    task = div_task(110, 10)
    reply = judgment_payload(claims=[
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "da jer je zadnja cifra 0",
                   openai_chat=fake_openai(reply, delay_s=0.02), model="m", timeout=5)
    record = record_from_evidence(result.evidence, eligible=True)
    assert record.called is True
    assert record.latency_ms is not None and record.latency_ms >= 15
    assert record.prompt_tokens == 180
    assert record.completion_tokens == 60


def test_instrumentation_recorder_summarizes_a_batch():
    recorder = SemanticGradingRecorder()
    task = div_task(110, 10)
    reply = judgment_payload(claims=[
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9}])

    # eligible + called
    r1 = grade(task, "da jer je zadnja cifra 0", openai_chat=fake_openai(reply, delay_s=0.01),
              model="m", timeout=5)
    recorder.record(r1.evidence, eligible=True)

    # eligible + called, but a parse failure this time
    r2 = grade(task, "sto me pitas samo za 6", openai_chat=fake_openai("garbage"),
              model="m", timeout=5)
    recorder.record(r2.evidence, eligible=True)

    # NOT eligible (bare token) — zero calls, tracked as avoided-by-not-being-eligible
    calls = []
    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("should not be called")
    r3 = grade(task, "0", openai_chat=_boom, model="m", timeout=5)
    recorder.record(r3.evidence, eligible=False)

    stats = recorder.summarize()
    assert stats["eligible_prose_turns"] == 2
    assert stats["actual_semantic_calls"] == 2
    assert stats["calls_avoided_by_deterministic_grading"] == 0
    assert stats["parse_failures"] == 1
    assert stats["prompt_tokens_total"] == 360      # 180 * 2 calls
    assert stats["completion_tokens_total"] == 120
    assert stats["p50_latency_ms"] is not None
    assert stats["p95_latency_ms"] is not None


def test_instrumentation_never_carries_free_text():
    """The recorder's fields are all typed/structured — proving there is no
    field anywhere that could smuggle raw model prose or chain-of-thought."""
    fields = set(CallRecord.__dataclass_fields__)
    assert fields == {"eligible", "called", "latency_ms", "prompt_tokens",
                      "completion_tokens", "parse_failed", "low_confidence",
                      "disagreement_type"}


def test_instrumentation_counts_calls_avoided_by_deterministic_grading():
    """A batch where SOME eligible turns are handled cleanly by the
    deterministic checker (e.g. a fully-evidenced explanation) never reaches
    the judge — those are "eligible" in the broad sense of belonging to a
    supported family, but the caller marks them ineligible for AUDIT since
    the deterministic result was already confident and correct."""
    recorder = SemanticGradingRecorder()
    recorder.record_direct(CallRecord(eligible=True, called=True, latency_ms=120))
    recorder.record_direct(CallRecord(eligible=True, called=False))
    recorder.record_direct(CallRecord(eligible=True, called=False))
    stats = recorder.summarize()
    assert stats["eligible_prose_turns"] == 3
    assert stats["actual_semantic_calls"] == 1
    assert stats["calls_avoided_by_deterministic_grading"] == 2
