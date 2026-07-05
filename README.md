# 🤖 MAT-BOT – AI tutor za matematiku

MAT-BOT je Flask aplikacija koja učenicima osnovne škole pomaže u matematici,
na bosanskom jeziku i u skladu sa NPP BiH. Glavni proizvod je **modularni AI
tutor za 6. i 7. razred** (teme, zadaci, česte greške i kontrolni materijal
dolaze iz Excel fajlova u `data/`), ugrađen kao iframe u Thinkific.

## ✨ Funkcionalnosti

- **Modularni tutor** (`/api/ai-tutor/chat`): 4 moda — Objasni mi / Vježbaj sa
  mnom / Sutra imam kontrolni / Samo rezultat; tema se bira ručno ili detektuje
  iz pitanja (heuristike → LLM klasifikator); tema se NIKAD ne izmišlja
- **Streaming odgovora** (`/api/ai-tutor/chat/stream`, SSE): tekst stiže
  progresivno; pad streama automatski pada na non-streaming put. Slike idu
  non-streaming putem (OCR + Vision zajedno kad OCR nije dovoljan)
- **Quick-reply chips** + preporuka video lekcije poslije odgovora
- Slike zadataka → Mathpix OCR, fallback na OpenAI Vision
- Excel kao izvor istine: `data/6_razred/`, `data/7_razred/` (MASTER + Thinkific mapa)
- SQLite activity log (samo metapodaci, bez sadržaja poruka) u `storage/`
- Legacy `/submit` tok (5–9. razred, tekst+slika, sync/async) — API postoji, UI je skriven

## 📁 Struktura projekta

```
├── app.py                  # Flask backend (rute, OpenAI/Mathpix klijenti)
├── matbot/                 # Modularni tutor: content_loader, topic_lookup,
│                           #   prompt_builder, topic_detector, ai_tutor_service,
│                           #   activity_log
├── prompts.py              # Bazni sistemski prompt (5–9. razred)
├── data/{6,7}_razred/      # Excel: AI_MATH_CONTENT_MASTER + THINKIFIC_MAP
├── templates/index.html    # Frontend (home/onboarding + chat; inline CSS/JS)
├── tests/                  # Pytest (svi vanjski servisi mockirani, bez mreže)
├── scripts/check_js.mjs    # Sintaksa + behavior provjere frontend JS-a (node)
├── Dockerfile              # Produkcijski image (gunicorn)
├── docker-compose.yml      # VPS/lokalni run (port 8080, ./storage volume)
├── .env.example            # Env varijable sa objašnjenjima (kopiraj u .env)
├── docs/deploy/            # nginx primjer konfiguracije
├── docs/archive/cloud-run/ # Legacy Cloud Run deploy fajlovi (samo referenca)
└── .github/workflows/deploy-vps.yml  # Deploy na Hetzner VPS (push na main)
```

## ⚙️ Lokalno pokretanje (bez stvarnih ključeva)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt

# LOCAL_MODE: bez Sheets/GCP; async jobovi idu u lokalni thread
$env:LOCAL_MODE="1"
$env:OPENAI_API_KEY="sk-..."        # potreban samo za stvarne odgovore
$env:FLASK_SECRET_KEY="lokalna-tajna"
.\.venv\Scripts\python app.py        # → http://localhost:8080
```

Frontend ima iframe provjeru — lokalno otvaraj sa:
`http://localhost:8080/?from=thinkific&t=1`

Docker Compose (isto kao na VPS-u):

```bash
cp .env.example .env      # popuni bar OPENAI_API_KEY i FLASK_SECRET_KEY
docker compose up -d --build
curl -fsS http://127.0.0.1:8080/healthz
```

## 🧪 Testiranje

```powershell
.\.venv\Scripts\python -m pytest -q
node scripts/check_js.mjs
```

Testovi **ne zovu nijedan vanjski servis** (OpenAI, Mathpix, Sheets, GCS,
Firestore, Cloud Tasks su mockirani; pokušaj stvarnog mrežnog poziva obara test).

Eval kvaliteta odgovora (`docs/eval/RUBRIC.md`):

```powershell
.\.venv\Scripts\python scripts\eval_tutor.py            # DRY: rutiranje/promptovi, bez API-ja
# LIVE (svjesno, zove OpenAI): MATBOT_EVAL_LIVE=1 + OPENAI_API_KEY + --live
```

## 🔧 Env varijable

Kompletna komentarisana lista: [.env.example](.env.example). Pregled:

