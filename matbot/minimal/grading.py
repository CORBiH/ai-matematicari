# -*- coding: utf-8 -*-
"""Concept 4: **GradingResult** — and the single component that produces it.

One turn produces at most one ``GradingResult``. It is created here and nowhere
else, from the student's raw text and the ``ActiveTask``'s question. The model
is never consulted: for the five supported skills deterministic evidence always
exists, so there is nothing for it to decide.

``GradingResult`` is FROZEN once returned. The renderer receives it and may
choose words for it; it cannot change it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from matbot.answer_checker import check_practice_answer
from matbot.minimal.state import ActiveTask

#: Checker verdicts that mean the task is genuinely solved.
_SOLVED = {"correct", "correct_equivalent_form", "correct_missing_notation"}
#: Right value, presentation not ideal. Solved, but worth a note.
_SOLVED_WITH_NOTE = {"correct_missing_unit", "correct_value_wrong_form"}
#: Real progress that is not yet the answer.
_PARTIAL = {"partially_correct", "incomplete", "correct_step"}


@dataclass(frozen=True)
class GradingResult:
    """The turn's single, authoritative correctness decision."""
    task_id: str
    verdict: str                    # correct | partial | incorrect | unverified
    solved: bool                    # the task is finished
    student_raw: str                # EXACTLY what the student typed
    expected_display: str = ""
    detail: str = ""                # the underlying checker verdict
    deterministic: bool = True
    # Audit evidence: what was compared, and what it normalized to. Produced
    # HERE because this is the grading owner — nothing downstream re-derives it.
    answer_type: str = ""
    normalized_expected: str = ""
    normalized_student: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def is_correct(self) -> bool:
        return self.verdict == "correct"

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "verdict": self.verdict,
                "solved": self.solved, "expected_display": self.expected_display,
                "detail": self.detail, "deterministic": self.deterministic,
                "answer_type": self.answer_type,
                "normalized_expected": self.normalized_expected,
                "normalized_student": self.normalized_student,
                "evidence": dict(self.evidence)}


def _normalized(value: Any) -> str:
    """Canonical form of a checker value, or "".

    ``Fraction`` reduces automatically, so 16/48 normalizes to 1/3 — which is
    exactly the evidence that explains why 4/48 (1/12) was rejected.
    """
    if value is None:
        return ""
    return str(value)


#: Verdict for "right value, wrong required form" on a form-bound skill.
WRONG_TARGET_DENOMINATOR = "incorrect_target_denominator"

_FRACTION_RE = re.compile(r"(-?\d+)\s*/\s*(\d+)")


def _apply_skill_policy(task: ActiveTask, raw: str, *, verdict: str,
                        solved: bool, detail: str) -> tuple[str, bool, str]:
    """Tighten acceptance where the TASK demands a specific form.

    ``fraction_expand`` names the target denominator in the question, so an
    answer is only complete when it uses it. Production accepted 4/8 for
    "Proširi 1/2 na nazivnik 4" (equivalent value, wrong denominator) and even
    2/4 for "Proširi 2/4 na nazivnik 20" (the unexpanded original), because the
    generic checker reports ``correct_value_wrong_form``, which mapped to
    "correct".

    Every other skill is returned unchanged — equivalent forms stay acceptable.
    """
    if task.skill_id != "fraction_expand":
        return verdict, solved, detail

    expected = _FRACTION_RE.search(task.expected_display or "")
    student = _FRACTION_RE.search(raw or "")
    if expected is None or student is None:
        return verdict, solved, detail

    exp_num, exp_den = int(expected.group(1)), int(expected.group(2))
    stu_num, stu_den = int(student.group(1)), int(student.group(2))
    if stu_num == exp_num and stu_den == exp_den:
        return verdict, solved, detail          # exactly the required form

    # Equivalent value but not the requested denominator → real progress, not a
    # solved task. The task stays active and the streak must not advance.
    if stu_den and exp_den and stu_num * exp_den == exp_num * stu_den:
        return "partial", False, WRONG_TARGET_DENOMINATOR
    return "incorrect", False, ("incorrect" if detail.startswith("correct")
                                else detail)


def target_denominator(task: ActiveTask) -> int | None:
    """The denominator a fraction_expand task explicitly requires."""
    if task.skill_id != "fraction_expand":
        return None
    match = _FRACTION_RE.search(task.expected_display or "")
    return int(match.group(2)) if match else None


def grade(task: ActiveTask, raw_message: Any) -> GradingResult:
    """Grade one answer against one task. The ONLY grading entry point.

    ``raw_message`` is stored verbatim — never normalized, trimmed into meaning,
    or reconstructed from anything the tutor said.
    """
    raw = str(raw_message if raw_message is not None else "")
    result = check_practice_answer(task.question, raw)
    if result is None or not getattr(result, "checkable", False) or not result.items:
        # Deterministic checking failed for a task we believed was checkable.
        # We say so honestly rather than asking the model to guess.
        return GradingResult(task_id=task.task_id, verdict="unverified",
                             solved=False, student_raw=raw,
                             expected_display=task.expected_display,
                             detail="not_checkable", deterministic=False)

    item = result.items[0]
    detail = str(item.verdict or "")

    # Deterministic evidence, straight off the checker's own objects.
    expected_obj = getattr(item, "expected", None)
    given_obj = getattr(item, "given", None)
    answer_type = str(getattr(expected_obj, "answer_type", "") or "")
    normalized_expected = _normalized(getattr(expected_obj, "value", None))
    normalized_student = _normalized(getattr(given_obj, "value", None))
    evidence = {
        "method": "deterministic",
        "checker_verdict": detail,
        "expected_display": task.expected_display,
        "expected_normalized": normalized_expected,
        "student_raw": raw.strip()[:120],
        "student_normalized": normalized_student,
        "answer_type": answer_type,
        "expected_unit": str(getattr(expected_obj, "unit", "") or ""),
        "gpt_check_used": False,
    }

    if detail in _SOLVED or detail in _SOLVED_WITH_NOTE:
        verdict, solved = "correct", True
    elif detail in _PARTIAL:
        verdict, solved = "partial", False
    elif detail in ("missing", "not_attempted", "unverified", "ambiguous",
                    "needs_review"):
        verdict, solved = "unverified", False
    else:
        verdict, solved = "incorrect", False

    # Skill-specific acceptance: the general checker judges VALUE, but some
    # tasks also require a FORM. Applied after the generic mapping so the
    # checker itself stays untouched for every other skill.
    verdict, solved, detail = _apply_skill_policy(
        task, raw, verdict=verdict, solved=solved, detail=detail)
    evidence["checker_verdict"] = detail
    evidence["match"] = solved
    return GradingResult(
        task_id=task.task_id, verdict=verdict, solved=solved, student_raw=raw,
        expected_display=task.expected_display, detail=detail,
        deterministic=True, answer_type=answer_type,
        normalized_expected=normalized_expected,
        normalized_student=normalized_student, evidence=evidence,
    )
