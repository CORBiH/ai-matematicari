# MAT-BOT

Bosnian-language maths tutor for grades 6–9 (osnovna škola, BiH). Flask + a
single-page frontend, backed by one OpenAI call per turn. No database.

## Modes

- **Vježbaj sa mnom** (`practice`) — one multiple-choice task at a time, graded by
  the server, with levelled hints and task-family progression.
- **Objasni mi** (`explain`) — explains a chosen lesson and answers follow-ups.
  Never grades, never sets a task.
- **Samo rezultat** (`quick`) — fast answer to a concrete task, from text or from a
  photo of the problem.

## Run locally

```bash
pip install -r requirements.txt
# .env must at least contain FLASK_SECRET_KEY (or SECRET_KEY) and OPENAI_API_KEY
python app.py                 # http://127.0.0.1:5000
python -m pytest -q           # 1308 tests, fully offline
```

Docker: `docker compose up -d --build`, then `curl -fsS http://127.0.0.1:8080/healthz`.

## Documentation

| Document | Contents |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Working agreement for Claude/Codex sessions — read first |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Request pipeline per mode, image upload, the one-call invariant, every deterministic validator |
| [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) | What is hardened, the open Explain risk register, known limitations, prioritized next steps |
| [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) | The four test layers, commands, baseline, rules for live model calls |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Deploy flow, rollback, configuration variables |

## Layout

```
app.py                Flask app, ProxyFix, body-size limit, /healthz
matbot/               backend: api, per-mode orchestrators, prompts, rules,
                      validators (mathsafe, mathcheck, geometrycheck, …), llm
templates/index.html  the entire frontend
data/topics.json      curriculum, built by scripts/build_topics_json.py
reference/            source curriculum documents (not parsed at runtime)
tests/                1308 offline tests
```

Code comments are written in Bosnian and usually cite the live finding that made the
code necessary — read them before changing anything.
