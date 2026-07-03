"""matbot — Stage 2 application package (SKELETON / Phase 2A).

This package is the target home for the Flask backend that currently lives in the
top-level ``app.py``. It is intentionally a *skeleton* in Phase 2A:

  * nothing here is imported by the running app yet, and
  * ``app.py`` remains the sole entrypoint (``python app.py`` / gunicorn ``app:app``).

Creating this empty package changes **no behavior** and is not wired into anything,
so the existing 104-test suite is unaffected.

Migration roadmap and the (critical) test-coupling rule live in ``matbot/README.md``.

Phase status
------------
  2A (this commit)  package skeleton only; ``app.py`` untouched; no behavior change.
  2B (next)         ``config.py`` + ``create_app()`` factory. NOT done in 2A because a
                    real factory must own the ``limiter``/config setup that routes and
                    tests reference as globals on ``app`` — moving it requires updating
                    the ``monkeypatch`` targets in ``tests/conftest.py`` (see README).
  2C+               ``clients/`` · ``services/`` · ``integrations/`` · ``security/`` ·
                    ``routes/`` blueprints, each move paired with its test-seam update.

NOTE: the test alias ``import app as matbot`` is a private alias of the legacy
entrypoint module and is unrelated to this package's name.

Phase 1 (6. razred modular MVP) adds two *self-contained* modules that are NOT
wired into ``app.py`` and do not read any file at import time (loading is lazy):
``content_loader`` (Excel → normalized data) and ``topic_lookup`` (final_topic
resolution). Phase 2 adds ``prompt_builder`` (structured prompt from Phase 1 output
+ master topic content; **no OpenAI call**). Phase 3 adds ``ai_tutor_service``
(orchestration for ``POST /api/ai-tutor/chat``); the app injects its existing
``_openai_chat`` into the service, so this package still never imports ``app.py``.
Importing this package changes no runtime behavior on its own.
"""

from matbot.ai_tutor_service import handle_chat, list_topics
from matbot.content_loader import (
    ContentLoadError,
    get_master,
    get_thinkific_map,
    load_master_content,
    load_thinkific_map,
    validate_mapped_topics,
)
from matbot.prompt_builder import (
    build_fallback_prompt,
    build_mode_instructions,
    build_tutor_prompt,
    get_topic_context,
    get_video_flow_context,
    normalize_mode,
    trim_conversation_history,
)
from matbot.topic_lookup import (
    find_lesson,
    get_final_topic,
    topic_exists,
    validate_detected_topic,
    validate_topic,
)

__all__: list[str] = [
    # content_loader
    "ContentLoadError",
    "load_master_content",
    "load_thinkific_map",
    "validate_mapped_topics",
    "get_master",
    "get_thinkific_map",
    # topic_lookup
    "topic_exists",
    "validate_topic",
    "validate_detected_topic",
    "find_lesson",
    "get_final_topic",
    # prompt_builder
    "build_tutor_prompt",
    "build_fallback_prompt",
    "get_topic_context",
    "get_video_flow_context",
    "normalize_mode",
    "trim_conversation_history",
    "build_mode_instructions",
    # ai_tutor_service (Phase 3/4)
    "handle_chat",
    "list_topics",
]
