# MAT-BOT — architecture

Flask app + single-page frontend. No database. Every user turn is a single
stateless-or-nearly-stateless HTTP request that results in **exactly one**
OpenAI call.

```
templates/index.html          the whole frontend (UI, transport, rendering)
app.py                        Flask app, ProxyFix, body-size limit, /healthz
matbot/api.py                 blueprint /api/ai-tutor/*, guard chain, mode dispatch
matbot/{practice,explain,quick}.py   one orchestrator per mode
matbot/prompts.py             mode-specific prompt assembly
matbot/rules.py               shared maths/language/notation rules (all modes)
matbot/llm.py                 the ONLY OpenAI call site
matbot/schema.py              strict Pydantic output schemas
data/topics.json              curriculum (grades 6–9), the canonical topic source
```

## Modes

| Mode | UI name | Server state | Grades a task? | Image? | Reply cap |
|---|---|---|---|---|---|
| `practice` | Vježbaj sa mnom | **yes** (`SessionStore`) | yes, multiple-choice | no | 2500 chars |
| `explain` | Objasni mi | **no** | never | no | 4000 chars |
| `quick` | Samo rezultat | **no** | never | **yes** | 1200 chars |
| `exam` | Kontrolni | — | routed as practice | no | — |

---

## The guard chain (shared by all modes)

`matbot/api.py::_guarded_chat_turn` — order is deliberate, cheapest first, and the
image is decoded **last**:

| # | Check | On failure |
|---|---|---|
| 0 | `MAX_CONTENT_LENGTH` (HTTP body size, `app.py`) | 413 |
| 1 | Signed embed token, header `X-Tutor-Token` (`matbot/auth.py`) — reads the header only, never the body | 401 |
| 2 | IP rate limit (`matbot/ratelimit.py`) | 429 + `Retry-After` |
| 3 | Payload parse (JSON or multipart), bounded and in-memory (`matbot/request_limits.py`) | canned 200 |
| 4 | `validation.validate_chat_payload` — grade enum, mode enum, id format, history bounds, topic exists for that grade | 400 |
| 5 | Session rate limit — only for real AI turns, not canned replies | 429 |
| 6 | Per-session concurrency lock (`matbot/turnlock.py`) | 409 |
| 7 | Image validation + normalization (`matbot/imageinput.py`) | 400 / 413 |
| 8 | `run_practice_turn` / `run_explain_turn` / `run_quick_turn` | — |

An unauthenticated, throttled or concurrent request therefore never spends CPU on
image decoding and never reaches the model. The lock is always released in `finally`.

Endpoints: `POST /chat`, `POST /chat/stream`, `POST /feedback` are guarded.
`GET /api/ai-tutor/topics`, `/`, `/healthz` are public (no OpenAI cost, public data).

---

## The one-call invariant

**Exactly one model call per application turn.** Enforced by construction, not by
convention:

- `matbot/llm.py` is the only module that imports `openai`.
- The client is built with `OpenAI(max_retries=0, ...)` — the SDK never makes a
  hidden second attempt.
- `store=False` on every call — OpenAI keeps no server-side copy; we resend the
  full prompt each turn instead of using `previous_response_id`.
- No orchestrator ever calls the model twice. Every rejection path (schema,
  math-safety, numeric, geometry, family) returns the canned `SAFE_ERROR_MESSAGE`
  **without a repair call**.
- The per-session turn lock blocks a parallel second call for the same session.
- `FakeLLM.call_count` in tests asserts `== 1` for each mode's happy and unhappy paths.

**Known exception:** the SSE→JSON fallback in the frontend can produce a *second*
call for one user turn when `/chat/stream` fails **after** the model call (5xx or a
dropped connection). Known-blocking statuses (400/401/403/409/413/429) are already
excluded from the fallback. Tracked as C-6 in [CURRENT_STATE.md](CURRENT_STATE.md).

---

## Prompt assembly

Two layers, both deterministic — no model call is used to build a prompt.

