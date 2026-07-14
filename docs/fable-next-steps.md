# MAT-BOT — Preporuke za kasnije (zahtijevaju produkcijsku odluku ili nadzirani deploy)

Redoslijed = preporučeni prioritet.

## 1. Postaviti `FLASK_SECRET_KEY` provjeru u produkciji (odmah uz prvi deploy)
Kod sada čita `FLASK_SECRET_KEY` (koji cloudbuild već postavlja), pa će se ovo
samo od sebe popraviti pri prvom deployu. Provjeriti u logovima da NEMA error
poruke o defaultnom ključu. Napomena: promjena ključa invalidira postojeće
sesije (bezopasno — korisnik samo ponovo bira razred).

## 2. CORS allow-lista (čeka tačan Thinkific origin)
Postaviti `CORS_ORIGINS=https://<škola>.thinkific.com,https://www.matematicari.com`
(provjeriti stvarne domene embeda!). Bez env varijable ponašanje je staro (sve
domene) — namjerno, da se ništa ne slomi prije potvrde origina.

## 3. Rate limit kalibracija
Default `RATE_LIMIT_SUBMIT="30 per minute"` po IP-u. Škole iza zajedničkog NAT-a
dijele IP — pratiti 429 u logovima prve sedmice i po potrebi dići
(`RATE_LIMIT_SUBMIT="90 per minute"`). Za više instanci razmisliti o
`RATE_LIMIT_STORAGE_URI` ka zajedničkom storageu (memorija je per-instanca —
limit je tada efektivno N× veći, što je prihvatljivo za zaštitu od grubog abusea).

## 4. ~~Slimovanje dependencija~~ — ✅ URAĐENO (Phase 2 audit)
`numpy, sympy, matplotlib, scikit-learn, argon2-cffi, PyJWT, psycopg2-binary` su
uklonjeni iz `requirements.txt`, a `build-essential`/`libpq-dev` iz Dockerfile-a.
Provjereno 2026-07-14: nema ih ni u `app.py`, ni u `matbot/`, ni u `scripts/`.

**Preostalo (veće, čeka odluku):** legacy `/submit` stack je MRTAV — UI je `hidden`
u templateu, a `matbot/` (tutor) ne importuje NIŠTA iz root modula. Uklanjanjem bi
otpalo i `google-cloud-storage/firestore/tasks`, `gspread`, `google-auth`.

## 5. SameSite kolačići u iframe-u
Sesijski kolačić sa `SameSite=Lax` ne radi u Thinkific iframe-u. Pošto kontekst
sada ide kroz `history_json`, sesija je manje bitna — ali ako se želi da radi:
`SESSION_COOKIE_SAMESITE="None"` + `COOKIE_SECURE=1`. Testirati u stvarnom embedu.

## 6. Prompt optimizacije (uz pregled kvaliteta odgovora)
- Slati sekcije prompta uslovno po razredu (npr. `LINEARNA_FUNKCIJA` ne treba
  5. razredu) — ušteda ~30% prompt tokena po pozivu.
- Dodati eksplicitno "Odgovaraj isključivo na bosanskom jeziku (ijekavica)" u
  `ULOGA` — trenutno je jezik samo implicitan.
- `prompt_hints_json` koji frontend šalje (jezik/ton/terminologija) backend nikad
  ne čita: ili ga uvezati u system prompt ili ukloniti iz frontenda.
Sve troje mijenja ponašanje modela → testirati na uzorku stvarnih zadataka.

## 7. Deduplicirati legacy `/` POST rutu
Template ne renderuje njen output i frontend je ne zove — kandidat za svođenje
na tanki wrapper oko iste pipeline logike kao `/submit` (ili 410 nakon potvrde
da je niko ne koristi — provjeriti access logove).

## 8. Observability
- Logovati `response.usage` (tokene) po zahtjevu radi praćenja troška;
  opcionalno u postojeći Sheet red.
- Razmotriti alert na "Job store je in-memory" upozorenje u produkciji.

## 9. Sesijska historija u `/` ruti
Legacy ruta i dalje sprema pune HTML odgovore u session kolačić (prepunjavanje).
Pošto je ruta praktično mrtva, nije dirano — riješiti zajedno sa tačkom 7.

## 10. `datetime.utcnow()` deprecation
Python 3.11 (produkcijski image) je OK; pri prelasku na 3.12+ zamijeniti sa
`datetime.now(datetime.UTC)`.
