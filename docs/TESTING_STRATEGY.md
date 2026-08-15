# MAT-BOT — testing strategy

## Baseline

**1465 tests, all passing**, across 50 files in `tests/`. Every test runs offline:
no test may reach the network.

```bash
python -m pytest -q                       # full suite — run ONCE, at the end
python -m pytest tests/test_explain.py -q  # while iterating
python -m pytest --collect-only -q | tail -1   # refresh the baseline count
```

When the count changes, update the number in this file and in
[CURRENT_STATE.md](CURRENT_STATE.md) in the same commit.

### Traps

- **Never pass `-p no:logging`.** It removes the `caplog` fixture and fabricates
  six setup ERRORs that have nothing to do with the code. Run pytest plainly.
- `tests/conftest.py` sets a clearly-labelled insecure `FLASK_SECRET_KEY`. That is
  the only place a test secret may exist.
- The `flask_app` fixture installs fresh rate limiters and a fresh lock registry per
  test with deliberately huge limits — without that, unrelated tests spend each
  other's rate-limit budget through the shared Flask app singleton.

---

## The four layers

Keep them separate. Mixing them is how a suite starts silently asserting model
behaviour it cannot control.

### 1. Deterministic `FakeLLM` tests

`FakeLLM` (`tests/conftest.py`) returns queued outputs or raises queued exceptions,
counts calls, and records every `(instructions, input_text)` pair.

These prove **server behaviour only**:

- the prompt actually contains what we think it contains (canonical lesson, oblast,
  history, the specific rule text),
- the response is parsed, sanitized and shaped into the frontend contract,
- rejection paths return the canned safe message with no `status`/`next_state`,
- the exact call budget on every path, happy and unhappy: `== 1` for Explain and
  Quick, `<= 2` for Practice (Tutor + Reviewer), `== 0` when the turn is blocked
  before the model.

**Which pipeline a Practice test exercises is explicit.** Practice has one
active path (universal two-call). Test modules listed in
`conftest._FROZEN_SINGLE_CALL_MODULES` are pinned to the frozen single-call path
via an autouse fixture, because that is what they were written against — each
encodes a live-found regression of that path. They prove nothing about the
active path; active-path coverage lives in
`tests/test_universal_tutor_pipeline.py`. When you add a Practice test, decide
deliberately which of the two it belongs to.

They **cannot** prove that the model obeys a rule. A test asserting "the answer
stays under 140 words" against a `FakeLLM` asserts only that the fixture author
typed a short string. Assert that the *rule text is sent*, and measure obedience live.

### 2. Unit tests

Pure functions, no Flask, no LLM: `mathsafe`, `mathcheck`, `geometrycheck`,
`geometry_rules`, `terminology`, `option_equivalence`, `answer_kind`,
`contracts.{schema,verifiers,
difficulty,generator,intent}`, `topics`, `ratelimit`,
`turnlock`, `session_store`.

Every verifier needs **both** directions:

- **true positives** — the exact live-observed bad output is rejected,
- **false-positive guards** — realistic *correct* output is passed through
  byte-identically.

The second is the one that gets forgotten and the one that breaks production.

Lesson-contract tests must cover both the primary and every shared family that
can be selected for that lesson. Include exact live regressions, equal-vs-unlike
operands, multiplication/division/expansion/reducing relevance,
`engaged=False` fail-closed behavior, normalized multiple/no-correct answers,
classified error ambiguity, and integration assertions that rejection mutates no
state, progression, or task signature. Harder and easier
tests must assert the curriculum fingerprint is unchanged and the primary family
is preferred. `tests/test_practice_lesson_semantic_contracts.py` is the focused
reference suite.

Because the server now **generates** the contracted task, engine tests are
**property + golden**, not fixture-echo:

- *property* — for a range of seeds, every generated skeleton must satisfy its
  own contract (operations, sign policy, ranges, denominator relation, scaling
  direction, difficulty bounds), have exactly one option equal to the truth,
  and carry pairwise-distinct option values and texts;
- *golden* — one frozen seed per capability, so a generator change that alters
  existing tasks shows up as an intentional diff
  (`test_31_golden_skeleton_stays_stable`);
- *fidelity gate* — model prose that invents a number is dropped without killing
  the turn (`tests/test_contract_intent.py`).

A fixture that hand-writes a task for a contracted lesson now proves nothing:
that content is discarded. Assert on what the **server** produced.

### 3. API / SSE tests

Flask test client + `FakeLLM`, exercising `/chat` and `/chat/stream`:
happy path · 401 no token · 429 both buckets · 409 concurrency · 400 unknown topic
· 413 oversized body · canned refusals (image in a non-quick mode, empty message)
· SSE frame shape (`event: done\ndata: {…}`) · blocking statuses returned as plain
JSON, not as SSE · and in every failure case `call_count == 0` where no call should
have happened.

