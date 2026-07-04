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

## Separate track: `content_loader` + `topic_lookup` (6. razred MVP, Phase 1)

These two modules are **not** part of the `app.py` → factory migration above and touch
none of its test seams. They are self-contained data logic for the modular 6th-grade
AI tutor:

- `content_loader.py` — loads/normalizes the two source-of-truth Excel files in
  `data/6_razred/` (`AI_MATH_CONTENT_MASTER…` TOPICS + optional sheets;
  `THINKIFIC_MAP…` MAP + optional TOPIC_REFERENCE) via `openpyxl`, and validates that
  every mapped topic exists in TOPICS.
- `topic_lookup.py` — `topic_exists` / `validate_topic` / `validate_detected_topic` /
  `find_lesson` / `get_final_topic`, implementing the lookup priority from
  `docs/handoff/…`. Returns structured `{final_topic, status, source, message, matches}`.

Nothing here is imported by `app.py`; there is no file I/O at import time (loading is
lazy + cached). Tests: `tests/test_content_loader.py`, `tests/test_topic_lookup.py`.
Content is **never** hardcoded — the Excel files are the source of truth.

### Phase 2 — `prompt_builder.py`

Turns Phase 1 output into a structured prompt (**no OpenAI/network/file writes** — pure
and deterministic):

- `build_tutor_prompt(payload, lookup_result, master_content, thinkific_map=None)` —
  composes `system_prompt` (base tutor via `prompts.build_system_prompt`, lazily +
  fallback, so `app.py`/`prompts.py` are untouched) + global modular guidelines +
  the topic row's pedagogy (`lesson_scope`, mistakes, hints, solved example, tasks,
  `controlni_*`, `forbidden_ai_behavior`, …), plus optional VIDEO_FLOW and mode
  instructions. Returns `{system_prompt, user_prompt, mode, final_topic,
  opened_lesson_topic, effective_topic, status, topic_context_used, video_flow_used,
  topic_conflict}`.
- Helpers: `get_topic_context`, `get_video_flow_context`, `normalize_mode`,
  `trim_conversation_history` (last 5), `build_mode_instructions`,
  `build_fallback_prompt` (unknown/ambiguous/invalid).
- Topic-conflict rule (guidelines §9): a valid `detected_topic` that differs from the
  opened `thinkific_lesson` topic becomes the `effective_topic`; both the original
  `opened_lesson_topic` and the `effective_topic` are returned. Never invents a topic.

Tests: `tests/test_prompt_builder_modular.py`.

### Phase 3 — `ai_tutor_service.py` + `POST /api/ai-tutor/chat`

Orchestrates the full chain for the widget endpoint: request payload → Phase 1
`get_final_topic` → Phase 2 `build_tutor_prompt` → (only when `status == "ready"`)
the app's existing `_openai_chat` → structured JSON.

- `handle_chat(data, openai_chat, master=None, tmap=None, *, model, timeout)` is pure
  given the injected `openai_chat` and **does not import `app.py`** (no cycle). The
  Flask route in `app.py` is a tiny wrapper that injects `_openai_chat` / `MODEL_TEXT`
  / `OPENAI_TIMEOUT` — reusing the existing OpenAI client, no new client.
- **Non-ready statuses (`fallback`/`ambiguous`/`invalid`) never call OpenAI**; they
  return the deterministic student-facing Bosnian message from Phase 1.
- `answer` is **raw model text** (no `render_model_html`); math/HTML rendering is left
  to the Phase 4 widget.
- Response: `answer, final_topic, opened_lesson_topic, effective_topic,
  entry_source_used, topic_conflict, recommended_mode, recommend_video,
  parent_report_signal, status, mode`.

Tests: `tests/test_ai_tutor_chat_endpoint.py` (OpenAI mocked via the existing
`fake_openai` fixture; non-ready tests prove OpenAI is not called).

### Phase 4 — frontend widget + `GET /api/ai-tutor/topics`

- `list_topics(master=None)` (in `ai_tutor_service.py`) → `{"grade", "topics":
  [{oblast, topic, display_name}], "grouped": {oblast: [...]}}`, READY-only, sorted
  by (oblast, display_name), loaded from Phase 1 `get_master()` (no hardcoding, no
  secrets). Exposed via `GET /api/ai-tutor/topics` (thin additive route in `app.py`).
