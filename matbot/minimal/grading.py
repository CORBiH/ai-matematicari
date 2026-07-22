# -*- coding: utf-8 -*-
"""Concept 4: **GradingResult** — and the single component that produces it.

One turn produces at most one ``GradingResult``. It is created here and nowhere
else, from the student's raw text and the ``ActiveTask``'s question.

The deterministic checker is ALWAYS tried first and, when it produces a
confident checkable result, ITS verdict is what ships — the model is never
even called. Only when that path cannot safely understand the message (see
``semantic_grading.py``) may a bounded, schema-validated SemanticAnswerJudge
interpret it into CLAIMS, which a deterministic verifier then checks against
verified facts. The model never returns a verdict, a streak, or a task
transition — only ``engine.py`` (via this module's return value) ever changes
state.

``GradingResult`` is FROZEN once returned. The renderer receives it and may
choose words for it; it cannot change it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
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


def grade(task: ActiveTask, raw_message: Any, *, openai_chat: Any = None,
         model: str = "", timeout: float | None = None) -> GradingResult:
    """Grade one answer against one task. The ONLY grading entry point.

    ``raw_message`` is stored verbatim — never normalized, trimmed into meaning,
    or reconstructed from anything the tutor said.

    ``openai_chat``/``model``/``timeout`` are used ONLY as the last resort,
    when the deterministic checker cannot produce a checkable result at all —
    see ``_semantic_fallback`` and ``MATBOT_SEMANTIC_GRADING``.
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
        # Before giving up, the SemanticAnswerJudge may INTERPRET the message
        # (never grade it) — see ``_semantic_fallback``. Off by default; a
        # model failure or low confidence still lands here unchanged.
        base = GradingResult(task_id=task.task_id, verdict="unverified",
                             solved=False, student_raw=raw,
                             expected_display=task.expected_display,
                             detail=NOT_CHECKABLE, deterministic=False)
        return _semantic_fallback(task, raw, base, openai_chat=openai_chat,
                                  model=model, timeout=timeout)

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
    final = GradingResult(
        task_id=task.task_id, verdict=verdict, solved=solved, student_raw=raw,
        graded_answer=graded_text,
        expected_display=task.expected_display, detail=detail,
        deterministic=True, answer_type=answer_type,
        normalized_expected=normalized_expected,
        normalized_student=normalized_student, evidence=evidence,
    )
    # The deterministic checker's own "incomplete"/"partially_correct" for a
    # boolean_with_explanation task is a REGEX GUESS about evidence
    # sufficiency ("mentioned = the divisor's literal digit appears") — the
    # exact class of judgment free-form child language breaks ("da jer je
    # zadnja cifra 0" never says "10"). Give the SemanticAnswerJudge a chance
    # to do better; the deterministic verdict remains authoritative unless
    # "on" mode AND the judge safely disagrees (see ``_semantic_fallback``).
    # A BARE decision ("da", "ne") with no explanatory text at all has
    # nothing for the judge to interpret — the deterministic "incomplete" is
    # already the right answer, so it is never worth a model call.
    from matbot.minimal import semantic_grading as _sg
    if (answer_type == "boolean_with_explanation" and _sg.is_prose_like(raw)
            and detail in ("incomplete", "partially_correct")):
        return _semantic_fallback(task, raw, final, openai_chat=openai_chat,
                                  model=model, timeout=timeout)
    # SHADOW AUDIT (never overrides): a confident "incorrect" is exactly where
    # a deterministic parser can silently misextract a token from prose (a
    # number that happens to appear in an unrelated sentence) and never get a
    # second look, because NOT_CHECKABLE/incomplete were the only paths that
    # ever reached the judge. This runs the judge PURELY to log whether it
    # would have disagreed — it can never flip a confident deterministic
    # verdict, in shadow OR "on" mode; that stays a deliberate safety
    # boundary until the audit data itself justifies loosening it.
    if verdict == "incorrect" and _sg.is_prose_like(raw):
        return _shadow_audit_incorrect(task, raw, final, openai_chat=openai_chat,
                                       model=model, timeout=timeout)
    # HEDGE CHECK: an EXACT rational/equation task's deterministic extractor
    # finds the VALUE anywhere in the prose ("oko 0.5") and grades it
    # confidently "correct" without ever noticing the surrounding hedge word —
    # detecting "otprilike"/"možda"/"oko" is a LANGUAGE judgment the model
    # makes (never a new Python regex list here); the deterministic policy
    # in ``_verify_rational_like`` decides what that costs.
    if (verdict == "correct" and answer_type in ("rational", "equation_solution")
            and _sg.is_prose_like(raw)):
        return _semantic_hedge_check(task, raw, final, openai_chat=openai_chat,
                                     model=model, timeout=timeout)
    return final


