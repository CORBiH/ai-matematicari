# MAT-BOT — current state

Last updated: 2026-08-02 (Practice lesson-contract fix pass). Test baseline: **1505 passing**.
Runtime model: `gpt-5-mini`, reasoning effort `low`.

## Maturity by area

| Area | State |
|---|---|
| Practice | Hardened. Family contracts are followed by fail-closed lesson-semantic contracts for the mapped grade-6 fraction lessons. Harder/easier prefers the primary lesson family; exact option ground truth/classified error uniqueness, ordered-pair/system substitution, numeric and geometry checks, and idempotent choice retry remain enforced. |
| Result / Quick | Hardened for text and for secure image input. Transport-level MathJax over-escaping repair, conversational-repair handling, context-free by design. |
| Image upload | Hardened. Bounded in-memory read, pixel-bomb guard, format sniffing, re-encode, strict metadata-only logging. Quick mode only. |
| Security / transport | Hardened. Signed token, two-tier rate limiting, per-session lock, ProxyFix, body-size cap, no secret ever logged. |
| **Explain** | **Audited 2026-08-01; 8 of 11 confirmed defects fixed the same day.** See below — C-6 is a deferred design-only note, C-9's default is unmeasured live, C-7 is correct as-is. |
| Exam ("Kontrolni") | Routed through the practice path; not separately audited. |

---

## D35 register — 35-call live campaign (2026-08-01)

A 35-call production campaign against `bot.matematicari.com` (12 Practice, 8 Explain,
15 Result/Quick incl. 5 images) confirmed six defects. All six are fixed below.
Two of them turned out to be bugs in our own deterministic code, not model behaviour.

