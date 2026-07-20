# MatBot — Recovery & Stabilization Audit (2026-07-19)

Status: **planning only, no code changed.** This document maps the current
architecture, explains why the same bug classes keep returning, and proposes a
staged consolidation (not a rewrite) toward a task/step-centric core.

The trigger example — *"Provjeri da li je broj 240 djeljiv sa 6"* stopping after
the student confirms divisibility by 2 — is used throughout as the canonical case
because it exercises every weak seam at once.

---

## 0. TL;DR

The system is a **single 6,177-line procedural pipeline** (`ai_tutor_service.py`)
in which ~40 "contract" functions mutate one shared `payload` dict in a strict,
comment-documented order, after which `_finalize_response` performs post-hoc
**string surgery on the model's prose** and *derives* lifecycle state from a dozen
overlapping heuristics reading that same payload.

There is **no persisted task object and no solution/step model anywhere** in the
codebase (`grep solution_plan|step_id|active_step|completed_steps` → 0 hits).
"Steps" exist only as transient GPT prose. Consequently the tutor cannot know it
is on substep 2 of 3, so multi-step guidance terminates whenever the model's prose
happens to stop. Every bug class in the brief is a symptom of these two facts:

1. **State is reconstructed per-turn from prose + heuristics** instead of read from a durable task record.
2. **Correctness/lifecycle decisions are made in many places** and reconciled by *editing the prose after the fact* rather than generating the prose from one decision.

The recommended target keeps the existing deterministic checker (`answer_checker.py`,
which is genuinely good) and the Sheets logging (already comprehensive), and
introduces three durable objects — **TaskDefinition**, **SolutionPlan/StepCursor**,
and a **GradingResult reducer** — plus **four explicit mode state machines**. The
contract chain is retired incrementally behind a feature flag, one mode at a time.

---

## 1. Current Architecture Map

### 1.1 Request lifecycle

```
app.py:ai_tutor_chat / ai_tutor_chat_stream
   └─ ai_tutor_service.handle_chat[_stream]
        ├─ _prepare_chat(...)                 # everything BEFORE the model call
        │    ├─ _sanitize_payload
        │    ├─ _apply_mode_preservation_contract
        │    ├─ _apply_explicit_intent
        │    ├─ _apply_confirmation_contract
        │    ├─ _apply_new_task_intent
        │    ├─ _apply_completed_exam_followup_contract
        │    ├─ _apply_active_exam_help_contract
        │    ├─ _apply_hint_request_contract
        │    ├─ _apply_multiple_choice_answer_contract
        │    ├─ _apply_video_recommendation_contract
        │    ├─ _apply_challenge_contract
        │    ├─ _apply_practice_help_contract
        │    ├─ _apply_student_task_contract
        │    ├─ _apply_explain_request_contract
        │    ├─ _apply_micro_task_contract
        │    ├─ _apply_pending_context_question_contract
        │    ├─ _apply_meta_identity_contract
        │    ├─ _run_answer_check            # deterministic grade (answer_checker)
        │    ├─ _soften_post_hint_reply
        │    ├─ _flag_non_answer_reflection
        │    ├─ _apply_image_practice_followup
        │    ├─ _run_contextual_gpt_grade    # 2nd, LLM grader
        │    ├─ _update_stuck_state
        │    ├─ _apply_exam_context_contract
        │    ├─ OCR / image_test resolution / result-mode branch
        │    └─ build_*_prompt (prompt_builder)   → system+user messages
        ├─ <MODEL CALL>  (app._tutor_openai_chat)
        └─ _finalize_response(prep, answer)   # everything AFTER the model call
             ├─ to_ijekavica                          # language surgery
             ├─ fix_repeated_item_numbering
             ├─ enforce_grading_consistency           # grading_guard: prose surgery
             ├─ neutralize_non_answer_grade
             ├─ _soften_micro_task_answer
             ├─ verify_image_result_answer            # rewrites numeric lines
             ├─ _apply_math_result_verification
             ├─ _apply_gpt_fallback_verdict           # prose → verdict
             ├─ task_text = extract_*_task(answer)    # prose → active task
             ├─ _validate_task_activation / exam validation
             ├─ _next_state_for_response(...)         # derive lifecycle
             ├─ _exam_state_for_response(...)
             └─ log_student_activity / log_transcript_to_sheet
```

### 1.2 Component inventory (file : responsibility)

