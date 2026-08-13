# Live release gate

The live release gate is mandatory before pushing a local commit that changes MAT-BOT behavior. It runs the real Practice pipeline against the real model only when a developer starts it deliberately; the pre-push hook only checks the saved result and never starts paid calls.

1. Run focused FakeLLM tests and the full suite.
2. Create the local commit to be tested.
3. Ensure the worktree is clean.
4. Install the committed hook once per clone:

```powershell
git config core.hooksPath .githooks
```

5. In a PowerShell process with the API key already inherited, run the exact current-HEAD gate:

```powershell
cd "C:\Users\Korisnik\ai-matematicari"

if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    throw "OPENAI_API_KEY nije prisutan."
}

$env:MATBOT_PRACTICE_DIFFICULTY_LEVELS = "enabled"
$env:MATBOT_FAST_SINGLE_CALL_SCOPE = "model_backed"
$env:AI_TUTOR_TIMEOUT = "45"

& .\.venv\Scripts\python.exe `
  .\tools\run_live_release_gate.py
```

The runner uses only this process environment. It does not load `.env`, never prints the API key, refuses a dirty worktree, and stores the result at `scratchpad/live_release_gate/<COMMIT_SHA>.json` plus `latest.json`. Generated result files are ignored and must not be committed.

The required campaign contains 14 scenarios. The plan covers **every** Practice route: `contract_fresh`/`contract_harder` exercise the deterministic K1/K3 generator (1 call each), `semantic_fresh`/`semantic_harder` exercise a lesson with a complete deterministic generator (**0 calls each**, proven by strict equality), and the model-backed scenarios exercise the **fast single-call route** (1 call each). Help is server-owned: `full_solution` is always 0 calls and `first_hint` is derived from the help policy before the turn (0 or 1), so the static plan is **10 calls**.

A Reviewer repair is **conditional**: it may add at most one more call to a model scenario. The gate does not accept that as a vague range — it requires the turn's *stage composition* to prove it, so a second call is valid only when it is a **Reviewer** stage. Two tutor-class calls in one turn are a hidden retry and still fail. The ceiling therefore covers the worst permitted outcome (**21**), and one call above it is refused before delegation. A PASS is valid only for the exact tested commit and tree, and expires after 24 hours.

The gate must run with the production route scope:

```powershell
$env:MATBOT_FAST_SINGLE_CALL_SCOPE = "model_backed"
```

Production receives that value from the deploy workflow, which writes it into the VPS `.env` the same way it writes `APP_VERSION`.

To check a completed result without any model or SDK activity:

```powershell
& .\.venv\Scripts\python.exe `
  .\tools\check_live_release_gate.py `
  .\scratchpad\live_release_gate\latest.json
```

On a behavior-affecting push, `.githooks/pre-push` verifies the result for each pushed local commit: PASS verdict, matching commit and tree hash, age, all fourteen completed scenarios, actual calls equal to the frozen plan, the refused call above the ceiling, and no hidden validation or infrastructure failure. It fails closed and prints the exact runner command when anything is absent or stale. Clearly documentation-only Markdown changes are exempt.

A passing live gate does not activate the production difficulty feature. It remains OFF until a separately authorized deployment and production smoke check.