#: Divisibility-specific detail values meaning "the yes/no DECISION was
#: correct, only the evidence is incomplete or factually off for this
#: number" — never a wrong attempt. ``incorrect_evidence`` is the semantic
#: verifier's counterpart to the deterministic checker's own
#: ``partially_correct``/``incomplete``.
DECISION_CORRECT_INCOMPLETE_DETAILS = (
    "incomplete", "partially_correct", "incorrect_evidence")


def _semantic_evidence(result: GradingResult, telemetry: dict) -> GradingResult:
    """Attach semantic telemetry to an EXISTING result without changing its
    verdict — used for shadow mode and every failure-to-interpret case."""
    evidence = dict(result.evidence)
    evidence.update(telemetry)
    return replace(result, evidence=evidence)


def _base_semantic_telemetry() -> dict:
    return {
        "semantic_judge_used": False, "semantic_judge_model": "",
        "semantic_judge_confidence": None, "semantic_response_kind": "",
        "semantic_decision": "", "semantic_claims": [],
        "semantic_fallback_reason": "", "deterministic_claim_verification": "",
        "semantic_certainty": "", "semantic_precision": "",
        "semantic_latency_ms": None, "semantic_prompt_tokens": None,
        "semantic_completion_tokens": None,
    }


def _shadow_audit_dict(sg: Any, base_verdict: str, candidate: str,
                       judgment: Any, outcome: Any) -> dict:
    """The comparison object requirement 1 asks for — built the SAME way
    regardless of which trigger reached the judge, so a shadow evaluator sees
    one consistent shape whether the deterministic path landed on
    NOT_CHECKABLE, incomplete, or a confident "incorrect"."""
    return {
        "deterministic_verdict": base_verdict,
        "deterministic_candidate": candidate,
        "semantic_response_kind": judgment.response_kind if judgment else "",
        "semantic_decision": judgment.decision if judgment else "",
        "semantic_verified_outcome": outcome.detail if outcome else "",
        "shadow_disagreement_type": sg.classify_shadow_disagreement(
            base_verdict, judgment, outcome),
    }


