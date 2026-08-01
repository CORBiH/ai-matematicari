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
5. `docker compose build` → `docker compose up -d --remove-orphans`.
6. Print `docker compose ps` and the last 80 log lines.
7. Poll `http://127.0.0.1:8080/healthz` up to 20 times, 3 s apart. Failure after 20
   attempts prints 150 log lines and exits non-zero.

Required GitHub secrets (names only): `VPS_SSH_KEY_B64`, `VPS_HOST`, `VPS_USER`,
`VPS_APP_DIR`.

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

### Verifying a deploy

```bash
docker compose ps                       # matbot healthy
docker compose logs --tail=80           # no tracebacks on boot
curl -fsS http://127.0.0.1:8080/healthz # {"ok": true}
grep '^APP_VERSION=' .env               # matches the intended short SHA
```

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
| `AI_TUTOR_TIMEOUT` | per-call timeout, seconds (30) |
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
