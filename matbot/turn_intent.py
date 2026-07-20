# -*- coding: utf-8 -*-
"""One typed classification of what the student's turn MEANS.

Before this module, "is this a help request?" was answered by four different
regexes in three modules (``ai_tutor_service`` twice, ``exam_engine``,
``answer_checker``), which is why the exam and the practice flow could disagree
about the same sentence. Intent is now decided ONCE, here, and consumed by every
caller.

Two hard rules:

* An intent NEVER changes the session mode. It describes the turn; the caller
  decides what that means for its own lifecycle. (Mode promotion stays in
  ``ai_tutor_service`` where the mode contract lives.)
* This module is pure text → label. It reads no state and mutates nothing, so it
  can never become a second source of truth about tasks, verdicts or progression.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Intent(str, Enum):
    ANSWER = "answer"
    HELP = "help"                       # "ne znam", "pomozi" — wants support
    HINT = "hint"                       # explicitly wants a nudge, not the answer
    EXPLANATION = "explanation"         # wants the concept / reasoning
    NEW_TASK = "new_task"
    HARDER = "harder"
    EASIER = "easier"
    SKIP = "skip"
    SUBMIT = "submit"
    FOLLOW_UP = "follow_up"             # "zašto?", "kako to?"
    CONFIRMATION = "confirmation"       # "da", "može", "hajde" with no math content
    OFF_TOPIC = "off_topic"
    IMAGE = "image"
    UNKNOWN = "unknown"


#: Intents that must never be recorded as an answer to the active task.
NON_ANSWER = frozenset({
    Intent.HELP, Intent.HINT, Intent.EXPLANATION, Intent.NEW_TASK, Intent.HARDER,
    Intent.EASIER, Intent.SKIP, Intent.SUBMIT, Intent.FOLLOW_UP, Intent.OFF_TOPIC,
    Intent.UNKNOWN,
})

#: Intents that keep an active task alive rather than consuming it.
PRESERVES_TASK = frozenset({
    Intent.HELP, Intent.HINT, Intent.EXPLANATION, Intent.FOLLOW_UP,
    Intent.CONFIRMATION,
})


def fold(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


# --------------------------------------------------------------------------- #
# Patterns — the ONLY place each of these questions is asked.                   #
# --------------------------------------------------------------------------- #
_SUBMIT_RE = re.compile(
    r"\b(predaj\w*|gotov\w*\s*sam|zavr[sš]i\w*\s*(kontroln\w*|test\w*)?|"
    r"to\s+je\s+to|nemam\s+vi[sš]e|zavr[sš]avam)\b")
_SKIP_RE = re.compile(
    r"\b(preskoc\w*|preskac\w*|dalje\s+molim|sljedec\w*\s+zadatak|"
    r"idemo\s+dalje|next)\b")
_HELP_RE = re.compile(
    r"\b(ne\s*znam|nemam\s+pojma|pomo[cčć]\w*|pomozi|ne\s+razumijem|ne\s+kapiram|"
    r"ne\s+kontam|daj\s+mi\s+savjet|zapeo\s+sam|zapela\s+sam|rijesi\s+ti|"
    r"uradi\s+ti|daj\s+odgovor|reci\s+mi\s+odgovor|ne\s+umijem|ne\s+mogu|"
    r"tesko\s+mi\s+je)\b")
_HINT_RE = re.compile(r"\b(hint|nagovjest\w*|mala\s+pomo[cčć]|navedi\s+me|daj\s+mi\s+trag)\b")
_EXPLANATION_RE = re.compile(
    r"\bobrazloz\w*|\bobjasn\w*|\bprovjeri\b|\bdokazi\b|\bkorak\s+po\s+korak\b"
    r"|\bkako\s+znas\b|\bpokazi\s+postup\w*|\bpravil\w*\s+dj?eljiv\w*"
    r"|\bkako\s+se\s+radi\b|\bkoje\s+je\s+pravilo\b|\bdaj\s+pravilo\b")
_FOLLOW_UP_RE = re.compile(
    r"^\s*(zasto|kako\s+to|a\s+zasto|zbog\s+cega|otkud|kako\s+tako)\b|^\s*zasto\s*\?*\s*$")
_HARDER_RE = re.compile(r"\btez\w*|\bteski\w*|\bkomplikovanij\w*|\bizazovnij\w*")
_EASIER_RE = re.compile(r"\blaks\w*|\bjednostavnij\w*|\blagan\w*")
_NEW_TASK_RE = re.compile(r"\b(zadatak|zadatke|vjezb\w*|jos\s+jedan|novi|drugi)\b")
_CONFIRM_RE = re.compile(r"^\s*(da|moze|hajde|ok|okej|vazi|jeste|naravno|hocu|idemo)\s*[.!]?\s*$")
#: Unambiguous mathematical content — safe to detect anywhere in the message.
_MATH_SIGNAL_RE = re.compile(r"\d|[=<>+\-/*^]|\bpi\b|π|\{|\}")
#: Yes/no words only count as an ANSWER when they essentially ARE the message.
#: Embedded, they are ordinary Bosnian conjunctions — "šta DA probam" is a
#: question, not the affirmative "da".
_BARE_BOOLEAN_RE = re.compile(
    r"^(da|ne|jeste|nije|jest|tacno|netacno)([\s.,!]+(je|nije))?\s*[.!]?$")


def _answer_signal(text: str) -> bool:
    return bool(_MATH_SIGNAL_RE.search(text) or _BARE_BOOLEAN_RE.match(text))


@dataclass(frozen=True)
class TurnIntent:
    intent: Intent
    #: True when the text could ALSO stand as an answer (e.g. "ne" answering a
    #: yes/no question). Callers with an active yes/no task use this to attribute
    #: the turn instead of treating it as a bare confirmation.
    answer_capable: bool = False
    matched: str = ""

    @property
    def is_answer(self) -> bool:
        return self.intent == Intent.ANSWER

    @property
    def is_non_answer(self) -> bool:
        return self.intent in NON_ANSWER

    @property
    def preserves_task(self) -> bool:
        return self.intent in PRESERVES_TASK


def _hit(name: str, intent: Intent, answer_capable: bool = False) -> TurnIntent:
    return TurnIntent(intent=intent, answer_capable=answer_capable, matched=name)


def classify(message: Any, *, has_image: bool = False,
             expects_boolean: bool = False) -> TurnIntent:
    """Classify one student turn.

    ``expects_boolean`` tells the classifier that an active task asks a yes/no
    question, so a bare "ne" is an ANSWER rather than a stray negation. This is
    the ONLY state the classifier accepts, and it only ever sharpens attribution.
    """
    if has_image:
        return _hit("image", Intent.IMAGE)
    text = fold(message)
    if not text:
        return _hit("empty", Intent.UNKNOWN)

    # A bare yes/no while a yes/no task is open is an ANSWER first. Checked
    # before HELP so "ne" cannot be read as "ne znam".
    if expects_boolean and re.fullmatch(r"(da|ne|jeste|nije|jest|tacno|netacno)\s*[.!]?", text):
        return _hit("boolean_answer", Intent.ANSWER, answer_capable=True)

    # Control intents first — they are unambiguous and must never be graded.
    if _SUBMIT_RE.search(text):
        return _hit("submit", Intent.SUBMIT)
    if _SKIP_RE.search(text):
        return _hit("skip", Intent.SKIP)
    if _HINT_RE.search(text):
        return _hit("hint", Intent.HINT)
    if _HELP_RE.search(text):
        return _hit("help", Intent.HELP)
    if _FOLLOW_UP_RE.search(text):
        return _hit("follow_up", Intent.FOLLOW_UP)

    # Difficulty is a NEW-TASK request with a direction; check before the plain
    # new-task words so "daj mi teži zadatak" is not merely NEW_TASK.
    wants_new = bool(_NEW_TASK_RE.search(text))
    if _HARDER_RE.search(text):
        return _hit("harder", Intent.HARDER)
    if _EASIER_RE.search(text):
        return _hit("easier", Intent.EASIER)

    if _EXPLANATION_RE.search(text):
        return _hit("explanation", Intent.EXPLANATION)
    if wants_new and not _answer_signal(text):
        return _hit("new_task", Intent.NEW_TASK)
    if _CONFIRM_RE.match(text):
        return _hit("confirmation", Intent.CONFIRMATION, answer_capable=True)
    if _answer_signal(text):
        return _hit("answer_signal", Intent.ANSWER, answer_capable=True)
    return _hit("no_signal", Intent.UNKNOWN)


def is_help(message: Any) -> bool:
    """Back-compat helper for callers that only need the boolean."""
    return classify(message).intent in (Intent.HELP, Intent.HINT)