| Concern | File / key functions |
|---|---|
| HTTP entry, OCR, model wrappers, embed auth, rate limit | `app.py` (`ai_tutor_chat`, `_tutor_openai_chat`, `mathpix_ocr_to_text`) |
| Orchestration + all contracts + lifecycle derivation | `matbot/ai_tutor_service.py` (6,177 lines) |
| Deterministic answer checking (the good part) | `matbot/answer_checker.py` (`derive_expected`, `check_practice_answer`, `_check`, ~40 `_try_*` solvers) |
| Post-hoc prose reconciliation | `matbot/grading_guard.py` (`enforce_grading_consistency`, `authoritative_verdict`) |
| Prompt assembly per mode | `matbot/prompt_builder.py` (`build_tutor_prompt`, `build_practice_*`, `build_result_mode_prompt`, `build_exam_oblast_prompt`) |
| Static prompt text / didactics | `matbot/tutor_prompts.py` |
| Topic resolution | `matbot/topic_lookup.py`, `matbot/topic_detector.py` |
| Curriculum data | `matbot/content_loader.py` + `data/*/AI_MATH_*_NPP_*.xlsx` |
| Language normalization | `matbot/bosnian.py` (`to_ijekavica`) |
| Image result verification | `matbot/image_result_verifier.py` |
| Telemetry | `matbot/sheets_log.py` (57-column schema), `matbot/activity_log.py` |
| Frontend | `templates/index.html` (1,910 lines; carries `previous_next_state`, mirrors backend task-extraction heuristics) |

### 1.3 Where each concern physically lives

- **Task generation:** the *model* invents question text; the server only
  post-validates it in `_finalize_response` via `_validate_task_activation` /
  `_validate_exam_oblast_task`, then `extract_practice_task` / `extract_marked_task`
  scrape the prose to decide what the "active task" is.
- **Answer checking:** `answer_checker.check_practice_answer` (deterministic) +
  `_run_contextual_gpt_grade` (LLM) + `_gpt_text_verdict` (regex over prose).
- **Task state storage:** *none server-side.* State round-trips through the client
  as `next_state` / `last_tutor_task` / `previous_next_state`. The server is
  stateless between turns; the browser is the store.
- **Exam state:** `exam_state` dict inside `next_state`, normalized by
  `_normalize_exam_state`, rebuilt each turn by `_exam_state_for_response`.
- **Adaptive hint state:** `hint_level`, `hint_history`, `progress_signature`,
  `multiple_choice_hint`, `completed_parent_task` — all inside `next_state`,
  managed by `_adaptive_lifecycle_fields`.
- **Logging:** `sheets_log.log_transcript_to_sheet` (57 columns, good coverage).

---

## 2. Conflict Matrix — who decides the same thing

| Decision | Components currently deciding it | Should be authoritative |
|---|---|---|
| Is the answer correct? | `answer_checker.check_practice_answer`, `_run_contextual_gpt_grade`, `_gpt_text_verdict` (regex on prose), `grading_guard.enforce_grading_consistency`, model prose itself | **GradingResult reducer** (deterministic first, LLM only when checker abstains, prose never) |
| Coarse verdict shown to UI/Sheets | `_answer_verdict_for_response` (prefers `_gpt_answer_verdict` **over** `authoritative_verdict`), `authoritative_verdict`, exam path `_exam_response_verdict` | Reducer output field |
| Is the task complete? | `_is_grading_turn`+`_grading_outcome`, `_next_state_for_response`, `_exam_state_for_response`, `task_items` pending logic, frontend `examStatus` check | **StepCursor.is_complete** |
| What is the active task (text)? | `extract_practice_task`, `extract_marked_task`, `_student_task`, `_image_test.current`, `last_tutor_task` persistence branch, **frontend mirror** (`index.html:971-991`) | **TaskDefinition.question**, server-authoritative |
| What is the next step? | model prose, `pending_action`, `_next_state_for_response`, `_adaptive_*`, exam reducer, `micro_task` | **SolutionPlan + StepCursor** |
| Which mode are we in? | `_session_mode` (UI), `_apply_mode_preservation_contract`, prompt-mode set by ~10 contracts, `recommended_mode`, frontend `j.session_mode || state.mode` | **Session mode = UI choice; internal routing separate & logged** |
| Counters (attempt/wrong/hint/streak) | `_attempt_count_for_next_state`, `_wrong_attempt_count_for_next_state`, `_apply_gpt_fallback_verdict` (also bumps streak), exam path | Reducer emits deltas; one applier |
| Active task after grading | `_finalize_response` task_text branch (8 elif arms), `_grading_should_keep_active_task`, exam override | StepCursor lifecycle |

