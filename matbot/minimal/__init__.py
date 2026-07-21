# -*- coding: utf-8 -*-
"""A minimal tutoring engine, built alongside Engine V2 rather than on top of it.

Five concepts, one direction of flow:

    raw input → TurnIntent → SessionState → ActiveTask → GradingResult
              → updated state → ResponseRenderer

Scope is deliberately small: Practice mode, grade 6, five deterministically
checkable skills. Anything outside that is refused honestly and may be handed to
the legacy engine through the explicit boundary in ``adapter``.

Enabled by ``MATBOT_MINIMAL_ENGINE=on``. With the flag off (the default) nothing
in this package runs and production behavior is unchanged.
"""
from matbot.minimal.adapter import (
    FLAG,
    handle_chat_minimal,
    minimal_engine_enabled,
)
from matbot.minimal.engine import TurnResult, handle_turn
from matbot.minimal.grading import GradingResult, grade
from matbot.minimal.intent import TurnIntent, classify
from matbot.minimal.renderer import RenderContext, render
from matbot.minimal.skills import SKILLS, Topic, resolve_topic
from matbot.minimal.state import ActiveTask, SessionState

__all__ = [
    "FLAG", "minimal_engine_enabled", "handle_chat_minimal",
    "TurnIntent", "classify",
    "SessionState", "ActiveTask",
    "GradingResult", "grade",
    "RenderContext", "render",
    "Topic", "resolve_topic", "SKILLS",
    "handle_turn", "TurnResult",
]
