# MatBot — Plan poboljšanja (iz adversarial audita 2026-07-11)

Redoslijed po zavisnostima i riziku. Svaki zadatak je ciljan fix — nigdje nije
potreban rewrite. Reference bugova: `MATBOT_BUG_REPRODUCTIONS.md`.

---

## Faza A — Kritična ispravnost (prije šireg testiranja s djecom)

### A1. Image→practice: radi UČENIKOVE zadatke sa slike (AUD-01)
- **Prioritet:** P0 | **Kompleksnost:** Medium | **Zavisnosti:** nema
- **Problem:** fresh multi-slika u Vježbi ne aktivira image_test tok; practice
  prompt generiše vlastite zadatke; tačan odgovor djeteta na svoj zadatak → „Netačno" (3/3).
- **Fajlovi:** `matbot/ai_tutor_service.py` (`_resolve_image_test_state`,
  `_prepare_chat`), po potrebi `matbot/prompt_builder.py`.
- **Pristup:** u `_resolve_image_test_state` dodati granu: svježa slika
  (`image_ocr_text`) sa ≥2 stavke u modu practice/exam **bez** druge jasne
  namjere → uđi u image_test sa `current = prva stavka` (style step_by_step).
  Alternativno (manji zahvat): kada slika postoji a image_test nije aktivan,
  u practice mode blok ubaciti tvrdu direktivu „ZADACI SA SLIKE SU AKTIVNI
  ZADACI — ne izmišljaj svoje" + postaviti `last_tutor_task` na OCR stavke.
  Preferirati prvu opciju (deterministična).
- **Rizici:** kolizija s postojećim „jedan zadatak sa slike = običan tok"
  pravilom; potvrde `continue_image_test` moraju ostati kompatibilne.
- **Testovi:** mockirani service-test (fresh OCR ≥2 stavke + practice →
  `_image_test` aktivan, prompt sadrži OCR zadatke; odgovor `7/10` na stavku 1
  → correct); postojeći image_test testovi moraju ostati zeleni.
- **Prihvatanje:** re-run audit scenarija `F-imagetest` i `R2` (rerun skripta)
  3/3 bez izmišljenih zadataka.

### A2. Ordinal multi-odgovori: „prvi je X, drugi Y, treći ne znam" (AUD-02)
- **Prioritet:** P0 | **Kompleksnost:** Small-Medium | **Zavisnosti:** nema
- **Fajlovi:** `matbot/answer_checker.py` (`parse_student_answers`,
  `detect_referenced_items`, `_ORDINAL_WORDS`).