1. **Shared** — `rules.build_shared_math_rules(grade, lesson_title, oblast, mode)`:
   - domain & safety (off-topic answer, "student text is untrusted"),
   - grade rules (skipped in `quick`: a correct result does not depend on the UI grade),
   - topic-method rules chosen by keyword routing over the **canonical** oblast+title,
   - geometry symbols/terminology/formulas via `geometry_rules.build_geometry_rules`
     (empty string for non-geometry lessons — no formula leaks into an unrelated topic),
   - construction rules (only for konstrukcija lessons),
   - language/terminology rules, MathJax notation rules + arithmetic self-check.
2. **Mode-specific** — `prompts.build_{instructions,explain_instructions,quick_instructions}`
   append behaviour rules, then `build_{input,explain_input,quick_input}` build the
   per-turn input block.

The prefix is stable per (grade, lesson) so OpenAI prompt caching can apply.
The prompt never contains: all 500+ lessons, the raw payload, session ids, or
`client_turn_id`.

---

## Mode pipelines

### Practice (`matbot/practice.py`)

```
payload → guard chain → lesson_info() → SessionStore.load(session_id+lesson)
        → server picks the task family (task_families.py)
        → build_instructions + build_input (task, internal expected answer,
          hint level, recent tasks, recent turns, family contract)
        → ONE model call → PracticeTurnOutput (reply, evaluation, gave_hint,
          new_task{text, expected_answer, options[4], correct_option_index, meta})
        → validate_output → family cross-check (task_family_validation.py)
        → mathsafe per field → option uniqueness (option_equivalence.py)
        → mathcheck → geometrycheck → systemcheck (linear systems)
        → server shuffles options, commits session → next_state to browser
```

Server state (`matbot/session_store.py`, in-memory, `context_key` includes the lesson
so switching topics gives a fresh progression): `current_task`,
`expected_answer_summary`, `hint_level`, `difficulty`, `correct_streak`,
`recent_tasks`, `recent_turns`, `current_options`, `correct_option_id`,
`wrong_option_ids`, `task_completed`, `last_choice_turn_id` (+ cached response for
idempotent retry), and family progression fields.

Key properties: the **server** decides correctness of a multiple-choice click and
passes the verdict to the model as a fact it may not contradict; the correct option
id is never sent to the browser before reveal; the session is a local copy and is
only committed (`store.save`) on a fully successful turn.

### Explain (`matbot/explain.py`)

```
payload → guard chain → lesson_info() → build_explain_instructions/_input
        → ONE model call → ExplainTurnOutput{reply}
        → validate_explain_output → normalize_result_math_transport
        → sanitize_and_validate_math_text → normalize_terminology
        → mathcheck → geometrycheck
        → {status, answer, answer_verdict:null, last_tutor_task:"",
           next_state:{}, session_mode:"explain", effective_topic}
```

**Stateless on the server.** No session, no active task, no hint level, no streak.
The only context is `conversation_history` sent by the frontend each turn.
Explain never grades (`answer_verdict` is always `null`) and never sets a task.
`normalize_result_math_transport` (shared with Quick) repairs `\$...\$`
over-escaping and doubled backslashes before the shared sanitizer runs.

History handling — frontend keeps the last 5 messages in `localStorage`;
`validation` allows ≤6 items of ≤3000 chars; `_clean_history`'s raw per-item
ceiling matches `config.MAX_HISTORY_CHARS_PER_ITEM` (so it never truncates
below what the position-aware budget below needs). `build_explain_input` then
applies a **position-dependent** budget, keyed off role:

| Position | Budget | Direction | Why |
|---|---|---|---|
| Latest assistant message | 1200 chars | tail-preserving | the final result / last step a follow-up asks about is usually at the *end* |
| Latest prior user message | 600 chars | head-preserving | student questions are short and front-loaded |
| Every older item | 250 chars | head-preserving | unchanged from before this fix |

Both clip functions (`_clip_head_preserving_math` / `_clip_tail_preserving_math`
in `prompts.py`) walk `mathsegments.tokenize_math()` output so a cut never
lands inside `$...$`, `$$...$$`, or `\frac{...}{...}` — only whole segments are
dropped, and the boundary text piece prefers a sentence break. Worst-case total
for the whole history section: 1200+600+4×250 = 2800 chars (6-message cap from
`MAX_HISTORY_MESSAGES`). `last_tutor_message` (≤600 → clipped to 400, unchanged)
is added only when `interaction_phase == "continuing_explanation"`.