**Key finding:** `_answer_verdict_for_response` (line 5512) returns the GPT
fallback verdict **before** consulting the deterministic `authoritative_verdict`,
and `_apply_gpt_fallback_verdict` derives a verdict from `_gpt_text_verdict(answer)`
— i.e. **the model's prose is parsed back into structured state.** This directly
violates the brief's "GPT prose must never independently change the verdict."

---

## 3. Root-Cause Analysis (why the classes recur)

### RC-1 — No durable task/step model → guided flow is prose-timed (the trigger bug)
`grep` confirms zero occurrences of `solution_plan / step_id / active_step /
completed_steps`. The divisibility task is represented as a **single**
`Expected(kind="divisibility_explained", answer_type="boolean_with_explanation",
required_concepts=(...))` (`answer_checker.py:974`). It is a *one-shot* rubric, not
a 3-step plan. When the student says *"da je djeljiv sa 2"*:
- the checker has no notion of "substep 1 of 3"; it either abstains or matches a
  partial concept, and `_soften_post_hint_reply` marks it a `correct_step`;
- **nothing advances a cursor**, because there is no cursor;
- whether substep 2 is asked depends entirely on the model's free prose, which
  stopped. There is no state that says "2 concepts remain."

This is unfixable by prompt or by another guard patch — the missing object must exist.

### RC-2 — Prose is treated as the source of truth, then edited
Lifecycle is recovered by scraping the answer (`extract_practice_task`,
`extract_marked_task`, `_gpt_text_verdict`) and correctness is repaired by rewriting
the answer (`enforce_grading_consistency`, `neutralize_non_answer_grade`,
`_make_incorrect/_make_positive/_make_step_confirmed`). Because prose is variable,
every new phrasing the model produces is a new escape hatch — hence the endless
"add one more regex to the guard" history (`grading_guard.py` alone has ~30 helper
functions). The 500-call sims keep finding *residual* leaks (BUG-SIM-01) precisely
because the guard is chasing prose, not enforcing a decision.

### RC-3 — Ordering-coupled contracts create hidden interactions
`_prepare_chat` documents a mandatory order in comments ("poslije confirmation
contract-a", "prije determinističkog ocjenjivanja"). ~40 mutators over one dict
means each new rule can silently change the input to a later rule. This is the
mechanism behind *"mode changing to practice/explain,"* *"one answer applied to
several questions,"* and *"short follow-up loses the task"*: a contract fires (or
fails to) and a downstream branch reads the wrong flag. There is no single place
that owns "what turn is this."

### RC-4 — Two graders, no defined precedence contract
Deterministic checker and `_run_contextual_gpt_grade` both run; the merge logic is
scattered across `_apply_gpt_fallback_verdict`, `_answer_verdict_for_response`,
`enforce_grading_consistency`, and the exam path. "Deterministic says correct, GPT
says incorrect" is possible because no component *reduces* the two into one result
before prose is generated — the reconciliation happens after, on prose.

### RC-5 — Client is a co-owner of state
`index.html` carries `previous_next_state`, does its own affirmative/negative
confirmation detection, and **mirrors the backend's task-extraction filter**
(lines 971-991: *"ogledalo backend filtera"*). Any divergence between the two
mirrors = UI/backend disagreement about active task, buttons, or completion.

### RC-6 — Task generation and grading are independent
The model writes the question *and* the answer freely; validation is a
*post-filter* that can only reject, not construct. So "no unique answer,"
"missing info," "ungradeable," "off-tema but on-oblast" all pass whenever the
post-filter's heuristics don't catch them. The safe sequence (skill → params →
compute answer → render → validate) does not exist.

### RC-7 — Language correctness is a final-pass rewrite
`to_ijekavica` runs once at the end over whatever the model produced. It fixes a
fixed dictionary of ekavica forms; anything outside the list (or new terminology)
leaks. Language is a symptom of prompting + a lossy post-filter, same pattern as RC-2.

---

## 4. Target Architecture (staged extraction, not rewrite)

Keep what works: `answer_checker.py` solvers, `sheets_log.py` schema, topic
resolution, curriculum loading, OCR, prompt *content*. Introduce three objects and
four state machines, and **invert the prose relationship**: decide first, then have
the model *narrate a decision it cannot change*.

### A. TaskDefinition (durable, server-authoritative)
No task becomes active without one. Stored in `next_state.task` (round-tripped) and
mirrored to Sheets.

