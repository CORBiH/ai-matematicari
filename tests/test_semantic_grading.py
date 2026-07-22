# -*- coding: utf-8 -*-
"""SemanticAnswerJudge — unit-level tests of the shared grading layer.

Direct calls to ``matbot.minimal.grading.grade`` and ``semantic_grading``,
with a fake ``openai_chat`` returning a canned JSON judgment per test. No
Flask, no network — this is the fastest, most precise place to pin down the
deterministic claim-verification rules themselves; the SSE-level file
(``test_minimal_semantic_grading.py``) proves the same behaviour end to end.
"""
import json

import pytest

from matbot.minimal import semantic_grading as sg
from matbot.minimal.grading import grade
from matbot.minimal.state import ActiveTask


def fake_openai(reply):
    """A minimal stand-in for the real ``openai_chat`` callable."""
    def _fake(model, messages, timeout=None, max_tokens=None, **kw):
        class _Msg:
            content = reply if isinstance(reply, str) else json.dumps(
                reply, ensure_ascii=False)
        class _Choice:
            message = _Msg()
        class _Resp:
            choices = [_Choice()]
        return _Resp()
    return _fake


def raising_openai(exc):
    def _fake(*a, **kw):
        raise exc
    return _fake


def judgment_payload(**overrides):
    payload = {
        "understood": True, "response_kind": "answer", "decision": "yes",
        "decision_source": "explicit", "final_answer_text": "",
        "claims": [], "explanation_present": True, "relevance": "direct",
        "completeness": "complete", "ambiguity": "", "confidence": 0.9,
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


@pytest.fixture(autouse=True)
def _semantic_on(monkeypatch):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")


# =========================================================================== #
# 1. Deterministic-first: a clearly checkable answer never calls the model    #
# =========================================================================== #
def test_bare_answer_never_calls_the_model():
    calls = []
    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("model should not be called")
    task = div_task(110, 10)
    result = grade(task, "da", openai_chat=_boom, model="m", timeout=5)
    assert calls == []
    assert result.verdict == "partial"          # bare "da": unchanged behaviour


def test_fully_matching_deterministic_answer_never_calls_the_model():
    calls = []
    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("model should not be called")
    task = div_task(110, 10)
    result = grade(task, "da, 110 je djeljiv sa 10 jer je zadnja cifra 0",
                   openai_chat=_boom, model="m", timeout=5)
    assert calls == []
    assert result.verdict == "correct"


def test_semantic_grading_off_never_calls_the_model():
    import os
    os.environ["MATBOT_SEMANTIC_GRADING"] = "off"
    try:
        calls = []
        def _boom(*a, **kw):
            calls.append(1)
            raise AssertionError("model should not be called in off mode")
        task = div_task(110, 10)
        result = grade(task, "da jer je zadnja cifra 0", openai_chat=_boom,
                       model="m", timeout=5)
        assert calls == []
        assert result.verdict == "partial"       # unchanged, off mode
    finally:
        os.environ["MATBOT_SEMANTIC_GRADING"] = "on"


# =========================================================================== #
# 3. Exact production cases                                                   #
# =========================================================================== #
def test_110_div_10_last_digit_zero():
    task = div_task(110, 10)
    reply = judgment_payload(claims=[
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "da jer je zadnja cifra 0", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "correct"
    assert result.detail == "correct"
    assert result.evidence["semantic_judge_used"] is True


def test_275_div_2_odd_rejects():
    task = div_task(275, 2)
    reply = judgment_payload(decision="no", claims=[
        {"type": "parity", "value": "neparan", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "ne jer je neparan", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "correct"


def test_84_div_2_implicit_yes():
    task = div_task(84, 2)
    reply = judgment_payload(decision_source="implicit", claims=[
        {"type": "parity", "value": "paran", "polarity": "positive", "confidence": 0.85}])
    result = grade(task, "jer je broj paran", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "correct"


def test_non_answer_comment_produces_no_verdict():
    task = div_task(252, 6)
    reply = judgment_payload(response_kind="other", decision="unknown",
                             decision_source="none", claims=[],
                             relevance="unrelated", completeness="absent")
    result = grade(task, "sto me pitas samo za 6", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.detail == "not_checkable"
    assert result.graded_answer == ""


# =========================================================================== #
# 4. False evidence is not accepted                                           #
# =========================================================================== #
def test_false_evidence_rejected_110_div_10():
    task = div_task(110, 10)
    reply = judgment_payload(claims=[
        {"type": "last_digit", "value": "5", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "da jer je zadnja cifra 5", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict != "correct"
    assert result.detail == "incorrect_evidence"


# =========================================================================== #
# 5. Compound rules (6, 15)                                                   #
# =========================================================================== #
def test_compound_6_correct_yes_explanation():
    task = div_task(252, 6)
    reply = judgment_payload(claims=[
        {"type": "divisibility_factor", "value": "2", "polarity": "positive", "confidence": 0.9},
        {"type": "divisibility_factor", "value": "3", "polarity": "positive", "confidence": 0.9},
    ])
    result = grade(task, "da jer je djeljiv i sa 2 i sa 3",
                   openai_chat=fake_openai(reply), model="m", timeout=5)
    assert result.verdict == "correct"


def test_compound_6_one_failing_condition_sufficient_for_no():
    task = div_task(155, 6)                       # odd -> fails factor 2 only
    reply = judgment_payload(decision="no", claims=[
        {"type": "parity", "value": "neparan", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "ne jer nije paran", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "correct"


def test_compound_15_correct_yes_explanation():
    task = div_task(45, 15)
    reply = judgment_payload(claims=[
        {"type": "digit_sum", "value": "9", "polarity": "positive", "confidence": 0.9},
        {"type": "last_digit", "value": "5", "polarity": "positive", "confidence": 0.9},
    ])
    result = grade(task, "da, zbir cifara je 9 i zadnja cifra je 5",
                   openai_chat=fake_openai(reply), model="m", timeout=5)
    assert result.verdict == "correct"


def test_compound_15_one_failing_condition_sufficient_for_no():
    task = div_task(51, 15)                       # fails factor 5 only
    reply = judgment_payload(decision="no", claims=[
        {"type": "last_digit", "value": "1", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "ne jer zadnja cifra nije 0 ni 5",
                   openai_chat=fake_openai(reply), model="m", timeout=5)
    assert result.verdict == "correct"


def test_wrong_decision_still_incorrect_regardless_of_claims():
    task = div_task(252, 6)                       # true answer: da
    reply = judgment_payload(decision="no", claims=[
        {"type": "parity", "value": "paran", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "ne, iako je paran", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "incorrect"


# =========================================================================== #
# 6. Rational and equation prose                                              #
# =========================================================================== #
def test_rational_prose_word_form():
    task = ActiveTask(task_id="t2", skill_id="fraction_add_unlike",
                      question="Izračunaj: 1/3 + 4/5.", expected_display="17/15",
                      npp_id="", tema_title="", attempts=0, wrong_attempts=0,
                      hints_given=0, solved=False, solution_revealed=False)
    reply = judgment_payload(decision="unknown", decision_source="none",
                             final_answer_text="17/15", claims=[
        {"type": "rational_result", "value": "17/15", "polarity": "positive",
         "confidence": 0.85}])
    result = grade(task, "mislim da je to sedamnaest petnaestina",
                   openai_chat=fake_openai(reply), model="m", timeout=5)
    assert result.verdict == "correct"
    assert result.graded_answer == "17/15"


def test_equation_prose_word_form():
    task = ActiveTask(task_id="t3", skill_id="fraction_equation_additive",
                      question="Riješi jednačinu: x + 1/3 = 5/6.",
                      expected_display="1/2", npp_id="", tema_title="",
                      attempts=0, wrong_attempts=0, hints_given=0, solved=False,
                      solution_revealed=False)
    reply = judgment_payload(decision="unknown", decision_source="none",
                             final_answer_text="1/2", claims=[
        {"type": "rational_result", "value": "1/2", "polarity": "positive",
         "confidence": 0.85}])
    result = grade(task, "mislim da je otprilike pola",
                   openai_chat=fake_openai(reply), model="m", timeout=5)
    assert result.verdict == "correct"
    assert result.graded_answer == "1/2"


def test_rational_prose_wrong_value():
    task = ActiveTask(task_id="t4", skill_id="fraction_add_unlike",
                      question="Izračunaj: 1/3 + 4/5.", expected_display="17/15",
                      npp_id="", tema_title="", attempts=0, wrong_attempts=0,
                      hints_given=0, solved=False, solution_revealed=False)
    reply = judgment_payload(decision="unknown", decision_source="none",
                             final_answer_text="1/2", claims=[
        {"type": "rational_result", "value": "1/2", "polarity": "positive",
         "confidence": 0.8}])
    result = grade(task, "mislim da je otprilike pola",
                   openai_chat=fake_openai(reply), model="m", timeout=5)
    assert result.verdict == "incorrect"


# =========================================================================== #
# 7-9. Safe failure                                                          #
# =========================================================================== #
def test_invalid_json_fails_safe():
    task = div_task(252, 6)
    result = grade(task, "sto me pitas samo za 6",
                   openai_chat=fake_openai("this is not json"), model="m", timeout=5)
    assert result.detail == "not_checkable"
    assert result.evidence["semantic_fallback_reason"] == "invalid_json"


def test_timeout_fails_safe():
    task = div_task(252, 6)
    result = grade(task, "sto me pitas samo za 6",
                   openai_chat=raising_openai(TimeoutError("slow")), model="m",
                   timeout=5)
    assert result.detail == "not_checkable"
    assert result.evidence["semantic_fallback_reason"] == "model_error"


def test_low_confidence_fails_safe():
    task = div_task(252, 6)
    reply = judgment_payload(confidence=0.2)
    result = grade(task, "sto me pitas samo za 6", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.detail == "not_checkable"
    assert result.evidence["semantic_fallback_reason"] == "low_confidence"


def test_schema_mismatch_missing_field_fails_safe():
    task = div_task(252, 6)
    broken = {"understood": True, "response_kind": "answer"}   # missing fields
    result = grade(task, "sto me pitas samo za 6",
                   openai_chat=fake_openai(broken), model="m", timeout=5)
    assert result.detail == "not_checkable"
    assert result.evidence["semantic_fallback_reason"] == "invalid_json"


def test_invalid_response_kind_fails_safe():
    task = div_task(252, 6)
    reply = judgment_payload(response_kind="verdict_correct")   # not allowed
    result = grade(task, "sto me pitas samo za 6", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.detail == "not_checkable"


def test_claim_type_outside_vocabulary_is_dropped_not_fatal():
    """An out-of-vocabulary claim type is silently dropped, not a hard fail —
    the rest of the judgment can still be used."""
    task = div_task(110, 10)
    reply = judgment_payload(claims=[
        {"type": "equation_step", "value": "irrelevant", "polarity": "positive",
         "confidence": 0.9},
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9},
    ])
    result = grade(task, "da jer je zadnja cifra 0", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "correct"


# =========================================================================== #
# 10. The model never decides counters or completes a task directly           #
# =========================================================================== #
def test_model_output_cannot_set_solved_directly():
    """Even if the model's JSON is well-formed and confident, ONLY the
    deterministic verifier (comparing claims to verified_facts) decides
    ``solved`` — proven here by feeding claims that CONTRADICT the decision."""
    task = div_task(110, 10)
    reply = judgment_payload(claims=[
        {"type": "last_digit", "value": "7", "polarity": "positive", "confidence": 0.99}])
    result = grade(task, "da jer je zadnja cifra 7", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.solved is False
    assert result.verdict != "correct"


def test_semantic_result_never_carries_a_hidden_expected_answer():
    """The judgment object itself has no field for a numeric verdict or
    expected answer — only claims/decision/response_kind."""
    fields = set(sg.SemanticJudgment.__dataclass_fields__)
    assert "verdict" not in fields
    assert "expected_answer" not in fields
    assert "correct" not in fields


# =========================================================================== #
# 11. Shadow mode: log, never change the verdict                              #
# =========================================================================== #
def test_shadow_mode_logs_but_does_not_change_verdict(monkeypatch):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "shadow")
    task = div_task(110, 10)
    reply = judgment_payload(claims=[
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "da jer je zadnja cifra 0", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    # the DETERMINISTIC verdict ("incomplete") survives unchanged...
    assert result.verdict == "partial"
    assert result.detail == "incomplete"
    # ...even though the judge ran and would have said "correct"
    assert result.evidence["semantic_judge_used"] is True
    assert result.evidence["deterministic_claim_verification"] == "correct"


def test_shadow_mode_logs_false_evidence_too(monkeypatch):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "shadow")
    task = div_task(110, 10)
    reply = judgment_payload(claims=[
        {"type": "last_digit", "value": "5", "polarity": "positive", "confidence": 0.9}])
    result = grade(task, "da jer je zadnja cifra 5", openai_chat=fake_openai(reply),
                   model="m", timeout=5)
    assert result.verdict == "partial"           # unchanged deterministic path
    assert result.evidence["deterministic_claim_verification"] == "incorrect_evidence"


# =========================================================================== #
# Cross-skill scoping                                                         #
# =========================================================================== #
def test_unsupported_skill_never_invokes_the_judge():
    calls = []
    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("should not be called for an unbuildable context")
    task = ActiveTask(task_id="t5", skill_id="prime_factorization",
                      question="Rastavi 60 na proste faktore.",
                      expected_display="2*2*3*5", npp_id="", tema_title="",
                      attempts=0, wrong_attempts=0, hints_given=0, solved=False,
                      solution_revealed=False)
    result = grade(task, "nemam pojma stvarno",
                   openai_chat=_boom, model="m", timeout=5)
    assert calls == []
    assert result.evidence.get("semantic_fallback_reason") == "unsupported_skill"


def test_build_context_returns_none_for_unsupported_skill():
    task = ActiveTask(task_id="t6", skill_id="prime_factorization",
                      question="Rastavi 60 na proste faktore.",
                      expected_display="2*2*3*5", npp_id="", tema_title="",
                      attempts=0, wrong_attempts=0, hints_given=0, solved=False,
                      solution_revealed=False)
    assert sg.build_context(task, "nesto") is None