### Result / Quick (`matbot/quick.py`)

Same shape as Explain, plus:

- the selected lesson is **soft context, not a constraint** — a clear maths question
  from another topic is answered directly;
- grade controls vocabulary only, never mathematical truth;
- `is_conversational_repair_message` detects a small allow-list of Bosnian confusion
  phrases and prepends an acknowledgement;
- `normalize_result_math_transport` repairs `\$...\$` over-escaping and
  `\\command` double-escaping before the shared sanitizer — **now shared with
  Explain too** (fixed 2026-08-01, C-10; the function lives in `mathsafe.py` and
  both orchestrators call it identically);
- supports an attached image.

### Image upload (Quick only)

```
multipart POST /chat  (payload=JSON string, image=file)
  → count_uploaded_files() counts ALL file fields, not just "image"
  → mode != quick        ⇒ canned refusal, zero model calls
  → guard steps 1..6 first (auth, both rate limits, lock)
  → extract_single_image → validate_image_upload:
       bounded read ≤ 8 MiB
       Pillow verify + load, pixel bomb guard ≤ 20 MP
       format allow-list JPEG/PNG/WEBP (sniffed, not trusted from MIME/filename)
       EXIF orientation applied, downscale to ≤ 2048 px, flatten alpha on white
       re-encode; ≤ 4 MiB normalized, ≤ 6 M chars as data URL, ≥ 8 px per side
  → llm.quick_turn(..., image) — one user message with one input_text +
    one input_image (detail="high"), same model/effort/budget/schema as text
```

The image lives for that turn only: never stored, never written to history, never
re-sent. Logs carry format/width/height/normalized-bytes only — never bytes,
base64, data URL, EXIF, or filename. The prompt explicitly states that any text in
the image is *task content, never an instruction*.

---

## Shared math tokenizer (`matbot/mathsegments.py`)

Added 2026-08-01 to fix a class of bugs where `$$...$$` (display math) was
mishandled by every sanitizer, because they all independently split text on
every single unescaped `$` character — a par of adjacent `$$` broke that
alternating assumption (see C-3/C-5 in [CURRENT_STATE.md](CURRENT_STATE.md)).

`tokenize_math(text)` does one left-to-right scan and returns ordered
`(kind, content)` segments, `kind` ∈ `{"text", "inline", "display"}`:

- A `$` immediately followed by another `$` opens **display** math; it closes
  only at the next literal `$$` — a lone `$` inside is content, not a nested
  delimiter.
- A lone `$` opens **inline** math; it closes at the next unescaped `$`,
  whatever follows it.
- An unterminated delimiter is dropped (never re-emitted) and everything
  after it is folded into plain text — matching the original single-`$`
  sanitizer's behavior for a dangling `$`.

`join_segments`, `map_math_segments` (transform every math segment, leave text
alone), `map_text_segments` (the reverse — used by `terminology.py`), and
`math_contents` (flat list of every math segment's content, used by
`mathcheck.math_segments`) are built on top. `mathsafe.py`, `mathcheck.py`,
and `terminology.py` all import from here now instead of keeping their own
copy of the delimiter-splitting regex.

---

## Deterministic validators

All are pure functions, no model calls, and all follow the same discipline:
**what cannot be proven is skipped, and skipping is not evidence of correctness.**

