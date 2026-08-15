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
| nerješiv / nepotpun zadatak (nalaz A25) | `task_self_contained` |
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

### Talas B — 60 scenarija izvedenih iz nalaza Talasa A

Napisan **tek nakon** izvršenog Talasa A (commit `1d1e84a`, 100 poziva,
30 REVIEW / 10 FAIL). Svaki red nosi obavezno polje
`targets_wave_a_findings` — ID scenarija iz Talasa A i/ili root-cause
kategoriju. Scenario bez te veze `--dry-run` odbija kao nasumičan uzorak.

| Grupa | ID-evi | n | Šta potvrđuje |
|---|---|---|---|
| Stopa pada zbog dokaza težine na nivou 1 | B01–B22 | 22 | 8 od 10 FAIL-ova; iste lekcije se ponavljaju 2–3× jer je A10 pokazao da isti zahtjev jednom padne a jednom prođe |
| Recenzent: nevidljiv `solution` i samoprotivrječnost | B23–B28 | 6 | A34 (`nebezbjedan zapis [solution]` tek u objavi), A37 (`correct` uz oborene vlastite provjere) |
| Nerješiv zadatak i MCQ granice | B29–B36 | 8 | A25 (objavljen zadatak bez ijednog izraza), ekvivalentne opcije, dva rješenja |
| Vjernost lekciji i primjerenost razredu | B37–B44 | 8 | A17 (Pitagora u 7. razredu), A20 (π u 7. razredu), A14, A38 |
| Follow-up tokovi (odvojeni preduslovom) | B45–B54 | 10 | hint 1/2/3, pogrešan pa tačan klik, idempotencija, nivo 1→2→3, spust 2→1, promjena lekcije, slobodan tekst |
| Kontrole i evaluator | B55–B60 | 6 | tokovi koji su u A prošli + provjera da je A10 bio test-design, ne bug |

Raspodjela: **45** scenarija cilja dokazane P0/P1 uzroke · **13** su kontrole ·
**16** pokriva kritične lekcije i granice ocjenjivanja · **17** session / JSON /
LaTeX / MCQ / recovery. Razred 9 dobija **26 od 60** (43 %), jer je u Talasu A
imao samo 30 % determinističkog prolaza.

**Trošak: min 84, tvrdi plafon 150** poziva. `--wave all` (A+B) ima plafon
**250**; Talas A se time ponovo izvršava, pa ga pokreni samo kad to zaista želiš.

#### `requires_active_task` — preduslov, ne očekivanje

Talas A je pokazao da follow-up korak nakon neuspjelog generisanja proizvodi
FAIL-ove koji **nisu** nezavisni kvarovi (A10, A31, A35). Korak s
`requires_active_task: true` se preskače kad nema aktivnog zadatka: nula
poziva, nijedna provjera se ne izvršava, scenario ne može završiti kao strogi
PASS. Nijedno očekivanje se time ne ublažava — svaka provjera je identična za
korak koji se stvarno izvrši.

### Ciljani talasi po porodici (`scenarios/family/`)

Fajlovi u `tools/practice_eval/scenarios/family/` **nisu** u podrazumijevanom
direktoriju scenarija: `--wave A|B|all` ih ne pokreće. Pokreću se isključivo
eksplicitnim `--scenarios <put>`, jer svaki cilja jednu dokazanu granicu, a ne
široku dijagnostiku. Uz svaki `.jsonl` stoji generator `.jsonl.py` koji ga
proizvodi i u docstringu nosi razlog postojanja.

#### Talas F4E — dva P0 nalaza iz produkcije (18 scenarija, max 62 poziva)

Napisan nakon ručnog produkcijskog smoke testa koji je dokazao dva defekta koje
uzorkovani release gate nije uhvatio:

- **P0-A** — objavljen MCQ „…koji od sljedećih brojeva je djeljiv i sa 6 i sa
  25?“ s opcijama 8 · 6 · 7 · 9. Broj djeljiv i sa 6 i sa 25 djeljiv je sa
  NZS(6,25)=150 — nijedna opcija nije bila tačna.
- **P0-B** — „Uradi ga ti“ je objavio POTPUNO NOV zadatak umjesto rješenja
  postojećeg.

| Grupa | ID-evi | n | Šta potvrđuje |
|---|---|---|---|
| Doslovan produkcijski niz (zadatak → hint → rješenje) | E01–E03 | 3 | P0-B; 3× radi mjerenja stope, ne jednog uzorka |
| Rješenje bez hinta, nov zadatak nakon rješenja, druga lekcija | E04–E06 | 3 | zabrana važi po UI akciji, ne po lekciji ni po redoslijedu |
| Istovremena djeljivost sa 6 i 25 | E07–E09 | 3 | P0-A na tačnom paru djelilaca iz nalaza |
| Drugi parovi/trojke djelilaca | E10–E12 | 3 | popravka je u parseru uslova, ne u brojevima 6 i 25 |
| Jedan djelilac i cifra-mjestodržač | E13–E14 | 2 | kontrole: uslov se ne proširuje, orakl preskače |
| Negacija i disjunkcija | E15–E16 | 2 | smiju samo sigurno pasti, nikad lažno dokazati |
| Klik i kontinuitet sesije | E17–E18 | 2 | označena opcija stabilna; klik nakon rješenja blokiran, 0 poziva |

Koraci „Ne znam“ i „Uradi ga ti“ šalju **tačan produkcijski payload** (`intent`,
`interaction_phase`, `last_tutor_task`), ne samo tekst dugmeta — jer se upravo
oslanjanje na tekst pokazalo kao uzrok P0-B.

Strukturu talasa čuva `tests/test_targeted_wave_f4e.py` (0 poziva).

#### `topic_id` na koraku

Korak smije promijeniti lekciju unutar iste sesije. To je jedini način da se
testira serverska invalidacija napretka pri promjeni teme
(`SessionStore.load` poredi `curriculum_fingerprint`). `--dry-run` provjerava i
tu lekciju.

---

## Komande

```powershell
# 0 SDK poziva
python tools/run_practice_eval.py --list
python tools/run_practice_eval.py --wave A --dry-run

# Live (traži OPENAI_API_KEY u OVOM procesu)
$env:MATBOT_PRACTICE_DIFFICULTY_LEVELS = "enabled"
$env:AI_TUTOR_TIMEOUT              = "45"
python tools/run_practice_eval.py --wave A --max-model-calls 100
python tools/run_practice_eval.py --wave B --max-model-calls 150

# Nastavak nakon prekida (preskače ID-eve koji su već u results.jsonl)
python tools/run_practice_eval.py --wave A --resume --output-dir <isti dir>

# Jedan scenario
python tools/run_practice_eval.py --scenario A14 --scenario A24

# Ciljani talas po porodici (nije u podrazumijevanom direktoriju)
python tools/run_practice_eval.py `
  --scenarios tools/practice_eval/scenarios/family/wave_f4e.jsonl `
  --max-model-calls 62
```

Zastavice: `--list`, `--scenario ID`, `--wave A|B|all`, `--resume`,
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