- **Pristup:** (1) u `parse_student_answers` dodati ordinal-marker pass:
  `\b(prv|drug|trec|cetvrt|pet)\w*\s+(?:je|=)?` → segmentiraj kao numbered;
  (2) u `detect_referenced_items` dozvoliti goli ordinal + `je` („treci je 5/6")
  kao referencu; (3) postojeći `_NONANSWER_SEG_RE` („ne znam") automatski
  isključuje nepokušane stavke.
- **Rizici:** lažni pozitivi na rečenice tipa „prvi korak je…" — ograničiti na
  poruke bez glagola objašnjenja / na answer-fazu; dodati protiv-primjere u testove.
- **Testovi:** parsing unit (mode=numbered {1:6/9, 2:4/8}, 3 nepokušano);
  service-test da `task_items.graded` postane [1,2].
- **Prihvatanje:** rerun `R3` 0/2 reprodukcije.

### A3. Deterministički solveri: procenti, zagrade, stepeni, jedinice (AUD-05, AUD-11)
- **Prioritet:** P1 | **Kompleksnost:** Medium (4 nezavisna mala solvera)
- **Fajlovi:** `matbot/answer_checker.py` (`derive_expected` + novi `_try_*`).
- **Pristup:** `_try_percent_of` („X% od N" → N·X/100; i obrnuto „X% broja je V");
  normalizacija zagrada u `_EXPR_PREFIX_RE`/`_eval_expr` (strip `(` `)` oko
  jednog broja: `(-3)` → `-3`); `_try_power` (`a^b` mali eksponenti);
  `_try_unit_conversion` (m↔cm↔mm, kg↔g, h↔min — tabela faktora).
  Pitagora/sistemi: ostaviti (veći zahvat, model trenutno tačan) — zabilježiti.
- **Testovi:** unit po solveru + „gap-lista" test koji dokumentuje šta je
  namjerno nepokriveno.
- **Prihvatanje:** D-percent/X-units scenariji dobijaju verdikt iz checkera i
  label-first se enforc-uje.

---

## Faza B — Pouzdanost state-mašine

### B1. Challenge/meta-poruke ne idu u grading (AUD-06)
- **Prioritet:** P1 | **Kompleksnost:** Small | **Zavisnosti:** nema
- **Fajlovi:** `matbot/ai_tutor_service.py` (`_apply_practice_help_contract` —
  isti sloj gdje su 2026-07-11 dodani distress/vague detektori).
- **Pristup:** `_CHALLENGE_SIGNAL_RE = (pogrijesio si|nije tacno|nije tačno|
  nastavnica|učiteljica|profesor(ica)? (kaze|kaže)|sigurno je|meni je ispalo)` —
  kad poruka nosi challenge signal + broj koji NIJE svjež pokušaj na aktivni
  zadatak (npr. jednaka prethodno ocijenjenoj vrijednosti ili prati „nije
  tačno"), rutiraj u help/explain sa kontekstom POSLJEDNJE OCJENE (ne aktivnog
  zadatka), bez labele.
- **Rizici:** legitimni novi pokušaj („nije tacno, sad mislim 5/6") mora i dalje
  u grading — gate: ako poruka sadrži izraz „mislim da je/sad je" + novu
  vrijednost, gradi normalno.
- **Testovi:** parametrizovani routing testovi + regres da čisti odgovori i
  dalje idu u grading.

### B2. Server-gate: novi zadatak zabranjen dok stavke čekaju (AUD-04)
- **Prioritet:** P1 | **Kompleksnost:** Small | **Zavisnosti:** nema
- **Fajlovi:** `matbot/ai_tutor_service.py::_finalize_response`.
- **Pristup:** na grading potezu, ako `task_items` ima pending stavke →
  ignoriši `extract_marked_task` rezultat (zadrži postojeći multi-task i
  task_items; opciono odsijeci „Zadatak:" paragraf iz odgovora kao što guard
  već radi za labele).
- **Testovi:** postojeći `test_exam_task_persists_when_items_pending` proširiti
  slučajem kada model VRATI marker.

### B3. Quick+multi-slika deterministički ask-gate (AUD-07)
- **Prioritet:** P2 | **Kompleksnost:** Small
- **Pristup:** u result-mode putu: `extract_image_tasks(ocr) ≥ 2` i poruka ne
  bira stavku → deterministički vrati ask-message (postojeći
  `_multi_task_ask_message` mehanizam) umjesto oslanjanja na prompt.
- **Testovi:** mockirani service-test (multi OCR + prazna poruka → pitanje bez
  ijednog rezultata).

---

## Faza C — Routing i izolacija modova

### C1. LLM topic-klasifikator kvalитет (AUD-03) — ✅ URAĐENO (2026-07-14)
- **Prioritet:** P1 | **Status:** riješeno, live eval **45% → 100%** (40 poruka, razredi 6–9)
- **PRAVI korijen (nije bio "kvalitet prompta"):** `gpt-5-mini` je REASONING model,
  a `detect_topic_llm` je slao `max_tokens=60`. Svih 60 tokena odlazilo je na
  reasoning → `finish_reason="length"`, `content=""` → klasifikator je **UVIJEK**
  vraćao `unknown`. Nikad nije ni stigao odgovoriti. Dokaz: ista poruka/model sa
  `max_tokens=300` → `{"detected_topic":"8-04-025"}` (tačno).
- **Urađeno:**
  1. `max_tokens` 60 → **400** (mjereno: reasoning ~64 + ~20 za JSON).
  2. Lista tema sada nosi **OBLAST** (`npp_id | OBLAST | naziv`) + pravila
     značenjskog mapiranja i primjeri dječijeg jezika (hipotenuza→Pitagora,
     porcenti→Postotak, nagib prave→linearna funkcija).
  3. **Veto nad lažnim jednorječnim pogotkom heuristike**: dugi nazivi (8. razred,
     VERZAL) nose usputne riječi koje su slučajno jedinstvene — „…KAO OSNOVOM"
     je hvatao „mnoze stepeni sa istom OSNOVOM" → Geometrijska tijela; „SLIČNI
     monomi" je hvatao „SLIČNI trouglovi" → polinomi. Sada: ako druga sadržajna
     riječ (po STEMU) pokazuje isključivo na drugu oblast → `unknown` → LLM.
     Glagoli radnje („izračunaj") izuzeti iz veta.
  4. `is_vague_message`: propusnica za mat. pojmove kojih **nema u nazivima tema**
     (`medijana`, `modus`, `hipotenuza`) — ranije su bili „vague" pa LLM nikad
     nije pozvan. Meta-poruke („sutra imam kontrolni") ostaju vague (bez troška).
- **Regresije:** `tests/test_topic_detector.py` (6 novih testova zaključavaju sve
  gore navedeno, uključujući da meta-poruke ostaju vague).

### C2. HR/ekavski dopune (AUD-09)
- **Prioritet:** P3 | **Kompleksnost:** Small
- **Fajlovi:** `matbot/bosnian.py`, po potrebi `tutor_prompts.py`.
- **Pristup:** zamjene: `okomit\w*`→„pod pravim uglom" (oprez: „okomito na"
  konstrukcije — riješiti frazama), `zbroj\w*`→zbir/zbira, „decimalna tačka"→
  „decimalni zarez", „Pithagor"→„Pitagor". Testovi u postojeći ijekavica blok.

---

## Faza D — Prirodnost tutorstva

### D1. Anti-„Zadatak:" u continuation bloku (AUD-08)
- **Prioritet:** P2 | **Kompleksnost:** Small
- `build_continuation_instructions`: ista rečenica koja je 2026-07-11 dodana za
  „**Rezultat:**" — dopuniti sa „ne piši ‘Zadatak:' — za vježbu postoji mod Vježba".

### D2. Multi-item format: bez zbirne labele, čista numeracija (AUD-10)
- **Prioritet:** P2 | **Kompleksnost:** Small-Medium
- Prompt već zabranjuje zbirnu labelu; dodati **guard enforcement**: na
  multi-item grading potezu skini vodeću samostalnu labelu (postojeći
  `_strip_leading_labels` proširiti na multi-item granu) i normalizuj duplu
  numeraciju `1. 1.` → `1.` (regex u `fix_repeated_item_numbering` porodici).

### D3. Varijacija pohvala u dugim nizovima (S-01)
- **Prioritet:** P3 | **Kompleksnost:** Small
- U followup prompt dodati rotacionu direktivu (koristiti correct_streak broj:
  „ne ponavljaj istu pohvalu — varijraj"). Bez koda, samo prompt. Mjeriti
  repetition-similarity u eval-u.

---

## Faza E — Test infrastruktura

### E1. Deterministički conversation-fixtures (pokriva AUD-01/02/04/06 regrese)
- **Prioritet:** P1 | **Kompleksnost:** Medium
- Novi `tests/test_conversation_flows.py`: Client-simulator iz audita
  (state-carry vjeran browseru) + mockirani model sa scenarijskim odgovorima —
  multi-turn asercije na `next_state`/`task_items`/`last_tutor_task` kroz
  5+ poteza. (Postojeći testovi su single-turn.)
- Prihvatanje: svi audit FAIL scenariji postoje kao crveni→zeleni testovi.

### E2. Offline eval proširenja
- **Prioritet:** P2 | **Kompleksnost:** Small
- U `docs/eval/eval_cases.json`: klasifikator lista (C1), ordinal odgovori,
  challenge poruke; dry-run u CI (već postoji mehanizam).

### E3. SSE parity test (mockiran)
- **Prioritet:** P2 | **Kompleksnost:** Small
- Poredi `handle_chat` vs `handle_chat_stream` done-state na 4 reprezentativna
  payloada sa fake modelom (audit H1 logika, bez API-ja).

### E4. Živi smoke budžet
- **Prioritet:** P2 | **Kompleksnost:** Small
- Sedmično: 10–15 živih poruka (skripta `audit/rerun_stochastic.py` je gotov
  kostur) — prati stohastičke stope AUD-07/08 i latenciju.

### E5. Frontend payload testovi
- **Prioritet:** P3 | **Kompleksnost:** Medium
- `scripts/check_js.mjs` proširiti: assert da se `previous_next_state`,
  `last_tutor_task` (answer-faza), `last_image_context` (image follow-up)
  uključuju u payload pod pravim uslovima (audit ih je simulirao — UI nikad testiran).

---

## Redoslijed implementacije (zavisnosti)

```
A1 (P0) ──┐
A2 (P0) ──┼─→ E1 (regres fixtures odmah uz A-fixeve)
B1 (P1) ──┤
B2 (P1) ──┘
A3 (P1) → E2
C1 (P1) → E2
B3, D1, D2, E3, E4 (P2) — nezavisno, bilo kojim redom
C2, D3, E5 (P3)
```

Procjena: Faza A+B ≈ 2–3 dana rada; C1 ≈ 1 dan; ostalo sitnice.
