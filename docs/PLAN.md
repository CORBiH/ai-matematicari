# MAT-BOT — Plan (od 2026-07-14)

Stanje: **852 testa zeleno**. Sve iz adversarial audita je zatvoreno
(A1–A3, B1–B3, C1, C2, D1–D2) plus svi nalazi iz dječijih simulacija (N1–N12).
Ostaje infrastruktura, sigurnost i praćenje kvaliteta u produkciji.

---

## ⚠️ Nalaz koji mijenja prioritete

**`git push origin main` = automatski deploy na produkciju (Hetzner VPS), BEZ ijednog
testa prije toga.**

`.github/workflows/deploy-vps.yml` na push u `main` radi `git reset --hard`, build i
`docker compose up`. Jedina provjera je `/healthz` — dakle "aplikacija se digla".
**852 testa nikad se ne izvrše u CI-ju.** Slomljena logika koja se uredno pokreće
otišla bi u produkciju i niko to ne bi primijetio dok se dijete ne požali.

Sve tri današnje izmjene (`d9166e4`, `efa437a`, `fa3e669`) su **već žive**.

---

## P0 — Zaštitna mreža (uraditi prvo, 1 sat)

### P0.1 Test-gate pred deploy
Dodati `test` job u workflow; `deploy` dobija `needs: test`.
Testovi traju ~8 s — ne usporava ništa, a uklanja cijelu klasu rizika.
- **Prihvatanje:** namjerno slomljen test → deploy se NE izvrši.

### P0.2 Staging ili ručna potvrda za deploy
Trenutno svaki push na `main` ide u produkciju dok djeca rade.
Minimum: `workflow_dispatch` (ručno okidanje) umjesto auto-deploya na push,
ili zaštićena grana + PR. Odluka je Farisova (vidi "Pitanja" dolje).

---

## P1 — Sigurnost i konfiguracija (kod je spreman, fale vrijednosti u `.env` na VPS-u)

### P1.1 `CORS_ORIGINS` — **trenutno otvoren za SVE domene**
App loguje upozorenje pri startu. Postaviti stvarne origine embeda:
`CORS_ORIGINS=https://<skola>.thinkific.com,https://www.matematicari.com`
- **Blokira:** treba potvrditi tačne domene.

### P1.2 `FLASK_SECRET_KEY`
Kod već loguje ERROR ako fali. **Provjeriti logove prvog starta** — ako se
poruka pojavljuje, postaviti ključ (invalidira sesije, bezopasno).

### P1.3 Rate limit
Default `30/min` po IP-u. Škole iza zajedničkog NAT-a dijele IP → lažni 429.
Pratiti 429 prvu sedmicu, po potrebi `RATE_LIMIT_SUBMIT="90 per minute"`.

---

## P2 — Vidljivost kvaliteta u produkciji (letimo naslijepo)

### P2.1 Logovati `response.usage` (tokeni + trošak)
Bez ovoga ne znamo ni koliko košta ni da li trošak raste.

### P2.2 Sedmični živi smoke (~15 poruka)
Bugovi u ovom sistemu su **stohastični** — prolaze u izolaciji, padaju pod
kontekstom. Deterministički testovi ih po definiciji ne hvataju.
Skripta prolazi kroz ključne tokove i mjeri **stopu pada**, ne "prolazi/pada".
Kandidati: hint-međukorak, težina u rečenici, mikro-zadatak (N9), topic-detekcija.

### P2.3 Prag za topic-detekciju
C1 eval je sada 100% (40 poruka). Ubaciti ga u sedmični smoke da se
regresija primijeti odmah.

---

## P3 — Trošak i performanse

### P3.1 Izbaciti neiskorištene dependencije (provjereno grepom)
`numpy, sympy, matplotlib, scikit-learn, argon2-cffi, PyJWT, psycopg2-binary`
ne importuje se NIGDJE u `app.py`/`matbot/`/`scripts/`. Uz njih i
`build-essential`/`libpq-dev` iz Dockerfile-a i mrtvi `AUTH_*` secreti.
- **Dobitak:** image manji za nekoliko stotina MB, brži build i cold start.
- **Rizik:** nizak, ali traži nadzirani deploy + health check.

### P3.2 Uslovne sekcije prompta po razredu
~30% ušteda prompt tokena po pozivu. Mijenja ponašanje modela → testirati.

---

## P4 — Proizvod

### P4.1 Dječija simulacija na NOVOM ponašanju
Mikro-zadaci (N9) su **potpuno novo ponašanje, nikad viđeno od pravog djeteta**.
Isto vrijedi za razjašnjenje kod slike s dva lista. Vrijedi jedna runda
simulacije baš na tim tokovima.

### P4.2 Preostala test-infrastruktura (E2/E3/E5 iz audita)
Offline eval proširenja, SSE parity test, frontend payload testovi. Nisko.

---

## Redoslijed

```
P0.1 test-gate ─┬─→ P1 (.env: CORS, SECRET_KEY)  ─→ P2 (usage log + sedmični smoke)
P0.2 deploy     ┘
                      P3 (slim deps) ── nezavisno, uz nadzirani deploy
                      P4 (kid-sim na N9) ── kad se P0/P1 slegnu
```

**Preporuka:** P0.1 danas (1 sat, uklanja najveći rizik), pa P1 čim potvrdiš domene.

---

## Pitanja za Farisa (blokiraju P0.2 i P1.1)

1. **Deploy:** smije li svaki push na `main` i dalje ići pravo u produkciju,
   ili hoćeš ručnu potvrdu / PR?
2. **CORS:** koje su tačne domene na kojima je bot embedovan?
3. **Korisnici:** ima li bot već prave učenike na sebi? (mijenja koliko je
   P0.2 hitan)