### 4. Focused live calls

**Not authorized by default.** Requires an explicit request from the user naming
the purpose and the call count. Rules:

- Smallest useful campaign, never a 200-call sweep.
- Model behaviour here is **stochastic**. Record a *rate* (failures per N), never
  "it worked once". A single green call proves nothing.
- Never paste raw campaign output into the repository. Summarize findings; keep
  numbers, drop transcripts.
- Log-derived evidence only: `usage`, `latency_ms`, category codes.

---

## Explain fix-verification tests (implemented 2026-08-01)

The confirmed-defect fixes from the 2026-08-01 pass each shipped with their
own tests, in the existing files rather than one new matrix file:

- `tests/test_mathjax_display_support.py` (new) — inline/display segmentation,
  mixed blocks, dangling/nested delimiters, byte-identical passthrough for
  well-formed `$...$` and `$$...$$`, tokenizer round-trip.
- `tests/test_mathcheck.py` (extended) — `a:b` division true positives, ratio
  false-positive guards, decimal-comma division, division-by-zero, prose-time
  and prose-punctuation exclusion (colons outside math are never even seen by
  `mathcheck`, since it only reads `$...$`/`$$...$$` content).
- `tests/test_explain.py` (extended) — transport-normalization parity with
  Quick, and the position-dependent history budget (latest-assistant
  tail-preservation, latest-user/older-item head-preservation, total-budget
  ceiling, MathJax-safe cuts, message ordering, malformed/empty history).
