# MatBot — Adversarial QA Audit (2026-07-11)

## 1. Executive summary

Izvršena je simulacija realističnog učeničkog korištenja kroz **177 konverzacija /
298 studentskih poruka** (291 sa stvarnim modelom `gpt-5-mini`, 7 mockiranih na
HTTP-endpoint nivou), pokrivajući sva 4 razreda (6–9), sva 4 moda
(Objasni mi / Vježbajmo / Kontrolni / Samo rezultat), image/OCR tok, SSE
streaming parity i adversarial ulaze.

**Rezultat: sistem je u osnovi zdrav** — 254/275 kataloških poteza PASS (92%),
matematika u quick/practice s determinističkim pokrićem nije nijednom pogriješila,
SSE parity 4/4, endpoint robusnost 7/7, adversarial 12/12 (prompt-injection
odbijena, dijeljenje nulom, nemoguće jednačine, mode-thrash — sve korektno).

Pronađeno je **11 potvrđenih bugova** (0 Critical, **2 High, 4 Medium, 5 Low**)
sa reproducibilnim dokazima, i 5 sumnji koje nisu klasifikovane kao bugovi.
Najozbiljniji nalazi su u **image→practice toku** (bot ignoriše učenikove
zadatke sa slike i izmišlja svoje, pa tačan odgovor djeteta proglasi netačnim —
3/3 sistematski) i u **parsiranju ordinalno imenovanih multi-odgovora**
("prvi je 6/9, drugi 4/8" — potpuno promašeno, 2/2).

**Da li je MatBot spreman za šire učeničko testiranje?** Uslovno DA za
tekstualne tokove (Objasni/Vježba/Rezultat/Kontrolni na jednostavnim zadacima) —
tu je kvalitet visok i konzistentan. **NE za slanje slika u Vježbi** dok se
AUD-01 ne popravi, jer sistematski obmanjuje dijete lažnim „Netačno".

## 2. Okruženje i ograničenja

- Nivo reprodukcije: **direct service** (`handle_chat` / `handle_chat_stream`)
  sa vjernim browser state-carry simulatorom (prema `templates/index.html`
  payload logici: `previous_next_state`, `last_tutor_task` na answer-fazi,
  historija, `recent_tasks`, confirmation intents). HTTP endpoint testiran
  mockirano (plumbing), SSE servisni generator testiran živo.
- **Pravi UI (browser) nije korišten** — nema Playwright okruženja; frontend
  logika je replicirana čitanjem koda. Payload-konstrukcijske greške frontenda
  van simuliranih putanja nisu pokrivene.
- **Mathpix OCR nije dostupan** (nema ključeva) — image tok testiran ubacivanjem
  `image_ocr_text` (isti ulaz koji OCR sloj proizvodi). Kvalitet samog OCR-a
  nije auditiran.
- Model: `gpt-5-mini`, `reasoning_effort=low`. Latencija: med **5.7 s**, max 14.5 s.
- Aplikativni kod NIJE mijenjan; sve skripte u scratchpad `audit/` direktoriju.

## 3. Arhitektura (sažetak toka zahtjeva)

```
Browser (templates/index.html: state-carry, chips, mode-switch heuristike)
  → POST /api/ai-tutor/chat (JSON ili multipart+slika) ili /chat/stream (SSE)
    → handle_chat / handle_chat_stream (matbot/ai_tutor_service.py)
       _sanitize_payload → contracts (confirmation / practice-help / new-task
       intent / result-mode) → _run_answer_check (answer_checker.py,
       deterministički) → topic lookup/detekcija (topic_detector.py: heuristika
       → LLM klasifikator) → prompt_builder.py + tutor_prompts.py →
       _call_model_with_retry (max_tokens po modu, retry s reasoning=minimal)
       → grading_guard.enforce_grading_consistency → bosnian.to_ijekavica →
       _finalize_response (task ekstrakcija, next_state, task_items,
       correct_streak, stuck_count, video reco, activity_log)
```

Stanje između poteza nosi klijent (localStorage → `previous_next_state`).

## 4. Metodologija

- Deklarativni scenariji (payload + očekivanja) + automatski validatori:
  Fraction-ekvivalencija vrijednosti u odgovoru, verdikt iz `answer_check`
  sažetka, label-first kontrakt, task-state asercije (`task_items`,
  `last_tutor_task`, `session_mode`, `final_topic`), zabranjeni/obavezni
  sadržaji i regexi, video flag, HR/ekavski leksik, kontradikcijski scan,
  repetition similarity (SequenceMatcher), dužina.
