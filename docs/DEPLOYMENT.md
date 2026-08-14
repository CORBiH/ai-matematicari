# MAT-BOT — deployment and rollback

Production runs on a Hetzner VPS: nginx (the only reverse proxy) → gunicorn in
Docker, bound to `127.0.0.1:8080`.

> **Pushing to `main` deploys to production.** There is no staging environment.
> Never push unless the user has asked for it in that session.

## Runtime

- `Dockerfile` — `python:3.11-slim`, `curl` (healthcheck only), gunicorn:
  `--workers ${WEB_CONCURRENCY:-1} --threads ${THREADS:-8} --timeout ${GUNICORN_TIMEOUT:-120}`.
- `docker-compose.yml` — service `matbot`, `restart: unless-stopped`, `env_file: .env`,
  published only on `127.0.0.1:8080`, healthcheck `GET /healthz` every 30 s.
- `app.py` — `ProxyFix(x_for=1, x_proto=1)`: trust exactly one hop, the nginx-added
  `X-Forwarded-For`. Do not add `x_host`/`x_port`/`x_prefix`; nothing needs them and
  trusting them widens the attack surface.
- Single worker by default, so the in-memory session store, rate-limit counters and
  turn locks are consistent. **Raising `WEB_CONCURRENCY` above 1 silently breaks
  all three** — there is no shared store. Do not raise it without adding one.

## Deploy (automatic)

`.github/workflows/deploy-vps.yml`, on push to `main` or manual dispatch,
`concurrency: deploy-vps` with `cancel-in-progress`:

1. Checkout, write the SSH key from secrets, `ssh-keyscan` the host.
2. SSH to the VPS and `cd` into the app directory.
3. `git fetch origin main` → **`git reset --hard origin/main`** (any manual edit on
   the VPS is destroyed — see the warning in `docker-compose.yml`).
4. Write the short SHA into `.env` as `APP_VERSION`.
5. **Persist the audited production configuration** —
   `sh deploy/apply_release_env.sh .env deploy/production_release.env` writes every
   value from the one declaration into `.env`, idempotently: an existing key is
   replaced in place, a missing key is appended, and nothing else in `.env` is
   touched. Secrets are neither read nor printed; only the *name* of each persisted
   key is logged.
6. `docker compose build`.
7. **Verify the configuration before the live service is replaced** —
   `docker compose run --rm --no-deps -T matbot python -m matbot.release_config --require`.
   This resolves the configuration exactly as the application does (including the
   code-owned fast-model choice) and exits non-zero on any deviation, so `set -e`
   aborts the deploy while the previously good container is still serving.
8. `docker compose up -d --remove-orphans`, then `docker compose ps` and the last
   80 log lines.
9. Poll `http://127.0.0.1:8080/healthz` up to 20 times, 3 s apart. Failure after 20
   attempts prints 150 log lines and exits non-zero.
10. **Verify the container that actually serves students** —
    `docker compose exec -T matbot python -m matbot.release_config --require`,
    which also prints the effective non-secret configuration into the workflow log.

Steps 5, 7 and 10 exist because production once ran silently on the legacy
single-call architecture while every release gate measured the universal one:
`MATBOT_PRACTICE_PIPELINE` and `MATBOT_PRACTICE_DIFFICULTY_LEVELS` were simply
absent from the VPS `.env`, and nothing failed — `/healthz` stayed green. The guard
written in response (`release_config.require_release_configuration`) was then never
called by anything for weeks. It is now on the real path, twice.

Required GitHub secrets (names only): `VPS_SSH_KEY_B64`, `VPS_HOST`, `VPS_USER`,
`VPS_APP_DIR`.

### The audited production configuration

