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

## The two-call Practice boundary

**Explain and Quick: exactly one model call per turn. Practice: at most exactly
two** (Tutor draft → independent Reviewer that finalizes). The Reviewer is a
verification stage, not a retry: its payload **is** the published answer, and
there is never a third call.

Call accounting for a Practice turn:

| Outcome | Calls |
|---|---|
| blocked before the model (unknown lesson, invalid/stale click, completed task) | **0** |
| help served by the server (see the help ladder below) | **0** |
| Tutor timeout / transport failure | **1** |
| Tutor draft violates the per-intent field rule (nothing to review) | **1** |
| model-authored hint level 1/2 on a computational task | **1** |
| approve, correct, fail_closed, or any server-side rejection afterwards | **2** |

Enforced by construction, not by convention:

- `matbot/llm.py` is the only module that imports `openai`.
- The client is built with `OpenAI(max_retries=0, ...)` — the SDK never makes a
  hidden second attempt.
- `store=False` on every call — OpenAI keeps no server-side copy; we resend the
  full prompt each turn instead of using `previous_response_id`.
- No orchestrator ever exceeds its budget. Every rejection path (schema,
  math-safety, numeric, geometry, option uniqueness, reviewer fail-closed, image
  gate, image check) returns the canned `SAFE_ERROR_MESSAGE` or a short
  server-owned message **without a repair call**. Image turns in particular use
  **no OCR call and no second model call** — an unreadable image is reported,
  never re-read.
- The two deterministic server-owned answers added in the D35 pass (the clock-time
  fallback and the image-unreadable message) are composed **after** the single call
  from data the server already has; neither triggers another call.
- The per-session turn lock blocks a parallel call for the same session.
- `FakeLLM.call_count` in tests asserts the exact budget for each mode's happy
  and unhappy paths (`== 1` for Explain/Quick, `<= 2` for Practice), and
  `tests/test_universal_tutor_pipeline.py::test_no_turn_ever_makes_a_third_call`
  sweeps every Practice outcome.

**Known exception:** the SSE→JSON fallback in the frontend can produce a *second*
call for one user turn when `/chat/stream` fails **after** the model call (5xx or a
dropped connection). Known-blocking statuses (400/401/403/409/413/429) are already
excluded from the fallback. Tracked as C-6 in [CURRENT_STATE.md](CURRENT_STATE.md).

### The help ladder (Phase 2)

Two published release blockers came out of the help path, and both were found by
**manual reading** while every automatic check was green: a first hint that
restated the marked *proposition* verbatim (the value-shaped leak oracle cannot
see a sentence), and a third hint that reached the correct conclusion through a
**false** intermediate step (fresh model prose, no Reviewer, no preflight). A
second campaign added a hint that leaked the same criterion as a *paraphrase*,
plus a hint that served a parametric line and a dot product to a grade-9
*primary-school* lesson.

Phase 2 does not answer these with more detection. It removes the unsafe classes
by construction, in `matbot/hint_policy.py` (pure policy, no lesson IDs, no model)
and `matbot/tutor/pipeline.py` (flow only):

| Help step | Author | Calls |
|---|---|---|
| hint 1/2, answer is a **value/expression** (computational) | model, through the shaped help prompt | **1** |
| hint 1/2, answer is a **proposition/recognition** | server template that copies **nothing** from the options | **0** |
| hint 3 (ladder top) — any class | server composition of the **Reviewer-approved** `solution` + `expected_answer` | **0** |
| full solution ("Uradi ga ti") — any class | same server composition | **0** |
| lesson with a complete deterministic generator | its own stored ladder (Phase 4H, unchanged) | **0** |

**Production serves ONE hint, not a ladder** (`MATBOT_PRACTICE_SINGLE_HINT`,
enabled by default and written explicitly by deploy). A student who asks for help
gets one useful strategic hint; **clicking again returns the identical stored text
and spends no call**. The full solution stays a separate action ("Uradi ga ti").
The 1→2→3 ladder above remains in the code as the rollback (`disabled`) because
its top step's composition is what binds the reveal to a verified artifact — that
proof is what the single hint inherits. Everything the ladder rows say about
authorship and call cost still applies to the one hint that is actually served.

- **Task class is a server-owned structural fact**, derived from the shape of the
  *published* options plus the server's own `correct_option_id` — never from
  `task_type`, never from `task_signature.answer_type` (whose *value* no server
  validator checks, so it is model prose in a structured field), never from model
  prose, never from a lesson title or ID. The same lesson may validly yield both
  classes.
- **`COMPUTATIONAL` needs positive proof, not the absence of prose.** A short
  *symbolic* option is not a value: `$p \perp \alpha$`, `$A \subset B$`,
  `$A\cap B=\varnothing$`, `$\mathbb{Z}$` and `$\alpha+\beta=180^\circ$` are all
  recognition answers. `hint_policy.value_shaped` therefore requires all four of:
  at most two prose words outside math; a numeric literal (or `\pi` / `\infty`)
  somewhere; no command that asserts a relation between named objects
  (`\perp`, `\parallel`, `\subset`, `\cong`, `\varnothing`, …); and no part of
  the answer naming two different objects — so `$x=2, y=3$` is two values while
  `$\alpha+\beta=180^\circ$` is one assertion.