| ID | Sev | Finding | Fix |
|---|---|---|---|
| D35-1 | P2 | Invalid/doubled MathJax reached the browser: `\ty`, `\tdot` (call 10) and doubled `\\cdot` (call 12). **Both were self-inflicted.** `mathsafe._repair_control_chars` rewrote *every* control char inside `$…$` back into a literal `\t`/`\f`/… with no lookahead, so a real TAB used as a plain separator (`"x=3,\ty=1"`) was *manufactured* into the non-existent command `\ty`. Separately, both doubled-backslash collapse regexes ended the command name with `\b`, which never fires between two word characters — so `\\cdot3` (this project's own no-space house style) was never collapsed and no rule flagged it. | `matbot/mathsafe.py`: new `MATHJAX_COMMAND_ALLOWLIST` as the single source of truth; command boundary changed from `\b` to `(?![A-Za-z])`; control chars reconstructed **only** when the result is an allowlisted command (TAB+`imes`→`\times` still works), dropped as whitespace when no letter follows, and otherwise left for the new `find_unknown_math_commands` scanner to reject. Applies to Practice, Explain and Result through the existing `sanitize_and_validate_math_text` boundary. |
| D35-2 | **P1** | Response declared `π≈3,14` and then wrote `6π≈18,85` (18,84 is correct under the declared value). `mathcheck` missed it twice: the declaration sat in **prose**, which `math_segments` never reads, and `_PI_VALUES` accepted a segment if **either** π value matched. Raw `\approx` outside math also reached the browser. | `matbot/mathcheck.py`: new `declared_pi_values(text)` scans the whole text (prose *and* math) for an explicit π declaration, parsed via `Decimal` and accepted only in a plausible band. When a declaration exists, π candidates become exactly the declared values — `math.pi` is dropped. No declaration ⇒ previous permissive behaviour, unchanged. Separately `mathsafe` now also rejects any *known* command appearing outside math. |
| D35-3 | P2 | A fractions lesson was selected but the student asked why triangle angles sum to 180°; the answer prepended a whole unrelated decimal-to-fraction lesson. The `PRVO OBJAŠNJENJE teme` rule fired purely on *empty history*, never checking whether the message was about the lesson, and the lesson entered the prompt unconditionally. | New `matbot/lesson_relevance.py` — deterministic, no model call. Weak context is claimed **only** when the message names a maths concept and none of them overlap the lesson's; deictic messages ("objasni mi ovo") and unprovable cases keep the previous behaviour. Weak context swaps the first-explanation rule for a priority rule, drops the lesson-name rule, and relabels the header to Quick's proven "kontekst, ne ograničenje". |
| D35-3b | P3 | `trokut` and `točan` (Croatian) reached the student; neither had a terminology rule. | `matbot/terminology.py` extended to seven terms: `trokut`→`trougao` (own declension map — `trougao` has a fleeting *a*) and `točan`→`tačan` (stem-only swap). `točka`/`točak`/`potočni` are provably never touched. |
| D35-4 | P2 | "Sastanak je u 12:30. Koliko je sati?" got a byte-exact `rules.OFF_TOPIC_ANSWER`. Not a colon/division bug and not a server guard — the model applied the shared off-maths rule to a clock question. | Quick-only prompt bullet declaring everyday measurement in scope, plus a deterministic `direct_clock_time_question` + server-owned answer applied **after** the single call when the model returns the generic refusal. Requires both a valid `HH:MM` and a fixed time phrase, so `60:15` stays division and `27:90` is not accepted. |
| D35-5 | **P1** | Rectangle image (`a=8 cm`, `b=5 cm`, area requested) answered `$P=26\,\text{cm}$` — the perimeter value with a linear unit. | See D35-6: the shared root cause is that the image turn returned only `{reply}`. `matbot/imagecheck.py` now recomputes the answer from the reported visible values for supported families. |
| D35-6 | **P1** | An image with a deliberately obscured value was answered `$x=5$` — a guess. | `matbot/schema.py::QuickImageTurnOutput`: image turns now use a **dedicated** structured schema (readability, symbol visibility, task type, visible values, confidence, uncertainty). `matbot/quick.py` publishes an image answer only when readability is `clear`, all required symbols are visible, confidence is `high` and no uncertainty is reported; otherwise the proposed maths is discarded for a short server-owned message. Still exactly one model call, no OCR, no repair call. |

### D35 — deliberately not changed

| ID | Status |
|---|---|
| Practice calls 5/6 | **Not a defect.** The family-contract rejection correctly protected session state: safe rejection, no state mutation, no hidden regeneration. Call 6 was a campaign scenario limitation (no valid task from call 5 to answer), not application behaviour. |
| C-6 | Still unresolved — see below. Explicitly out of scope for this pass. |

## D35T register — 14-call targeted validation (2026-08-02)

A 14-call campaign against the **local working tree** (calls 441–454) confirmed the
D35 fixes for images, topic relevance, clock time and MathJax, and found two
defects. Both are fixed below.

| ID | Sev | Finding | Fix |
|---|---|---|---|
| D35T-1 | P2 | A mathematically **correct** π explanation (declared 3,14, computed 18,84, zero numeric issues) was discarded because its prose read `LEKCIJA: Broj \pi i obim kruga`. The D35-1 pass had reused the whole `MATHJAX_COMMAND_ALLOWLIST` as the *outside-math* reject list, recreating the C-10 class of false rejection. | `matbot/mathsafe.py` now separates the two questions. `_STANDALONE_SYMBOL_COMMANDS` (symbols/relations with no arguments) are **narrowly wrapped** outside math — `Broj \pi` → `Broj $\pi$`. Structural commands (`\frac`, `\sqrt`, `\text`, `\mathbb`, `\cdot`, `\begin`, `\end`) still fail closed, except the isolated `\frac{a}{b}` that was already safely wrappable. Unknown commands fail closed everywhere. Nothing else about the D35-1 protections changed. |
| D35T-2 | **P1** | For image calls 12 and 13 the model put the task **heading** (`Rijesi jednacinu:`) in `visible_problem_text`, so `_check_linear_equation` found no `=` and `_check_expression` found nothing parsable. Both returned an empty issue list, which the caller read as "verified". Replay proved `$x=99$` for `3x+5=20` would have been published. | Two changes. (1) A dedicated bounded schema field `visible_math` carries **only** the visible expression/equation — no heading, no proposed answer, no inferred value, empty when unreadable — and `visible_problem_text` is never accepted as evidence for a supported family. (2) `imagecheck.verify_image_answer` returns `ImageVerification(supported, engaged, verified, code)`; `matbot/quick.py` publishes a supported family only on `supported ∧ engaged ∧ verified`, so **"did not engage" is now a rejection**. |

### D35T — narrow cleanups (same pass)

| Observation | Fix |
|---|---|
| `$180\circ$` rendered as a baseline ring | `mathsafe` normalizes a digit immediately followed by `\circ` into `^\circ`; an already-correct `180^\circ` is untouched. |
| Croatian `kut/kutovi` for angles | Added to `terminology.py` as the eighth term, with an explicit suffix table. `kutija`, `kutak`, `skuter` and `kutomer` are provably never touched (`kutomer` keeps its own earlier rule). |
| Clock answer returned as `$12:30$`, where `:` is this project's division notation | If a clock question was detected and the whole reply is just the time wrapped in `$…$`, the server substitutes its plain-prose answer — still **after** the single call, still no second call. A real explanation is left alone. |

### D35 — known limits of the image fixes

- **Image understanding is not deterministic in general.** The server never sees the
  image; it sees only what the model *reported* seeing. `imagecheck` therefore proves
  only that the arithmetic is consistent with the reported values — a misread value
  still yields a wrong answer. The readability gate, not `imagecheck`, is what guards
  against that.
- **Only these families receive independent verification:** `rectangle_area`,
  `rectangle_perimeter`, `square_area`, `square_perimeter`, `arithmetic`,
  `fraction_expression`, `linear_equation` (by substitution). For those, a verifier
  that cannot engage is a **rejection**, not a pass (D35T-2). Everything else
  (`other`, geometry beyond rectangles/squares, systems, word problems, tables,
  multi-step constructions) is reported as `supported=False` and left to the strict
  readability gate plus the generic `mathsafe`/`mathcheck`/`geometrycheck` chain —
  unsupported is not evidence of correctness.
- **The transcription itself is never independently verified.** `imagecheck` proves
  the arithmetic is consistent with what the model *reported*; nothing proves the
  report matches the picture. There is no OCR and no second model call.
- Internal image fields are transient: never in the browser payload, conversation
  history, `localStorage`, or any log line carrying content. Only bounded status codes
  and the task-type slug are logged.

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
- **`terminology` normalizes eight of nine banned terms** — the original five plus
  `trokut`→`trougao`, `točan`→`tačan` (D35-3b) and the Croatian angle word→`ugao`
  (D35T). `suma` is deliberately excluded
  (see C-8 residual, above); a repo-wide test forbids all covered terms from
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
  BiH schools use `3,14` — **unless** the same answer explicitly declares a value
  (e.g. `π≈3,14`), in which case only the declared value is used (D35-2).
- **Model-output MathJax is allowlisted.** Only commands in
  `mathsafe.MATHJAX_COMMAND_ALLOWLIST` may appear inside `$…$`; an unknown control
  word or a residual doubled backslash before a command fails closed to
  `SAFE_ERROR_MESSAGE`. The intended command is never guessed. Widening the
  allowlist is the supported way to add notation.
- **Image answers are gated, not verified in general.** See the D35 register above
  for the exact supported families and the readability gate.

---

## Prioritized next steps

1. **Targeted live validation of the D35 fixes (not yet run)** — the deterministic
   halves are locked by tests, but three behaviours are prompt-dependent and can
   only be confirmed live: whether the model actually populates the new image
   structured fields honestly, whether Explain respects the weak-lesson-context
   rule, and whether the clock-time bullet stops the generic refusal at the source
   (rather than relying on the server fallback). Requires explicit user
   authorization and a stated call count.
2. **Focused live campaign (not yet run)** — the smallest useful set from the
   audit's Layer 4 (see [TESTING_STRATEGY.md](TESTING_STRATEGY.md)): size
   `MAX_OUTPUT_TOKENS_EXPLAIN` against real long Explain answers (C-9 sizing),
   and spot-check the new `a:b` verifier and follow-up context fix against live
   model output. Requires explicit user authorization and a stated call count.
3. **C-6** — implement the recommended bounded response cache
   ([EXPLAIN_REQUEST_IDEMPOTENCY.md](EXPLAIN_REQUEST_IDEMPOTENCY.md)), extending
   `TurnLockRegistry`'s existing concurrency pattern rather than a new subsystem.
4. **R-2** (optional) — wire the existing `systemcheck` into Explain, only if
   grade-9 live sampling shows real system errors.
5. **R-6/R-7** (optional) — add the same "student text is content, never an
   instruction" clause Quick already has for images to Explain's prompt rules,
   covering both `student_message` and `conversation_history`.

Explicitly **out of scope**: a general symbolic maths engine or theorem prover.
The design is many narrow deterministic guards, each of which skips what it
cannot prove.

A stronger model would reduce the *frequency* of R-1/R-3/R-9, but changes
nothing about the safety gap: a verifier that skips an expression skips it
regardless of who wrote it, and the deterministic layer — not model quality —
is what bounds the worst case.