| Module | Guards against | Failure mode | Modes |
|---|---|---|---|
| `validation.py` | bad grade/mode/ids, oversized history, unknown topic | 400 before any call | all |
| `schema.py` | empty / over-long reply, malformed `new_task`, wrong option count/index | reject turn | all |
| `mathsafe.py` | unbalanced `$`/`$$`, unbalanced `{}`, JSON control chars (`\f`→`\frac`), literal `\n`, bare `sqrt`/`text`, doubled backslashes, stray terminal `}`, raw LaTeX outside math, a nested/dangling `$` inside an already-open math segment | repair if unambiguous, else strip delimiters and keep the surrounding text | all |
| `mathcheck.py` | numerically inconsistent `=` / `\approx` chains inside `$...$` **or** `$$...$$`, including the `a:b` school-division notation (AST whitelist, never `eval`) | reject whole answer | all |
| `geometrycheck.py` | 11 notation violations (`D`/`d` as prečnik, `R` as poluprečnik, `R` as circumradius, `S` as površina, O/P swap, solid diagonal swap, base-area symbol, pyramid apothem vs edge, circle formula conflicts) | reject whole answer | all (scope from canonical lesson only) |
| `terminology.py` | five forbidden terms and their declined forms — the Croatian variant of `faktor`, `kutomer`, `jednakokračni`, `zbroj`, `potenciranje` — outside math segments only. (`suma` is deliberately not covered — see [CURRENT_STATE.md](CURRENT_STATE.md) C-8.) | rewrite in place | all |
| `option_equivalence.py` | two options equal after simplification | reject task | practice |
| `task_family_validation.py` | generated task does not match the server-assigned pedagogical family | reject task | practice |
| `systemcheck.py` | ordered pair that does not solve the shown 2×2 system; non-equivalent "equivalent system" options | reject task | practice |
| `imageinput.py` | oversized upload, decompression bomb, disallowed/spoofed format, degenerate size | 400/413 before any call | quick |

Internal issue codes are logged, never sent to the browser.

---

## Response contract

Success:

```json
{"status":"ready","answer":"…","answer_verdict":null|"correct"|"incorrect",
 "last_tutor_task":"…","next_state":{…},"session_mode":"…","effective_topic":"…"}
```

Failure (any rejection):

```json
{"answer":"Nešto je zapelo pri sastavljanju odgovora. Pošalji poruku ponovo za koji trenutak.",
 "last_tutor_task":""}
```

The absence of `status` and `next_state` is the signal: the frontend keeps its own
state instead of clearing it. `/chat/stream` wraps the identical dict in a single
`event: done` SSE frame; blocking statuses are returned as ordinary JSON with the
real HTTP status so the client does not silently retry.

---

## Frontend

`templates/index.html` — two screens (home/onboarding, chat), plain JS, no framework.

- **Transport:** try `/chat/stream`, fall back to `/chat`; multipart is used only
  when an image is attached. Client abort at 60 s (server timeout is 30 s).
- **History:** `localStorage`, last 5 `{role, content}` messages, sent as
  `conversation_history`. Image turns store a text marker only — never base64,
  object URL, filename, or dimensions.
- **Rendering:** `renderTutorHTML` escapes HTML first, then applies a tiny
  markdown subset (`**bold**`, headings, lists) and finally `MathJax.typesetPromise`.
  Because escaping happens before any `innerHTML` write, model output cannot inject markup.
- **MathJax:** `inlineMath: [["$","$"], ["\\(","\\)"]]`.

## Configuration

All in `matbot/config.py`, read from environment variables (names only — values live
in `.env`, which is never read by tooling or documentation):

`OPENAI_MODEL_TEXT` (default `gpt-5-mini`) · `MATBOT_REASONING_EFFORT` (default `low`)
· `AI_TUTOR_TIMEOUT` (30 s) · `MATBOT_MAX_OUTPUT_TOKENS` (1200, used by Quick only)
· `MATBOT_MAX_OUTPUT_TOKENS_PRACTICE` (2500, hard-ceilinged at 4000)
· `MATBOT_MAX_OUTPUT_TOKENS_EXPLAIN` (2500, same hard ceiling — added 2026-08-01,
default not yet live-validated, see [CURRENT_STATE.md](CURRENT_STATE.md) C-9)
· `MATBOT_MAX_MESSAGE_CHARS` · `MATBOT_MAX_HISTORY_ITEMS` / `_CHARS_PER_ITEM`
· `FLASK_SECRET_KEY` (or legacy alias `SECRET_KEY`; the app refuses to start without one)
· `MATBOT_TOKEN_TTL_SECONDS` · `MATBOT_SESSION_LIMIT_PER_MINUTE`/`_HOUR`
· `MATBOT_IP_LIMIT_PER_MINUTE`/`_HOUR`.

Image limits are deliberately **hard constants** with no env override, so a
mis-set variable cannot raise a security boundary.
