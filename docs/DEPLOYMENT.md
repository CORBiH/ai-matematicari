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
- Exactly one worker is mandatory so the in-memory session store, rate-limit
  counters, turn locks, and Reports concurrency guard are consistent.
  `deploy/production_release.env` pins `WEB_CONCURRENCY=1`; the deploy verifier and
  production startup reject any other effective value. The thread default remains
  eight.

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
6. Tag the image ID of the currently serving container as `matbot:rollback`, then
   run `docker compose build`. The tag is local to the VPS and contains no data.
7. **Verify the configuration before the live service is replaced** —
   `docker compose run --rm --no-deps -T matbot python -m matbot.release_config --require`.
   This resolves the configuration exactly as the application does (including the
   code-owned fast-model choice) and exits non-zero on any deviation, so `set -e`
   aborts the deploy while the previously good container is still serving.
8. **Preflight the exact persistent bind mount before replacement** —
   `docker compose run --rm --no-deps -T matbot python -m matbot.reporting_outbox --check`.
   This initializes/version-checks the local schema and performs a rolled-back
   main-database write probe. A missing writable directory is created; a
   read-only, locked, corrupt, or incompatible database aborts while the old
   container is still serving.
9. Run the additive Turso migration in the temporary container.
10. `docker compose up -d --remove-orphans`, then `docker compose ps` and the last
   80 log lines.
11. Poll `http://127.0.0.1:8080/healthz` up to 20 times, 3 s apart. Failure after 20
   attempts prints 150 log lines and exits non-zero.
12. **Verify the container that actually serves students** —
    `docker compose exec -T matbot python -m matbot.release_config --require`,
    which also prints the effective non-secret configuration into the workflow log.

Steps 5, 7 and 10 exist because production once ran silently on a different
Practice architecture than every release gate measured: two architecture flags
were simply absent from the VPS `.env`, and nothing failed — `/healthz` stayed green. The guard
written in response (`release_config.require_release_configuration`) was then never
called by anything for weeks. It is now on the real path, twice.

(One of those two flags, `MATBOT_PRACTICE_PIPELINE`, was **removed** on
2026-08-14 when the legacy single-call engine was retired: there is only one
engine now, so nothing is left to select. A stale value in the VPS `.env` is
inert — deploy no longer writes it and no code reads it.)

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

**Immediate local image rollback** after a failed container replacement (the
workflow creates `matbot:rollback` from the image that was actually serving):

```bash
# on the VPS, in VPS_APP_DIR; do not delete or replace ./storage
docker image inspect matbot:rollback >/dev/null
docker image tag matbot:rollback matbot:latest
docker compose up -d --no-build --force-recreate matbot
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
```

This restores code quickly but uses the current `.env`; follow it with the
preferred Git revert so code, configuration and `APP_VERSION` agree again.

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
| `MATBOT_REPORTING_OUTBOX_PATH` | durable SQLite/WAL reporting queue; Compose fixes it to `/app/storage/reporting-outbox.sqlite3` |
| `MATBOT_REPORTING_OUTBOX_POLL_S`, `_RETRY_BASE_S`, `_RETRY_MAX_S` | worker polling and bounded exponential retry cadence |
| `APP_VERSION` | written by the deploy workflow |
| `PORT`, `THREADS`, `GUNICORN_TIMEOUT` | container/gunicorn |
| `WEB_CONCURRENCY` | gunicorn worker count; production release contract requires exactly `1` |

Image-size and pixel limits are **hard constants** in `matbot/config.py` with no env
override, so a mis-set variable cannot raise a security boundary. Keep it that way.

Model and budget changes are a *deploy*: they change cost, latency and failure rate
in production immediately. Measure before changing `MATBOT_MAX_OUTPUT_TOKENS*`
(see C-9 in [CURRENT_STATE.md](CURRENT_STATE.md)).

## Operational notes

- Restarting the container clears rate-limit counters, Practice sessions and turn
  locks. Harmless, but a student mid-task loses the active question.
- `./storage` is mounted so the reporting outbox survives rebuilds and restarts.
  It stores structured activity facts and keyed pending identities only; no
  student message, tutor answer, image, conversation, or raw e-mail is persisted.
  The application creates and versions four local tables automatically:
  `outbox_meta`, `reporting_outbox`, `unattributed_activity`, and
  `approved_subjects`. This is not a Turso migration and does not alter the
  production reporting schema.
- Check privacy-neutral outbox state with
  `docker compose exec -T matbot python -m matbot.reporting_outbox --check`.
  A non-zero exit means the file is unavailable or contains permanently failed
  rows. Pending/retrying rows block only the affected student/month report.
- Back up `storage/reporting-outbox.sqlite3` with SQLite's online backup API (or
  stop the container before copying the database together with its `-wal`/`-shm`
  files). Never copy only the main file while the container runs. The Python
  standard library is already present in the application image, so a host
  `sqlite3` package is not required.
- Recovery is automatic: rows left `in_flight` by a crash return to `pending`
  at startup, and Turso writes are idempotent by their existing event/attempt
  keys. Unattributed rows do **not** move merely because an account is created,
  linked, roster-added, or later resolved. An administrator must open the linked
  student profile, re-enter the address and explicitly accept the unsigned-URL
  warning before historical rows are attributed.
- Exactly one Gunicorn worker and one `matbot` container may use this file. The
  release guard enforces one worker, and Compose declares one container; there is
  no cross-process claim owner/lease. Do not scale replicas or mount this SQLite
  file over NFS. Multi-process delivery can replay an active claim and race its
  acknowledgement even though the downstream Turso keys are idempotent.
- Delivered tombstones are retained to preserve local duplicate prevention;
  unattributed, retrying and permanently failed payloads are also retained.
  Therefore disk use is not intrinsically bounded. Monitor `file_bytes`, queue
  counts and free space. A retention/compaction policy is required before this
  is ever operated at materially larger volume.
- A permanent `failed` row intentionally blocks the affected student/month.
  There is no blind automatic retry for malformed payloads: inspect the
  privacy-neutral error code, preserve a backup, repair the cause, and use an
  audited one-off recovery procedure. Never delete the outbox to unblock a
  report.
- Pending subject keys are HMACs derived from `FLASK_SECRET_KEY`. Before rotating
  that key, drain or explicitly reconcile all `unattributed` rows; the raw
  e-mail is intentionally absent, so rows made with a discarded key cannot be
  reverse-mapped later.
- Rollback must preserve `./storage`. An older release ignores the outbox file;
  rolling forward restores delivery. Do not delete the file to roll back code.
  If possible, require `pending=0`, `in_flight=0`, and `failed=0` before rolling
  back; activity created while old best-effort code is serving is outside the
  durable guarantee and must be treated as a known reporting gap.
- Logs never contain: the API key, the embed token, the full prompt, the full model
  output, image bytes/base64/data URLs, EXIF, or filenames. `matbot/llm.py` scrubs
  anything resembling a secret out of diagnostics before logging.
