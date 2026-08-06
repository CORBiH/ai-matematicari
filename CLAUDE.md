# CLAUDE.md — working agreement for MAT-BOT

Read this before touching anything. It exists so a fresh Claude/Codex session does
not have to re-derive the project's rules from the code.

MAT-BOT is a Bosnian-language maths tutor for grades 6–9 (osnovna škola, BiH).
Flask + a single-page frontend, one OpenAI call per turn, no database.

**Start here:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) ·
[docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) ·
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

---

## Hard rules

1. **Never make real OpenAI calls** unless the user explicitly authorizes them for
   that specific task, with a count. Tests use `FakeLLM` (`tests/conftest.py`).
   A "quick sanity call" is not authorized by default.
2. **Never commit, push, tag, or deploy unless asked.** Pushing to `main`
   auto-deploys to the production VPS (`.github/workflows/deploy-vps.yml`).
   There is no staging environment.
3. **Never read, write, print, or paste `.env` values, API keys, or secrets.**
   Refer to environment variables by *name* only.
4. **Bounded model calls per application turn — the limit depends on the mode.**
   - **Explain and Quick: exactly one call.** Unchanged.
   - **Practice: at most exactly two** — Tutor draft, then an independent
     Reviewer that finalizes. This replaced the former one-call rule in the
     universal-pipeline pivot; the Reviewer is *not* a retry or a repair call,
     it is a separate verification stage whose output **is** the published
     answer.

   In every mode: no retries, no repair loop, no third call, no SDK auto-retry,
   no hidden replacement call. If output is bad, reject it and return the canned
   safe message. A rejection never costs an extra call. See
   [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#the-two-call-practice-boundary).
5. **All 534 lessons use ONE active Practice pipeline** — the universal
   two-call Tutor+Reviewer path (`matbot/tutor/`). There is no separate active
   orchestration for "contracted" vs "legacy" lessons. Curriculum metadata and
   the 528/528 family mapping are **lesson context fed into one prompt**, never
   a second execution branch. No lesson-ID branching in `matbot/tutor/`.
   See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

   *Amendment (Phase 4H):* the single orchestrator remains mandatory, but it
   may select between **execution strategies** behind itself: the model-backed
   Tutor+Reviewer strategy, and a **deterministic strategy**
   (`matbot/deterministic/`) for structured actions on lessons whose blocking
   semantic family has a complete server-owned generator. Strategy selection
   uses only server-owned facts (lesson family, UI fields, a closed message
   set) — never model prose and never lesson IDs. Deterministic packages pass
   the byte-for-byte same validators and the same `_publish_task` as model
   packages; there are **never** two competing session/publication pipelines.
   Deterministic turns make **zero** model calls; model turns keep the exact
   two-call bound of rule 4. Rollback: `MATBOT_DETERMINISTIC_PRACTICE=disabled`.
5b. **The Reviewer is the main semantic scope gate.** It independently solves
   generated tasks before approving, and returns either the approved payload, a
   corrected complete payload (which is final — no third call), or
   `fail_closed`. Deterministic server checks still run on top of whatever the
   Reviewer returns: math safety, terminology, numeric consistency, geometry
   notation, and option uniqueness can reject a task the Reviewer approved.
5c. **Practice difficulty never changes lesson identity.** Easier/harder keep
   the lesson's skill and move only difficulty dimensions. The Tutor must report
   which dimensions moved (`difficulty_diagnostics`) and the Reviewer verifies
   the direction. Those diagnostics are **internal**: logged, never shown.
5d. **The deterministic K1/K3 generator is preserved, not active.**
   `matbot/contracts/generator.py` and its tests stay green and are reachable
   through `MATBOT_PRACTICE_PIPELINE=legacy_single_call` (rollback only). Do not
   expand it, and do not reintroduce it as a second active top-level branch.
5a. **Adding a normal supported lesson must change data, not Python.** Do not add
   a per-lesson validator, a per-lesson family list, a topic-ID branch, or a
   lesson name inside `matbot/contracts/`. `tests/test_contract_architecture_gate.py`
   enforces this; genuine exceptions must be registered in
   [docs/LESSON_CONTRACTS.md](docs/LESSON_CONTRACTS.md).
6. **Do not re-run the full suite repeatedly.** It is ~1500 tests; run it once
   at the end, and run a single file while iterating.
7. **Never leak internal codes to the browser.** Validator issue codes
   (`numeric_equality_mismatch`, `circle_diameter_uses_D`, `llm_schema_parse_error`,
   `unknown_mathjax_command:…`, `image_rectangle_value_mismatch`,
   `image_math_source_missing`, …) go to logs only.
   The student sees `SAFE_ERROR_MESSAGE` or nothing. The image turn's internal
   fields (`readability`, `visible_math`, `visible_problem_text`,
   `answer_confidence`, …) are the
   same: server-only, never in the payload, history, `localStorage`, or a log line
   carrying content.

## Conventions

- **Code comments are in Bosnian.** Match the surrounding style: comments explain
  *why*, usually citing the live finding that forced the code to exist. Keep that
  habit — it is the project's main institutional memory.
- **Output language is Bosnian (ijekavica).** `matbot/terminology.py`
  deterministically enforces nine terms (all declined forms, outside math
  segments only): `faktor` not the Croatian variant, `uglomjer` not `kutomer`,
  `jednakokraki` not `jednakokračni`, `zbir` not `zbroj`, `stepenovanje` not
  `potenciranje`, `trougao` not the Croatian variant, `tačan` not the Croatian
  variant, `ugao` not the Croatian variant, and `presjek` not the ekavian
  variant (Live96 call 551). `suma`→`zbir` is **deliberately not enforced** — see
  `docs/CURRENT_STATE.md` C-8: "suma" also means "amount of money" in word
  problems, and a blanket regex swap would corrupt that legitimate usage. A
  repo-wide test forbids all covered terms from appearing anywhere except
  the files that declare or document the ban (`matbot/rules.py`,
  `matbot/terminology.py`, `matbot/lesson_relevance.py`,
  `tests/test_terminology.py`, `tests/test_rules.py`,
  `tests/test_lesson_relevance.py`, this file, `docs/CURRENT_STATE.md`,
  `docs/ARCHITECTURE.md`) — so don't spell one out casually in a new comment or
  doc without adding it to that allow-list too.

- **Model-produced MathJax is allowlisted, not merely sanitized.** Only commands
  in `mathsafe.MATHJAX_COMMAND_ALLOWLIST` may appear inside `$…$`. An unknown
  control word (`\ty`) or a residual doubled backslash before a command fails
  closed to `SAFE_ERROR_MESSAGE` — the intended command is **never** guessed.
  If you introduce new notation, add the command to that frozenset; that is the
  supported way to widen it. Student input is never rewritten by this layer.

- **"Valid inside `$…$`" and "allowed outside `$…$`" are different questions.**
  Do **not** reuse `MATHJAX_COMMAND_ALLOWLIST` as the outside-math reject list —
  doing so once destroyed a fully correct answer because its prose read
  `LEKCIJA: Broj \pi …` (D35T-1). Outside math there are three classes:
  a **standalone symbol** (`mathsafe._STANDALONE_SYMBOL_COMMANDS`: `\pi`, Greek
  letters, relations — no arguments) is narrowly wrapped into `$\pi$`; a
  **structural** command (`\frac`, `\sqrt`, `\text`, `\mathbb`, `\cdot`,
  `\begin`, `\end`) still fails closed, except the isolated `\frac{a}{b}` that
  `wrap_isolated_frac_tokens` can safely wrap; an **unknown** command always
  fails closed.

- **π approximations must be internally consistent.** When an answer explicitly
  declares a value (`π≈3,14`), `mathcheck` evaluates every π expression in that
  answer with *only* the declared value. Without a declaration both `math.pi` and
  `3.14` stay acceptable, as before.

- **Explain does not force the selected lesson onto an unrelated question.**
  `matbot/lesson_relevance.py` decides deterministically (no model call). It claims
  weak context only when the message names a maths concept that does not overlap
  the lesson's; deictic messages and unprovable cases keep the previous behaviour.

- **Result mode answers direct clock-time questions.** A valid `HH:MM` plus a fixed
  time phrase is in scope; `60:15` in a calculation stays division. If the model
  still returns the generic off-topic refusal, the server substitutes a
  deterministic answer **after** the single call — never a second call.

- **Image turns use their own schema and fail closed.** See the image rows in
  `docs/ARCHITECTURE.md`. Only a `clear` / all-symbols-visible / `high`-confidence /
  no-uncertainty image may reach the browser, and only a short list of task families
  gets independent verification (`matbot/imagecheck.py`). Image understanding is
  **not** deterministic in general — do not describe it as such, and never claim the
  model's transcription is independently known to be correct.

- **For a supported image family, "the verifier did not engage" means FAIL CLOSED.**
  `imagecheck.verify_image_answer` returns `ImageVerification(supported, engaged,
  verified, code)` — never a bare list, because an empty list once meant both
  "verified" and "had nothing to check" and let a wrong answer through (D35T-2).
  Publication requires `supported ∧ engaged ∧ verified`. Deterministic ground truth
  comes only from validated structured evidence (`visible_values`, `visible_math`) —
  never from the model's public reply, its proposed answer, textbook patterns, or
  "the value that makes the equation solvable". `visible_problem_text` is prose and
  is **never** trusted as evidence for a supported family.
- **Geometry notation is project-specific and deliberately non-standard:**
  `R` = prečnik (diameter), `r` = poluprečnik, `R = 2r`; `d`/`d_1`/`d_2` = dijagonala
  and **never** diameter; `r_o`/`r_u` = opisana/upisana kružnica radii; `P` = površina
  (never `S`); `O` = obim. See `matbot/geometry_rules.py`.
- **MathJax:** the model is instructed to use single `$...$` only (for verifier
  consistency, not because the frontend can't render `$$...$$` — it can). The
  sanitizer/verifier pipeline (`matbot/mathsegments.py`) correctly handles both
  as a defensive net, but don't rely on `$$...$$` as an authoring convention;
  see [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md).
- **Client input is never trusted.** `selected_oblast` from the client is discarded;
  the canonical lesson/oblast come from `data/topics.json` via `matbot/topics.py`.

## Where things live

| Concern | File |
|---|---|
| HTTP routing, guard chain, mode dispatch | `matbot/api.py` |
| Payload validation (grade/mode/ids/history/topic) | `matbot/validation.py` |
| Practice turn orchestration + session state | `matbot/practice.py`, `matbot/session_store.py` |
| Explain turn orchestration (stateless) | `matbot/explain.py` |
| Result/Quick turn orchestration (stateless) | `matbot/quick.py` |
| Shared maths/language/notation prompt rules | `matbot/rules.py` |
| Mode-specific prompt assembly | `matbot/prompts.py` |
| Universal lesson-contract engine (Practice) | `matbot/contracts/` |
| Deterministic execution strategy (family generators) | `matbot/deterministic/` |
| Lesson contract data (no Python per lesson) | `data/contract_templates.json`, `data/lesson_contracts.json` |
| Geometry symbols, formulas, topic routing | `matbot/geometry_rules.py` |
| The only OpenAI call site | `matbot/llm.py` |
| Strict output schemas (incl. the image-only Quick schema) | `matbot/schema.py` |
| Explain selected-lesson relevance | `matbot/lesson_relevance.py` |
| Deterministic image-answer verifier | `matbot/imagecheck.py` |
| Shared `$...$`/`$$...$$` tokenizer (text/inline/display segments) | `matbot/mathsegments.py` |
| MathJax safety + repair | `matbot/mathsafe.py` |
| Numeric-consistency verifier | `matbot/mathcheck.py` |
| Geometry-notation verifier | `matbot/geometrycheck.py` |
| Linear-system substitution verifier (Practice only) | `matbot/systemcheck.py` |
| Terminology normalizer | `matbot/terminology.py` |
| Image upload validation/normalization | `matbot/imageinput.py` |
| Auth token, rate limits, per-session lock | `matbot/auth.py`, `matbot/ratelimit.py`, `matbot/turnlock.py` |
| Entire frontend (UI, transport, rendering) | `templates/index.html` |
| Curriculum data | `data/topics.json` (built by `scripts/build_topics_json.py`) |

## How to make a change

1. Read the relevant module top-to-bottom first. The docstrings carry the reasons
   behind non-obvious code; changing code without reading them re-introduces
   already-fixed bugs.
2. Prefer the **smallest isolated change**. This codebase deliberately favours
   many narrow deterministic guards over one general engine.
3. If a guard cannot prove something, it must **skip**, not guess. "Skipped" is
   explicitly documented as *not* evidence of correctness.
4. Add tests in the same pass. New deterministic behaviour needs a `FakeLLM` test;
   new prompt text needs an assertion that the text is actually sent.
5. Run `python -m pytest -q` once. Report the real numbers, including failures.
6. Do not restructure modes that are not part of the task. Practice, Explain and
   Quick share `rules.py` and `mathsafe.py` — a change there touches all three.

## Definitely do not

- Build a general symbolic maths engine or theorem prover. Out of scope by decision.
- Add a second OpenAI call to "fix" a bad first response.
- Use `eval()` on model output. The verifiers use AST whitelists for a reason.
- Widen `MAX_*` limits without stating the cost/risk trade-off.
- Store student text, images, or conversation history server-side. Nothing is persisted.
- Add `-p no:logging` to pytest — it removes `caplog` and fabricates setup errors.