- **The option shape alone is still not enough: the same form means two things.**
  With options `$x>3$` / `$x>5$` / `$x>1$` / `$x\ge 4$`, "Riješi nejednačinu
  $x-1>2$" asks for a *derived result* while "Which inequality describes all
  numbers to the right of 3?" asks for *recognition of a notation*. The
  classifier therefore takes the **published task text** as its first input, and
  splits the value proof in two:
  - a **pure quantity** (no relation operator, no named object: `150`, `$12$ cm`,
    `$\frac{3}{4}$`, `$\sqrt{2}$`, `$4\pi$`, `$\{1,2,3\}$`, `$(2,3)$`) is
    `COMPUTATIONAL` from its shape alone — a quantity cannot be a proposition, so
    no task-level evidence is needed;
  - a **relation-like** answer (`$x=3$`, `$x>3$`, `$y=2x+1$`, `$\alpha=60^\circ$`,
    `$P=24$ cm`, set-builders) is `COMPUTATIONAL` **only** when
    `mcq_integrity.evaluate_linear_solve_mcq` — the pre-existing server oracle
    that already gates publication — reads a relation out of the published task,
    solves it with exact `Fraction` arithmetic, and confirms the marked option is
    that derived solution. No new mathematics and no keyword catalogue is added.
  Anything unprovable falls back to the *propositional* ladder, the one that
  states no decision criterion. Known conservative losses (labelled geometry
  results such as `$P=24$ cm`, angle values, set-builder answers, system
  solutions, computed set operations naming the sets, purely symbolic algebraic
  results, domain classification) get useful server-composed help, never a
  refusal — an availability trade Phase 3/4 can recover with richer contracts.
- **No fresh unverified proof can reach the ladder top.** The published text is
  a composition of artifacts that already passed generation, the Reviewer, and
  every publication validator. Without such an artifact the full reveal **fails
  closed** — the server never asks a model to invent a derivation.
- The typed-message route cannot be classified before the call (intent is model
  output there), so the call is spent — but the same server composition replaces
  the model text. The guarantee never depends on which route the turn took.
