"""matbot.ai_tutor_v3 — the isolated AI-first tutor backend.

Package skeleton only. No schemas, orchestrator, verifier, reducer, state store,
adapter, feature flags or dispatcher yet — those are later stages.

**This file must contain no imports.** Two separate reasons, both enforced by
``tests/test_v3_isolation.py``:

1. *Isolation.* V3 must never depend on the frozen tutoring stack —
   ``ai_tutor_service``, ``answer_checker``, ``grading_guard``, ``engine_v2``,
   ``exam_engine``, ``solution_plan``, ``task_templates``, ``task_activation``,
   ``task_model``, ``turn_intent``, ``prompt_builder``, ``tutor_prompts``,
   ``topic_detector``, ``topic_lookup``, ``image_result_verifier`` or
   ``matbot.minimal``. The point of the boundary is that deleting all of them
   leaves this package working.

2. *No eager loading.* Even for the retained dependencies it IS allowed to use
   (``content_loader``, ``topic_resolver``, ``bosnian``, the standard library),
   a package ``__init__`` that imports submodules forces every consumer to load
   all of them. Importing ``matbot.ai_tutor_v3`` must stay free.

Consumers import the submodule they need directly, e.g.
``from matbot.ai_tutor_v3.schemas import TutorSessionState``.
"""
