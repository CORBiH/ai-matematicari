# Vježbajmo v1 — final production acceptance

Candidate: `a31ef4d` / tree `ab34a2b`. Test-only campaign; **no code was changed.**

## Verdict: PRACTICE_V1_ACCEPTANCE_FAIL

One confirmed zero-tolerance blocker: a published task whose **marked answer is
mathematically false**.

### The blocker (WRONG_MARKED, lesson 6-03-001, grade 6, model route)

> Koja tvrdnja tačno opisuje odnos brojeva $5$ i $20$?
>
> * $5$ nije povezan s brojem $20$ jer nisu jednaki.
> * $20$ je sadržilac broja $5$ jer je manji od njega.
> * **$5$ je sadržilac broja $20$ jer je $20=5\cdot4$.  ← MARKED CORRECT**
> * $20$ je djelilac broja $5$ jer je veći broj.

`sadržilac` = multiple. From `20 = 5·4` it follows that **20** is the multiple of
5, so "5 je sadržilac broja 20" is false. The justification attached to the
marked option actually supports the opposite statement.

No option is fully correct: option 2 carries the true claim ("20 je sadržilac
broja 5") with a false justification ("jer je manji od njega" — 20 is not
smaller than 5).

**The system contradicts itself inside the same lesson, in the same campaign.**
Another published task reads: *"Učenik je zaključio da je 4 sadržilac broja 20.
Koja ispravka je tačna?"* → marked **"4 je djelilac broja 20"**. The project
treats that exact phrasing as the error to be corrected, then marks the same
phrasing correct elsewhere.

**Why nothing caught it:** the error is domain-term semantics, not arithmetic.
`mcq_integrity` has no equation to solve, `option_equivalence` sees four distinct
strings, and `mathcheck` sees `20 = 5·4`, which is numerically true. This is
squarely in the semantic-authority UNKNOWN territory: `common_divisors_multiples`
has no implemented detector.

A programmatic sweep of every marked answer making a divisor/multiple claim
found 6 such claims, of which **4 records (1 distinct published task)** were
false — all the same task persisting across follow-up turns.

## Everything else was clean

| | |
|---|---|
| publication | 54/56 = **96.4 %** (target ≥95 %) |
| first-call publication | 83.9 % · Reviewer rate 16.1 % · repair 7/9 |
| wrong math / multi-correct / solution divergence | 0 / 0 / 0 |
| lesson drift / hint leak / state corruption | 0 / 0 / 0 |
| third model calls | **0** |
| grading | deterministic 4/4 correct, 2/2 incorrect; model route 2/2 and 2/2 |
| help | 1 active hint level, repeated hint identical 10/10, 0 model calls |
| difficulty | harder up 10/10, easier down 10/10 |

Performance: deterministic p50 5 ms / p95 14 ms; Luna first-call p50 8.5 s /
p95 13.2 s; Luna+Reviewer p50 18.0 s / p95 26.9 s.

## Non-blocking weaknesses

1. **Variety on the deterministic route** — 4 consecutive published pairs shared
   an identical structural signature (same exercise, different numbers). The
   `task_too_similar` guard is a model-route preflight finding only; the
   deterministic route guarantees a non-identical task but not a different shape.
2. **Recognition tasks with numerically equal options** — e.g. `72·10³`,
   `0,72·10⁵` and `7,2·10⁴` all equal 72000; only the justification separates
   them. Legitimate but close to the multi-correct line.
3. 2 fail-closed turns (6-05-001, 9-07-004) — correct fail-closed behaviour, no
   bad package published.
