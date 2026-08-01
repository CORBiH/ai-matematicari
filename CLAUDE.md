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
4. **One model call per application turn is an architectural invariant.**
   No retries, no repair calls, no "second opinion" calls. If output is bad,
   reject it and return the canned safe message. See
   [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#the-one-call-invariant).
5. **Do not re-run the full suite repeatedly.** It is ~1308 tests; run it once
   at the end, and run a single file while iterating.
6. **Never leak internal codes to the browser.** Validator issue codes
   (`numeric_equality_mismatch`, `circle_diameter_uses_D`, `llm_schema_parse_error`, …)
   go to logs only. The student sees `SAFE_ERROR_MESSAGE` or nothing.

## Conventions

- **Code comments are in Bosnian.** Match the surrounding style: comments explain
  *why*, usually citing the live finding that forced the code to exist. Keep that
  habit — it is the project's main institutional memory.
- **Output language is Bosnian (ijekavica).** `matbot/terminology.py`
  deterministically enforces five terms (all declined forms, outside math
  segments only): `faktor` not the Croatian variant, `uglomjer` not `kutomer`,
  `jednakokraki` not `jednakokračni`, `zbir` not `zbroj`, `stepenovanje` not
  `potenciranje`. `suma`→`zbir` is **deliberately not enforced** — see
  `docs/CURRENT_STATE.md` C-8: "suma" also means "amount of money" in word
  problems, and a blanket regex swap would corrupt that legitimate usage. A
  repo-wide test forbids all five covered terms from appearing anywhere except
  the files that declare or document the ban (`matbot/rules.py`,
  `matbot/terminology.py`, `tests/test_terminology.py`, `tests/test_rules.py`,
  this file, `docs/CURRENT_STATE.md`) — so don't spell one out casually in a
  new comment or doc without adding it to that allow-list too.
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
| Geometry symbols, formulas, topic routing | `matbot/geometry_rules.py` |
| The only OpenAI call site | `matbot/llm.py` |
| Strict output schemas | `matbot/schema.py` |
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