- **The propositional templates prescribe only universally valid reasoning.**
  Level 1 compares objects, relations, assumed conditions and strength across all
  options. Level 2 compares each option against the task's *given* conditions and
  the lesson's definition, and rejects one only on a concrete conflict with those
  two sources. An earlier level 2 told the student that one counterexample
  refutes an option; that is invalid for existential claims ("a line and a plane
  *can* have …" — such a distractor sits in the live TR-B1 package), for claims
  about the task's specific configuration, and for definition recognition. The
  server must never teach an invalid method, so that specialization was removed
  outright: `implication_shaped` proves the *shape* of the options, never that
  the task concerns a universal implication. The one remaining specialization is
  a pure reading aid — separate hypothesis from conclusion and note that a
  reversed direction is a *different* statement to be checked separately.
- **Proportionality without Phase-3 curriculum data:** advanced machinery
  (`\vec`, `\int`, `\sum`, `\lim`, matrices …) may appear in help only if the
  approved task, its options, or its approved solution already use it. This gate
  proves that certain advanced **notation** was introduced; it proves **nothing**
  about semantic grade appropriateness. For propositional help the historical
  TR-B1 advanced-technique class is eliminated by construction (server-composed
  hints 1–2, verified-artifact hint 3); for **model-authored computational hints
  1–2 grade/lesson semantic fit stays a manual live-review duty** until Phase 3
  provides richer lesson contracts.
- Secondary guards on model-authored help: the exact-token proposition-disclosure
  measure, the contentless-help floor, and the pre-existing `feedback.leaks_answer`
  gate. The token measure cannot reach a paraphrase — that class is closed by
  construction above, and the evaluator classifies its PASS as bounded evidence,
  never as semantic proof.
- **A scenario label is not branch coverage.** The model owns the generated
  answer shape, so the class is only knowable after publication. The campaign
  records it with `task_class:<class>`; a mismatch is a SKIP → `COVERAGE_GAP`,
  never a product failure. `release_contract.hint_branch_coverage` requires at
  least 3 propositional and 3 computational full ladders plus one
  short-symbolic propositional task before Phase 2 may claim coverage.
- **One classifier, one context.** `hint_policy.session_task_class(session)` is
  the single entry point: the help path (`matbot/tutor/`) and the evaluator
  (`tools/practice_eval/`) both call it on the same session, so neither can
  measure a weaker option-only class than the server actually acts on. The same
  rule holds for the composition itself —
  `hint_policy.compose_top_hint_for_session` /
  `compose_full_solution_for_session` are the only entry points production and
  the evaluator use, so they cannot pick different sources for the final answer.

### Provenance is not option binding (live H12)

Wave F6H proved the whole ladder above and left exactly one product blocker. A
Reviewer-approved `solution` read "…su $(3,2)$, što je opcija a."; the server
then shuffled the options and committed `correct_option_id = c`. The full
solution is a **byte-for-byte** composition of that verified artifact, so
`hint_top_from_verified_solution` passed — correctly. **Provenance proves where
the text came from; it never proves the text points at the right option.** The
two are separate properties and are never merged into one check.

Ownership: the model may own the mathematics and the mathematical answer; the
**server alone owns option identity (a/b/c/d), and only after the shuffle**. So
a model-authored artifact must never name an option letter.

- **Publication is the primary defense** (`matbot/tutor/pipeline.py::
  _bind_artifact_to_published_options`) — the first moment the final
  `correct_option_id` exists and the session is still untouched. An artifact
  with no MCQ label passes untouched; a **provably removable** appositive or
  parenthetical clause (`, što je opcija a.`, `(opcija c)`, `— odgovor b.`) is
  deleted, with the deletion proven content-preserving (identical digits,
  identical math segments, non-empty result); anything else **fails closed**. A
  letter is never rewritten into another letter — that would turn the model's
  claim into the server's. A marked option or `expected_answer` naming a letter
  fails closed outright: those *define* option identity.
- **The grammar is closed and narrow** (`mcq_integrity.option_label_claims`): an
  explicit MCQ word must sit immediately before the letter (`opcija a`,
  `odgovor b`, `izbor c`, `pod d)`, `option a`), and only text segments are
  scanned. Named mathematical objects — "tačka A", "prava a", "skup B",
  "ugao C" — are therefore untouched by construction.
- **Help time is defense in depth.** Server-composed help fails closed on a
  surviving label rather than repairing it (repair would break the byte-for-byte
  provenance guarantee); a *model-authored* hint naming any letter is replaced by
  the server scaffold — a correct letter is an answer leak that
  `feedback.leaks_answer` cannot see, since that oracle compares option *text*.
- **Measured separately** by `checks.solution_option_binding_consistent`, which
  proves that every asserted letter, `revealed_correct_option_id`, and
  `expected_answer_summary` all agree with the currently marked option.

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

The universal Practice path builds **three** prompts
(`matbot/tutor/prompts.py`): `build_tutor_*` (draft), `build_reviewer_*`
(independent verification), and — since Phase 2 — `build_help_*`, used only when
the server has *deterministically* established that the turn is help (the student
pressed a help button). The help prompt carries only what help needs: lesson
identity, the visible task and options, the hint level and its ladder contract,
the notation/terminology rules. It deliberately drops the task-authoring contract
(structured package, target difficulty level, starting complexity, difficulty
direction, solve-set authoring, the intent enum), which measurement showed help
turns were receiving in full: 19 634 → 11 024 characters of system instructions
on a sample lesson. Nothing was removed from the generation prompt; every rule
family's reachability is gated by `tests/test_prompt_architecture_gate.py`.

The prefix is stable per (grade, lesson) so OpenAI prompt caching can apply.
The prompt never contains: all 500+ lessons, the raw payload, session ids, or
`client_turn_id`.

---

## Mode pipelines

### Practice (`matbot/practice.py`)

```
payload → guard chain → tutor.lesson_context.build(grade, topic)   ← all 534
          (None ⇒ untrusted curriculum context: safe message, ZERO calls)
        → SessionStore.load(session_id+lesson)      [copy-on-write]
        → click? server decides correctness DETERMINISTICALLY first
          (idempotent retry / completed task / invalid id ⇒ ZERO calls)
        → CALL 1 — Tutor draft (TutorDraft): picks one intent from a closed
          enum, returns reply + only the fields that intent allows
          → per-intent field rule (tutor.schema.validate_final)
            violated ⇒ STOP at one call, nothing to review
        → CALL 2 — Reviewer (ReviewerFinal): independently solves the task,
          runs 10 checks, returns approve | correct | fail_closed.
          A corrected payload IS the final answer — there is no third call.
        → common server validation for every lesson: mathsafe → terminology
          → mathcheck → geometrycheck → option uniqueness/equivalence
        → server shuffles options, assigns ids, commits session (single
          commit point) → next_state to browser
```

**One active pipeline for all 534 lessons.** `matbot/tutor/` contains no lesson
name and no topic ID. What differs per lesson is *context*, not code path:
`LessonContext` merges canonical identity (`data/topics.json`), the frozen
528/528 family mapping (`task_families.py`) and — where one exists — the
declarative contract row. A lesson without a contract is not a special case; it
simply has fewer populated fields.

**Ownership.** The Tutor proposes and the Reviewer disposes; deterministic
server checks then run on top of whatever the Reviewer returned and can still
reject it. The server alone owns option shuffling, option ids, which option is
correct in the browser, the deterministic verdict for a click, and session
state.

**Current production state and rollback.** There is exactly **one** supported
Vjezbajmo engine. The legacy single-call Practice path was **retired on
2026-08-14** — not disabled, deleted. `matbot/practice.py` is a thin boundary
that unconditionally calls `tutor_pipeline.run_turn`; `MATBOT_PRACTICE_PIPELINE`
and `MATBOT_CONTRACT_ENGINE` no longer exist in the code or in
`deploy/production_release.env`, so a stale value left in the VPS `.env` is inert
and cannot resurrect the old path.

**Rollback is Git history**, i.e. redeploying a known-good commit — no longer a
configuration switch. The engine chooses between the deterministic strategy
(zero model calls) and the model route (Luna Tutor + conditional Reviewer, hard
ceiling two calls) from server-owned facts, inside that single engine.

**Measured production routing (HEAD `0c02fca`, exhaustive offline audit).** Of the
534 lessons, **189 across 29 semantic families are served with zero model calls**
today — that figure comes from calling the orchestrator's real routing function in
the production environment, not from the coverage table. The gap to the 352
lessons / 44 families that *have* complete generators is the variety gate: 19
families measured as producing too little task variety (163 lessons) are routed to
the Luna model path instead, with 5 lesson-level exceptions pinned back to
deterministic. Every remaining lesson runs the **GPT-5.6 Luna fast single-call
route**, where the Reviewer is a *conditional* escalation — it fires only when
deterministic preflight proves a defect, repairs on the same fast model, and a
failed repair falls back to the already-validated draft rather than costing the
student a task. The two-call ceiling of rule 4 is unchanged.

**Release configuration is enforced, not hoped for.** Production once ran the
legacy single-call architecture silently for weeks while every release gate
measured the universal one, because two environment variables were simply absent
from the VPS `.env` and nothing failed — `/healthz` stayed green. The guard written
in response (`release_config.require_release_configuration`) was then **never called
by anything**: not by startup, not by the deploy workflow, not by the pre-push hook,
not by a test, while `app.py` claimed "the deploy check decides to fail closed" and
no such check existed. Startup logged a WARNING and served students anyway.

Since this phase there is one declaration, `deploy/production_release.env`, read by
`matbot/release_config.py`, the deploy script, the live release gate and the offline
artifact checker — no value is written in two places. Three independent enforcement
points use it:

| Where | What it does |
|---|---|
| `deploy/apply_release_env.sh` | idempotently writes every declared value into the VPS `.env` on each deploy; existing keys replaced in place, secrets untouched and never printed |
| deploy workflow, twice | `python -m matbot.release_config --require` in a throwaway container *before* the live service is replaced, and again inside the running container |
| `app.py` startup | under `MATBOT_RELEASE_ENFORCEMENT=enabled`, `require_release_configuration()` raises and the worker refuses to boot |

The enforcement flag is opt-in so local work and the test suite still import `app`
without production flags; its own absence in production is caught by the two deploy
checks, which run unconditionally. The fast-model choice stays **code-owned** rather
than an environment value, but is still verified through
`REQUIRED_EFFECTIVE_CONFIG`, which compares the *effective resolved* model — catching
both a wrong environment variable and an unreviewed change to the built-in default.

```
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
 
 
For mapped grade-6 fraction lessons, family identity is followed by a second,
deterministic lesson-level proof. It uses exact `Fraction` arithmetic over the
visible operands to distinguish equal-denominator addition/subtraction,
unlike-denominator work, multiplication, division, expansion, and reducing.
Shared error/word-problem families must engage the same lesson proof; "could not
parse/classify" is an explicit rejection, never a successful empty result.
Numeric answer options are independently normalized and must contain exactly one
ground-truth value. Supported error chains must demonstrate one error category
and exactly one matching explanation. Harder/easier requests select the lesson's
primary family and may vary difficulty only inside that skill. Every rejection
occurs before signatures, shuffle, progression, or session commit and never
causes an automatic model retry.

**Deterministic execution strategy (Phase 4H + capability expansion).** The one
orchestrator selects between two execution strategies using only server-owned
facts: a lesson whose **blocking** semantic contract belongs to a family with a
registered generator that fully `supports()` the contract parameters gets its
structured actions (fresh/new/easier/harder task, MCQ grading, hints, full
solution) served with **zero model calls**. The registry lives in
`matbot/deterministic/__init__.py`; capability engines
(`anglework`, `arithmetic`, `conversions`, `equations`, `fractions`,
`functions`, `geometry` — plane figures, Pythagoras and solids on an exact
`RadicalValue`/`PiValue` number authority (`radicals.py`) —, `numbertheory`,
`ordering`, `polynomials`, `powers`, `quantities`, `ratio`, `systems`,
`units`; Batch #4 added `algfractions` — symbolic rational expressions over
the pure `matbot/mathkernel/` authority —, `wordproblems` (facts-before-prose
structured word problems), `settheory`, `statsdata`, `finance`, `parametric`,
`inequalities`, `properties`, `fractionconcepts`, `similarity`, `polygons`,
`linefacts`) share one core (`matbot/deterministic/core.py`) and *have complete generators for*
**44 semantic families covering 352 lessons (65.9%) across all four grades**
— of which **29 families / 189 lessons are actually served with zero calls in
production**; the rest are routed to Luna by the variety gate (see *Measured
production routing* above)
(source of truth: `data/lesson_semantic_assignments.json`, compiled by
`scripts/build_lesson_semantics.py`; bulk activation table and coverage report:
`scripts/bulk_onboard_deterministic.py` →
`reference/curriculum/semantics/deterministic_coverage_report.json`).

**The math kernel (`matbot/mathkernel/`, Batch #4).** Pure exact solvers with
**no** lesson, MCQ, session, or Practice knowledge — the intended seam for the
future "Daj mi rezultat" mode: `rationalexpr.py` (single-variable polynomials
and rational expressions over ℚ where **domain exclusions are part of
identity** — `(x²-1)/(x-1)` is equivalent to `x+1` only with `x ≠ 1` recorded;
equivalence compares canonical form AND exclusion set, fail-closed when a
denominator does not fully split over ℚ), `wordfacts.py` (structured
word-problem facts solved **before** any prose is rendered; the practice
engine additionally render-audits that every number in the prose is an IR
quantity), and `finiteset.py` (canonical finite-set algebra where {1,2,3} and
{3,2,1} are one object). A permanent test
(`tests/test_batch4_deterministic.py`) forbids the kernel from importing any
Practice module, naming any lesson, or using float authority.
Deterministic packages pass byte-for-byte the same validators and the same
`_publish_task` as model packages; free-form messages, help on a model task,
and all unmapped lessons keep the Tutor+Reviewer path unchanged. Rollback:
`MATBOT_DETERMINISTIC_PRACTICE=disabled`.

### Semantic authority — what "blocking" is allowed to mean

**BLOCKING does not mean "we hope the prompt follows it".** It means: *the
server can independently prove, from the published package, that the lesson's
semantic rule is violated, and it refuses publication if the violation is not
repaired.* Anything weaker is labelled honestly instead.

Every lesson contract in `data/lesson_semantics.compiled.json` carries
`enforcement_mode: blocking` — all 354 of them, across 41 detector names. That
label was written faster than the detectors were: `matbot/semantics/detectors.py`
implements a subset, and `detect()` returns `UNSUPPORTED` for the rest.
`UNSUPPORTED` never rejects, so those contracts were labelled blocking while
proving nothing. The three-state engine was right; the label over-promised.

The more useful measurement is **route**, not label:

| | lessons |
|---|---|
| contract lessons served by a deterministic generator (0 calls) | **189** |
| contract lessons served by the model route | **165** |

On the deterministic route the server builds the package itself, so lesson
drift is structurally impossible and a detector adds nothing — those contracts
are `REDUNDANT` by construction. Only the model route can drift. Both
previously implemented detectors (`fraction_arithmetic`, `polynomial_basic`)
cover *only* deterministic lessons, so **effective blocking coverage on the
drift-prone route was 0 lessons**.

**The measured-quantity dimension primitive** is the first real one. Two live
P1s were the same class of error: a net/surface lesson answered with a *volume*
formula (F5K), and a requested *area* answered with a perimeter — `$P=26$ cm`
instead of `cm²` (D35-5). Neither is a mathematical mistake; both measure the
wrong quantity. That is objectively provable:

```
contract `kinds`          → allowed dimension set   (data)
unit exponent of the      → measured dimension      (cm=1, cm²=2, cm³=3)
  server-marked option
outside the allowed set   → PROVEN violation
```

- The kind→dimension map is **derived, not hand-written**:
  `scripts/build_measure_dimensions.py` samples the deterministic generators of
  the same families and records the exponent each kind actually produces. A kind
  whose exponent is not unanimous across the sample is **not** written to
  `data/semantic_measure_dimensions.json`, and therefore can never block.
- Evidence is the **marked option**, never the task text: the task states what
  is *given* (`a = 16` cm even in an area task), the answer states what was
  *asked*. The detector interface takes `answer_text` for exactly this reason.
- It stays `UNSUPPORTED` — never a rejection — when the answer carries no unit,
  when the lesson may legitimately ask for a unitless count (edges, diagonals,
  angle sums), or when the declared kinds span all three dimensions.
- A lesson that deliberately teaches **both** area and perimeter has allowed
  set `{1,2}`; cm and cm² both pass, cm³ still fails. Uncertainty is never
  converted into rejection, and never into "semantic check passed".

Measured before enabling it: **0 false positives across 39,384 known-good
packages** (31,680 deterministic + 7,704 harvested live), and **100 % detection
of 667 out-of-scope dimension mutations**.

Effective coverage: **54 model-route lessons run the detector, 43 of them with a
contract that genuinely constrains the answer.** The other 11 run it and honestly
report `UNSUPPORTED`. The remaining 18 detector names on the model route stay
`UNKNOWN`: they keep their prompt guidance and every generic validator, but the
server does not claim to enforce them. Current inventory:
`python scripts/build_semantic_authority_report.py` →
`scratchpad/semantic_authority/{detector_matrix,lesson_coverage}.json`, `audit.md`.

**Runtime authority is published separately from the contract label.**
`enforcement_mode: blocking` in `data/lesson_semantics.compiled.json` says what a
contract *requests*; it does not say what the server *can do*.
`data/semantic_authority_status.json` (built by
`scripts/build_semantic_triage.py`) states the latter per lesson —
`IMPLEMENTED` / `REDUNDANT` / `UNKNOWN` plus an explicit
`server_can_refuse_publication` flag — so no future reader can mistake the label
for enforcement. The contract schema is deliberately **not** migrated; a derived
artifact solves the honesty problem without touching data 354 lessons depend on.

**A rejected candidate, kept as evidence (UNKNOWN triage).** The strongest
follow-up primitive was a generic **answer class** check — recognition vs
computed result — reusing the live-proven `hint_policy.value_shaped` classifier,
with the token→class map derived from the deterministic generators exactly as the
accepted dimension map was. It was measured and **rejected**:

| corpus | packages | false blocks |
|---|---|---|
| deterministic known-good | 21,120 | **0** |
| live, model-authored | 3,287 | **48 (1.46 %)** |

All 48 were inspected and all are false — a system of equations as the answer on
an equivalent-systems lesson, a decision plus justification, a symbolic assertion
(`$A=N$`), `"Tačno"` on a verification task. The acceptance bar for a blocker is
zero, so it is **not wired into `DETECTORS`**, and a test keeps it that way.

The transferable rule: deriving a check from the deterministic generator works
for a **unit** — the dimension of the quantity asked is a physical property of the
question — but not for an **answer class**, which is an authoring choice the model
may legitimately make differently. *The deterministic corpus is not a valid
stand-in for model-authored content when proving an author-chosen property.*

**A second rejected candidate: canonical scientific notation** (lesson 8-01-017,
the only `EXACT_PARSED_MATH` rule left). The checker works and is kept unwired in
`detectors.scientific_notation_form`: 16/16 notation variants read correctly,
180 deterministic packages → 156 PASS / 24 UNSUPPORTED / 0 FAIL, 12 live shadow
turns → 2 PASS / 10 UNSUPPORTED / 0 FAIL, and 315/315 mutations caught. It is
conditional on the *answer* being written as `a·10^n`, so the lesson's reverse
direction ("Koliko iznosi $9,9\cdot 10^5$?", whose correct answer is an ordinary
decimal) can never be blocked.

It is still **not enabled**, because the same shadow run published and accepted

```
$2,4\cdot 10^3 = 0,24\cdot 10^4$
```

as a marked answer — where `0,24·10^4` is deliberately non-canonical and the
statement is true. That package survived only because it carries *two* powers of
ten. A task with one non-canonical but correct answer ("which notation equals
2400?", "which is *not* scientific notation?") is equally natural for this lesson
and would have been falsely blocked; 12 turns simply did not hit it. The bar is
zero false blocks and the upside was one lesson, so the rule stays `UNKNOWN`.

**Task diversity has three independent axes.** "Daj mi novi zadatak" must not
return the same exercise with different numbers or names. Three separate
mechanisms enforce that, and the difference between them is deliberate and
measured:

| Axis | Module | Status of a repeat |
|---|---|---|
| **Structural identity** | `matbot/tutor/task_identity.py` (`same_exercise`) | **Defect.** The same exercise with cosmetic substitutions is rejected; if the Reviewer does not repair it, the turn fails closed. |
| **Archetype rotation** | `matbot/archetype_support.py`, `data/task_archetype_support.json` (`MATBOT_ARCHETYPE_ROTATION`) | **Missed preference.** The server names the next archetype in the prompt by LRU rotation, but a repeat never costs the student a task: when it is the only reason to escalate, a failed repair republishes the draft that already passed every deterministic validator. |
| **Form variants** | `matbot/form_variants.py`, `data/task_form_variants.json` (`MATBOT_FORM_ROTATION`) | Second axis, orthogonal to archetype. |

The thresholds are measured, not estimated: over 852 really published tasks, whole-text
overlap ≥ 0.70 was always the same exercise, and because word overlap measures *topic*
rather than *exercise*, the final clause (what the task actually asks) is scored
separately — the canonical pair that must fail shares only 45% of its words but 100%
of its requirement.

**Form coverage is derived from the curriculum, never from a hand list.**
`scripts/build_form_variant_support.py` reads lesson titles and NPP outcomes and
finds exactly **2 grade-6 lessons** that declare both inequality forms. Those
lessons had produced the `x`-first form 20 times out of 20 — structurally
impossible to vary, because the generator chose between `x_plus` and `x_plus`. This
matters mathematically, not cosmetically: in `a − x` the unknown is the
**subtrahend**, so isolating it *reverses* the inequality — precisely what the
lesson teaches. The classifier reads the **published task text** and looks only at
which side the unknown stands on, so swapping sides (`b > x − a`) cannot fake a
different form. A lesson that does not declare `a ± x` never receives it. Measured
after: 5/5/5/5 across 20 tasks, versus 13/7/0/0 before; independent exact-rational
verification over 400 packages found 0 marked-option mismatches and the direction
reversed in every `a − x` task.

**Lesson-relative difficulty profiles (Phase F5G, model route only).** The
1–3 difficulty rubric (`matbot/tutor/schema.py::difficulty_evidence_errors`)
is global by default. Four repeated live collisions (two on the system word
problem lesson, one each on practical Pythagoras and the triangular-pyramid
volume lesson, including a final release gate) proved that for some lessons the
*minimum legitimate task* honestly exceeds the global Level-1 thresholds: a
direct multi-quantity geometry formula needs up to three connected operations,
and a system word problem inherently carries two conditions plus one
representation change. `data/difficulty_profiles.json` therefore declares
**lesson-relative level bounds as data**, keyed by the lesson's **frozen
primary task family** (`matbot/task_families.py`) — never by lesson ID and
never by model prose. `matbot/difficulty_profiles.py` resolves a profile
**only for lessons without a semantic contract** (model route; the 272
deterministic lessons keep the global rubric byte-for-byte) and enforces it
identically in draft preflight, the Reviewer's own-evidence invariant, and
publication. The identical profile text is sent to both the Tutor and the
Reviewer from the data artifact. Lessons without an assigned profile keep the
global rubric unchanged — an easy lesson is never loosened, and Level 2/3
floors start strictly above the profile's Level-1 caps so progression stays
measurable.

### Explain (`matbot/explain.py`)

```
payload → guard chain → lesson_info() → lesson_context_is_strong()
        → build_explain_instructions/_input
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

**Selected-topic relevance (D35-3).** `lesson_relevance.lesson_context_is_strong`
decides deterministically — no model call — whether the selected lesson may shape
the answer. It claims *weak* context **only** when the message names a maths
concept and none of those concepts overlap the lesson's; deictic messages
("objasni mi ovo", "ne razumijem") and every case it cannot prove keep the previous
behaviour. Under weak context the prompt drops the "first explanation of the topic
+ one worked example" rule and the "keep the lesson name" rule, and the lesson
header is relabelled to Quick's wording (`IZABRANA LEKCIJA (kontekst, ne
ograničenje; pitanje NIJE iz nje)`). Conversation history is never dropped in
either branch, so follow-ups keep working. Grade still controls depth only.

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
    one input_image (detail="high"), same model/effort/budget as text but the
    DEDICATED QuickImageTurnOutput schema
  → validate_quick_image_output  (bounded internal field sizes)
  → readability gate             (clear + all symbols visible + high confidence
                                  + no uncertainty, else server-owned message)
  → imagecheck.verify_image_answer   (supported families: publish only on
                                      supported ∧ engaged ∧ verified)
  → the usual mathsafe → terminology → mathcheck → geometrycheck chain
```