- `tests/test_explain_mode_isolation.py` (new) — static wiring checks (no
  browser/DOM in this repo, see `test_frontend_retry_ux.py`'s precedent) that
  every `LASTTASK_KEY`/`interactionPhase` read-or-write site is gated by the
  shared `modeTracksPracticeTask` guard.
- `tests/test_llm_failure_categories.py` (extended) — `MAX_OUTPUT_TOKENS_EXPLAIN`
  is used by Explain, Quick's and Practice's budgets are unchanged, and the
  hard-ceiling/invalid-env-value fallback behavior.
- `tests/test_terminology.py` (extended) — all declined forms of the four
  newly-covered terms, capitalization preservation, math-segment immunity,
  and an explicit test that `suma`-meaning-"amount" is never rewritten.

**Not yet built** (still planned, since C-6 itself was deliberately left as a
design note, not implemented — see [CURRENT_STATE.md](CURRENT_STATE.md)):

- An SSE-then-fallback regression asserting the fallback cannot increment
  `call_count` twice for one user turn — write this alongside whichever C-6
  option from [EXPLAIN_REQUEST_IDEMPOTENCY.md](EXPLAIN_REQUEST_IDEMPOTENCY.md)
  is eventually implemented.
- A full (grade × topic-class) Explain content matrix
  (`tests/test_explain_matrix.py`) — fractions/decimals/percentages/equations/
  systems/powers/number-sets/geometry/units/word-problems across grades 6–9.
  Useful for future regression coverage but not required to verify the
  confirmed-defect fixes above, which is why it wasn't built in this pass.

## D35 fix-verification tests (implemented 2026-08-01)

The six confirmed defects from the 35-call live campaign each shipped with tests.
Regression cases are pinned to the **exact strings observed live** (a real TAB
character from call 10, a doubled backslash before a digit from call 12, the π
sentence from call 19), so a future refactor cannot quietly reopen them.

- `tests/test_mathjax_commands.py` (new) — allowlisted commands pass; unknown
  control words fail closed; the control-character reconstruction only fires for a
  real command; doubled backslash before a digit is repaired, before an unknown
  command rejected, and never globally stripped; braces/arguments survive; the same
  protection is asserted in all three modes and at the HTTP payload boundary.
- `tests/test_pi_consistency.py` (new) — no declaration keeps the permissive
  behaviour; a declared value is enforced in both directions; declarations are
  found in prose *and* math; a product like `2π≈6,28` is not misread as a
  declaration; an implausible declared value is ignored rather than guessed;
  exact symbolic `6π` is never rejected; negative coefficients and both decimal
  separators; an AST-level assertion that no `eval`/`exec` was introduced.
- `tests/test_lesson_relevance.py` (new) — classifier in both directions, prompt
  assertions that the unrelated question drops the first-explanation and
  lesson-name rules while a deictic message keeps them, history preserved in both
  branches, and the new terminology pairs including the near-miss words that must
  never be touched (the noun for a dot, the noun for a wheel, and an unrelated
  adjective sharing the same stem).
- `tests/test_clock_time.py` (new) — valid 12/24-hour and leading-zero times;
  invalid hour/minute, division, ratio, URL colon and prose colon all excluded;
  the server fallback only replaces the generic refusal, keeps a real model answer,
  and uses exactly one model call.
- `tests/test_image_contract.py` (new) — text-only schema unchanged; image schema
  fields fixed; `llm.quick_turn` picks the schema by image presence; every gate
  branch (partially unreadable, unreadable, missing symbol, medium/low confidence,
  reported uncertainty, multiple tasks, non-math); internal fields absent from
  payload, state and logs; all eight rectangle/square cases; fraction, arithmetic
  and linear-equation verification in both directions; one-call and
  no-state-persistence invariants.
- `tests/test_result_image.py` (updated) — the existing image tests now queue the
  dedicated schema via the new `make_quick_image_output` fixture, whose defaults
  ("clear image, model confident") reproduce the previous behaviour exactly.
- `tests/test_terminology.py` (extended) — the repo-wide forbidden-term guard now
  also covers the two terms added in this pass (named in
  [CURRENT_STATE.md](CURRENT_STATE.md) D35-3b); `matbot/lesson_relevance.py` and
  `tests/test_lesson_relevance.py` are added to its allow-list because the
  classifier must recognise the Croatian form in **student input**, which is never
  normalized. Note this file is *not* on that allow-list, which is why the terms
  are referenced here rather than spelled out — the guard enforces that.

## D35T fix-verification tests (implemented 2026-08-02)

The 14-call targeted campaign confirmed two defects; both fixes are pinned to the
exact live strings.

- `tests/test_mathjax_commands.py` (extended) — the outside-math policy is now
  proven separate from the inside-math allowlist: a standalone `\pi` in prose is
  wrapped into `$\pi$`, already-delimited `$\pi$` stays byte-identical, structural
  commands (`\sqrt`, `	ext`, `\mathbb`, `egin`) and unknown commands still fail
  closed outside math, the isolated `rac{a}{b}` is still wrapped, and the exact
  call-3 reply is no longer falsely rejected. Plus the narrow degree normalization
  and a structural assertion that student input never reaches the sanitizer.
- `tests/test_image_contract.py` (extended) — every verification state is asserted
  separately (`supported`/`engaged`/`verified`), including an explicit test that an
  empty result can no longer mean both "skipped" and "verified"; heading-only and
  missing/unparsable evidence are rejections; `visible_problem_text` is proven
  unusable as evidence even when it happens to contain the right expression; and
  the internal `visible_math` never reaches payload, state or logs.
- `tests/test_lesson_relevance.py` / `tests/test_clock_time.py` (extended) — the
  optional cleanups: the Croatian angle term normalizes while the near-miss words
  (box, corner, scooter, and the protractor term that already has its own earlier
  rule) stay untouched, and a bare `$12:30$` reply is replaced by plain prose while
  a real explanation is left alone. This file is **not** on the forbidden-term
  allow-list, which is why those words are described rather than spelled out.

## Explain focused live campaign (still not authorized)

Layer 4, when explicitly authorized — **18 calls total**, unchanged from the
audit's recommendation, still not run:

| Purpose | Calls |
|---|---|
| Token-budget sizing (C-9): longest realistic Explain answers, grades 6 and 9, 2 runs each — validates the new `MAX_OUTPUT_TOKENS_EXPLAIN=2500` default | 4 |
| Follow-up context after the C-2 fix: "objasni drugi korak" over a long previous answer, 2 topics | 4 |
| `a:b` verifier false positives on real answers (decimals + proportions lessons) | 4 |
| Grade-appropriateness spot check (6 vs 9, same conceptual question) | 2 |
| Geometry notation on a real circle lesson and a real solid lesson | 2 |
| Off-topic question inside a selected lesson | 2 |

## D35 targeted live validation (still not authorized)

The deterministic halves of the D35 fixes are locked by the tests above. Three
behaviours remain prompt-dependent and can only be confirmed with real calls:

| Purpose | Why a fake test cannot prove it |
|---|---|
| The model actually populates the new image structured fields honestly — especially reporting `partially_unreadable` / non-`high` confidence for a deliberately obscured value instead of claiming `clear` | A fixture asserts only what its author typed. If the model lies in the structured fields, the gate passes a guess through. |
| Explain respects the weak-lesson-context prompt when the question is from another topic | We can prove the rule text is sent; obedience is model behaviour. |
| The clock-time bullet stops the generic refusal at the source, rather than relying on the server fallback | The fallback masks non-compliance; only live output shows whether the prompt fixed it. |

Measure a **rate** over N runs, not a single green call — these are stochastic.

---

## Writing a good test here

1. Name the live finding that motivated it, in the docstring, in Bosnian, like the
   surrounding code does. That is how this project keeps from re-breaking things.
2. Assert the *contract*, not the phrasing: `status`, `answer_verdict`,
   `last_tutor_task`, `next_state`, `call_count`, and whether internal codes leaked.
3. For anything a validator rejects, also assert the **whole** answer was discarded
   and no session mutation occurred.
4. For any new prompt rule, assert the rule text reaches `instructions` — that is
   the only thing a fake test can honestly prove.