- `templates/index.html` gains a **separate additive "Modularni AI tutor" card**
  (4 mode buttons, backend-populated topic `<select>`, message box, fallback area,
  debug meta) that POSTs to `/api/ai-tutor/chat`, keeps the last 5 messages in
  `localStorage` (safe `matbot_tutor_history_<cid|default>` key), and renders `answer`
  as escaped `pre-wrap` text through the page's existing MathJax. The **legacy
  `#ask-form` → `/submit` flow and image upload are untouched**; no new JS/CSS
  frameworks. Tests: `tests/test_ai_tutor_topics_endpoint.py`.

### Phase 4.1 — mode buttons become action shortcuts

The four mode buttons (`data-action="tutor-send"`) now set the mode, mark themselves
active, and **immediately send** (same path as "Pošalji tutoru"). When the textarea is
empty they use sensible defaults: explain/practice/exam with a selected topic send a
default Bosnian prompt; exam with no topic still sends (backend asks which area);
`quick` with an empty textarea shows a validation message and does **not** send;
explain/practice with no topic and no text show the topic-selector fallback. Typed
text is cleared only after a successful send; a busy-guard prevents overlapping
requests. Backend unchanged. Tests: `tests/test_ai_tutor_widget_template.py`.

### Phase 4.2 — one polished "AI Tutor" panel (template-only)

`templates/index.html` now has a single main **AI Tutor card**: title + subtitle,
topic selector, the four action buttons, the tutor transcript (`#tutorChat`, bubbles
user-right / tutor-left), fallback banner near the selector (with focus + brief
highlight), textarea + "Pošalji tutoru", and the muted meta line — all inside the
card. Loading shows "Tutor razmišlja..." inside the transcript and disables the
buttons. Answers go through a small safe renderer (`renderTutorHTML`: escape first,
`**bold**` → `<strong>`, line breaks, `-`/`1.` lists; MathJax still typesets
`$$...$$`; no markdown lib, no XSS). After a ready practice answer the placeholder
becomes "Upiši svoj odgovor na zadatak...". The **legacy chat + `/submit` form +
image upload moved unchanged into a collapsed `<details>` "Upload slike / napredni
način"** section below. Backend untouched.

### Phase 5 — session_id + minimal activity log (SQLite)

- Frontend auto-creates `session_id` (localStorage key `matbot_session_id`,
  `crypto.randomUUID()` with fallback) and sends it in every `/api/ai-tutor/chat`
  payload; never shown in UI. No student_id/parent-email UI was added.
- `matbot/activity_log.py`: SQLite at `MATBOT_DB_PATH` env or
  `storage/matbot.sqlite3` (folder auto-created, gitignored). Table
  `student_activity_log` stores **metadata only** — never `student_message`, AI
  answers, images, or secrets. `init_db` / `log_student_activity` /
  `get_recent_activity` / `classify_event_type` (`practice_answer` >
  `exam_mode_used` > `topic_selected` > `ai_message`). Logging errors are caught —
  a failed insert never breaks the chat response (`handle_chat` also wraps the
  call). Parent-report email/Thinkific integration is deliberately NOT implemented.
  Tests: `tests/test_activity_log.py` + logging tests in
  `tests/test_ai_tutor_chat_endpoint.py`.

### Phase 6 — free_chat topic detection + smarter fallbacks

Topic selection is now truly optional. `matbot/topic_detector.py`:
`is_vague_message` (math signals + topic keywords), `detect_topic_heuristic`
(static patterns → candidate ids validated against the master; broad terms map to
the *first* topic of that prefix in TOPICS sheet order — data-driven), and
`detect_topic_llm` (cheap classifier through the injected `openai_chat`, JSON-only
`{"detected_topic": ...}`, every output coerced through `validate_detected_topic`
— garbage/invented → unknown; never raises). Order: heuristics first, LLM only when
they miss on a concrete message.

`handle_chat` flow: lookup unknown + concrete message → detect → if valid topic,
re-resolve via `get_final_topic` and answer normally; if still unknown → new
`build_general_tutor_prompt` (base prompt, **no invented topic**, `final_topic=
"unknown"`, `status="ready"`). Vague messages still fall back — now mode-specific
(`_fallback_answer`): exam asks "Iz koje oblasti je kontrolni?" with the oblast
list built from the master, practice asks for a topic/task, quick requires a
concrete task. Also added: input caps (message 4000, history 5×1500,
last_tutor_task 1000), per-mode `max_tokens` (quick 250 / explain+practice 700 /
exam 900), and sanitized 500 responses in `app.py` (no raw `str(e)` to clients).
Tests: `tests/test_topic_detector.py` + Phase 6 block in
`tests/test_ai_tutor_chat_endpoint.py`.

### Phase 6.2 — base-prompt unification, single visible tutor, image in tutor

