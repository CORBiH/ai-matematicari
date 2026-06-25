# MAT-BOT — Refactoring summary (Stage 1)

Local-only refactor of the Flask backend. **No behavior change, no deploy, no dependency
change, no test rewrites.** All 104 tests pass at every step.

## Why

`app.py` was a single 1643-line module mixing env/config, a ~320-line pedagogical prompt
wall, pure text/rendering/image helpers, the OpenAI wrapper, image flows, Sheets/GCS/
Firestore, the Cloud-Tasks async pipeline, and ~12 routes. Hard to read and audit. Goal:
split the **pure, side-effect-free** logic into focused modules while keeping every route,
response shape, env var, and the test suite exactly as-is.

## What changed

`app.py`: **1643 → 1179 lines.** Five new pure modules were extracted and re-imported back
into `app.py`, so `app.<name>` keeps resolving (see "Why re-export" below).

| New module | Responsibility | Key symbols |
|---|---|---|
| `prompts.py` | Sectioned BiH math system prompt (verbatim) | all `*_PRAVILA`/section consts, `RAZREDNA_PRAVILA`, `DOZVOLJENI_RAZREDI`, `build_system_prompt` |
| `rendering.py` | Model output → HTML + graph detection | `render_model_html`, `latexify_fractions`, `strip_ascii_graph_blocks`, `add_plot_div_once`, `should_plot`, `extract_plot_expression` |
| `history.py` | Conversation context sanitization | `sanitize_history`, `strip_html_to_text`, `_append_history_messages`, `HISTORY_MAX_TURNS/CHARS`, `HISTORY_CONTEXT_TURNS` |
| `task_parsing.py` | Task-number extraction from user text | `extract_requested_tasks`, `requested_clause`, `FOLLOWUP_TASK_RE`, `ORDINAL_WORDS` |
| `utils.py` | Small pure helpers | `_short_name_for_display`, `_name_from_url`, `_sniff_image_mime`, `_bytes_to_data_url` |

Each module imports **stdlib only** and never imports `app` → no import cycle. The
pedagogical prompt text was moved **byte-for-byte** (no retyping) — content is unchanged.
Also removed the now-unused `import re` from `app.py`.

## What deliberately stayed in `app.py`

Everything security-sensitive or test-patched: all env/config + patched constants
(`LOCAL_MODE`, `MATHPIX_MODE`, `DIAG_TOKEN`, `ALLOW_PRIVATE_IMAGE_URLS`, `UPLOAD_DIR`, …);
`_openai_chat` + the OpenAI clients; `answer_with_text_pipeline`, `route_image_flow(_url)`,
`_vision_messages_base`; **`is_safe_external_url` + `_fetch_image_bytes` (SSRF)**;
`cleanup_stale_uploads`; Mathpix/Sheets/GCS/Firestore; the async pipeline
(`_enqueue`/`_local_worker`/`_process_job_core`/`_create_task_cloud`); `looks_heavy`;
`limiter`, job store; **all routes and error handlers.** No security logic was moved.

## Why re-export (the rule for future contributors)

The test suite patches internal seams directly on the module, e.g.
`monkeypatch.setattr(app, "_openai_chat", ...)` and constants like `app.MATHPIX_MODE`,
`app.LOCAL_MODE`, `app.ALLOW_PRIVATE_IMAGE_URLS`, `app.UPLOAD_DIR`. In Python, such a patch
only intercepts a call if the **caller also looks the name up in `app`'s namespace**.

> **Rule:** A symbol may live outside `app.py` only if it is (a) never patched by a test,
> (b) never *reads* a patched module-constant, and (c) never *calls* a patched function.
> Moved pure symbols are re-imported into `app.py` so `app.X` still resolves for tests that
> only read them. Do **not** move a patched seam (or anything that reads/calls one) out of
> `app.py` without also updating the corresponding `monkeypatch.setattr` target.

## How to run

App (local, offline):
```
LOCAL_MODE=1 OPENAI_API_KEY=<key> python app.py     # serves on :8080 (PORT overridable)
GET /healthz  -> {"ok": true, "local_mode": true}
GET /version  -> {"version": ..., "app_py_sha": ...}
```
Tests (fully mocked — no network, no real OpenAI/Sheets):
```
python -m pytest -q        # 104 passed
python -m py_compile app.py prompts.py rendering.py history.py task_parsing.py utils.py
```

## Security-sensitive areas (unchanged, easier to audit now)

- **XSS:** `render_model_html()` in `rendering.py` is still the single sink (escape →
  latexify → `<br>`); every text/Mathpix/Vision path routes through it.
- **SSRF:** `is_safe_external_url()` + `_fetch_image_bytes()` remain in `app.py` (they read
  `ALLOW_PRIVATE_IMAGE_URLS`, a test-patched flag).
- Secret key, rate limiting, diag-endpoint gating, upload limits/cleanup: all in `app.py`,
  untouched.

## Remaining risks

- Low. Behavior is byte-identical for prompts and unchanged elsewhere; verified by 104
  mocked tests + standalone import smoke (17 routes intact).
- The 5 re-import lines sit mid-file at the former definition sites (each with a comment).
  Functional and self-documenting; a stricter style would hoist them to the top import block.

## Next improvements (TODO — Stage 2, needs sign-off)

Deeper split into a real package: `config.py`, `clients/openai.py`, `services/`
(text/image/async pipelines), `security.py` (SSRF), `integrations/` (Sheets/GCS/Firestore),
`routes/` blueprints + an app factory. This **requires** updating the `monkeypatch` targets
in `tests/conftest.py` + ~4 test files to the new module paths (behavior assertions
unchanged). Higher value, more churn — gated on explicit approval, run `pytest` after each
move. Unrelated pre-existing items live in `docs/fable-next-steps.md` (e.g. `requirements.txt`
slimming) and need a supervised deploy — out of scope here.
