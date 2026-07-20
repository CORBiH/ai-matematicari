# Engine V2 — Controlled Canary Rollout Plan (Phase 7)

**Status: PREPARED, NOT EXECUTED.** No deployment, no production config change.
Run `python scripts/eval_engine_v2.py` before every promotion step; all release
gates must be PASS.

---

## 1. Exact environment flags

All flags are read per-request from the environment; **default is off**, so an
absent variable is always the legacy path.

| Flag | Values | Default | Controls |
|---|---|---|---|
| `MATBOT_ENGINE_V2` | `off` \| `shadow` | `off` | Phase 0 shadow grading reducer + Phase 1 TaskDefinition emission (observability only) |
| `MATBOT_ENGINE_V2_GRADING` | `off` \| `on` | `off` | Phase 2: prose is never a grader (removes prose→verdict fallback) |
| `MATBOT_ENGINE_V2_PRACTICE` | `off` \| `on` | `off` | Phase 3 Practice Step Engine + Phase 5 deterministic practice task generation |
| `MATBOT_ENGINE_V2_EXAM` | `off` \| `on` | `off` | Phase 4 Exam Engine + Phase 5 topic-aware exam items |

Any unrecognized value resolves to `off` (fail-safe).

## 2. Staged order, modes and supported skills

Promote **one stage at a time**; never skip. Each stage runs its own canary window.

| Stage | Flags added | Blast radius | Supported scope |
|---|---|---|---|
| **S0 Observability** | `MATBOT_ENGINE_V2=shadow` | none (read-only telemetry) | all traffic |
| **S1 Grading** | `+ MATBOT_ENGINE_V2_GRADING=on` | verdicts only where prose was the grader | all grading turns |
| **S2 Practice** | `+ MATBOT_ENGINE_V2_PRACTICE=on` | Vježba only | guided skills: `divisibility_by_6`, `prime_factorization`, `linear_equation`, `fraction_add_sub`; generation for grade-6/7 covered temas |
| **S3 Exam** | `+ MATBOT_ENGINE_V2_EXAM=on` | Kontrolni only | grade-6/7 covered oblasti; uncovered → explicit labeled generic fallback |

**Grade 8/9 remain unsupported** — no V2 template, grading, or exam generation is
enabled for them; they stay entirely on the legacy path.

## 3. Identifying internal / beta sessions

The flags are process-wide, so a canary is a **separate process**, not a per-user
toggle:

1. Deploy a **second VPS instance / container** ("canary") from the same image,
   with the V2 flags set; the primary keeps all flags off.
2. Route only internal/beta traffic to it — either a distinct embed origin
   (e.g. `beta.` host) or a dedicated Thinkific test lesson whose widget points at
   the canary URL. `session_id` from those sessions is the canary cohort.
3. Tag canary rows for querying: set `ENGINE_CANARY=1` in the canary env and
   include it as a telemetry field (**follow-up: not yet emitted — add before S1**).
   Until then, cohort = sessions whose rows carry `shadow_telemetry` (S0+) or
   `engine="exam_v2"` (S3).

## 4. Monitoring: metrics and queries

**Sources:** Google Sheet (`shadow_telemetry` JSON column, `answer_verdict`,
`answer_verdict_detail`, `task_id`, `task_status`, counters), and
`GET /diag/engine-v2` (in-process aggregates, protected by `_diag_allowed`).

| Metric | Where | Healthy |
|---|---|---|
| shadow agreement rate | `shadow_telemetry.shadow_agrees_with_legacy` | ≥ 95%, stable |
| conflict mix | `shadow_conflict_type` | dominated by `no_conflict`; `legacy_prose_verdict` is the expected S1 change |
| grader source mix | `shadow_grader_source` | `deterministic` + `structured_gpt`; `none` not trending up |
| false-incorrect rate | `answer_verdict=incorrect` where checker said correct | **0** |
| ungraded rate | `answer_verdict` empty on answering turns | not materially above baseline |
| step completion | `step_cursor.is_complete` reached per started plan | ≥ 95% |
| exam integrity | one `graded` flip per answer; `exam_status` never active→active after completed | **0** violations |
| mode drift | rows where `session_mode != "exam"` inside an exam session | **0** |
| duplicate rows | identical `sheets_event_id` | **0** |
| latency | p50/p95 per turn | ≤ baseline + 25% |
| errors | `engine_v2 ... failed` / `exam engine` in logs | 0 |