```
TaskDefinition {
  task_id, grade, oblast_id, tema_id, skill_id, mode,
  question,                       # rendered text
  answer_schema: Expected-like,   # reuse answer_checker.Expected
  solution_plan: SolutionPlan | null,
  validation_status: "validated" | "rejected",
  source: "template" | "template_gpt_wording" | "gpt_rubric"
}
```

`answer_checker.Expected` already carries most of `answer_schema` (kind,
answer_type, expected_boolean, divisor, required_concepts, expected_display,
required_form, unit). **Promote `Expected` into the schema rather than inventing a
new one.**

### B. SolutionPlan + StepCursor (the missing object)
For skills that are multi-step, a machine-readable plan and a live cursor that is
persisted in `next_state`:

```
SolutionPlan(skill="divisibility_by_6").steps = [
  Step(id="div2",   expected=True,  prompt="Je li 240 djeljivo sa 2?"),
  Step(id="div3",   expected=True,  check="digit_sum%3==0",
       prompt="Sada saberi cifre: 2+4+0. Je li taj zbir djeljiv sa 3?"),
  Step(id="final",  expected=True,  requires=["div2","div3"],
       prompt="Pošto je djeljiv i sa 2 i sa 3 — je li djeljiv sa 6? Napiši puno obrazloženje.")
]
StepCursor { active_step_id, completed_step_ids, refers_to: "substep"|"whole_task", is_complete }
```

The tutoring engine advances the cursor deterministically; the model only *renders*
the current step's prompt and *reacts warmly* to the graded result.

### C. GradingResult reducer (the one authoritative verdict)
All evidence sources produce inputs; **one** reducer emits the final result and
**everything else is generated from it** (prose, counters, UI flags, Sheets).

```
GradingResult {
  verdict: correct|partial|incorrect|ambiguous|step_correct|step_incorrect,
  detail, grader_source: "deterministic"|"llm"|"none",
  task_completed: bool, step_completed: bool, next_step_id,
  attempt_delta, wrong_attempt_delta, feedback_action
}
```

Precedence contract (fixed, testable):
1. deterministic checker if `checkable` → authoritative;
2. else LLM structured grader (`_run_contextual_gpt_grade`) with a confidence floor;
3. else `ambiguous` → ask for clarification.
4. **Prose is never a grader.** Delete `_gpt_text_verdict`-as-verdict.

