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
$env:AI_TUTOR_TIMEOUT = "45"

& .\.venv\Scripts\python.exe `
  .\tools\run_live_release_gate.py
```

The runner uses only this process environment. It does not load `.env`, never prints the API key, refuses a dirty worktree, and stores the result at `scratchpad/live_release_gate/<COMMIT_SHA>.json` plus `latest.json`. Generated result files are ignored and must not be committed.

The required campaign contains 12 scenarios and exactly 19 actual SDK calls. A twentieth attempted SDK invocation is refused before delegation. A PASS is valid only for the exact tested commit and tree, and expires after 24 hours.

To check a completed result without any model or SDK activity:

```powershell
& .\.venv\Scripts\python.exe `
  .\tools\check_live_release_gate.py `
  .\scratchpad\live_release_gate\latest.json
```

On a behavior-affecting push, `.githooks/pre-push` verifies the result for each pushed local commit: PASS verdict, matching commit and tree hash, age, all twelve completed scenarios, exactly 19 calls, the rejected twentieth call, and no hidden validation or infrastructure failure. It fails closed and prints the exact runner command when anything is absent or stale. Clearly documentation-only Markdown changes are exempt.

A passing live gate does not activate the production difficulty feature. It remains OFF until a separately authorized deployment and production smoke check.
