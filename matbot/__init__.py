"""matbot — application package.

**This module is deliberately free of imports, and must stay that way.**

It used to eagerly re-export symbols from ``activity_log``, ``ai_tutor_service``,
``content_loader``, ``prompt_builder`` and ``topic_lookup``. That made importing
*any* submodule execute the entire legacy tutoring stack: ``import
matbot.ai_tutor_v3.schemas`` would pull in ``ai_tutor_service`` (7.5k lines) and,
transitively, ``answer_checker``, ``grading_guard``, ``exam_engine``,
``engine_v2`` and ``matbot.minimal``.

That is fatal for the V3 isolation boundary — an "isolated" package cannot be
isolated while its own parent package imports everything it is isolated from.
So the re-exports are gone. Nothing in the repository used them: every caller
already imports the submodule it needs (``from matbot import content_loader``,
``from matbot.answer_checker import ...``), which keeps working because Python
binds a submodule to its package on import, with or without a re-export here.

Adding an import to this file re-breaks V3 isolation for every module at once.
``tests/test_v3_isolation.py`` fails if that happens.

NOTE: the test alias ``import app as matbot`` (see ``tests/conftest.py``) is a
private alias of the legacy top-level entrypoint module and is unrelated to this
package.
"""
