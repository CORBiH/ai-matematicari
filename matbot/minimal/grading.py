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

from dataclasses import dataclass
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

    @property
    def is_correct(self) -> bool:
        return self.verdict == "correct"

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "verdict": self.verdict,
                "solved": self.solved, "expected_display": self.expected_display,
                "detail": self.detail, "deterministic": self.deterministic}


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

    detail = str(result.items[0].verdict or "")
    if detail in _SOLVED or detail in _SOLVED_WITH_NOTE:
        verdict, solved = "correct", True
    elif detail in _PARTIAL:
        verdict, solved = "partial", False
    elif detail in ("missing", "not_attempted", "unverified", "ambiguous",
                    "needs_review"):
        verdict, solved = "unverified", False
    else:
        verdict, solved = "incorrect", False

    return GradingResult(
        task_id=task.task_id, verdict=verdict, solved=solved, student_raw=raw,
        expected_display=task.expected_display, detail=detail,
        deterministic=True,
    )
