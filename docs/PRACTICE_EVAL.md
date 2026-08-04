# Live dijagnostika moda „Vježbaj sa mnom“ (FAZA 1)

Zaseban evaluacijski sistem koji stvarnim modelom mjeri kvalitet Practice moda i
grupiše nalaze po root cause-u, **prije** bilo kakvih širokih izmjena koda.

Ovo **nije** i **ne zamjenjuje** [live release gate](LIVE_RELEASE_GATE.md).
Release gate ostaje netaknut: 12 scenarija, tačno 19 SDK poziva, vezan za
commit + tree, i dalje obavezan prije pusha. Ovaj alat ništa ne mijenja u njemu,
ne zaobilazi ga i ne može ga zadovoljiti.

| | Live release gate | Practice eval (ovaj alat) |
|---|---|---|
| Svrha | „smije li se ovaj commit pushati“ | „gdje Practice stvarno puca“ |
| Ulazna tačka | `tools/run_live_release_gate.py` | `tools/run_practice_eval.py` |
| Put poziva | `practice.run_practice_turn` direktno | prava Flask ruta `/api/ai-tutor/chat` |
| Obim | 12 scenarija / 19 poziva | 40 scenarija / **max 100 poziva** (Talas A) |
| Verdikt | PASS/FAIL, blokira push | izvještaj, ne blokira ništa |
| Čist worktree | obavezan | nije obavezan (bilježi se) |

---

## Šta se izvršava

Svaki potez ide kroz **cijeli produkcijski put zahtjeva**:

```
POST /api/ai-tutor/chat  (Flask test client, isti payload kao templates/index.html)
  → matbot/api.py::_guarded_chat_turn   (token → IP limit → parse → validation
                                          → session limit → turn lock)
  → matbot/practice.py::run_practice_turn
  → matbot/tutor/pipeline.py            (universal_two_call)  ILI
    matbot/practice.py::_run_legacy_single_call_turn (lekcije s ugovorom)
  → matbot/llm.py::OpenAIPracticeLLM    (STVARNI OpenAI poziv)
```

Runner dodaje **samo posmatrački omotač** oko adaptera: broji stvarne SDK pozive
na granici invokacije, hvata već scrubovanu dijagnostiku neuspjeha i zapamti
strukturisani paket. Ne dodaje retry, ne mijenja prompt i ne dira budžet poziva.

### Šta runner NAMJERNO mijenja (i mora biti u svakom izvještaju)

- **Rate limiteri se dižu** na praktično beskonačno, da kampanja ne proizvede
  lažne 429/409. Posljedica: rate limit, concurrency i auth ponašanje **nisu
  mjereni** u ovoj kampanji.
- **`FLASK_SECRET_KEY`**, kad ga nema u okruženju, kuje se efemerno za taj proces
  (embed token potpisuje i verifikuje ista instanca). Vrijednost se nigdje ne
  ispisuje niti zapisuje.
- **`.env` se nikad ne učitava.** Kao i release gate, koristi se isključivo
  okruženje procesa.

---

## Ocjenjivanje

### Deterministički sloj (odlučuje o PASS/FAIL)

Provjere pozivaju **postojeće validatore iz `matbot/`**, da bi evaluacija mjerila
isto što i server: `mathsafe`, `mathcheck`, `geometrycheck`, `terminology`,
`option_equivalence`, `mcq_integrity`, `tutor/package_preflight`, plus session
invarijante iz `SessionStore` i stvarni broj SDK poziva.

Pravilo je isto kao za validatore u aplikaciji: **provjera koja ništa ne može
dokazati vraća `SKIP`, nikad `PASS`.** Preskočeno diže scenario na REVIEW.

Puna lista imena: `tools/practice_eval/checks.py::known_check_names`.

| Traženo ponašanje | Provjera |
|---|---|
| validna response schema (oba smjera ugovora) | `response_schema` |
| tehnički fallback predstavljen kao uspjeh | `not_safe_error`, `no_fallback_text` |
| stvarno objavljen zadatak | `task_published`, `published` |
| lesson/topic fidelity | `lesson_matches`, `stays_in_lesson` |
| MCQ opcije i označena tačna opcija | `options_ok`, `package_clean` |
| choice grading | `verdict_correct`, `verdict_incorrect`, `correct_option_stable` |
| free-text grading (**nema oraclea**) | `free_text_grading_no_oracle` (uvijek SKIP), `no_verdict` |
| hint 1/2/3 i njihova različitost | `help_nonempty`, `hint_differs` |
| curenje odgovora prije `solution_request` | `hint_no_leak`, `no_answer_leak`, `reveal_absent` |
| puni postupak nakon `solution_request` | `solution_complete`, `reveal_present`, `task_completed` |
| session kontinuitet | `task_preserved`, `state_unchanged`, `identical_response`, `zero_calls` |
| lakši / teži / novi zadatak | `level:N`, `task_differs` |
| JSON, LaTeX i kontrolni znakovi | `no_leak`, `math_safe`, `no_control_chars`, `numeric_consistent`, `geometry_ok` |
| ponavljanje istog zadatka | `task_differs` |
| jezik i terminologija | `bosnian`, `terminology_clean` |
| budžet poziva | `calls_at_most:N` |