- Učenički jezik namjerno raznolik: bez dijakritika, tipfeleri („porcente",
  „dvanest"), sleng („brt msm da je 3/5 sigurica"), CAPS, emoji, kratki
  odgovori („da", „hajde", „ne znam", „tezi", „jos"), reference („za 2. je",
  „uradi treci", „zadnji", „ovo gore"), osporavanja, samouvjereni netačni odgovori.
- Ability-profili: struggling (hint-lanci, frustracija), average, advanced
  (ljestvica težine), random-answer, skip-to-solution, mind-change.
- Svaki model-zavisan nalaz re-run ×3 radi razdvajanja sistematskog od
  stohastičkog (`rerun_results.json` u katalogu).

## 5. Brojevi

| Metrika | Vrijednost |
|---|---|
| Konverzacija | **177** |
| Studentskih poruka (kataloških poteza) | **275** + 23 re-run = **298** |
| Sa stvarnim modelom | **291** |
| Mockirano (endpoint plumbing) | 7 |
| API poziva ukupno | ~327 |
| PASS / FAIL / WARN | 254 / 17 / 4 |
| Po razredu | g6: 210, g7: 21, g8: 13, g9: 20, bez: 11 |
| Po kategoriji | A:22 B:30 C:38 D:37 E:12 F:19 G:17 H:11 I:17 X:39 Y:33 |

**Zašto ispod 500 poruka:** pokrivenost je saturirala — pretposljednji batch
(X: exam + duge konverzacije, 39 poruka) dao je 1 FAIL čiji je korijen već
poznat (AUD-05), a posljednji batch (Y: confirmation tokovi, sleng-stres,
per-grade didaktika, 33 poruke) **0 nalaza**. Svi FAIL-ovi zadnjih 100 poruka
svode se na ranije identifikovane korijene; dalje poruke bi trošile budžet bez
novih informacija.

## 6. Matrica pokrivenosti

| Područje | Pokriveno | Nalazi |
|---|---|---|
| A Routing (4 razreda, selected/free/vague/cross-grade/ne-matematika) | ✅ | AUD-03 |
| B Explain (follow-upi, jednostavnije, video, repeticija, leak gradiva) | ✅ | AUD-08; koordinatni leak NIJE reprodukovan (fix drži) |
| C Practice state machine (hint/lakši/teži/da/ne/frustracija/skip) | ✅ | AUD-06 |
| D Grading (ekvivalencije, oblici, znak, nejednačine, jedinice, %) | ✅ | AUD-05, AUD-11; **0 pogrešnih matematičkih presuda** |
| E Multi-item reference (uradi prvi/za 2. je/zadnji/ordered) | ✅ | AUD-02, AUD-04, AUD-10 |
| F Image/OCR (single/multi/blurry/rate/persist/nova slika) | ✅ (OCR sloj simuliran) | **AUD-01**, AUD-07 |
| G Result mode (aritmetika/jednačine/tekstualni/multi/div-0) | ✅ | čisto |
| H SSE + endpoint parity | ✅ (H1 živo, H2 mocked) | čisto |
| I Adversarial (injection, empty, huge, mixed-lang, stale state) | ✅ | čisto |
| X Exam tok (generacija → djelimični odgovori → rješenja → novi) | ✅ | čisto (1 AUD-05 posljedica) |
| Y Confirmation/pending, sleng-grading, per-grade didaktika | ✅ | čisto |
| Pravi browser UI / pravi Mathpix / multipart slike | ❌ | ograničenje okruženja |

## 7. Potvrđeni bugovi (detalji i transkripti u MATBOT_BUG_REPRODUCTIONS.md)

| ID | Sev | Kratko | Reprodukcija |
|---|---|---|---|
| **AUD-01** | **High** | Practice+multi-slika: bot izmišlja svoje zadatke umjesto učenikovih sa slike → tačan odgovor djeteta = „Netačno" | **3/3 sistematski** |
| **AUD-02** | **High** | „prvi je 6/9, drugi 4/8, treci ne znam" — multi-odgovor potpuno promašen (parsing ∅, stanje ne napreduje, odgovori ignorisani) | **2/2 + offline dokaz** |
| AUD-03 | Med | LLM topic-klasifikator `unknown` za jasne poruke (g6/7/8) → tiha degradacija konteksta | 3/3 |
| AUD-04 | Med | Novi „Zadatak:" marker pregazi multi-item stanje dok stavke čekaju (server ne enforc-uje prompt pravilo) | 1/1 + kod |
| AUD-05 | Med | `derive_expected` rupe: procenti, zagrade `(-3)·4`, stepeni, Pitagora, sistemi, konverzije jedinica | offline determinist. |
| AUD-06 | Med | Osporavanje ocjene veže se kao odgovor na NOVI zadatak → „Netačno" za nepokušано | binding uvijek; tekst ~1/4 |
| AUD-07 | Low-Med | Quick+multi-slika ponekad riješi sve umjesto „koji zadatak?" | 1/4 stohastički |
| AUD-08 | Low | „Zadatak:" procuri u explain continuation (blok nema anti-task pravilo) | 1/4 stohastički |
| AUD-09 | Low | HR leksik promiče: „okomit", „zbroj", „decimalna tačka" | 3 live opažanja |
| AUD-10 | Low | Multi-stavke: zbirna labela na vrhu + dupla numeracija „1. 1." | 2 live |
| AUD-11 | Low | Bez determinističke presude model krši label-first kontrakt | 2 live (posljedica AUD-05) |

## 8. Ključni nalazi po područjima

**State machine.** Jednostavni tokovi (hint → lakši → teži → da/ne, ljestvica
težine, correct_streak, stuck-ramp, confirmation intents) rade pouzdano —
uključujući edge-ove poput odgovora s praznom historijom i mode-thrash.
Slabosti su koncentrisane na **meta-poruke o prethodnom potezu** (AUD-06) i
**višestavkovna stanja** (AUD-02/04): sistem nema pojam „poruka o prošlosti"
— sve što liči na broj tretira kao odgovor na aktivni zadatak.

**Grading.** Deterministički checker + guard rade odlično GDJE IMA POKRIĆA:
ekvivalentni oblici (2/4, 0,5, 1 1/2, −1/6, „2 i 1/4", „trideset"→30 nije
parsiran ali model korektan), znak, nejednačine s negativnim koeficijentom,
NZD/NZS, decimalna dijeljenja, linearne jednačine — **nijedna pogrešna presuda
u 298 poruka**. Rupe u pokriću (AUD-05) danas ne prave pogrešnu matematiku
(model je tačan), ali skidaju zaštitu label-kontrakta i mrežu za buduće modele.

**Image tok.** Result-mode single-image, kontekst-persist, druga slika preko
prve, „koliko je ispalo drugo sa one slike" — sve radi. Kritična rupa je
**fresh multi-slika u practice** (AUD-01) i djelimično quick (AUD-07).

**Routing.** Selected-topic rute 4/4 razreda tačne; heuristika hvata
distinktivne nazive; exam stale-topic kontrakt drži. LLM fallback klasifikator
je slaba karika (AUD-03).

**Streaming/parity.** Nema razlika sync vs stream u answer/mode/state/task-key;
SSE format ispravan; malformed payloadi vraćaju 200/400 bez 500-ica.

**Pedagoški/UX.** Empatija na frustraciju radi i u explain i u practice
(„Nisi glup — mnogi se zbune…"); imenovanje greške radi; per-grade didaktika
poštovana (g6 bez „prebacivanja", g9 sa); ijekavica čvrsta uz 3 HR izuzetka
(AUD-09). Repetitivnost pohvala u dugim nizovima (S-01) i povremeno duga
objašnjenja su stilske slabosti.

## 9. Rupe u postojećem test-sistemu (mapirano na nalaze)

Postojeći suite (743 testa, sve mockirano + eval harness offline):
- **Nema testa** da practice+fresh-multi-image koristi zadatke sa slike (AUD-01
  prolazi kroz sve postojeće testove jer image_test testovi počinju od već
  aktivnog stanja).
- **Nema parsing testova** za ordinalno imenovane odgovore (AUD-02).
- **Nema enforcement testa** „novi zadatak zabranjен dok stavke čekaju" (AUD-04).
- **Nema eval-a kvaliteta LLM klasifikatora** — eval_cases testiraju samo
  deterministički put (AUD-03 nevidljiv offline).
- **Nema challenge-intent testova** (AUD-06).
- derive_expected testovi pokrivaju implementirano; gap-lista (AUD-05) nema
  „expected-to-fail" marker pa se rupe ne vide.
- SSE parity nije u suite-u (sada dokazano živo — vrijedi dodati mockirani).

Duplikati/niska vrijednost: nisu nađeni značajni duplikati; suite je
konzistentan, ali je 100% mockiran — što je tačno razlog zašto AUD-01/02/06
nisu ranije uhvaćeni.

## 10. Prioritetne preporuke

Vidi `MATBOT_IMPROVEMENT_PLAN.md` (faze A–E sa zadacima, testovima i
kriterijima prihvatanja). Top 5 po vrijednosti:

1. **AUD-01** — image_test aktivacija za fresh multi-image u practice (ili
   prompt-vezivanje na OCR zadatke). *Prije šireg testiranja s djecom.*
2. **AUD-02** — ordinal parsing („prvi je X, drugi Y") u answer_checker.
3. **AUD-06 + AUD-04** — challenge-intent ruta + server-side gate za novi
   zadatak dok stavke čekaju (isti kontrakt-sloj).
4. **AUD-05** — derive_expected: procenti, zagrade, stepeni, jedinice (svaki
   solver ~30 linija; direktno smanjuje oslanjanje na model).
5. **AUD-03** — klasifikator: dodati sinonime/opise u prompt liste tema ili
   few-shot; mjeriti offline eval listom.
