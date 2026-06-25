# 🤖 MAT-BOT – AI pomoćnik za matematiku

MAT-BOT je Flask web aplikacija koja pomaže učenicima osnovne škole (5–9. razred)
u rješavanju matematičkih zadataka, na bosanskom jeziku i u skladu sa NPP BiH.
Učenik postavi pitanje tekstom ili slikom; bot vraća postupak korak-po-korak sa
LaTeX prikazom (MathJax) i grafovima funkcija (Plotly).

## ✨ Funkcionalnosti

- Tekstualna pitanja → OpenAI model (sinhrono za kratka, asinhrono za duga)
- Slike zadataka → Mathpix OCR, fallback na OpenAI Vision
- Pravila rješavanja prilagođena razredu (5–6: veza operacija; 7–9: prebacivanje)
- Grafovi funkcija na zahtjev ("nacrtaj graf y=2x+1")
- Evidencija pitanja/odgovora u Google Sheet (best-effort)
- Async obrada preko Cloud Tasks + Firestore (Cloud Run), uz lokalni fallback

## 📁 Struktura projekta

```
├── app.py                # Cijeli Flask backend
├── templates/index.html  # Frontend (chat UI, inline CSS/JS)
├── tests/                # Pytest suite (svi vanjski servisi mockirani)
├── docs/                 # Audit, changelog, plan daljih koraka
├── requirements.txt      # Produkcijske zavisnosti
├── requirements-dev.txt  # + pytest za lokalni razvoj
├── Dockerfile            # Cloud Run image (gunicorn)
├── cloudbuild.yaml       # Build → push → deploy → health check
└── deploy.sh             # Ručni deploy (gcloud)
```

## ⚙️ Lokalno pokretanje (bez stvarnih ključeva)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt

# LOCAL_MODE: bez Firestore/GCS/Cloud Tasks/Sheets; jobovi idu u lokalni thread
$env:LOCAL_MODE="1"
$env:OPENAI_API_KEY="sk-..."        # potreban samo za stvarne odgovore
$env:FLASK_SECRET_KEY="lokalna-tajna"
.\.venv\Scripts\python app.py        # → http://localhost:8080
```

Frontend ima iframe provjeru — lokalno otvaraj sa:
`http://localhost:8080/?from=thinkific&t=1`

## 🧪 Testiranje

```powershell
.\.venv\Scripts\python -m pytest -q
```

Testovi **ne zovu nijedan vanjski servis** (OpenAI, Mathpix, Sheets, GCS,
Firestore, Cloud Tasks su mockirani; pokušaj stvarnog mrežnog poziva obara test).

## 🔧 Env varijable

| Varijabla | Default | Opis |
|---|---|---|
| `OPENAI_API_KEY` | — | **Obavezno.** |
| `FLASK_SECRET_KEY` (ili `SECRET_KEY`) | nesiguran default + ERROR log | Potpis sesijskih kolačića. |
| `LOCAL_MODE` | `0` | `1` = bez GCP servisa, async u lokalnom threadu. |
| `OPENAI_MODEL_TEXT` / `OPENAI_MODEL_VISION` | `gpt-5-mini` / `gpt-5.2` | Modeli. |
| `OPENAI_TIMEOUT` / `OPENAI_MAX_RETRIES` | `HARD_TIMEOUT_S` / `2` | OpenAI klijent. |
| `HARD_TIMEOUT_S` / `SYNC_SOFT_TIMEOUT_S` | `120` / `8` | Tvrdi i sync-soft timeout. |
| `MAX_CONTENT_LENGTH_MB` | `20` | Maksimalna veličina zahtjeva (upload). |
| `UPLOAD_DIR` / `UPLOAD_MAX_AGE_S` | `/tmp/uploads` / `3600` | Privremeni fajlovi + starost za čišćenje. |
| `MATHPIX_APP_ID` / `MATHPIX_APP_KEY` / `MATHPIX_MODE` | — / — / `prefer` | OCR; `off` isključuje. |
| `GOOGLE_SHEETS_CREDENTIALS_B64` / `GSHEET_ID` / `GSHEET_NAME` | — | Sheets evidencija (lijena init). |
| `SHEETS_ASYNC_LOG` | `1` | Upis u Sheet van hot patha (thread). |
| `GCS_BUCKET` / `GCS_SIGNED_GET` | — / `1` | Slike za async jobove. |
| `USE_FIRESTORE` | `1` | Job store (inače in-memory — samo za 1 instancu!). |
| `TASKS_TARGET_URL` / `TASKS_QUEUE` / `TASKS_SECRET` / `REGION` | — | Cloud Tasks. |
| `RATE_LIMIT_ENABLED` | `1` | Rate limiting on/off. |
| `RATE_LIMIT_SUBMIT` / `RATE_LIMIT_DIAG` | `30 per minute` / `10 per minute` | Limiti po IP-u (X-Forwarded-For). |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | Storage za limiter (per-instanca!). |
| `CORS_ORIGINS` | sve (kompatibilnost) | Zarezom odvojene dozvoljene domene. |
| `DIAG_TOKEN` | — | `X-Diag-Token` header za `/sheets/*`, `/mathpix/selftest`, `/gcs/signed-upload` van LOCAL_MODE. |
| `ALLOW_PRIVATE_IMAGE_URLS` | `0` | `1` isključuje SSRF zaštitu (samo dev). |
| `HISTORY_MAX_TURNS` / `HISTORY_MAX_CHARS` / `HISTORY_CONTEXT_TURNS` | `5` / `2000` / `5` | Kontekst razgovora. |
| `COOKIE_SECURE` / `FRAME_ANCESTORS` | `0` / — | Kolačići / CSP za iframe. |

## 🚀 Deploy

Cloud Build (`cloudbuild.yaml`) radi build → push → deploy na Cloud Run → health
check. **Napomena:** deploy se radi isključivo svjesno i nadzirano — vidjeti
`docs/fable-next-steps.md` prije sljedećeg deploya (CORS, rate limit kalibracija,
slimovanje dependencija).

## 📚 Dokumentacija

- `docs/fable-audit.md` — kompletan audit koda
- `docs/fable-changes.md` — šta je promijenjeno i zašto
- `docs/fable-next-steps.md` — šta raditi dalje (produkcijske odluke)

## 👤 Autor

Faris Mujačić