Daily query set: agreement rate, conflict histogram, false-incorrect count,
ungraded count, exam violations, p95 latency, error count.

## 5. Canary duration

- **S0 Observability:** 3–5 days (needs volume for a stable conflict baseline).
- **S1 Grading:** 5–7 days (verdict changes need real student variety).
- **S2 Practice:** 5–7 days, ≥ 200 guided turns.
- **S3 Exam:** 5–7 days, ≥ 50 completed exams.

Minimum **48 h** at each stage even if volume targets are met early.

## 6. Promotion criteria (all must hold for the full window)

1. All 12 offline release gates PASS on the current commit.
2. 0 false-incorrect verdicts on deterministically-verified-correct answers.
3. 0 exam answer bleed, 0 mode drift, 0 completed-exam reopen.
4. 0 verdict/prose/counter contradictions.
5. 0 duplicate Sheets rows; 0 secrets/prompts in telemetry.
6. Step-plan completion ≥ 95%; ungraded rate not materially above baseline.
7. p95 latency ≤ baseline + 25%.
8. No unexplained `legacy_vs_v2` divergence class (every divergence must map to a
   documented intended change).
9. Zero P0/P1 student-reported language or grading complaints from the cohort.

## 7. Immediate rollback conditions (no discussion)

- Any false-incorrect on a verified-correct answer.
- Any exam answer bleed, mode drift, or completed-exam reopen.
- Any verdict/prose contradiction shown to a student.
- Ungraded rate spike (> 2× baseline).
- p95 latency > baseline + 50%.
- Any unhandled exception attributable to an Engine V2 path.
- Any secret/prompt/reasoning leak in telemetry.

**Rollback = unset the newest flag (or set it to `off`) and restart the canary
process.** Legacy is always the default path; no data migration is needed.

## 8. Performing flag changes safely

1. Change **one** flag per deployment; never combine.
2. Apply to the **canary process only**; primary stays all-off until promotion.
3. Prefer a **low-traffic window** (evening/weekend for a school product).
4. Restart is required (flags are read from the environment).
5. Record the change (flag, value, timestamp, commit) in a rollout log.
6. Re-run `scripts/eval_engine_v2.py` on the deployed commit **before** flipping.

## 9. Partially active legacy / V2 sessions

State travels through the client (`previous_next_state`), so a session can span a
flag change:

- **Grading (S1):** stateless per turn — safe in both directions.
- **Practice (S2):** flag off mid-session leaves a `step_cursor` in state that the
  legacy path simply ignores (it is an additive field); the task remains active via
  `last_tutor_task`. Degrades to prose-timed hints. **Safe.**
- **TaskDefinition (S0):** `next_state.task` is additive; legacy ignores it. **Safe.**
- **Exam (S3):** see §10 — the only case needing care.

## 10. Preventing flag flips from stranding exams

Current safeguards:
- The V2 exam engine **never takes over a legacy exam** already in flight
  (`should_handle` returns False when a non-v2 `exam_state` is present), so turning
  the flag **on** mid-exam is safe.
- Turning the flag **off** mid-exam is the risk: a `v2` `exam_state` would then be
  read by the legacy normalizer, which does not understand its shape.

Operational rules until a drain mode exists:
1. Only flip `MATBOT_ENGINE_V2_EXAM` **off** during a low-traffic window.
2. Prefer rolling back the **whole canary process** (traffic returns to the
   all-off primary) rather than flipping the flag on a live process.
3. A student caught mid-exam simply starts a new kontrolni; no data is lost
   (exam state is per-session, not persisted server-side).

**Recommended follow-up (not implemented):** add `MATBOT_ENGINE_V2_EXAM=drain` —
serves existing `v2` exams to completion but starts no new ones — plus a legacy
guard that ignores (rather than mangles) an `engine=="v2"` `exam_state`. This
removes the only unsafe flip direction.

## 11. Pre-flight checklist

- [ ] `python scripts/eval_engine_v2.py` → ALL GATES PASS
- [ ] `pytest -q` green with flags off and with each V2 flag on
- [ ] `node scripts/check_js.mjs` green
- [ ] `git diff --check` clean
- [ ] Rollout log entry prepared
- [ ] Monitoring queries saved and baseline captured
- [ ] Rollback owner and window agreed
