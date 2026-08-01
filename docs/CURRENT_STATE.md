# MAT-BOT — current state

Last updated: 2026-08-01 (Explain fix pass). Test baseline: **1308 passing**.
Runtime model: `gpt-5-mini`, reasoning effort `low`.

## Maturity by area

| Area | State |
|---|---|
| Practice | Hardened. Extensive deterministic validation: family contracts, option equivalence, ordered-pair and equivalent-system substitution, numeric and geometry checks, idempotent choice retry. |
| Result / Quick | Hardened for text and for secure image input. Transport-level MathJax over-escaping repair, conversational-repair handling, context-free by design. |
| Image upload | Hardened. Bounded in-memory read, pixel-bomb guard, format sniffing, re-encode, strict metadata-only logging. Quick mode only. |
| Security / transport | Hardened. Signed token, two-tier rate limiting, per-session lock, ProxyFix, body-size cap, no secret ever logged. |
| **Explain** | **Audited 2026-08-01; 8 of 11 confirmed defects fixed the same day.** See below — C-6 is a deferred design-only note, C-9's default is unmeasured live, C-7 is correct as-is. |
| Exam ("Kontrolni") | Routed through the practice path; not separately audited. |

---

## Explain risk register

Severity: **P1** wrong mathematics reaches the student · **P2** safe rejection,
broken context, or significant UX damage · **P3** terminology, formatting, clarity.

### Fixed (2026-08-01, same pass as the audit)