**HTTP 200 + `SAFE_ERROR_MESSAGE` je FAIL, nikad uspjeh** — `not_safe_error` mu
daje vlastito ime u izvještaju, a `TurnObservation.published` ga ne priznaje kao
objavljen turn. Isto važi za svaki drugi serverski canned tekst.

`free_text_grading_no_oracle` je **namjerno uvijek SKIP**: server ocjenjuje samo
klik na opciju, `answer_verdict` je za utipkan odgovor uvijek `null`, a modelov
`grading` se ne objavljuje niti commituje — dakle deterministički oracle ne
postoji. To je nalaz, ne propust runnera.

### Kvalitativni sloj (0–2 rubrika)

| Ocjena | Značenje |
|---|---|
| 0 | neprihvatljivo |
| 1 | djelimično prihvatljivo |
| 2 | dobro |

Rubrike: `clarity`, `grade_fit`, `lesson_alignment`, `hint_usefulness`,
`difficulty_appropriate`, `pedagogy`, `refusal_quality`, `unit_correctness`.

**Model-sudija nije uključen i ne smije se uključiti bez izričitog odobrenja.**
Dok ga nema, svaki scenario s rubrikom završava kao **REVIEW**, ne PASS. Prijedlog
za model-sudiju mora unaprijed navesti: koji model, koliko dodatnih poziva, šta
se ne može procijeniti deterministički, i rizik da isti tip modela ocjenjuje sam
sebe.

### Statusi

| Status | Kada |
|---|---|
| `PASS` | nijedna provjera nije pala, nijedna nije preskočena, nema rubrike |
| `FAIL` | bar jedna deterministička provjera je pala |
| `REVIEW` | nijedan deterministički pad, ali ima preskočene provjere ili nerazriješene rubrike |
| `INFRA_ERROR` | `llm_sdk_error` ili HTTP 5xx |
| `RATE_LIMITED` | SDK je vratio rate-limit grešku |
| `TIMEOUT` | `llm_timeout` |

Transport ima prednost nad `FAIL`: timeout ne smije biti prijavljen kao loš
kvalitet modela. Izvještaj uvijek daje **dvije** brojke — strogi `PASS` i
„bez ijednog determinističkog pada“ (`PASS + REVIEW`). Druga je ono što ova faza
stvarno mjeri; **`REVIEW` znači nedokazano, nikad dobro.**

---

## Scenariji

JSONL je **jedini izvor istine**: `tools/practice_eval/scenarios/*.jsonl`.
Runner nikad ne izmišlja korak kojeg u fajlu nema. Format i obavezna polja:
`tools/practice_eval/scenario.py`.

Svaki scenario nosi jedinstven ID, razred, oblast, lesson ID, važnost
(`critical`/`high`/`medium`/`exploratory`), početno stanje (izolovan
`session_id`), tačan niz učeničkih poruka, determinističke provjere po koraku,
eventualnu rubriku, deklarisan broj modelskih poziva i **razlog zašto postoji**.
`--dry-run` odbija scenario bez razloga, bez provjera ili s nepoznatom lekcijom.

### Talas A — 40 širokih dijagnostičkih scenarija

39 jedinstvenih lekcija od 534 = **7,30 %**, 27 od 36 oblasti, po 10 scenarija
na svaki razred 6–9. Pokriva: početak sesije, tačan/netačan/djelimično tačan
odgovor, ekvivalentan zapis, hint 1/2/3, „uradi ga ti”, zahtjev za novim
zadatkom, nejasan i vrlo kratak unos, jedinice, decimale naspram razlomaka,
negativne brojeve, jednačine i nejednačine, geometriju, tekstualne zadatke,
LaTeX/razlomke, kontinuitet sesije, zaštitu od ponavljanja zadataka i hintova,
promjenu teme / prompt injection, i pedagoški loš ali tehnički validan odgovor.

**Trošak: min 60, tvrdi plafon 100 modelskih poziva.** Budžet je izveden, ne
procijenjen:

| Vrsta koraka | Poziva | Broj koraka | Ukupno |
|---|---|---|---|
| generisanje zadatka, lekcija bez ugovora | 2 | 40 | 80 |
| generisanje zadatka, lekcija s ugovorom (3 od 6 postojećih) | 1 | 4 | 4 |
| hint / rješenje / klik / obična poruka | 1 | 16 | 16 |
| idempotentan retry i blokiran klik na završen zadatak | 0 | 2 | 0 |
| | | **62** | **100** |

Ako neki turn potroši više od svog arhitektonskog budžeta (npr. klik koji
proizvede novi zadatak → 2 poziva umjesto 1), plafon zaustavi kampanju ranije i
to se vidi kao `calls_at_most:1` pad. Nastavak ide preko `--resume`.

### Talas B — 60 ciljanih scenarija (namjerno NIJE unaprijed zaključan)

Piše se **tek nakon analize Talasa A**, po ovoj raspodjeli:

| Segment | Min. broj | Šta ispituje |
|---|---|---|
| Ponovljeni kvarovi | ≥ 30 | najčešće root cause kategorije iz Talasa A, na drugim lekcijama i razredima |
| Kritične lekcije i granice ocjenjivanja | ≥ 15 | lekcije s najvećom upotrebom + granice: ekvivalentne opcije, dva rješenja, jedinice, znak |
| Session / JSON / LaTeX / timeout / recovery | ≥ 10 | gubitak zadatka, idempotencija, `llm_schema_parse_error`, budžet tokena, oporavak nakon odbijenog turna |
| Regresija osnovnih tokova | ≥ 5 | tokovi koji su u Talasu A prošli bez determinističkog pada |

Zaključavanje Talasa B prije Talasa A značilo bi pogađanje kvarova umjesto
mjerenja.

---

## Komande

```powershell
# 0 SDK poziva
python tools/run_practice_eval.py --list
python tools/run_practice_eval.py --wave A --dry-run

# Live (traži OPENAI_API_KEY u OVOM procesu)
$env:MATBOT_PRACTICE_PIPELINE      = "universal_two_call"
$env:MATBOT_PRACTICE_DIFFICULTY_LEVELS = "enabled"
$env:AI_TUTOR_TIMEOUT              = "45"
python tools/run_practice_eval.py --wave A --max-model-calls 100

# Nastavak nakon prekida (preskače ID-eve koji su već u results.jsonl)
python tools/run_practice_eval.py --wave A --resume --output-dir <isti dir>

# Jedan scenario
python tools/run_practice_eval.py --scenario A14 --scenario A24
```

Zastavice: `--list`, `--scenario ID`, `--wave A|B`, `--resume`,
`--max-scenarios N`, `--max-model-calls N`, `--dry-run`, `--output-dir PATH`,
`--concurrency N` (1–4, podrazumijevano 1), `--delay-ms N`.

`--max-model-calls` je **tvrd plafon**: poziv iznad njega se odbija prije
delegacije SDK-u, isto kao u release gateu.

## Rezultati

Podrazumijevano `scratchpad/practice_eval/<YYYYmmdd-HHMMSS>/` (gitignorisano,
nikad se ne commituje):

| Fajl | Sadržaj |
|---|---|
| `run_meta.json` | commit, dirty status, model, effort, timeout, pipeline, plafon, stvarni broj poziva |
| `results.jsonl` | jedan red po scenariju — statusi, pale/preskočene provjere, svi turnovi, request i response |
| `summary.json` | mašinski čitljiv zbir po razredu/oblasti/važnosti/tipu interakcije + root cause |
| `report.md` | pregledan izvještaj s reprezentativnim primjerima |

Zapisuje se svaki zahtjev i odgovor. **Nikad se ne zapisuju** tajne, API ključ,
`X-Tutor-Token` ni ijedno zaglavlje — samo payload i tijelo odgovora, plus
log redovi s unaprijed dozvoljenim prefiksima (aplikacija ih već emituje
ograničene i scrubovane).

## Šta izvještaj NE dokazuje

- `numeric_consistent` po dizajnu preskače izraze s promjenljivom, procentima,
  stepenima, nejednačinama, korijenima i uređenim parovima.
- `geometry_ok` provjerava samo notaciju i samo unutar geometrijskih lekcija.
- `mcq_integrity` ima nezavisan oracle samo za djeljivost; za ostale porodice
  „tačno jedna tačna opcija“ ostaje nedokazano.
- Recenzentove `checks.*` su modelove tvrdnje i **nikad** se ne broje kao kvalitet.
- Rate limit, 409 i auth ponašanje nisu mjereni (limiteri podignuti).
- Pokrivenost lekcija se uvijek navodi kao izračunat broj i procenat, nikad kao
  procjena.
