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
SOURCE_EXAM = "exam_item"
SOURCE_FOLLOWUP = "followup"
#: A candidate the LEGACY prose ladder produced. It is a migration input only —
#: it never carries activation authority, and it is subject to every gate.
SOURCE_LEGACY = "legacy_extracted"

_TRUSTED_SOURCES = frozenset({SOURCE_TEMPLATE, SOURCE_STUDENT, SOURCE_IMAGE,
                              SOURCE_EXAM})

#: Sources whose text is a STRUCTURED artifact rather than model prose. Only
#: these may activate without the prose gate below.
STRUCTURED_SOURCES = frozenset({SOURCE_TEMPLATE, SOURCE_STUDENT, SOURCE_IMAGE,
                                SOURCE_EXAM, SOURCE_MICRO, SOURCE_FOLLOWUP})

#: Content the system did not generate. Re-presenting it is normal (an OCR item
#: persists across turns), so recent-task rejection does not apply.
_EXTERNALLY_SUPPLIED = frozenset({SOURCE_STUDENT, SOURCE_IMAGE, SOURCE_EXAM})

#: Text that is lifecycle chatter, not a task. A candidate made only of this is
#: refused outright — praise, invitations and headings must never become tasks
#: just because a regex found a question mark.
_NON_TASK_RE = re.compile(
    r"^(?:"
    r"brav[oy]\b|odlicno\b|super\b|tacno\b|netacno\b|dobro\s+je\b|"
    r"zelis\s+li\b|hoces\s+li\b|jesi\s+li\s+spreman\b|da\s+li\s+zelis\b|"
    r"idemo\s+dalje\b|nastavljamo\b|razumijes\s+li\b|je\s+li\s+jasno\b|"
    r"sljedeci\s+zadatak\b|evo\s+objasnjenja\b|pokusaj\s+ponovo\b|"
    r"zadatak\s*[:.]?\s*$|vjezba\s*[:.]?\s*$|primjer\s*[:.]?\s*$"
    r")",
)

#: A real task carries something to compute or decide.
_TASK_CONTENT_RE = re.compile(r"\d|[=<>+\-/*^]|π|\bpi\b|\{|\}")


def looks_like_a_task(text: Any) -> bool:
    """Does this candidate contain an actual problem to solve?

    Guards the difference between "Bravo! Želiš li novi zadatak?" and
    "Izračunaj 3/4 + 1/4." Prose sources must clear this; structured sources
    already know what they are.
    """
    folded = _fold(text)
    if not folded:
        return False
    if _NON_TASK_RE.match(folded):
        return False
    return bool(_TASK_CONTENT_RE.search(folded))

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


@dataclass(frozen=True)
class TaskCandidate:
    """A PROPOSAL that some code path believes should be the active task.

    Producing a candidate carries no authority whatsoever — only ``activate``
    decides. The legacy ladder produces candidates; it no longer activates.
    """
    question: str = ""
    source: str = SOURCE_MODEL
    kind: str = "task"
    parent_task_id: str = ""
    #: True when this is the SAME task continuing (hint, retry, help). A
    #: continuation keeps its task_id and is exempt from duplicate rejection —
    #: it is deliberately identical.
    continuation: bool = False
    #: Set when the producer already validated the text (template, exam item).
    prevalidated: bool = False

    @property
    def empty(self) -> bool:
        return not (self.question or "").strip()


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
    continuation: bool = False          # same task carried forward

    def to_dict(self) -> dict:
        return {
            "activated": self.activated, "question": self.question,
            "task_id": self.task_id, "source": self.source, "kind": self.kind,
            "parent_task_id": self.parent_task_id, "reason": self.reason,
            "continuation": self.continuation,
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
    continuation: bool = False,
    validation_hint: Any = None,
) -> ActivationDecision:
    """The single activation gate.

    ``validator`` is the caller's existing validation callable (question → dict
    with ``validation_status``); it is NOT reimplemented here, so gradeability
    keeps exactly one owner.

    Order matters: identity is checked BEFORE gradeability, because an off-topic
    task that happens to be gradeable is the defect we are fixing.
    """
    # Collapse only HORIZONTAL whitespace: newlines separate the items of a
    # multi-item task, so flattening them silently destroys item splitting.
    q = re.sub(r"[ \t]+", " ", str(question or ""))
    q = re.sub(r"\n{3,}", "\n\n", q).strip()[:600]
    if not q:
        return _refuse("empty", topic)

    # A continuation is the SAME task carried through a hint/retry/help turn. It
    # was already activated once, so it repeats every gate it already passed —
    # including, deliberately, being identical to itself.
    if continuation:
        return ActivationDecision(
            activated=True, question=q, task_id=str(task_id or uuid.uuid4().hex[:12]),
            source=source, kind=kind, parent_task_id=str(parent_task_id or ""),
            topic=topic, continuation=True,
            validation=validation_hint if isinstance(validation_hint, dict) else {},
        )

    # Prose must actually look like a problem. Praise, invitations and headings
    # are lifecycle text and may never become tasks.
    if source not in STRUCTURED_SOURCES and not looks_like_a_task(q):
        return _refuse("not_a_task", topic)

    # Never re-serve a task the student just saw ("daj mi teži" repeated the
    # identical arc-length task with identical values in production).
    # Only GENERATED candidates are subject to this: an OCR item from the
    # student's own photo, their typed task, or an exam item is externally
    # supplied and may legitimately reappear across turns.
    if source not in _EXTERNALLY_SUPPLIED:
        folded = _fold(q)
        if any(_fold(r) == folded for r in (recent or ())):
            return _refuse("duplicate_recent", topic)

    if source not in _TRUSTED_SOURCES:
        ok, why = on_topic(q, topic)
        if not ok:
            return _refuse(why, topic)

    if validator is None:
        # The producer already ran the mode's real validator (template, exam
        # item, or a legacy block above). Re-running it would only re-derive the
        # same schema; an ABSENT validator is not evidence of failure.
        validation = validation_hint if isinstance(validation_hint, dict) else {}
    else:
        validation = validator(q)
        if isinstance(validation, dict) and \
                validation.get("validation_status") != "validated":
            return _refuse(f"invalid:{validation.get('reason') or 'ungradeable'}",
                           topic, validation)

    return ActivationDecision(
        activated=True, question=q,
        task_id=str(task_id or uuid.uuid4().hex[:12]),
        source=source, kind=kind, parent_task_id=str(parent_task_id or ""),
        topic=topic, validation=validation if isinstance(validation, dict) else {},
    )


def activate_candidate(
    candidate: TaskCandidate,
    *,
    validator,
    topic: TopicIdentity | None = None,
    mode: str = "practice",
    task_id: Any = None,
    recent: Any = (),
    validation_hint: Any = None,
) -> ActivationDecision:
    """The single entry point callers should use: candidate in, decision out."""
    if candidate.empty:
        return _refuse("empty", topic)
    return activate(
        question=candidate.question, source=candidate.source, kind=candidate.kind,
        parent_task_id=candidate.parent_task_id, topic=topic, mode=mode,
        validator=(None if candidate.prevalidated else validator),
        task_id=task_id, recent=recent, continuation=candidate.continuation,
        validation_hint=validation_hint,
    )
