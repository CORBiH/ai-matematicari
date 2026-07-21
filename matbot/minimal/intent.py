# -*- coding: utf-8 -*-
"""Concept 1 of 5: **TurnIntent** — what the student's turn means.

Deliberately tiny. The V2 classifier grew thirteen intents because it served
every mode; this engine supports Practice only, so a turn is one of four things.
Anything it cannot place is ``OTHER`` and is answered honestly rather than
guessed at.

Pure text → label. Reads no state, mutates nothing, and cannot be a second
source of truth about tasks, verdicts or progression.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any


class TurnIntent(str, Enum):
    ANSWER = "answer"
    HELP = "help"
    NEW_TASK = "new_task"
    OTHER = "other"


def fold(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


_HELP_RE = re.compile(
    r"\b(ne\s*znam|nemam\s+pojma|pomo[cčć]\w*|pomozi|hint|savjet\w*|help|"
    r"ne\s+razumijem|ne\s+kapiram|ne\s+kontam|zapeo\s+sam|zapela\s+sam|"
    r"objasni\w*|obrazloz\w*|kako\s+se\s+radi|kako\s+da|odakle\s+da\s+krenem|"
    r"ne\s+umijem|ne\s+mogu|zasto)\b")

#: Any mention of wanting a task. Deliberately broad on the NOUN rather than
#: enumerating verb+adjective combinations: production sent "Daj mi teži zadatak
#: iz iste teme", which no adjacency pattern matched, so it was read as a
#: non-answer and the student got the same task back.
#: Difficulty words are matched but NOT honoured — harder/easier is out of
#: scope, so such a request yields a normal new task rather than a wrong one.
_NEW_TASK_RE = re.compile(
    r"\b(zadat\w*|vjezb\w*|primjer\w*|jos\s+jedan|idemo\s+dalje|dalje)\b")

#: Something to compute or decide. A message with no mathematical content is
#: not an answer, however confident it sounds.
_MATH_RE = re.compile(r"\d|[=<>+\-/*^]|π|\bpi\b|\{|\}")
#: A bare yes/no is an answer only when it stands alone — embedded, "da" is an
#: ordinary Bosnian conjunction ("šta DA probam").
_BARE_BOOL_RE = re.compile(r"^(da|ne|jeste|nije|jest|tacno|netacno)\s*[.!]?$")


@dataclass(frozen=True)
class Classification:
    intent: TurnIntent
    matched: str = ""


def classify(raw_message: Any) -> Classification:
    """Classify one turn. ``raw_message`` is never modified."""
    text = fold(raw_message)
    if not text:
        return Classification(TurnIntent.OTHER, "empty")
    # HELP first: "ne znam" must never be read as the answer "ne".
    if _HELP_RE.search(text):
        return Classification(TurnIntent.HELP, "help")
    # A task REQUEST carries no mathematical content; "zadatak 5/20" is an
    # answer that happens to name the task, so math wins.
    if _NEW_TASK_RE.search(text) and not _MATH_RE.search(text):
        return Classification(TurnIntent.NEW_TASK, "new_task")
    if _MATH_RE.search(text) or _BARE_BOOL_RE.match(text):
        return Classification(TurnIntent.ANSWER, "answer")
    return Classification(TurnIntent.OTHER, "no_signal")