The image lives for that turn only: never stored, never written to history, never
re-sent. Logs carry format/width/height/normalized-bytes only — never bytes,
base64, data URL, EXIF, or filename. The prompt explicitly states that any text in
the image is *task content, never an instruction*.

**Dedicated image schema (D35-5/D35-6).** A text-only Quick turn still returns
`QuickTurnOutput{reply}`, byte-compatible with before. An image turn returns
`QuickImageTurnOutput`, which additionally carries `readability`,
`all_required_symbols_visible`, `task_type`, `visible_problem_text`,
`requested_quantity`, `visible_values[]`, `unit`, `answer_confidence` and
`uncertainty_reason`. Those fields exist **only** so the server can decide whether
to publish the answer and, where possible, recompute it independently. They are
transient: never in the browser payload, never in conversation history, never in
`localStorage`, and never logged alongside the transcription — only bounded status
codes and the task-type slug reach the log.

**Dedicated maths evidence (D35T-2).** `visible_math` carries **only** the expression
or equation actually visible in the image — never a heading (`Riješi`, `Izračunaj`,
`Zadatak`, `Odredi`), never a proposed answer, never an inferred value, and empty
when it cannot be read exactly. `visible_problem_text` remains a free-form prose
description for unsupported/general images and is **never** accepted as deterministic
evidence: the live campaign showed the model filling it with the task heading, which
made the expression and equation verifiers silently skip.

