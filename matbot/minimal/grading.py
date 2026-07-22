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
    #: The value that was actually graded: the extracted candidate, or the raw
    #: text when it was directly checkable. "" when nothing could be identified,
    #: so the audit never records prose as if it were the student's answer.
    graded_answer: str = ""
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
                "graded_answer": self.graded_answer,
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


#: An explicit "the result is …" marker. The fraction AFTER it wins, so an
#: intermediate step earlier in the sentence is never mistaken for the answer.
_RESULT_MARKER_RE = re.compile(
    r"(?:rezultat|odgovor|rje[sš]enj\w*|kona[cč]n\w*|dobij\w*|dobio\s+sam|"
    r"ispada|zato\s+je|znaci|dakle|to\s+je)\b", re.IGNORECASE)
#: A fraction, a mixed number, or a bare integer — any final answer shape.
_CANDIDATE_RE = re.compile(r"\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+")


#: The student wrote several numbers but never said which one is the answer.
AMBIGUOUS_FINAL_ANSWER = "ambiguous_final_answer"

#: Nothing in the message could be matched to any recognised answer form at
#: all — not even a candidate number to retry. Like ``AMBIGUOUS_FINAL_ANSWER``,
#: no ``graded_answer`` was ever identified, so this must not count as an
#: attempt either (engine.py treats both identically for that reason).
NOT_CHECKABLE = "not_checkable"

#: Status of an extraction attempt.
FOUND = "found"
AMBIGUOUS = "ambiguous"
NONE = "none"

#: How far after a marker the declared answer may sit. "odgovor je 11/15" and
#: "rezultat: 11/15" both fit; anything further is reasoning, not the answer.
_MARKER_WINDOW = 24


def extract_final_answer(raw: str) -> tuple[str, str]:
    """The student's DECLARED final answer, with a status.

    Reusable for any rational-answer skill; the skill-specific acceptance
    policies still decide whether the extracted value/form is acceptable.

    Policy, in order:
      1. the candidate DIRECTLY after an explicit final-answer marker;
      2. a final equality / conclusion line whose right-hand side is a
         candidate, when it is safely the last thing said;
      3. exactly one candidate in the whole message;
      4. otherwise ``AMBIGUOUS`` — several candidates and no declared answer.

    Deliberately never "the last fraction": in "Mislim da je odgovor 11/15 jer
    je 1/3 = 5/15, a 2/5 = 6/15." the last token is 6/15 but the answer is
    11/15, and guessing would misgrade a correct student.
    """
    text = str(raw or "")
    if not text.strip():
        return "", NONE

    # 1. directly after the LAST explicit marker
    for marker in reversed(list(_RESULT_MARKER_RE.finditer(text))):
        window = text[marker.end():marker.end() + _MARKER_WINDOW]
        first = _CANDIDATE_RE.search(window)
        if first and not window[:first.start()].strip(" :=\t"):
            return first.group(0).strip(), FOUND
        if first:
            return first.group(0).strip(), FOUND

    # 2. a conclusion on the final line ("= 11/15", or the answer alone)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        last = lines[-1].lstrip("=").strip()
        if _CANDIDATE_RE.fullmatch(last):
            return last, FOUND

    found = [m.group(0).strip() for m in _CANDIDATE_RE.finditer(text)]
    if not found:
        return "", NONE
    # 3. exactly one candidate is unambiguous
    if len(found) == 1:
        return found[0], FOUND
    # 4. several candidates, nothing declared → do NOT guess
    return "", AMBIGUOUS


def extract_answer_candidate(raw: str) -> str:
    """Back-compat helper: the declared answer, or "" when it is not clear."""
    return extract_final_answer(raw)[0]


def grade(task: ActiveTask, raw_message: Any) -> GradingResult:
    """Grade one answer against one task. The ONLY grading entry point.

    ``raw_message`` is stored verbatim — never normalized, trimmed into meaning,
    or reconstructed from anything the tutor said.
    """
    raw = str(raw_message if raw_message is not None else "")
    graded_text, candidate = raw, ""
    result = check_practice_answer(task.question, raw)
    if result is None or not getattr(result, "checkable", False) or not result.items:
        # The whole message was not checkable. Before giving up, try the FINAL
        # answer extracted from the prose — a wrong answer wrapped in a sentence
        # is still a wrong answer, not an unverifiable one.
        candidate, status = extract_final_answer(raw)
        if status == AMBIGUOUS:
            # Several numbers, no declared answer. Guessing would misgrade, so
            # we ask instead of inventing a verdict.
            # AUDIT: no answer was identified, so the answer columns stay EMPTY
            # rather than recording the whole sentence as if the student had
            # submitted it. The prose is preserved in the evidence (and, as
            # always, verbatim in student_message).
            return GradingResult(
                task_id=task.task_id, verdict="unverified", solved=False,
                student_raw=raw, graded_answer="", normalized_student="",
                expected_display=task.expected_display,
                detail=AMBIGUOUS_FINAL_ANSWER, deterministic=True,
                evidence={
                    "method": "deterministic",
                    "checker_verdict": AMBIGUOUS_FINAL_ANSWER,
                    "expected_display": task.expected_display,
                    "student_raw": raw.strip()[:200],
                    "graded_text": "",
                    "extracted_candidate": "",
                    "gpt_check_used": False,
                    "match": False,
                })
        if candidate and candidate != raw.strip():
            retry = check_practice_answer(task.question, candidate)
            if retry is not None and getattr(retry, "checkable", False) \
                    and retry.items:
                result, graded_text = retry, candidate
    if result is None or not getattr(result, "checkable", False) or not result.items:
        # Deterministic checking failed for a task we believed was checkable.
        # We say so honestly rather than asking the model to guess.
        return GradingResult(task_id=task.task_id, verdict="unverified",
                             solved=False, student_raw=raw,
                             expected_display=task.expected_display,
                             detail=NOT_CHECKABLE, deterministic=False)

    item = result.items[0]
    detail = str(item.verdict or "")

    # Deterministic evidence, straight off the checker's own objects.
    expected_obj = getattr(item, "expected", None)
    given_obj = getattr(item, "given", None)
    answer_type = str(getattr(expected_obj, "answer_type", "") or "")
    normalized_expected = _normalized(getattr(expected_obj, "value", None))
    normalized_student = _normalized(getattr(given_obj, "value", None))
    # AUDIT: what was ACTUALLY graded. The generic rational checker parses a
    # single token straight out of prose ("ja mislim da je 9/10" → 9/10), so the
    # extraction retry never runs and ``graded_text`` would otherwise be the
    # whole sentence. ``given.raw`` is the token the checker itself used, which
    # is true for every rational skill and for mixed numbers ("1 2/15").
    parsed_token = str(getattr(given_obj, "raw", "") or "").strip()
    graded_text = candidate or parsed_token or graded_text

    evidence = {
        "method": "deterministic",
        "checker_verdict": detail,
        "expected_display": task.expected_display,
        "expected_normalized": normalized_expected,
        "student_raw": raw.strip()[:200],
        "graded_text": graded_text.strip()[:120],
        "extracted_candidate": candidate,
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
        task, graded_text, verdict=verdict, solved=solved, detail=detail)
    evidence["checker_verdict"] = detail
    evidence["match"] = solved
    return GradingResult(
        task_id=task.task_id, verdict=verdict, solved=solved, student_raw=raw,
        graded_answer=graded_text,
        expected_display=task.expected_display, detail=detail,
        deterministic=True, answer_type=answer_type,
        normalized_expected=normalized_expected,
        normalized_student=normalized_student, evidence=evidence,
    )
