# -*- coding: utf-8 -*-
"""One gate every task must pass to become active state.

Production evidence for why this exists:

* An explanation said "Probaj ti: da li je broj 24 djeljiv sa 4?" and the student
  answered "ne". The question lived only in prose, so there was no task id, no
  expected schema and no lifecycle — the student's answer had nothing to attach
  to and hit the "send me a concrete task" guard.
* A tema "Odnos dvije kružnice" produced an arc-length task. The task was
  mathematically valid and gradeable, so numeric validation passed it, and
  nothing ever asked whether it belonged to the SELECTED tema.

Both are the same defect: activation had many entrances and only one of them
(numeric validity) was ever checked. This module makes activation a single
decision that checks BOTH gradeability and topic identity.

It owns no state. It returns a decision; the caller applies it.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from matbot.topic_resolver import TopicIdentity

#: Where an activation request came from. Prose-scraped candidates are the only
#: ones subject to the topic gate — a deterministic template already knows its
#: own tema, and a student's own task defines its own topic by definition.
SOURCE_TEMPLATE = "template"
SOURCE_MICRO = "micro_task"
SOURCE_STUDENT = "student_task"
SOURCE_MODEL = "gpt_generated"
SOURCE_IMAGE = "image_task"

_TRUSTED_SOURCES = frozenset({SOURCE_TEMPLATE, SOURCE_STUDENT, SOURCE_IMAGE})

#: Concept vocabulary per tema keyword. A model-generated task under an EXACT
#: selected tema must speak that tema's language. Deliberately small and
#: additive: an unlisted tema imposes no vocabulary constraint, so this can never
#: silently reject a topic we simply have no opinion about.
_TEMA_VOCAB: dict[str, tuple[str, ...]] = {
    "odnos dvije kruznice": ("odnos", "dvije kruznice", "dodiruju", "sijeku",
                             "koncentri", "rastojanje sredista", "presjek"),
    "kruzni luk": ("luk", "kruzni luk", "duzina luka"),
    "centralni ugao": ("centralni ugao",),
    "prosirivanje razlomaka": ("prosiri", "prosirivanje"),
    "skracivanje razlomaka": ("skrati", "skracivanje"),
}

#: Concepts that positively identify a DIFFERENT tema. When a candidate carries
#: one of these and the selected tema is not the owner, the task is off-topic.
_CONCEPT_OWNERS: dict[str, tuple[str, ...]] = {
    "kruzni luk": ("luk",),
    "centralni ugao": ("centralni ugao",),
    "povrsina kruga": ("povrsina kruga",),
    "obim kruga": ("obim kruga",),
}


def _fold(text: Any) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


@dataclass
class ActivationDecision:
    activated: bool
    question: str = ""
    task_id: str = ""
    source: str = SOURCE_MODEL
    kind: str = "task"                  # "task" | "micro"
    parent_task_id: str = ""
    reason: str = ""                    # why it was refused
    topic: TopicIdentity | None = None
    validation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "activated": self.activated, "question": self.question,
            "task_id": self.task_id, "source": self.source, "kind": self.kind,
            "parent_task_id": self.parent_task_id, "reason": self.reason,
            "topic": self.topic.to_dict() if self.topic else None,
        }


def _refuse(reason: str, topic: TopicIdentity | None = None,
            validation: dict | None = None) -> ActivationDecision:
    return ActivationDecision(activated=False, reason=reason, topic=topic,
                              validation=validation or {})


def on_topic(question: Any, topic: TopicIdentity | None) -> tuple[bool, str]:
    """Does this candidate belong to the SELECTED exact tema?

    Returns ``(ok, reason)``. Only an exact, resolved tema is enforced — a bare
    oblast selection imposes no constraint, so this never narrows a request the
    student did not actually narrow themselves.
    """
    if topic is None or not topic.is_exact_tema or not topic.tema:
        return True, ""
    q = _fold(question)
    if not q:
        return True, ""
    tema_key = _fold(topic.tema)

    # 1. The tema has a known vocabulary → the task must use at least one term.
    vocab = _TEMA_VOCAB.get(tema_key)
    if vocab and not any(v in q for v in vocab):
        # 2. …and if it positively belongs to a DIFFERENT tema, say so precisely.
        for owner, markers in _CONCEPT_OWNERS.items():
            if owner != tema_key and any(m in q for m in markers):
                return False, f"off_topic:{owner}"
        return False, "off_topic:vocabulary"

    # 3. Even without a vocabulary entry, a task owned by another tema is wrong.
    for owner, markers in _CONCEPT_OWNERS.items():
        if owner != tema_key and any(m in q for m in markers):
            if not vocab or not any(v in q for v in vocab):
                return False, f"off_topic:{owner}"
    return True, ""


def activate(
    *,
    question: Any,
    source: str,
    validator,
    topic: TopicIdentity | None = None,
    mode: str = "practice",
    kind: str = "task",
    parent_task_id: Any = "",
    task_id: Any = None,
    recent: Any = (),
) -> ActivationDecision:
    """The single activation gate.

    ``validator`` is the caller's existing validation callable (question → dict
    with ``validation_status``); it is NOT reimplemented here, so gradeability
    keeps exactly one owner.

    Order matters: identity is checked BEFORE gradeability, because an off-topic
    task that happens to be gradeable is the defect we are fixing.
    """
    q = re.sub(r"\s+", " ", str(question or "")).strip()[:600]
    if not q:
        return _refuse("empty", topic)

    # Never re-serve a task the student just saw ("daj mi teži" repeated the
    # identical arc-length task with identical values in production).
    folded = _fold(q)
    if any(_fold(r) == folded for r in (recent or ())):
        return _refuse("duplicate_recent", topic)

    if source not in _TRUSTED_SOURCES:
        ok, why = on_topic(q, topic)
        if not ok:
            return _refuse(why, topic)

    validation = validator(q) if validator else {}
    if isinstance(validation, dict) and validation.get("validation_status") != "validated":
        return _refuse(f"invalid:{validation.get('reason') or 'ungradeable'}",
                       topic, validation)

    return ActivationDecision(
        activated=True, question=q,
        task_id=str(task_id or uuid.uuid4().hex[:12]),
        source=source, kind=kind, parent_task_id=str(parent_task_id or ""),
        topic=topic, validation=validation if isinstance(validation, dict) else {},
    )
