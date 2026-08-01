# MAT-BOT — testing strategy

## Baseline

**1308 tests, all passing**, across 47 files in `tests/`. Every test runs offline:
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
- `call_count == 1` on every path, happy and unhappy.

They **cannot** prove that the model obeys a rule. A test asserting "the answer
stays under 140 words" against a `FakeLLM` asserts only that the fixture author
typed a short string. Assert that the *rule text is sent*, and measure obedience live.

### 2. Unit tests

Pure functions, no Flask, no LLM: `mathsafe`, `mathcheck`, `geometrycheck`,
`geometry_rules`, `terminology`, `option_equivalence`, `systemcheck`,
`task_family_validation`, `topics`, `ratelimit`, `turnlock`, `session_store`.

Every verifier needs **both** directions:

- **true positives** — the exact live-observed bad output is rejected,
- **false-positive guards** — realistic *correct* output is passed through
  byte-identically.

The second is the one that gets forgotten and the one that breaks production.

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
