# `matbot/` — Stage 2 package (migration in progress)

Stage 1 split the **pure** logic out of `app.py` into top-level modules
(`prompts.py`, `rendering.py`, `history.py`, `task_parsing.py`, `utils.py`) and
re-imported them, with zero test changes. Stage 2 moves the rest into this package
behind an application factory.

**Phase 2A (current): skeleton only.** This package is empty of logic and not
imported anywhere. `app.py` is still the entrypoint. No behavior change.

## The rule that governs every move (do not break this)

The test suite patches internal seams **directly on the `app` module**, e.g.
`monkeypatch.setattr(app, "_openai_chat", ...)`, and constants like `app.MATHPIX_MODE`,
`app.LOCAL_MODE`, `app.DIAG_TOKEN`, `app.ALLOW_PRIVATE_IMAGE_URLS`, `app.UPLOAD_DIR`.
In Python such a patch only intercepts a call if the **caller also looks the name up in
`app`'s namespace**.

> Therefore: whenever a patched seam (or its caller, or anything that reads a patched
> constant) moves into this package, the corresponding `monkeypatch.setattr(...)` target
> in `tests/conftest.py` (and the affected test files) **must move with it**, in the same
> step, verified by `python -m pytest -q`. This is the one place Stage 2 *is* allowed to
> edit tests — patch *location* only, never behavior assertions.

## Target layout & suggested phase order

| Phase | New module(s) | Moves out of `app.py` | Test-seam updates |
|---|---|---|---|
| 2B | `matbot/config.py`, `matbot/factory.py` (`create_app`) | env/config parsing, Flask app construction, CORS, secret key, `limiter` | `LOCAL_MODE`, `DIAG_TOKEN`, `ALLOW_PRIVATE_IMAGE_URLS`, `UPLOAD_DIR`, `MATHPIX_MODE`, `limiter` |
| 2C | `matbot/clients/openai.py` | `_openai_chat`, OpenAI clients | `_openai_chat` (conftest `fake_openai` + `_isolate`) |
| 2D | `matbot/services/text.py`, `matbot/services/image.py` | `answer_with_text_pipeline`, `route_image_flow(_url)`, Mathpix | `_mathpix_enabled`, `mathpix_ocr_to_text`, `MATHPIX_MODE` |
| 2E | `matbot/security.py` | `is_safe_external_url`, `_fetch_image_bytes` | `ALLOW_PRIVATE_IMAGE_URLS` |
| 2F | `matbot/services/jobs.py`, `matbot/integrations/{sheets,gcs,firestore}.py` | async pipeline, job store, integrations | `_enqueue`, `_local_worker`, `JOB_STORE`, `sheet` |
| 2G | `matbot/routes/*.py` (blueprints) | all `@app.route` handlers | route handlers reference factory-provided `limiter` |

End state: `app.py` becomes a thin compatibility entrypoint
(`from matbot.factory import create_app; app = create_app()`) plus any re-exports still
needed for back-compat, and `gunicorn app:app` keeps working unchanged.

Each phase is its own reviewable step: small move → update its test seam → `pytest` green
→ stop and summarize. Do **not** batch phases.