`deploy/production_release.env` is the single declaration; `matbot/release_config.py`,
the deploy script, the live release gate and the offline artifact checker all read
it, so no value is written twice. It contains **no secret** and is committed. The
full table lives in [LIVE_RELEASE_GATE.md](LIVE_RELEASE_GATE.md#required-production-configuration).

`MATBOT_RELEASE_ENFORCEMENT=enabled` is what makes the running application refuse to
start on a wrong value. It is deliberately opt-in so local work and the test suite
still import `app` without production flags; its own absence in production is caught
by steps 7 and 10, which check unconditionally.

## Rollback

**Preferred — revert on `main`,** so the deployed state and the repository never diverge:

```bash
git revert <bad-sha>        # or: git revert <oldest-bad>..<newest-bad>
git push origin main        # the same workflow redeploys automatically
```

**Emergency — pin the VPS to a known-good commit** (leaves the VPS ahead of/behind
`main`; the next push to `main` will overwrite it, so follow up with a revert):

```bash
# on the VPS, in VPS_APP_DIR
git fetch origin
git reset --hard <good-sha>
docker compose build
docker compose up -d --remove-orphans
curl -fsS http://127.0.0.1:8080/healthz
```

**Config-only rollback** (a bad model/limit setting, no code change): edit the
variable in `.env` on the VPS and `docker compose up -d` to restart. No rebuild
needed — `.env` is read at container start.

> **This no longer works for anything in `deploy/production_release.env`.** Those
> values are rewritten from the declaration on *every* deploy, so a hand-edit on the
> VPS survives only until the next push — and the application will refuse to start
> on it in the meantime, because the declaration is also what the guard enforces.
> A deliberate rollback of the Practice architecture, the fast-route scope, the
> variety gate, single-hint, archetype or form rotation is therefore a **commit**:
> change the value in `deploy/production_release.env`, run the live release gate,
> and push. That is the point — the audited state is restored automatically instead
> of depending on someone remembering an undocumented server edit.

### Verifying a deploy

```bash
docker compose ps                       # matbot healthy
docker compose logs --tail=80           # no tracebacks on boot
curl -fsS http://127.0.0.1:8080/healthz # {"ok": true}
grep '^APP_VERSION=' .env               # matches the intended short SHA

# Which architecture is this process ACTUALLY running? (no secrets in the output)
docker compose exec -T matbot python -m matbot.release_config --require
```

The startup log carries the same line, once per boot:
`matbot_effective_configuration practice_pipeline=… fast_single_call_scope=…
fast_model=… single_hint=… archetype_rotation=… form_rotation=… timeout_seconds=…`.
If that line ever disagrees with the release-gate artifact, the deployment is
measuring one architecture and running another — the exact failure this chain
exists to make impossible.

Then check the public URL: load the page, open a lesson, send one Explain message.
Look for `explain_turn request_id=… ok latency_ms=… usage=…` in the logs.

## Configuration

`.env` lives on the VPS and is **never** read, printed, copied, or committed by
tooling or documentation. Names only:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | provider credential |
| `OPENAI_MODEL_TEXT` | runtime model (currently `gpt-5-mini`) |
| `MATBOT_REASONING_EFFORT` | reasoning effort (currently `low`) |
| `AI_TUTOR_TIMEOUT` | per-call timeout, seconds — **45**, written by deploy and enforced; the live release gate measures the same value |
| `MATBOT_MAX_OUTPUT_TOKENS` | output budget for Quick (1200) |
| `MATBOT_MAX_OUTPUT_TOKENS_PRACTICE` | output budget for Practice (2500, hard ceiling 4000) |
| `MATBOT_MAX_OUTPUT_TOKENS_EXPLAIN` | output budget for Explain (2500, hard ceiling 4000 — added 2026-08-01, default unmeasured against live output) |
| `MATBOT_MAX_MESSAGE_CHARS` | inbound message cap |
| `MATBOT_MAX_HISTORY_ITEMS`, `MATBOT_MAX_HISTORY_CHARS_PER_ITEM` | history bounds |
| `FLASK_SECRET_KEY` (alias `SECRET_KEY`) | signs the embed token; **the app refuses to start without it** |
| `MATBOT_TOKEN_TTL_SECONDS` | embed-token lifetime (7200) |
| `MATBOT_SESSION_LIMIT_PER_MINUTE` / `_PER_HOUR` | per-session rate limit (15 / 150) |
| `MATBOT_IP_LIMIT_PER_MINUTE` / `_PER_HOUR` | per-IP rate limit (120 / 1000) |
| `APP_VERSION` | written by the deploy workflow |
| `PORT`, `WEB_CONCURRENCY`, `THREADS`, `GUNICORN_TIMEOUT` | container/gunicorn |

Image-size and pixel limits are **hard constants** in `matbot/config.py` with no env
override, so a mis-set variable cannot raise a security boundary. Keep it that way.

Model and budget changes are a *deploy*: they change cost, latency and failure rate
in production immediately. Measure before changing `MATBOT_MAX_OUTPUT_TOKENS*`
(see C-9 in [CURRENT_STATE.md](CURRENT_STATE.md)).

## Operational notes

- Restarting the container clears rate-limit counters, Practice sessions and turn
  locks. Harmless, but a student mid-task loses the active question.
- `./storage` is mounted so it survives rebuilds. Nothing in the current AI-tutor
  path writes there — no student text, image, or conversation is persisted anywhere.
- Logs never contain: the API key, the embed token, the full prompt, the full model
  output, image bytes/base64/data URLs, EXIF, or filenames. `matbot/llm.py` scrubs
  anything resembling a secret out of diagnostics before logging.