The model receives the reduced verdict as a *frozen fact* in the prompt and is
told to narrate it; `grading_guard` becomes a thin **assertion** ("prose must not
contradict verdict → if it does, regenerate/replace opener") instead of ~30 prose
surgeries.

### D. Four mode state machines
Explicit, table-driven; a generic intent handler may **route** but never silently
change `session_mode`.

| Mode | Inputs | Completion | Help behavior | New-task |
|---|---|---|---|---|
| **Explanation** | question, "dalje", "ne razumijem" | user leaves / opens practice | deeper explanation + optional `micro_task` (own field) | never auto |
| **Practice** | answer, hint req, "novi zadatak" | StepCursor.is_complete on final step | plan-driven hint, no leak, requires independent final | on explicit request/confirm |
| **Quick/Result** | expression/image | one result returned | n/a (context-free) | n/a |
| **Exam** | per-item answers, "predaj" | all items graded | reveals *nothing*, no new exam | blocked until submit |

### E. Task library & generation policy
Per skill, choose the safest supported production path:

1. deterministic template (compute answer in code) — **preferred**;
2. parameterized template (params in code, answer in code, wording fixed);
3. GPT *wording only* over a code-computed TaskDefinition;
4. GPT structured rubric (conceptual, boolean-with-explanation) — LLM-graded, flagged;
5. reject (drawing/measurement/construction).

Generation sequence: **skill → params → compute expected → render → validate →
activate.** The model never authors question and answer independently.

### F. Guided tutoring engine
Classifies each student turn against the StepCursor into: full answer / intermediate
answer / correct substep / wrong substep / clarification / help / no progress. A
correct intermediate answer: closes only the current step, preserves the parent
TaskDefinition, auto-asks the next step, does **not** bump full-task streak, does
**not** end the turn. After full reveal: parent marked *assisted*, a validated
same-skill follow-up is generated, independent final required.

---

## 5. Phased Recovery Plan (dependency order)

Each phase ships behind flag `MATBOT_ENGINE_V2` (per-mode sub-flags) so the legacy
contract chain stays default until a mode is proven.

### Phase 0 — Freeze & observability
- **Objective:** stop the bleeding; make every decision observable before changing any.
- **Files:** `sheets_log.py`, `ai_tutor_service._finalize_response`, `app.py`.
- **Create/change:** add `grader_source`, `active_step_id`, `step_completed`,
  `task_completed`, `feedback_action`, `engine_version` columns; add a
  `decision_trace` (which contract fired) to telemetry, not to prose.
- **Migration:** additive columns only; `_append_row_once` already pads.
- **Tests:** `test_sheets_log` extended for new columns; snapshot of current verdicts.
- **Acceptance:** every graded turn logs one authoritative verdict + grader source.
- **Rollback:** columns are additive/no-op.
- **Complexity:** S. **Risk:** low.
- **Freeze list:** see §"Freeze" below.

### Phase 1 — Unified TaskDefinition
- **Objective:** no task activates without a validated TaskDefinition; server owns it.
- **Files:** new `matbot/task_model.py`; `ai_tutor_service` (task_text branches),
  `prompt_builder` (task rendering), `index.html` (stop mirroring; read `task.question`).
- **Create:** `TaskDefinition`, `validate_task()`, `activate_task()`. Wrap existing
  `_validate_task_activation` / `_validate_exam_oblast_task` as validators that
  *populate* a TaskDefinition instead of returning ad-hoc dicts.
- **Migration:** `next_state.task` added; `last_tutor_task` kept as derived mirror
  for one release for old clients.
- **Tests:** schema-validation suite; "no activation without expected answer/rubric."
- **Acceptance:** frontend no longer runs task-extraction heuristics.
- **Rollback:** flag off → legacy `last_tutor_task` path.
- **Complexity:** M. **Risk:** medium (client contract).

### Phase 2 — Authoritative GradingResult reducer
- **Objective:** one verdict; prose generated from it; guard becomes assertion.
- **Files:** new `matbot/grading_reducer.py`; `ai_tutor_service` (`_run_answer_check`,
  `_apply_gpt_fallback_verdict`, `_answer_verdict_for_response`); `grading_guard.py`.
- **Create:** `reduce_grading(deterministic, llm) -> GradingResult`. Delete prose→verdict
  path (`_gpt_text_verdict` as authority). Reorder `_answer_verdict_for_response`
  to consult deterministic first.
- **Migration:** verdict fields unchanged in shape; internal source swapped.
- **Tests:** grader-conflict matrix (det correct/llm incorrect etc.); guard becomes
  no-op when prose already agrees.
- **Acceptance:** no turn where Sheets verdict ≠ prose ≠ counters.
- **Rollback:** flag off.
- **Complexity:** M. **Risk:** medium (this is the core inversion).

### Phase 3 — Practice step engine (fixes the trigger bug)
- **Objective:** plan-driven multi-step practice with a real cursor.
- **Files:** new `matbot/solution_plan.py`, `matbot/tutor_engine.py`;
  `prompt_builder.build_practice_*`; reuse `answer_checker` step checks.
- **Create:** `SolutionPlan`, `StepCursor`, `advance(cursor, grading)`,
  `classify_turn(...)`. Seed plans for the skills already deterministically
  checkable (divisibility, prime factorization, linear equation, fraction ops).
- **Migration:** `next_state.step_cursor` added; explanation `micro_task` unchanged.
- **Tests:** the 240÷6 scenario as a multi-turn fixture (div2→div3→final→independent);
  correct-intermediate must not end turn or bump streak.
- **Acceptance:** guided flows complete deterministically regardless of model prose.
- **Rollback:** flag off → prose-timed hints (current).
- **Complexity:** L. **Risk:** medium.

### Phase 4 — Stable exam engine
- **Objective:** exam as its own state machine; no mode drift, no answer bleed.
- **Files:** new `matbot/exam_engine.py` extracted from `_exam_state_for_response`
  et al.; `ai_tutor_service` exam branches.
- **Create:** `ExamState` machine: pre-validated item list, per-item cursor,
  submit→grade-all, completed=terminal. Active-exam help = zero reveal.
- **Migration:** `_normalize_exam_state` becomes the loader for the machine.
- **Tests:** one answer → one item; post-completion "objasni treći" never reopens;
  active help never creates a new exam.
- **Acceptance:** all exam bugs from the brief have red→green fixtures.
- **Rollback:** flag off.
- **Complexity:** L. **Risk:** medium-high (most tangled current code).

### Phase 5 — Task-template coverage
- **Objective:** generation via skill→params→compute→render for supported skills.
- **Files:** `matbot/task_model.py` templates; `prompt_builder` wording-only path.
- **Tests:** each template: unique answer, gradeable, on-tema; explicit gap list.
- **Acceptance:** supported-skill matrix (§6) all green; unsupported explicitly rejected.
- **Complexity:** M-L (incremental per skill). **Risk:** low (additive).

### Phase 6 — Bosnian language quality
- **Objective:** move language correctness upstream (prompt + terminology table)
  and make `to_ijekavica` a safety net, not the mechanism.
- **Files:** `tutor_prompts.py` (explicit ijekavica directive — noted undone in
  fable-next-steps §6), `bosnian.py`.
- **Tests:** extend ijekavica corpus (razumijem/obje/djevojčica + terminology).
- **Complexity:** S. **Risk:** low.

### Phase 7 — Evaluation & rollout
- **Objective:** offline harness gates + canary. See §7–9.
- **Complexity:** M. **Risk:** low.

---

## 6. Supported-Skill Matrix (evidence-based; from `answer_checker.py` solvers)

Legend: ✅ supported · ⚠️ partial/heuristic · ❌ not supported. "Step-guided" and
"Exam-safe" are ❌ almost everywhere today because no step engine / pre-validated
exam item pipeline exists yet.

| Grade | Skill | Generate | Det. check | Rubric | Step-guided | Exam-safe |
|---|---|---|---|---|---|---|
| 6 | Djeljivost (2,3,5,6,9,10) + obrazloženje | ⚠️ gpt | ✅ `_try_divisibility_with_explanation` | ✅ | ❌→P3 | ❌→P4 |
| 6 | Rastavljanje na proste faktore | ⚠️ gpt | ✅ `_try_prime_factorization` | ✅ | ❌→P3 | ❌ |
| 6 | NZD/NZS | ⚠️ gpt | ✅ `_try_gcd_lcm` | — | ❌ | ❌ |
| 6 | Razlomci: sabiranje/oduzimanje/množenje | ⚠️ gpt | ✅ `_try_arithmetic`/`_try_worded_fraction_operation` | — | ⚠️ | ❌ |
| 6 | Skraćivanje/proširivanje razlomka | ⚠️ gpt | ✅ `_try_simplify`/`_try_expand` | — | ⚠️ | ❌ |
| 6 | Poređenje razlomaka | ⚠️ gpt | ✅ `_try_fraction_comparison` | — | ❌ | ❌ |
| 6/7 | Postotak (X% od N, obratno) | ⚠️ gpt | ✅ `_try_percent_of`/`_try_percent_fraction_conversion` | — | ❌ | ❌ |
| 6/7 | Pretvaranje jedinica (m/cm/mm, kg/g, h/min) | ⚠️ gpt | ✅ `_try_unit_conversion`/`_try_conversion` | — | ❌ | ❌ |
| 7 | Linearna jednačina (jedna nepoznata) | ⚠️ gpt | ✅ `_try_linear_equation`/`_check_single_equation` | — | ⚠️ | ❌ |
| 7 | Linearna nejednačina | ⚠️ gpt | ✅ `_check_single_inequality`/`_solve_linear_inequality` | — | ❌ | ❌ |
| 7 | Stepeni (mali eksponenti) | ⚠️ gpt | ✅ `_try_power` | — | ❌ | ❌ |
| 7 | Omjer/razmjera, rate | ⚠️ gpt | ✅ `_try_rate_or_ratio` | — | ❌ | ❌ |
| 7 | Skupovi (unija/presjek/razlika) | ⚠️ gpt | ✅ `_try_set_operation`/`_check_set_task` | — | ❌ | ❌ |
| 7/8 | Uglovi: aritmetika, trougao (zbir 180°) | ⚠️ gpt | ✅ `_try_angle_arithmetic`/`_try_triangle_missing_angle` | — | ❌ | ❌ |
| 8 | Kružnica: dužina luka, tangenta-radijus ugao | ⚠️ gpt | ⚠️ `_try_arc_length`/`_try_tangent_radius_angle` | — | ❌ | ❌ |
| 8 | Komplement/dopuna | ⚠️ gpt | ✅ `_try_complement` | — | ❌ | ❌ |
| 8 | Pitagora | ⚠️ gpt | ❌ (noted absent in improvement plan) | — | ❌ | ❌ |
| 8 | Množenje/dijeljenje stepena, monomi/polinomi | ⚠️ gpt | ❌ | ⚠️ | ❌ | ❌ |
| 9 | Sistemi jednačina | ⚠️ gpt | ❌ | — | ❌ | ❌ |
| 9 | Linearna funkcija (nagib, presjek) | ⚠️ gpt | ❌ | — | ❌ | ❌ |
| 9 | Statistika: medijana/modus/aritm. sredina | ⚠️ gpt | ❌ | ⚠️ | ❌ | ❌ |
| 6–9 | Konstrukcije (šestar/lenjir), mjerenje | ❌ reject | ❌ | ❌ | ❌ | ❌ reject |

**Honest coverage statement:** deterministic grading is strong for grade-6 number
theory and fractions, and for grade-7 equations/inequalities/sets/percent/units.
It is thin-to-absent for grade-8 algebra-of-powers/Pythagoras and most of grade-9
(systems, linear functions, statistics). **No skill is step-guided or exam-safe
under a validated pipeline today** — those are Phase 3/4 deliverables. Do not claim
curriculum coverage beyond the deterministic column.

---

## 7. Test Strategy

- **Unit:** each `answer_checker` solver (exists); each new reducer/plan/cursor.
- **State-machine:** table-driven transition tests per mode (Explanation/Practice/
  Quick/Exam) — allowed input → expected next state; illegal transitions rejected.
- **Schema-validation:** "task rejected when no expected answer/rubric"; "generated
  question has a unique computable answer."
- **Grader-conflict:** deterministic × LLM × prose combinations → single reducer
  verdict; assert prose never wins.
- **Multi-turn conversation fixtures:** extend `tests/test_conversation_flows.py`
  (client-simulator carrying `next_state`) — the 240÷6 flow is the flagship case.
- **UI-contract:** `scripts/check_js.mjs` assert the client sends
  `previous_next_state`/`last_tutor_task`/`last_image_context` correctly and **does
  not** re-derive active task once Phase 1 lands.
- **Regression corpus from Sheets:** every production bug → a permanent fixture
  (already the practice for AUD/SIM bugs; formalize as a directory of replayable rows).
- **Local smoke:** mocked-model `handle_chat`/`handle_chat_stream` parity.
- **Production canary:** N scripted live sessions/week (skeleton exists:
  `scripts/eval_tutor.py`, `audit/rerun_stochastic.py`).

Rule: **every production bug becomes a permanent regression case** (already 800+
tests; keep the discipline).

### Highest-value regression cases to seed immediately
1. 240÷6 guided completion (RC-1).
2. correct intermediate step must not end turn / bump streak.
3. det-correct vs llm-incorrect → correct.
4. exam: one answer → one item.
5. post-exam "gdje sam pogriješio" → no reopen, no label.
6. `12 - 23x = 4x` → x = 4/9 (result-mode verification).

---

## 8. Evaluation Harness

Extend `docs/eval/eval_cases.json` + `scripts/eval_tutor.py` into a labeled offline
set with measurable pass criteria:

| Category | Example | Pass criterion |
|---|---|---|
| Exact correct | 3/4 + 5/6 = 19/12 | verdict=correct, 100% |
| Equivalent form | 6/8 for 3/4 | never `incorrect`; verdict=correct(_equivalent) |
| Partial reasoning | states div-by-2 only | verdict=step/partial, task stays active |
| Correct intermediate | "da, djeljiv sa 2" | step_completed, next step asked, streak unchanged |
| Wrong step, right final | bad middle, right end | flagged, not silently accepted |
| Ambiguous | "možda 5" | verdict=ambiguous → clarify |
| "ne znam" | — | hint, no negative label |
| Multiple answers | "prvi 6/9, drugi 4/8" | correct item attribution |
| Mode switching | "objasni mi" mid-practice | session_mode unchanged; internal route only |
| Short follow-up | "šta dalje" | active task preserved |
| Off-topic generation | exam item off-tema | rejected/regenerated |
| Ungradeable task | "nacrtaj trougao i izmjeri" | rejected before activation |
| Language | razumem/obe/devojčica | 0 ekavica leaks |

Targets: correctness ≥ 99% on deterministic categories; **0** false-incorrect on
verified-correct; **0** ekavica leaks on the language set; step-flow completion 100%
on seeded plans.

---

## 9. Rollout Plan

1. **Dev branch** per phase; never to `main` until green + canary (recall:
   push to `main` auto-deploys to prod VPS — `deploy-vps.yml`).
2. **Feature flags:** `MATBOT_ENGINE_V2` global + `_PRACTICE/_EXAM/_GRADING`
   sub-flags; legacy chain is default.
3. **Limited supported skills first:** enable step engine only for
   divisibility/prime-factorization/linear-equation/fractions (the ✅ det. column).
4. **Shadow grading:** run reducer in parallel, log divergence vs legacy for a week
   before it drives prose.
5. **Beta exam mode:** exam engine behind flag for internal sessions only.
6. **Canary sessions:** scripted live runs; watch Sheets divergence + latency.
7. **Rollback conditions:** any false-incorrect on verified-correct, any verdict/
   prose/counter disagreement, exam mode drift, or latency regression > current
   (reasoning_effort=low baseline) → flag off.

---

## 10. Final Priority List

**P0 — blockers (correctness/lifecycle, cannot be prompted away)**
- Durable TaskDefinition + StepCursor (Phase 1 + 3) — fixes the trigger bug class.
- Authoritative GradingResult reducer; delete prose→verdict (Phase 2).

**P1 — reliability**
- Exam engine extraction (Phase 4).
- Remove frontend state co-ownership / task-extraction mirror (Phase 1 tail).
- Result-mode math verification coverage (`12-23x=4x` class) tied to reducer.

**P2 — quality**
- Task-template generation for supported skills (Phase 5).
- Bosnian language upstream (Phase 6).
- Evaluation harness expansion (Phase 7/8).

**Postpone**
- New curriculum breadth (grade-8 algebra-of-powers, Pythagoras det. checks,
  grade-9 systems/functions/statistics) until the engine is stable — breadth on an
  unreliable core multiplies bug surface. Prefer **smaller reliable scope**.

---

## Appendix — Requested closing items

**1. Executive summary.** MatBot is one large procedural pipeline where state is
reconstructed each turn from model prose plus ~40 order-coupled heuristics, and
correctness is repaired by editing prose after the fact. There is no durable task
or step object, so multi-step guidance ends whenever the prose ends (the 240÷6
bug), and correctness/lifecycle decisions are duplicated across many components and
reconciled too late. The fix is architectural but **not** a rewrite: introduce a
server-authoritative TaskDefinition, a SolutionPlan/StepCursor, and a single
GradingResult reducer; make the model narrate frozen decisions instead of owning
them; retire the contract chain mode-by-mode behind flags. Keep the strong
deterministic checker and the good Sheets telemetry.

**2. Recommended target architecture.** §4 — three durable objects
(TaskDefinition, SolutionPlan/StepCursor, GradingResult) + four explicit mode state
machines; "decide first, narrate second"; deterministic-grader-first precedence;
generation via skill→params→compute→render→validate.

**3. Phased recovery plan.** §5 — Phase 0 observability → 1 TaskDefinition →
2 Grading reducer → 3 Practice step engine → 4 Exam engine → 5 Templates →
6 Language → 7 Eval/rollout, each flagged and rollback-able.

**4. First smallest implementation milestone.** **Phase 0 + the reducer's
read-only shadow:** add observability columns (`grader_source`, `active_step_id`,
`step_completed`, `task_completed`, `engine_version`) and run a `reduce_grading()`
in shadow that only *logs* its verdict alongside the current one. Zero behavior
change, fully reversible, and it produces the divergence data that de-risks Phase 2
— plus it immediately quantifies how often prose currently overrides the checker.

**5. Features to disable/freeze temporarily.**
- Freeze grade-8/9 skills that have no deterministic check from **exam** generation
  (they can stay in Explanation).
- Freeze GPT free-invention of exam items until Phase 4 (use only validated/oblast
  fallback items).
- Freeze the `_gpt_text_verdict`-as-authority path (prose→verdict) — demote to
  shadow only.
- Freeze new guard/contract patches (`grading_guard`, `_apply_*_contract`) except
  security/crash fixes — every new one deepens RC-2/RC-3.
- Freeze frontend task-extraction heuristic changes; it is being removed in Phase 1.

**6. Five highest-risk existing code paths.**
1. `ai_tutor_service._finalize_response` (lines ~5547-6026) — the ~8-arm task_text
   branch + exam override + prose rewrites; the single densest failure surface.
2. `ai_tutor_service._prepare_chat` contract chain (order-coupled ~40 mutators).
3. `grading_guard.enforce_grading_consistency` (+ ~30 prose-surgery helpers) —
   correctness depends on regex over model prose.
4. `ai_tutor_service._exam_state_for_response` / `_normalize_exam_state` /
   `_deterministic_exam_response` — exam state rebuilt from prose+heuristics each turn.
5. `_answer_verdict_for_response` + `_apply_gpt_fallback_verdict` — where GPT prose
   currently outranks the deterministic checker.
