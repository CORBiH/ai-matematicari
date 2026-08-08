# MAT-BOT VJEŽBAJMO V1 — SEMANTIC FIDELITY PHASE (F5K)

Commit range: `5c8a8b5` (baseline, prior real-state audit) → `4ee5b89`+ (this phase).
All numbers below are mechanically derived from the referenced artifacts.

## What changed

Server-owned **semantic practice contracts**: 27 blocking lessons across 6
requirement types (graph 12, net 8, congruence criterion 4, congruence proof 1,
rational word problem 1, algebraic identity proof 1), compiled from
`data/semantic_practice_contracts.json` with Phase-1/2 NPP provenance, enforced
by generic bounded feature checkers (`matbot/semantic_practice.py`) at draft
preflight AND at the final-package gate before any state mutation. Identical
contract text goes to Tutor and Reviewer; `inside_lesson` now means "satisfies
the contract". Fake references to absent pictures are globally rejected.

## Historical replay

`historical_failures_replay.jsonl`: **14/14 captured P1 packages REJECTED**
by the new validator; positive counterparts for every category pass
(tests/test_semantic_practice_fidelity.py, 43 tests).

## Non-deterministic classification (semantic_contract_audit.json)

| Bucket | Count |
|---|---|
| SEMANTIC_BLOCKING_READY | 27 |
| SEMANTIC_ADVISORY_ONLY | 0 |
| VISUAL_ESSENTIAL | 57 |
| INSUFFICIENT_EVIDENCE | 90 |
| NEEDS_NEW_REPRESENTATION | 8 |
| (deterministic, unchanged) | 352 |

## Targeted wave (targeted_results.jsonl + targeted_rerun_k07.jsonl)

13 scenarios / 20 turns / 31 SDK calls (ceiling 40). All seven historical
lessons: 8 faithful publications (nets ×3, rational word problem, identity
proof, graphs via corrected packages), 4 protective fail-closes, 0 unfaithful
publications. One semantic false accept found by manual review (K07: included
angle expressed by ∠-notation with the SSU claim in the marked option) — fixed
generically (notation-based detection + options scanned), regression-pinned,
and proven blocked in the single authorized rerun.

## MODEL50 fresh audit (seed 20260809; model50_results.jsonl)

50 turns over 25 stratified non-deterministic lessons; **83/100 SDK calls**, no
over-budget attempts, no retries.

| Metric | Old audit (20260808) | New MODEL50 |
|---|---|---|
| Publish rate | 98% | 90% |
| **Useful-turn rate** | **70%** | **90%** |
| **Lesson-fidelity failures** | **14/50** | **0/50** |
| Semantic false accepts | n/a (no validator) | **0** |
| Fail-closed | 1/50 (2%) | 4/50 (8%) |
| Timeouts | 1/50 | 0/50 |
| Wrong math published | 0 | 0 |
| Wrong MCQ published | 0 | 0 |
| State corruption | 0 | 0 |
| Routing errors / third calls | 0 | 0 |
| Latency median / p95 / max (model) | 24.0s / 54.1s / 70.0s | 21.8s / 36.8s / 44.6s |

The four non-useful turns are all protective: two solution help-turns rejected
for unsafe MathJax notation (tasks preserved), one SSU fresh turn blocked by
the semantic validator (notation detector fired live), one harder turn
fail-closed by the Reviewer's honest difficulty judgment. Every published task
on a contracted lesson satisfied its contract (0 semantic violations across
all strata; graph 8/8, net 6/6, rational word 2/2 useful).

## Boundary

`MECHANICALLY_PROVEN`: validator results, call accounting, state checks,
latency. `FABLE_REVIEW_NOTE`: my manual reading of every published task text
in both waves — recorded per turn in the results files; the K07 finding came
from this channel and was then mechanized.