def _semantic_fallback(task: ActiveTask, raw: str, base_result: GradingResult,
                       *, openai_chat: Any, model: str,
                       timeout: float | None) -> GradingResult:
    """The ONLY bridge between deterministic grading and the
    SemanticAnswerJudge. Returns ``base_result`` (the deterministic
    NOT_CHECKABLE outcome), untouched except for telemetry, unless
    ``MATBOT_SEMANTIC_GRADING=on`` AND the judge produced a confidently
    verified, checkable outcome.
    """
    from matbot.minimal import semantic_grading as sg

    mode = sg.semantic_mode()
    telemetry = _base_semantic_telemetry()
    if mode == "off":
        return base_result                     # NO model call at all

    context = sg.build_context(task, raw)
    if context is None:
        telemetry["semantic_fallback_reason"] = "unsupported_skill"
        telemetry["shadow_audit"] = _shadow_audit_dict(
            sg, base_result.verdict, base_result.graded_answer, None, None)
        return _semantic_evidence(base_result, telemetry)

    telemetry["semantic_judge_model"] = model or ""
    judgment, reason, metrics = sg.judge(context, openai_chat=openai_chat,
                                        model=model, timeout=timeout)
    telemetry.update(metrics.to_telemetry())
    telemetry["semantic_judge_used"] = True     # the call WAS attempted
    if judgment is None:
        telemetry["semantic_fallback_reason"] = reason
        telemetry["shadow_audit"] = _shadow_audit_dict(
            sg, base_result.verdict, base_result.graded_answer, None, None)
        return _semantic_evidence(base_result, telemetry)

    telemetry.update(
        semantic_judge_confidence=judgment.confidence,
        semantic_response_kind=judgment.response_kind,
        semantic_decision=judgment.decision,
        semantic_certainty=judgment.certainty,
        semantic_precision=judgment.precision,
        semantic_claims=[{"type": c.type, "value": c.value,
                          "polarity": c.polarity} for c in judgment.claims])
    outcome = sg.verify_claims(context, judgment)
    telemetry["deterministic_claim_verification"] = outcome.detail
    telemetry["shadow_audit"] = _shadow_audit_dict(
        sg, base_result.verdict, base_result.graded_answer, judgment, outcome)

    if mode == "shadow" or not outcome.checkable:
        # shadow: log only, verdict unchanged. "on" but still not checkable:
        # the judge could not safely understand it either — same honest
        # NOT_CHECKABLE result, just with the attempt visible in telemetry.
        return _semantic_evidence(base_result, telemetry)

    # mode == "on" AND the judge + deterministic verifier agreed on a
    # checkable outcome — THIS function never invents the verdict itself,
    # it only carries what ``verify_claims`` (pure arithmetic) decided.
    evidence = dict(base_result.evidence)
    evidence.update(telemetry)
    evidence["method"] = "semantic"
    evidence["checker_verdict"] = outcome.detail
    evidence["graded_text"] = outcome.graded_answer
    evidence["match"] = outcome.verdict == "correct"
    return GradingResult(
        task_id=task.task_id, verdict=outcome.verdict,
        solved=(outcome.verdict == "correct"), student_raw=raw,
        graded_answer=outcome.graded_answer,
        expected_display=task.expected_display, detail=outcome.detail,
        deterministic=False, answer_type=context.answer_type,
        normalized_expected=str(context.expected_answer),
        normalized_student=outcome.graded_answer, evidence=evidence,
    )


def _shadow_audit_incorrect(task: ActiveTask, raw: str, base_result: GradingResult,
                            *, openai_chat: Any, model: str,
                            timeout: float | None) -> GradingResult:
    """Audit a CONFIDENT deterministic "incorrect" against the semantic judge.

    Requirement 1: a regex/token-based parser can silently misextract a
    number out of prose ("sto me pitas samo za 6" grabbing the bare "6") and
    land on a confident wrong verdict that NEVER reaches the judge under the
    ordinary NOT_CHECKABLE/incomplete fallback — shadow evaluation would have
    no way to discover that false negative. This runs the SAME judge purely
    to LOG a comparison; it never changes ``base_result``'s verdict, detail,
    counters, or graded_answer, in shadow OR "on" mode. Overriding a confident
    deterministic verdict is a deliberate line this round does not cross.
    """
    from matbot.minimal import semantic_grading as sg

    mode = sg.semantic_mode()
    telemetry = _base_semantic_telemetry()
    if mode not in ("shadow", "on"):
        return base_result

    context = sg.build_context(task, raw)
    if context is None:
        telemetry["semantic_fallback_reason"] = "unsupported_skill"
        telemetry["shadow_audit"] = _shadow_audit_dict(
            sg, base_result.verdict, base_result.graded_answer, None, None)
        return _semantic_evidence(base_result, telemetry)

    telemetry["semantic_judge_model"] = model or ""
    judgment, reason, metrics = sg.judge(context, openai_chat=openai_chat,
                                        model=model, timeout=timeout)
    telemetry.update(metrics.to_telemetry())
    telemetry["semantic_judge_used"] = True     # the call WAS attempted

    outcome = None
    if judgment is None:
        telemetry["semantic_fallback_reason"] = reason
    else:
        telemetry.update(
            semantic_judge_confidence=judgment.confidence,
            semantic_response_kind=judgment.response_kind,
            semantic_decision=judgment.decision,
            semantic_certainty=judgment.certainty,
            semantic_precision=judgment.precision,
            semantic_claims=[{"type": c.type, "value": c.value,
                              "polarity": c.polarity} for c in judgment.claims])
        outcome = sg.verify_claims(context, judgment)
        telemetry["deterministic_claim_verification"] = outcome.detail

    telemetry["shadow_audit"] = _shadow_audit_dict(
        sg, base_result.verdict, base_result.graded_answer, judgment, outcome)
    # AUDIT ONLY: base_result is returned exactly as graded — verdict, detail,
    # counters and response are never touched by this function, regardless of
    # mode or of what the judge concluded.
    return _semantic_evidence(base_result, telemetry)