| Varijabla | Default | Opis |
|---|---|---|
| `OPENAI_API_KEY` | — | **Obavezno.** |
| `FLASK_SECRET_KEY` (ili `SECRET_KEY`) | nesiguran default + ERROR log | Potpis sesijskih kolačića. |
| `LOCAL_MODE` | `0` | `1` = bez GCP servisa, async u lokalnom threadu. |
| `OPENAI_MODEL_TEXT` / `OPENAI_MODEL_VISION` | `gpt-5-mini` / `gpt-5.2` | Modeli. |
| `OPENAI_TIMEOUT` / `OPENAI_MAX_RETRIES` | `HARD_TIMEOUT_S` / `2` | OpenAI klijent (legacy tok). |
| `AI_TUTOR_TIMEOUT` / `AI_TUTOR_MAX_RETRIES` | `45` / `1` | Modularni tutor: kraći timeout (frontend prekida na 60s). |
| `HARD_TIMEOUT_S` / `SYNC_SOFT_TIMEOUT_S` | `120` / `8` | Tvrdi i sync-soft timeout. |
| `MAX_CONTENT_LENGTH_MB` | `20` | Maksimalna veličina zahtjeva (upload). |
| `UPLOAD_DIR` / `UPLOAD_MAX_AGE_S` | `/tmp/uploads` / `3600` | Privremeni fajlovi + starost za čišćenje. |
| `MATHPIX_APP_ID` / `MATHPIX_APP_KEY` / `MATHPIX_MODE` | — / — / `prefer` | OCR; `off` isključuje. |
| `GOOGLE_SHEETS_CREDENTIALS_B64` / `GSHEET_ID` / `GSHEET_NAME` | — | Sheets evidencija (lijena init). |
| `SHEETS_ASYNC_LOG` | `1` | Upis u Sheet van hot patha (thread). |
| `GCS_BUCKET` / `GCS_SIGNED_GET` | — / `1` | Slike za async jobove (Cloud Run era). |
| `USE_FIRESTORE` | `1` | Job store (inače in-memory — samo za 1 instancu/workera!). |
| `TASKS_TARGET_URL` / `TASKS_QUEUE` / `TASKS_SECRET` / `REGION` | — | Cloud Tasks. **`TASKS_SECRET` nema default** — prazno = `/tasks/process` odbija sve. |
| `RATE_LIMIT_ENABLED` | `1` | Rate limiting on/off. |
| `RATE_LIMIT_SUBMIT` / `RATE_LIMIT_DIAG` | `30 per minute` / `10 per minute` | Limiti po IP-u (X-Forwarded-For). |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | Storage za limiter (per-instanca!). |
| `CORS_ORIGINS` | sve + WARNING log | Zarezom odvojene dozvoljene domene. |
| `DIAG_TOKEN` | — | `X-Diag-Token` header za `/sheets/*`, `/mathpix/selftest`, `/gcs/signed-upload` van LOCAL_MODE. |
| `AI_TUTOR_EMBED_SECRET` / `AI_TUTOR_TOKEN_TTL_S` | — / `7200` | Potpisani token za `/api/ai-tutor/chat[/stream]` (prazno = isključeno + warning); vidi `docs/deploy/embed-token.md`. |
| `ALLOW_PRIVATE_IMAGE_URLS` | `0` | `1` isključuje SSRF zaštitu (samo dev). |
| `HISTORY_MAX_TURNS` / `HISTORY_MAX_CHARS` / `HISTORY_CONTEXT_TURNS` | `5` / `2000` / `5` | Kontekst razgovora (legacy tok). |
| `MATBOT_DB_PATH` | `storage/matbot.sqlite3` | SQLite activity log (metapodaci tutora). |
| `COOKIE_SECURE` / `FRAME_ANCESTORS` | `0` / — | Kolačići / CSP za iframe. |
| `APP_VERSION` | `dev` | Postavlja ga deploy workflow (git SHA); vidi `/version`. |

Aplikacija pri startu (van LOCAL_MODE) loguje **`ENV SANITY`** upozorenja za
sve što nedostaje — provjeri `docker compose logs` odmah nakon deploya.

## 🚀 Deploy (Hetzner VPS)

Push na `main` pokreće `.github/workflows/deploy-vps.yml`, koji preko SSH na
VPS-u radi: `git reset --hard origin/main` → upiše `APP_VERSION` (git SHA) u
`.env` → `docker compose build && up -d --remove-orphans` → health check na
`http://127.0.0.1:8080/healthz`. Nginx na VPS-u proksira javni domen na
127.0.0.1:8080 — primjer konfiguracije: `docs/deploy/nginx.example.conf`.

**Šta živi samo na VPS-u (nije u repou):** `.env` sa stvarnim tajnama,
nginx site konfiguracija + SSL certifikati (certbot) i `storage/matbot.sqlite3`
(activity log; perzistira kroz `./storage` volume). GitHub Secrets za workflow:
`VPS_SSH_KEY_B64`, `VPS_HOST`, `VPS_USER`, `VPS_APP_DIR`.

**⚠️ Prvi deploy sa repo `docker-compose.yml`:** ako VPS već ima ručno održavan
compose fajl, `git reset --hard` će ga pregaziti repo verzijom — prije merge-a
uporedi ih (`diff`) i uskladi.

Health check ručno:

```bash
curl -fsS https://<domena>/healthz    # {"ok": true, ...}
curl -fsS https://<domena>/version    # koja je verzija (git SHA) živa
```

## 📚 Sadržaj (Excel je izvor istine)

Teme, lekcije, tipični zadaci, česte greške i kontrolni materijal žive u
`data/<razred>_razred/AI_MATH_CONTENT_MASTER_*.xlsx` (sheet `TOPICS`) i
`THINKIFIC_MAP_*.xlsx` (sheet `MAP`). Ništa od sadržaja se ne hardkodira u kod;
novi razred = novi folder sa ista dva fajla + dodavanje razreda u
`matbot/content_loader.SUPPORTED_GRADES`. Redoslijed redova u TOPICS sheetu je
nastavni redoslijed — UI ga poštuje (ne sortira abecedno).

## 📄 Dokumentacija

- `docs/fable-audit.md` — kompletan audit koda (jun 2026)
- `docs/fable-changes.md` — šta je promijenjeno i zašto
- `docs/fable-next-steps.md` — šta raditi dalje (produkcijske odluke)
- `docs/handoff/` — produktna specifikacija modularnog tutora (6. i 7. razred)

## 👤 Autor

Faris Mujačić
