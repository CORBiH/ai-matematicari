# Live release gate

The live release gate is mandatory before pushing a local commit that changes MAT-BOT behavior. It runs the real Practice pipeline against the real model only when a developer starts it deliberately; the pre-push hook only checks the saved result and never starts paid calls.

1. Run focused FakeLLM tests and the full suite.
2. Create the local commit to be tested.
3. Ensure the worktree is clean.
4. Install the committed hook once per clone:

```powershell
git config core.hooksPath .githooks
```

5. In a PowerShell process with the API key already inherited, run the gate:

```powershell
cd "C:\Users\Korisnik\ai-matematicari"

if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    throw "OPENAI_API_KEY nije prisutan."
}

& .\.venv\Scripts\python.exe .\tools\run_live_release_gate.py
```

**You no longer set the runtime flags by hand.** The gate applies the audited production configuration itself, from the one declaration every other consumer reads (`deploy/production_release.env`). An environment variable that is *absent* is filled in; one that is *present and different* is refused rather than silently overwritten, so a deliberate operator choice never disappears without a message. The applied values are echoed before the first call as `matbot_release_gate_configuration …` and stored in the result artifact.

This replaced the previous instruction to export `MATBOT_PRACTICE_DIFFICULTY_LEVELS`, `MATBOT_FAST_SINGLE_CALL_SCOPE` and `AI_TUTOR_TIMEOUT` manually. That instruction was the defect: the gate validated only two of the declared values and checked the timeout merely as "a positive number", so the campaign for `0c02fca` passed with **`timeout_seconds = 30.0`** — the built-in default — while production runs at 45. The route-selecting flags had the same gap: nothing forced the gate to measure production's fast-route scope or variety gate.

The runner uses only this process environment. It does not load `.env`, never prints the API key, refuses a dirty worktree, and stores the result at `scratchpad/live_release_gate/<COMMIT_SHA>.json` plus `latest.json`. Generated result files are ignored and must not be committed.

## The campaign

The required campaign contains **15 scenarios** and covers **every** Practice route:

| Scenario group | Route | Calls each |
|---|---|---|
| `contract_fresh`, `contract_harder` | deterministic K1/K3 generator | 1 |
| `semantic_fresh`, `semantic_harder` | lesson with a complete deterministic generator | **0** (proven by strict equality) |
| `migrated_deterministic` | measured-weak family moved to the Luna fast route | 1 |
| `fresh_level1`, `correct_choice`, `harder_level2`, `easier_level1`, `same_level_new`, `grade7`, `grade8`, `grade9` | Luna fast single-call route | 1 |
| `full_solution` | server-composed help | **0**, always |
| `first_hint` | server help policy decides before the turn | 0 or 1, frozen before the turn |

The static part of the plan is 11 calls; `first_hint` adds 0 or 1, so a passing campaign records **11 or 12 planned calls**. A Reviewer repair is **conditional**: it may add at most one more call to a model scenario, and only when the turn's *stage composition* proves the second call was a **Reviewer** stage. Two tutor-class calls in one turn are a hidden retry and still fail. The ceiling therefore covers the worst permitted outcome — **23** — and one call above it is refused before delegation.

A PASS is valid only for the exact tested commit and tree, and expires after 24 hours.

## Required production configuration

The gate measures, and the artifact records, exactly this — the same declaration the deploy workflow writes into the VPS `.env`:

| Variable | Value |
|---|---|
| `MATBOT_PRACTICE_DIFFICULTY_LEVELS` | `enabled` |
| `MATBOT_FAST_SINGLE_CALL_SCOPE` | `model_backed` |
| `MATBOT_DETERMINISTIC_VARIETY_GATE` | `enabled` |
| `MATBOT_DETERMINISTIC_PRACTICE` | `enabled` |
| `MATBOT_PRACTICE_SINGLE_HINT` | `enabled` |
| `MATBOT_ARCHETYPE_ROTATION` | `enabled` |
| `MATBOT_FORM_ROTATION` | `enabled` |
| `OPENAI_MODEL_TEXT` | `gpt-5-mini` |
| `MATBOT_REASONING_EFFORT` | `low` |
| `AI_TUTOR_TIMEOUT` | `45` |
| `MATBOT_RELEASE_ENFORCEMENT` | `enabled` |

The fast-model choice (`MATBOT_FAST_MODEL=gpt-5.6-luna`, `MATBOT_FAST_REASONING_EFFORT=low`, `MATBOT_FAST_REVIEWER_MODEL` derived from the fast model) is deliberately **code-owned** rather than an environment value — see the rationale at the bottom of `deploy/production_release.env`. It is still verified: `release_config.REQUIRED_EFFECTIVE_CONFIG` compares the *effective resolved* choice, which catches both a wrong environment variable and an unreviewed change to the built-in default.

## Checking a result without any model activity

```powershell
& .\.venv\Scripts\python.exe `
  .\tools\check_live_release_gate.py `
  .\scratchpad\live_release_gate\latest.json
```

On a behavior-affecting push, `.githooks/pre-push` verifies the result for each pushed local commit: PASS verdict, matching commit and tree hash, age, all fifteen completed scenarios, actual calls equal to the frozen plan plus proven escalations, the refused call above the ceiling, no hidden validation or infrastructure failure, **`timeout_seconds` equal to the production timeout**, and **`release_configuration` equal to the declaration**. It fails closed and prints the exact runner command when anything is absent or stale. Clearly documentation-only Markdown changes are exempt.

An artifact produced before this change carries neither `timeout_seconds` parity nor `release_configuration`, and therefore no longer authorizes a push — deliberately, because such a campaign cannot prove which architecture it measured.

## Configuration verification is not the same as the gate

The gate proves *behavior*. A separate, unpaid check proves *configuration*, and it runs on the real deployment path:

```
python -m matbot.release_config --require
```

The deploy workflow runs it twice — once in a throwaway container before the live service is replaced, once inside the container that actually serves students — and the application refuses to start under `MATBOT_RELEASE_ENFORCEMENT=enabled` when anything deviates. See [DEPLOYMENT.md](DEPLOYMENT.md).