def _semantic_hedge_check(task: ActiveTask, raw: str, base_result: GradingResult,
                          *, openai_chat: Any, model: str,
                          timeout: float | None) -> GradingResult:
    """A rational/equation answer the deterministic extractor already graded
    "correct" — but the message is prose, so a hedge word ("otprilike",
    "oko") could be sitting right next to the value without the extractor
    ever noticing. Recognising the hedge is the model's job; whether it costs
    full credit is ``_verify_rational_like``'s policy, reused unchanged here.
    Shadow mode only ever logs; "on" mode may downgrade to
    ``NEEDS_CONFIRMATION``, never invent a NEW wrong verdict.
    """
    from matbot.minimal import semantic_grading as sg

    mode = sg.semantic_mode()
    telemetry = _base_semantic_telemetry()
    if mode == "off":
        return base_result

    context = sg.build_context(task, raw)
    if context is None:
        return base_result                      # unsupported family, no call

    telemetry["semantic_judge_model"] = model or ""
    judgment, reason, metrics = sg.judge(context, openai_chat=openai_chat,
                                        model=model, timeout=timeout)
    telemetry.update(metrics.to_telemetry())
    telemetry["semantic_judge_used"] = True     # the call WAS attempted
    if judgment is None:
        telemetry["semantic_fallback_reason"] = reason
        telemetry["shadow_audit"] = _shadow_audit_dict(
            sg, base_result.verdict, base_result.graded_answer, None, None)
        return _semantic_evidence(base_result, telemetry)

    telemetry.update(
        semantic_judge_confidence=judgment.confidence,
        semantic_response_kind=judgment.response_kind,
        semantic_decision=judgment.decision,
        semantic_certainty=judgment.certainty,
        semantic_precision=judgment.precision,
        semantic_claims=[{"type": c.type, "value": c.value,
                          "polarity": c.polarity} for c in judgment.claims])
    outcome = sg.verify_claims(context, judgment)
    telemetry["deterministic_claim_verification"] = outcome.detail
    telemetry["shadow_audit"] = _shadow_audit_dict(
        sg, base_result.verdict, base_result.graded_answer, judgment, outcome)

    if mode == "shadow":
        return _semantic_evidence(base_result, telemetry)

    # mode == "on": only ever DOWNGRADE a confident "correct" to
    # needs_confirmation when the judge safely established hedging on the
    # SAME value — a failed/uncheckable judge call leaves the deterministic
    # "correct" exactly as it was.
    if outcome.checkable and outcome.detail == sg.NEEDS_CONFIRMATION:
        evidence = dict(base_result.evidence)
        evidence.update(telemetry)
        evidence["checker_verdict"] = outcome.detail
        return replace(base_result, verdict="partial", solved=False,
                      detail=outcome.detail, evidence=evidence)
    return _semantic_evidence(base_result, telemetry)