**Explicit verification states.** `verify_image_answer` returns
`ImageVerification(supported, engaged, verified, code)`. The four meaningful states
are: family not supported; supported and verified; supported but the required
evidence was missing/unparsable (**not engaged**); supported but mathematically
wrong. For a supported family the answer is published only on
`supported ∧ engaged ∧ verified` — **"the verifier did not engage" is a rejection,
never a pass.** Ground truth is taken only from validated structured evidence
(`visible_values`, `visible_math`), never from the public reply, the proposed
answer, textbook patterns, or the value that would make an equation solvable.

**What this does and does not buy.** The server never sees the image, only what the
model reported seeing, so `imagecheck` proves the arithmetic is consistent with the
*reported* values — it does **not** establish that the transcription itself is
correct. Independent verification exists for `rectangle_area`, `rectangle_perimeter`,
`square_area`, `square_perimeter`, `arithmetic`, `fraction_expression` and
`linear_equation` (by substitution) only. Every other image task is left to the
readability gate plus the generic checks. **Image understanding is not deterministic
in general**, and there is still no OCR and no second model call.

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
| `mathsafe.py` | unbalanced `$`/`$$`, unbalanced `{}`, JSON control chars (`\f`→`\frac`), literal `\n`, bare `sqrt`/`text`, doubled backslashes, stray terminal `}`, a nested/dangling `$` inside an already-open math segment, **any command inside math that is not in `MATHJAX_COMMAND_ALLOWLIST`** (D35-1), and **structural or unknown commands outside math** (D35T-1) | repair if unambiguous (incl. wrapping a standalone `\pi` into `$\pi$`), else strip delimiters / reject the whole answer; the intended command is never guessed | all |
| `mathcheck.py` | numerically inconsistent `=` / `\approx` chains inside `$...$` **or** `$$...$$`, including the `a:b` school-division notation, and π expressions that contradict a π value the same answer explicitly declared (D35-2) (AST whitelist, never `eval`) | reject whole answer | all |
| `imagecheck.py` | image answers whose arithmetic contradicts the values the model reported as visible — rectangle/square area and perimeter, arithmetic and fraction expressions, linear equations by substitution (D35-5). Returns `ImageVerification(supported, engaged, verified, code)`; for a supported family anything short of all three is a rejection (D35T-2) | reject whole answer | quick (image only) |
| `geometrycheck.py` | 11 notation violations (`D`/`d` as prečnik, `R` as poluprečnik, `R` as circumradius, `S` as površina, O/P swap, solid diagonal swap, base-area symbol, pyramid apothem vs edge, circle formula conflicts) | reject whole answer | all (scope from canonical lesson only) |
| `terminology.py` | eight forbidden terms and their declined forms — the Croatian variant of `faktor`, `kutomer`, `jednakokračni`, `zbroj`, `potenciranje`, plus `trokut`→`trougao`, `točan`→`tačan` (D35-3b) and the Croatian word for angle→`ugao` — outside math segments only. (`suma` is deliberately not covered — see [CURRENT_STATE.md](CURRENT_STATE.md) C-8.) | rewrite in place | all |
| `lesson_relevance.py` | the selected Explain lesson overriding a self-contained question from another topic (D35-3) | drop the "teach the lesson" prompt rules for that turn | explain |
| `option_equivalence.py` | two options equal after simplification | reject task | practice |
| `imageinput.py` | oversized upload, decompression bomb, disallowed/spoofed format, degenerate size | 400/413 before any call | quick |
| `quick.py` image gate | an image answer whose reported readability is not `clear`, whose required symbols are not all visible, whose confidence is not `high`, or which reports any uncertainty (D35-6) | discard the proposed maths, return a short server-owned message | quick (image only) |

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
in `.env`, which is never read by tooling or documentation). The subset that decides
**which architecture production runs** is declared once in
`deploy/production_release.env` and enforced — see *Release configuration is enforced*
above and [LIVE_RELEASE_GATE.md](LIVE_RELEASE_GATE.md#required-production-configuration)
for the full table.

`OPENAI_MODEL_TEXT` (default `gpt-5-mini`) · `MATBOT_REASONING_EFFORT` (default `low`)
· `AI_TUTOR_TIMEOUT` (**45 s in production**; the built-in default is 30 and the
release gate now applies and records the production value, after a campaign passed
at 30 while production ran at 45) · `MATBOT_MAX_OUTPUT_TOKENS` (1200, used by Quick only)
· `MATBOT_MAX_OUTPUT_TOKENS_PRACTICE` (2500, hard-ceilinged at 4000)
· `MATBOT_MAX_OUTPUT_TOKENS_EXPLAIN` (2500, same hard ceiling — added 2026-08-01,
default not yet live-validated, see [CURRENT_STATE.md](CURRENT_STATE.md) C-9)
· `MATBOT_MAX_MESSAGE_CHARS` · `MATBOT_MAX_HISTORY_ITEMS` / `_CHARS_PER_ITEM`
· `FLASK_SECRET_KEY` (or legacy alias `SECRET_KEY`; the app refuses to start without one)
· `MATBOT_TOKEN_TTL_SECONDS` · `MATBOT_SESSION_LIMIT_PER_MINUTE`/`_HOUR`
· `MATBOT_IP_LIMIT_PER_MINUTE`/`_HOUR`.

Image limits are deliberately **hard constants** with no env override, so a
mis-set variable cannot raise a security boundary.
