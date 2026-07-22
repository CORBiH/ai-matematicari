# -*- coding: utf-8 -*-
"""Concepts 2 and 3: **SessionState** and **ActiveTask**.

``SessionState`` is the ONE server-owned record of a Practice session, and
``ActiveTask`` is the ONLY way a task can exist. There is no parallel
``last_tutor_task`` string, no ``task_items``, no mirrors — inside the core a
task either is an ``ActiveTask`` or does not exist.

Both are immutable: every transition returns a NEW object. That is what makes
"one component owns the lifecycle" checkable rather than aspirational — nothing
can mutate a task in passing.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from matbot.minimal.skills import Topic

MAX_QUESTION_CHARS = 400


@dataclass(frozen=True)
class ActiveTask:
    """A task that genuinely exists: it has an id, a question, an expected
    answer, and the skill/topic it came from."""
    task_id: str
    skill_id: str
    question: str
    expected_display: str
    npp_id: str = ""
    tema_title: str = ""
    attempts: int = 0               # graded answers against this task
    wrong_attempts: int = 0
    hints_given: int = 0
    solved: bool = False
    #: The worked solution was shown on request. The task is finished, but
    #: it was NOT solved independently, so it must not count as progress.
    solution_revealed: bool = False

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "skill_id": self.skill_id,
            "question": self.question, "expected_display": self.expected_display,
            "npp_id": self.npp_id, "tema_title": self.tema_title,
            "attempts": self.attempts, "wrong_attempts": self.wrong_attempts,
            "hints_given": self.hints_given, "solved": self.solved,
            "solution_revealed": self.solution_revealed,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ActiveTask | None":
        if not isinstance(raw, dict):
            return None
        question = str(raw.get("question") or "").strip()[:MAX_QUESTION_CHARS]
        task_id = str(raw.get("task_id") or "").strip()[:40]
        skill_id = str(raw.get("skill_id") or "").strip()[:40]
        # A task without identity is not a task. Reconstructing one would be
        # exactly the prose-recovery this engine exists to avoid.
        if not (question and task_id and skill_id):
            return None
        def _int(key: str) -> int:
            try:
                return max(0, min(int(raw.get(key) or 0), 999))
            except (TypeError, ValueError):
                return 0
        return cls(
            task_id=task_id, skill_id=skill_id, question=question,
            expected_display=str(raw.get("expected_display") or "")[:120],
            npp_id=str(raw.get("npp_id") or "")[:40],
            tema_title=str(raw.get("tema_title") or "")[:120],
            attempts=_int("attempts"), wrong_attempts=_int("wrong_attempts"),
            hints_given=_int("hints_given"), solved=bool(raw.get("solved")),
            solution_revealed=bool(raw.get("solution_revealed")),
        )


@dataclass(frozen=True)
class SessionState:
    """Everything the engine remembers between turns. Nothing else is state."""
    session_id: str = ""
    grade: Any = 6
    topic: Topic = field(default_factory=lambda: Topic(grade=6))
    active_task: ActiveTask | None = None
    turn_index: int = 0
    correct_streak: int = 0
    solved_count: int = 0
    recent_questions: tuple[str, ...] = ()      # avoid re-serving these
    #: Bounded 1..3. Only "teži"/"lakši" move it; a plain new task keeps it.
    difficulty_level: int = 1
    #: The runtime topic id as FIRST seen this session. The client later echoes
    #: back the canonical id, which would otherwise erase the original.
    origin_runtime_id: str = ""
    #: A yes/no question the tutor asked, awaiting the student's reply.
    #: Only ever set by the engine, never inferred from prose. "" = nothing
    #: pending. Currently the single value is "new_task".
    pending_confirmation: str = ""

    # -- transitions: each returns a NEW state --------------------------------
    def with_task(self, task: ActiveTask) -> "SessionState":
        recent = tuple([task.question] + list(self.recent_questions))[:8]
        return replace(self, active_task=task, recent_questions=recent)

    def with_updated_task(self, task: ActiveTask) -> "SessionState":
        return replace(self, active_task=task)

    def cleared_task(self) -> "SessionState":
        return replace(self, active_task=None)

    def completed_task(self) -> "SessionState":
        return replace(self, active_task=None,
                       solved_count=self.solved_count + 1,
                       correct_streak=self.correct_streak + 1)

    def broke_streak(self) -> "SessionState":
        return replace(self, correct_streak=0)

    def next_turn(self) -> "SessionState":
        return replace(self, turn_index=self.turn_index + 1)

    def with_topic(self, topic: Topic) -> "SessionState":
        return replace(self, topic=topic)

    def with_difficulty(self, level: int) -> "SessionState":
        return replace(self, difficulty_level=max(1, min(int(level), 3)))

    def with_origin_runtime_id(self, runtime_id: str) -> "SessionState":
        return replace(self, origin_runtime_id=str(runtime_id or "")[:80])

    def awaiting(self, what: str) -> "SessionState":
        return replace(self, pending_confirmation=str(what or "")[:40])

    def confirmation_consumed(self) -> "SessionState":
        return replace(self, pending_confirmation="")

    # -- serialization: the ONLY representation crossing the wire -------------
    def to_dict(self) -> dict:
        return {
            "engine": "minimal",
            "session_id": self.session_id,
            "grade": self.grade,
            "topic": self.topic.to_dict(),
            "active_task": self.active_task.to_dict() if self.active_task else None,
            "turn_index": self.turn_index,
            "correct_streak": self.correct_streak,
            "solved_count": self.solved_count,
            "recent_questions": list(self.recent_questions),
            "difficulty_level": self.difficulty_level,
            "origin_runtime_id": self.origin_runtime_id,
            "pending_confirmation": self.pending_confirmation,
        }

    @classmethod
    def from_dict(cls, raw: Any, *, session_id: str = "", grade: Any = 6) -> "SessionState":
        """Rebuild from a round-tripped dict. Anything unrecognised is dropped
        rather than guessed — a corrupt task becomes no task, never a wrong one."""
        raw = raw if isinstance(raw, dict) else {}
        if str(raw.get("engine") or "") != "minimal":
            raw = {}                    # foreign state (legacy V2) is not adopted
        def _int(key: str) -> int:
            try:
                return max(0, min(int(raw.get(key) or 0), 99999))
            except (TypeError, ValueError):
                return 0
        recent = raw.get("recent_questions")
        recent_t = tuple(str(q)[:MAX_QUESTION_CHARS] for q in recent[:8]) \
            if isinstance(recent, list) else ()
        return cls(
            session_id=str(raw.get("session_id") or session_id or "")[:80],
            grade=raw.get("grade", grade),
            topic=Topic.from_dict(raw.get("topic")),
            active_task=ActiveTask.from_dict(raw.get("active_task")),
            turn_index=_int("turn_index"),
            correct_streak=_int("correct_streak"),
            solved_count=_int("solved_count"),
            recent_questions=recent_t,
            difficulty_level=max(1, min(_int("difficulty_level") or 1, 3)),
            origin_runtime_id=str(raw.get("origin_runtime_id") or "")[:80],
            pending_confirmation=str(raw.get("pending_confirmation") or "")[:40],
        )


def new_task(*, skill_id: str, question: str, expected_display: str,
             topic: Topic) -> ActiveTask:
    """The ONE constructor for an active task."""
    return ActiveTask(
        task_id=f"mt_{uuid.uuid4().hex[:12]}",
        skill_id=skill_id,
        question=question.strip()[:MAX_QUESTION_CHARS],
        expected_display=str(expected_display or "")[:120],
        npp_id=topic.npp_id, tema_title=topic.title,
    )
