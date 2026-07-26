# -*- coding: utf-8 -*-
"""V3 output quality gate: deterministic checks + the ONE bounded model repair.

No live/paid OpenAI call — repair is exercised through the FakeClient pattern
already used by ``tests/test_v3_practice.py``.
"""
from __future__ import annotations

from matbot.ai_tutor_v3 import quality_gate as qg
from matbot.ai_tutor_v3 import orchestrator


def test_empty_text_fails():
    r = qg.check("", response_type="task", grade=6)
    assert not r.ok
    assert "empty_text" in r.failure_categories


def test_ordinary_task_passes():
    r = qg.check("Da li je 252 djeljiv sa 6?", response_type="task", grade=6)
    assert r.ok


def test_internal_terminology_is_rejected():
    r = qg.check("Prema Blueprint-u, tvoj confidence je nizak.",
                 response_type="task", grade=6)
    assert not r.ok
    assert "internal_terminology" in r.failure_categories


def test_undelimited_latex_is_detected():
    r = qg.check(r"Proširi razlomak \frac{3}{4} brojem 2.",
                 response_type="task", grade=6)
    assert not r.ok
    assert "undelimited_latex" in r.failure_categories


def test_already_delimited_latex_passes():
    r = qg.check(r"Proširi razlomak \( \frac{3}{4} \) brojem 2.",
                 response_type="task", grade=6)
    assert r.ok


def test_unbalanced_braces_is_malformed():
    r = qg.check(r"\( \frac{3}{4 \)", response_type="task", grade=6)
    assert not r.ok
    assert "malformed_fraction_notation" in r.failure_categories


def test_html_is_rejected():
    r = qg.check("Tačno! <script>alert(1)</script>", response_type="task", grade=6)
    assert not r.ok
    assert "unsupported_html" in r.failure_categories


def test_br_tag_is_allowed():
    r = qg.check("Prvi red.<br>Drugi red.", response_type="feedback", grade=6)
    assert r.ok


def test_duplicated_sentence_is_detected():
    r = qg.check("Tačno je. Tačno je.", response_type="feedback", grade=6)
    assert not r.ok
    assert "duplicated_sentence" in r.failure_categories


def test_task_too_long_for_grade_6():
    long_task = "Riješi ovaj zadatak. " * 15
    r = qg.check(long_task, response_type="task", grade=6)
    assert not r.ok
    assert ("task_too_long" in r.failure_categories
           or "task_too_long_for_grade" in r.failure_categories)


def test_grade_9_allows_longer_task_than_grade_6():
    text = "Riješi jednačinu koristeći supstituciju i zapiši svaki korak. " * 4
    r6 = qg.check(text, response_type="task", grade=6)
    r9 = qg.check(text, response_type="task", grade=9)
    # Same text may still fail for both at this length, but grade 6's budget
    # must never be MORE permissive than grade 9's.
    assert qg._TASK_MAX_CHARS_BY_GRADE[6] < qg._TASK_MAX_CHARS_BY_GRADE[9]


def test_short_boolean_answer_is_not_treated_as_a_leak():
    """'da'/'ne' are ordinary Bosnian words that appear in normal question
    phrasing ('Da li je...') — must never false-positive as a revealed
    answer just because the expected boolean answer is also 'da'."""
    r = qg.check("Da li je 252 djeljiv sa 6?", response_type="task", grade=6,
                 forbidden_reveal="da")
    assert r.ok


def test_long_distinctive_answer_is_detected_as_a_leak():
    r = qg.check("Odgovor je razlomak 17/23, izračunaj ga.",
                 response_type="task", grade=6, forbidden_reveal="17/23")
    assert not r.ok
    assert "answer_revealed_in_task" in r.failure_categories


def test_repeated_task_prefix_is_detected():
    text = "Izračunaj 2 + 2."
    r = qg.check(text, response_type="task", grade=6, previous_text=text)
    assert not r.ok
    assert "repeated_task_prefix" in r.failure_categories


def test_shaming_language_is_rejected():
    r = qg.check("To je glupo, pokušaj ponovo.", response_type="feedback", grade=6)
    assert not r.ok
    assert "inappropriate_language" in r.failure_categories


# --------------------------------------------------------------------------- #
# Bounded repair (orchestrator.repair_student_text) — no live OpenAI call     #
# --------------------------------------------------------------------------- #
class _FakeBlueprint:
    allowed_methods = ["školska metoda"]

    class _Lang:
        language_register = "grade_6"
    language_guidance = _Lang()


class _RepairFakeClient:
    """Returns a fixed, GATE-PASSING repaired narration for any repair call."""

    def __init__(self, repaired_text="U redu, hajde ponovo.", status="ok"):
        self.calls = []
        self.repaired_text = repaired_text
        self.status = status

    def generate(self, *, purpose, system, user, schema_name, schema, model, timeout,
                response_model=None, max_output_tokens=None):
        self.calls.append(purpose)
        if self.status != "ok":
            return orchestrator.ModelCallResult(status=self.status, model=model,
                                                purpose=purpose, error_code=self.status)
        parsed = {"schema_version": "v1", "student_text": self.repaired_text,
                 "response_category": "feedback", "confidence": 0.9}
        if response_model is not None:
            parsed = response_model.model_validate(parsed).model_dump(mode="json")
        return orchestrator.ModelCallResult(status="ok", parsed=parsed, model=model,
                                            purpose=purpose)


def test_repair_student_text_returns_a_narration_result():
    client = _RepairFakeClient()
    result, call = orchestrator.repair_student_text(
        client, grade=6, rejected_text="Prema Blueprint-u, tačno je.",
        failure_categories=["internal_terminology"], response_type="feedback",
        blueprint=_FakeBlueprint(), model="gpt-5-mini", timeout=5)
    assert call.purpose == orchestrator.PURPOSE_REPAIR
    assert result is not None
    assert result.student_text == "U redu, hajde ponovo."


def test_repair_student_text_handles_model_error_safely():
    client = _RepairFakeClient(status="error")
    result, call = orchestrator.repair_student_text(
        client, grade=6, rejected_text="Prema Blueprint-u, tačno je.",
        failure_categories=["internal_terminology"], response_type="feedback",
        blueprint=_FakeBlueprint(), model="gpt-5-mini", timeout=5)
    assert result is None
    assert call.status == "error"


def test_repair_prompt_does_not_include_full_blueprint_dump():
    """The bounded-repair contract: only the rejected text, failure reasons,
    grade, response type, and a COMPACT blueprint constraint summary — never
    the full Blueprint (e.g. its raw concepts/misconceptions lists)."""
    captured = {}

    class _CapturingClient(_RepairFakeClient):
        def generate(self, *, purpose, system, user, schema_name, schema, model, timeout,
                    response_model=None, max_output_tokens=None):
            captured["system"] = system
            captured["user"] = user
            return super().generate(purpose=purpose, system=system, user=user,
                                    schema_name=schema_name, schema=schema,
                                    model=model, timeout=timeout,
                                    response_model=response_model,
                                    max_output_tokens=max_output_tokens)

    orchestrator.repair_student_text(
        _CapturingClient(), grade=6, rejected_text="tekst",
        failure_categories=["internal_terminology"], response_type="feedback",
        blueprint=_FakeBlueprint(), model="gpt-5-mini", timeout=5)
    assert "concepts" not in captured["user"].lower()
    assert "common_misconceptions" not in captured["user"].lower()
    assert "rejected" not in captured["system"].lower()  # system = policy layers only