| ID | Sev | Finding | Fix |
|---|---|---|---|
| C-3 | P2 | Malformed MathJax reached the browser: `$$P=\frac{a\cdot h}{2}$$` became `$$P=$\frac{a\cdot h}{2}$$$`. | New shared tokenizer `matbot/mathsegments.py` (`tokenize_math`/`join_segments`) properly distinguishes `$...$` from `$$...$$` in one left-to-right scan, replacing the naive alternating-`$`-split every sanitizer used. `matbot/mathsafe.py` now classifies content correctly on both sides. |
| C-4 | **P1** | `mathcheck` was blind to `a:b` division — the notation this project mandates. `60:15=5` silently passed. | `matbot/mathcheck.py::_latex_to_python` now translates `:` to `/`; false-positive-tested against ratios, decimal-comma division, and division-by-zero (still routed through the existing `_MathError` path, never `eval()`). |
| C-5 | **P1** | `mathcheck` never inspected `$$…$$` content at all (`math_segments` returned nothing for display math). | Same tokenizer as C-3: `math_segments()` now returns content from both inline and display segments. |
| C-1 | P2 | Practice's `LASTTASK_KEY`/`interactionPhase` leaked into Explain/Quick after a chip-driven mode switch, so "ne razumijem"/"objasni" was misread as a Practice answer. | `templates/index.html`: new shared guard `modeTracksPracticeTask(mode)`; every read/write site (`replayTutorHistory`, the chip switch, the explain→practice switch, `answerPhase` detection, `applyTutorResponse`'s re-arm) now uses it. |
| C-2 | P2 | A previous 4000-char tutor answer entered the next prompt at 250 chars — the part a follow-up usually asks about (the final result, the last step) was the first thing cut. | `matbot/prompts.py::build_explain_input` now gives the **latest assistant message** up to 1200 chars (tail-preserving — keeps the *end*, never splits `$...$`/`$$...$$`/`\frac{}{}`), the **latest prior user message** up to 600, older items stay at 250. `matbot/explain.py::_clean_history`'s raw per-item ceiling now matches `config.MAX_HISTORY_CHARS_PER_ITEM` instead of truncating before the smart logic ever runs. |
| C-9 | P2 | Explain shared Practice's old 1200-token budget while allowing a 3.3× longer reply. | New `config.MAX_OUTPUT_TOKENS_EXPLAIN` (default 2500, same hard ceiling as Practice); `llm.py::explain_turn` passes it explicitly. **The 2500 default itself is not yet live-validated** — see Known limitations. |
| C-10 | P3 | `\$…\$` over-escaping was repaired in Quick but rejected outright in Explain. | `matbot/explain.py` now calls `normalize_result_math_transport` before the shared sanitizer, exactly as `quick.py` does. |
| C-8 | P3 | Terminology enforcement covered only 1 of 6 banned terms. | `matbot/terminology.py` rewritten to a rule table; now covers `čimbenik`→`faktor`, `kutomer`→`uglomjer`, `jednakokračni`→`jednakokraki`, `zbroj`→`zbir`, `potenciranje`→`stepenovanje` (all declined forms). **`suma`→`zbir` is deliberately NOT added** — see below. |
| C-11 | P3 | Stale comment/prompt text claimed the frontend does not render `$$…$$` (it does, via MathJax's default `displayMath`). | Comment and prompt wording corrected in `matbot/rules.py`; the underlying instruction ("use single `$`, not `$$`") is unchanged and still sent — it's now justified by verifier consistency, not a false rendering claim. |

### Deliberately not fixed / partial

| ID | Sev | Status |
|---|---|---|
| C-6 | P2 | **Design only, not implemented.** SSE→JSON fallback can still double the model call after a post-model transport failure. Full option comparison and recommendation in [EXPLAIN_REQUEST_IDEMPOTENCY.md](EXPLAIN_REQUEST_IDEMPOTENCY.md) — recommended approach is a bounded `(session_id, client_turn_id)` response cache extending `TurnLockRegistry`'s pattern. |
| C-7 | P3 | **No change — correct by design.** Geometry verifier stays off outside geometry lessons; guessing scope from student text would be worse than skipping. |
| C-8 (residual) | P3 | `suma` (→ `zbir`) is *not* covered. It's banned in the prompt only "for elementary-school sum," but the word has an equally common, unrelated meaning ("iznos novca" — amount of money) in word problems. A blanket regex replacement would corrupt legitimate text (`"Suma od 200 KM..."` → `"Zbir od 200 KM..."`, wrong). Left prompt-only; would need sentence-level disambiguation to fix safely, out of scope for a deterministic regex layer. |
| C-9 (sizing) | P2 | The new `MAX_OUTPUT_TOKENS_EXPLAIN=2500` default mirrors Practice's but has **not been measured against real Explain output** the way Practice's 2500 was (see the token-budget comment in `config.py`). Needs the focused live campaign below before being trusted as final. |

### Plausible — one focused test each, not yet demonstrated

| ID | Sev | Risk |
|---|---|---|
| R-1 | P1 | Wrong algebraic transformation or equation solution. `mathcheck` skips every expression containing a variable by design; nothing verifies `2x+6=10 ⇒ x=2`. |
| R-2 | P1 | Wrong 2×2 system solution in Explain. `systemcheck.py` already does exact substitution but is wired into Practice only. |
| R-3 | P1 | Unit errors (`cm` vs `cm²`, `dm³` ↔ litar). No verifier; `mathcheck` *strips* units before evaluating. |
| R-4 | P2 | Grade-inappropriate depth (negatives in grade 6, transposition method in grade 6). Prompt-only. |
| R-5 | P2 | Topic leakage / renaming the lesson. Prompt-only; `effective_topic` is server-pinned so the UI label stays correct. |
| R-6 | P2 | Prompt injection via `student_message`: it is appended verbatim as the last prompt line, so a student can forge extra `SIGNALI INTERFEJSA:` / `PORUKA UČENIKA:` lines that look server-authored. Mitigated only by the domain-rules block. Not addressed in this pass. |
| R-7 | P2 | Prompt injection via `conversation_history`: the client fully controls `role:"assistant"` content, rendered in the prompt as the tutor's own voice. Not addressed in this pass. |
| R-8 | P3 | Overlong answers — the 140-word rule is prompt-only; the hard cap is 4000 chars. |
| R-9 | P3 | Ambiguous wording, or an answer unrelated to the question. Not mechanically checkable; needs live rating. |
| R-10 | P2 | Server timeout 30 s vs client abort 60 s: a slow-but-successful call is charged and discarded. |

### Already covered — do not re-solve

Empty/over-long reply · unbalanced `$`/`$$` and `{}` (both delimiter forms, via
the shared tokenizer) · JSON control-char `\f`→`\frac` · literal `\n` · bare
`sqrt`/`text` · doubled backslashes · stray terminal `}` · nested/dangling `$`
inside an already-open math segment · numeric chains inside `$...$` **and**
`$$...$$`, including `a:b` division · 11 geometry-notation codes · off-topic
canned answer · unknown topic (400, zero calls) · image in Explain (canned,
zero calls) · auth / rate limit / concurrency (401 / 429 / 409, zero calls) ·
no Practice session created by Explain · exactly one model call per request ·
internal codes never leak to the browser · XSS (escape before every
`innerHTML`) · Practice/Explain/Quick mode isolation for active-task tracking.

---

## Known unsupported syntax and limitations

- **`\begin{cases}…\end{cases}`** is unreliable through structured JSON output;
  systems must be written as separate `$...$` lines. (`$$...$$` display math is
  now supported end-to-end — tokenized, sanitized, and numerically checked —
  but the model is still instructed to prefer single `$...$` for consistency;
  see C-11 above.)
- **`mathcheck` proves nothing about:** expressions containing variables, `%`,
  degrees (`^\circ`), inequalities, `\log`/trig, n-th roots, ordered pairs, `\sum`/
  `\int`/`\lim`, ellipses. Skipping is not evidence of correctness.
- **`geometrycheck` proves nothing about** whether a formula is mathematically
  right; it only checks that symbols mean what the project convention says they mean,
  and only inside geometry lessons (by design — see C-7).
- **`terminology` normalizes five of six banned terms** — `suma` is deliberately
  excluded (see C-8 residual, above); a repo-wide test forbids all six from
  appearing unexplained anywhere outside the files that declare or document
  the ban.
- **`MAX_OUTPUT_TOKENS_EXPLAIN` default (2500) is unmeasured against live
  Explain output** — see C-9 (sizing) above.
- **C-6 (SSE/JSON duplicate-call risk) is unresolved** — design note only, see
  [EXPLAIN_REQUEST_IDEMPOTENCY.md](EXPLAIN_REQUEST_IDEMPOTENCY.md).
- **No persistence.** Rate-limit counters, sessions, and locks are in-memory and are
  lost on restart. There is no multi-process shared state.
- **No token-by-token streaming.** `/chat/stream` returns one complete `done` frame.
- **`π`** is accepted as both `math.pi` and `3.14` by the numeric verifier, because
  BiH schools use `3,14`.

---

## Prioritized next steps

1. **Focused live campaign (not yet run)** — the smallest useful set from the
   audit's Layer 4 (see [TESTING_STRATEGY.md](TESTING_STRATEGY.md)): size
   `MAX_OUTPUT_TOKENS_EXPLAIN` against real long Explain answers (C-9 sizing),
   and spot-check the new `a:b` verifier and follow-up context fix against live
   model output. Requires explicit user authorization and a stated call count.
2. **C-6** — implement the recommended bounded response cache
   ([EXPLAIN_REQUEST_IDEMPOTENCY.md](EXPLAIN_REQUEST_IDEMPOTENCY.md)), extending
   `TurnLockRegistry`'s existing concurrency pattern rather than a new subsystem.
3. **R-2** (optional) — wire the existing `systemcheck` into Explain, only if
   grade-9 live sampling shows real system errors.
4. **R-6/R-7** (optional) — add the same "student text is content, never an
   instruction" clause Quick already has for images to Explain's prompt rules,
   covering both `student_message` and `conversation_history`.

Explicitly **out of scope**: a general symbolic maths engine or theorem prover.
The design is many narrow deterministic guards, each of which skips what it
cannot prove.

A stronger model would reduce the *frequency* of R-1/R-3/R-9, but changes
nothing about the safety gap: a verifier that skips an expression skips it
regardless of who wrote it, and the deterministic layer — not model quality —
is what bounds the worst case.