- **Base math rules**: every modular path (ready/thinkific/free_chat/general/
  fallback/practice/follow-up/exam/quick) already builds its system prompt from
  `prompts.build_system_prompt(grade)` via `_base_system_prompt` — Phase 6.2 adds
  tests proving it (`BASE_MARKERS`: non-math refusal, `\frac`/`\cdot` rules,
  grade-6 rules, 5–6 equation method, terminology) and a source-check that the
  base prompt is never duplicated into `prompt_builder.py`. `is_vague_message`
  now checks math signals before length, so "5-1" is a concrete task.
- **UI**: the legacy `/submit` chat+form markup is fully **hidden**
  (`#advancedLegacy.legacy-holder[hidden]`; no more `<details>`), keeping legacy
  JS/backend intact — the page shows one AI Tutor only. Backend `/submit` remains.
- **Image in the tutor**: "📷 Dodaj sliku zadatka" inside the tutor card; sends
  `multipart/form-data` (`payload` JSON + `image`) to `/api/ai-tutor/chat`. Route
  validates image type/size and builds a data-URL; `handle_chat` first tries the
  injected legacy `mathpix_ocr_to_text` (OCR text → normal text pipeline with
  topic detection), else sends a multimodal Vision message (`MODEL_VISION`) with
  the same modular system prompt. Empty-text image sends get per-mode default
  messages. No new OCR/vision code; keys stay server-side.
- **Composer polish**: the image control is now a compact round "＋" button
  (`.tutor-plus`, `aria-label="Dodaj sliku zadatka"`) at the left of the composer
  row — compact `[textarea] [＋] [Pošalji]` (42px controls). It's a `<label
  for="tutorImage">` opening the same hidden file input; filename chip + remove
  sit just above the row. All send/validation/multipart behavior unchanged.

### Phase 6.1 — quick hardening + UX cleanup

- Frontend: changing the topic resets the practice phase and stored last task;
  "Očisti chat" now also removes `matbot_tutor_history_*` / `matbot_tutor_lasttask_*`
  (reload resets transcript/phase); Enter sends (Shift+Enter = newline, blocked while
  busy); a friendly note appears if `/api/ai-tutor/topics` fails (free_chat still works).
- `activity_log._connect` sets `PRAGMA journal_mode=WAL` + `busy_timeout=5000`;
  `init_db` creates indexes `idx_activity_session_ts` / `idx_activity_student_ts`
  (for the future parent-report queries). Concurrency smoke-tested.
- `scripts/check_js.mjs` — repeatable node check (inline JS syntax + renderTutorHTML
  behavior: headings/bold/lists/XSS/dot-lines/br-collapse). Run: `node scripts/check_js.mjs`.

### Phase 5.1 — single-tutor layout polish (template-only)

The page now renders **one** card: the legacy `/submit` form (grade select, textarea,
image upload, its chat area) moved unchanged into a collapsed `<details>` in the
tutor card's footer — summary "📷 Imam sliku zadatka / napredni način", with a note
that it's only for image tasks. Added an empty-state helper inside the tutor area
("Izaberi temu ili samo upiši pitanje…", hidden after the first message) and the
topic label now reads "Tema ako znaš (opcionalno):". No JS behavior changes beyond
hiding the empty state; backend untouched.

### Phase 4.3 — practice answer flow + rendering polish

- **Frontend state:** after a ready practice answer the widget sets
  `interactionPhase = "awaiting_practice_answer"` and stores the task in
  `localStorage` (`matbot_tutor_lasttask_<cid>`). The next typed message is sent as
  an **answer**, with `interaction_phase: "answering_practice_task"`,
  `last_tutor_task` (truncated to 600 chars) and `mode: "practice"` (via
  "Pošalji tutoru"). Clicking any mode button resets the phase.
- **`prompt_builder.py`:** new `build_practice_followup_instructions(payload,
  topic_context)`; when `payload.interaction_phase == "answering_practice_task"` it
  replaces the fresh-practice block — AI must evaluate the student's answer against
  `last_tutor_task`/history (correct → confirm + brief why + optional next small
  task; wrong → gentle + one hint), no topic re-explanations, no "### Tema"
  headings, and the mode is forced to `practice`. `ai_tutor_service` needed no
  change (payload passes through).
- **Renderer:** `renderTutorHTML` now converts `###`→`<h3>`, `#`/`##`→`<h2>`,
  drops "."-only lines, collapses excess `<br>`, still escapes first (no XSS, no
  markdown lib); bubbles got heading/math/width polish. Meta line is friendly
  ("Tema: <display_name> · Režim: Vježba") and hides raw topic ids.
