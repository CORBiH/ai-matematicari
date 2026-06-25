# MAT-BOT — Senior audit (jun 2026)

Audit lokalne kopije repozitorija (produkcija netaknuta). Aplikacija je arhitektonski
zdrava — jedan Flask backend (`app.py`), jedan template (`templates/index.html`),
sync/async tok preko Cloud Tasks + Firestore, Mathpix→Vision tok za slike.
**Rewrite nije potreban**; problemi su lokalizovani.

## Arhitektura (zatečeno stanje)

- Frontend šalje na `/submit` (AJAX). Lagana tekstualna pitanja → sinhroni OpenAI
  poziv (soft timeout 8s, `gpt-5-mini`); slike i "teška" pitanja → async job
  (Cloud Tasks → `/tasks/process`, ili lokalni thread fallback), rezultat u
  Firestore (ili in-memory dict), frontend polluje `/status/<job_id>` svake 2s.
- Slike: prvo Mathpix OCR (mode `prefer`), fallback na OpenAI Vision (`gpt-5.2`)
  sa base64 data URL-om.
- Prompt: veliki sekcijski bosanski system prompt sa pravilima po razredima
  (5–6: veza operacija; 7–9: prebacivanje članova), šalje se cijeli u svaki zahtjev.
- Svaki Q/A se best-effort upisuje u Google Sheet.
- Legacy: `/` POST ruta duplira cijeli pipeline, ali je praktično mrtva —
  template ne renderuje `history`/`error`, a frontend koristi samo `/submit`.

## KRITIČNI nalazi

### C1. Tajni ključ sesije — env mismatch (POPRAVLJENO)
`app.py` je čitao `SECRET_KEY`, a `cloudbuild.yaml` postavlja **`FLASK_SECRET_KEY`**.
Imena se ne poklapaju → produkcija gotovo sigurno potpisuje sesije javno poznatim
fallbackom `"tajna_lozinka"` (nalazi se u repou i README-u). Sesije su krivotvorive.

### C2. XSS na vision/slikovnoj putanji (POPRAVLJENO)
Tekstualni tok je escapovao odgovor modela; oba vision toka su ubacivala sirovi
output u HTML (`f"<p>{latexify_fractions(raw)}</p>"`), a frontend renderuje kroz
`innerHTML`. Prompt-injection preko slike → izvršenje skripte u browseru učenika.
Usput i bug prikaza: svako `<` u odgovoru (npr. `2 < 5`) lomilo je HTML.

### C3. Kontekst razgovora ne radi (POPRAVLJENO)
Četiri problema koja se sabiraju:
1. `/submit` je ignorisao `history_json` koji frontend uredno šalje.
2. Serverska `api_history` je čuvala **puni HTML odgovora u session kolačiću**
   (~4KB limit) — jedan stvarni odgovor ga prepuni i browser tiho odbaci kolačić.
3. Aplikacija živi u Thinkific **iframe-u** uz `SameSite=Lax` — kolačići se u
   third-party kontekstu uopšte ne šalju u modernim browserima.
4. Async jobovi (sve slike!) su išli sa `history=[]` — nula konteksta;
   follow-up "uradi i b)" nije mogao raditi.

### C4. Otvoren API: CORS + bez rate limita + javni dijagnostički endpointi (POPRAVLJENO/PRIPREMLJENO)
- `CORS(app, supports_credentials=True)` bez liste domena.
- `flask-limiter` u requirements, a nigdje upotrijebljen — `/submit` direktno
  troši OpenAI tokene bez ikakvog limita.
- `/mathpix/selftest` (košta novac), `/sheets/selftest` (spam u Sheet),
  `/gcs/signed-upload` (potpisani PUT URL-ovi svakome; frontend ga uopšte ne koristi)
  — svi javni.
- "Thinkific-only" provjera je client-side JS — trivijalno zaobilazna.

### C5. SSRF preko `image_url` (POPRAVLJENO)
`requests.get(image_url)` bez ikakve validacije sheme/hosta — sonda u internu
GCP mrežu; URL se na grešci prosljeđivao i OpenAI-ju.

### C6. Upload do 200 MB u RAM, /tmp bez čišćenja (POPRAVLJENO)
Default limit tijela 200 MB; fajlovi iz `/` rute pisani u `/tmp/uploads`
(na Cloud Runu = RAM) i nikad brisani.

## VISOKA VRIJEDNOST

- **H1 (popravljeno):** `strip_ascii_graph_blocks` je zbog opcionog prefiksa u
  regexu brisala **sve** fenced blokove, ne samo ASCII grafove.
- **H2 (popravljeno):** graf (`plot-request` div) se dodavao samo u sync putanji —
  async/slikovni odgovori nikad nisu dobili graf.
- **H3 (popravljeno):** `OPENAI_TIMEOUT` i `OPENAI_MAX_RETRIES` su postavljani
  kao secreti u cloudbuildu, a kod ih je ignorisao (hardkodirano).
- **H4 (popravljeno):** tihi `except: pass` na Sheets/GCS/Mathpix/enqueue putanjama;
  bez loga o tome koji job-store i queue se koristi.
- **H5 (popravljeno):** upis u Google Sheet je bio sinhron u hot pathu (+300–800ms
  po brzom odgovoru); inicijalizacija Sheets klijenta na importu (spor cold start).
- **H6 (popravljeno):** nije postojao nijedan test; sad ih je 104 (sve mockirano).
- **H7 (popravljeno):** `extract_requested_tasks` lažni pogoci: "Imam 5. razred" → zadatak 5;
  decimale "3.5" → zadatak 3; "zadnji" → literal `-1` u promptu.
- **H8 (popravljeno):** frontend polling: jedna prolazna mrežna greška prekidala je
  čekanje; uspješan async odgovor se NIJE upisivao u lokalnu historiju (a greška jeste — obrnuto).
- **H9 (dokumentovano, nije dirano):** `numpy, sympy, matplotlib, scikit-learn,
  argon2-cffi, PyJWT, psycopg2-binary` se ne importuju nigdje — stotine MB u imageu.
  Vidjeti `fable-next-steps.md`.

## Šta je namjerno NETAKNUTO

- Arhitektura (sync→async, Cloud Tasks, Firestore, polling).
- Izbor modela i OpenAI pozivni obrazac (`gpt-5.2`/`gpt-5-mini`, chat.completions).
- Pedagoški sadržaj promptova (pravila po razredima, terminologija) — samo je
  uklonjena jedna konstanta koja se nikad nije koristila.
- Oblici JSON odgovora `/submit`, `/status`, `/result` (embed zavisi od njih).
- Legacy `/` ruta (kompatibilnost unazad).
- Bosanski korisnički tekstovi.
- `Dockerfile`, `cloudbuild.yaml`, `deploy.sh`, `requirements.txt` (build/deploy
  promjene zahtijevaju nadzirani deploy — vidjeti next-steps).
