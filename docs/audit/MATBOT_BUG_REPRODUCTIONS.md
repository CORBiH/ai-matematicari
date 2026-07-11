# MatBot — reprodukcije potvrđenih bugova (adversarial audit 2026-07-11)

Svaka reprodukcija je izvršena kroz **stvarni servisni ulaz** (`matbot.ai_tutor_service.handle_chat`)
sa **stvarnim modelom** (`gpt-5-mini`, `reasoning_effort=low`), osim gdje je izričito
označeno `MOCKED`. Klijent-simulator vjerno replicira browser state-carry
(`previous_next_state`, `last_tutor_task`, `conversation_history`, `recent_tasks`)
prema logici u `templates/index.html`.

Puni katalog svih poteza: `docs/audit/MATBOT_TEST_CATALOG.json`.

---

## AUD-01 — Practice + slika s više zadataka: bot ignoriše zadatke sa slike i izmišlja svoje

| | |
|---|---|
| **Severity** | **High** |
| **Kategorija** | image-flow / state-machine |
| **Razred/Mod/Tema** | 6 / practice / bez teme |
| **Reprodukcija** | direct service, stvarni model |
| **Broj reprodukcija** | **3/3 (sistematski)** |
| **Confidence** | visok |

### Koraci
1. Učenik u Vježbi pošalje sliku sa svoja 3 zadatka (payload: `mode=practice`,
   `student_message=""`, `image_ocr_text`):
   ```
   1. Izračunaj: 3/10 + 4/10
   2. Izračunaj: 7/12 - 5/12
   3. Marko je prešao 15 km za 3 sata. Kolikom brzinom se kretao?
   ```
2. **Bot (stvarni transkript):**
   > Zadatak: Izračunaj: a) 2/5+1/5  b) 9/14−2/14  c) Ana je prešla 18 km za 2 sata…

   → Ni jedan od učenikovih zadataka. Bot je generisao *slične* zadatke sa drugim brojevima.
3. Učenik odgovori `7/10` — **tačan odgovor na NJEGOV 1. zadatak sa slike**.
4. **Bot:** `Netačno. … 2/5+1/5=3/5 … Rezultat: 3/5`
   — `answer_check`: `{n:1, verdict:"incorrect", expected:"3/5", given:"7/10"}`.

### Očekivano
Zadaci sa slike su učenikovi zadaci: bot ih rješava/vodi kroz NJIH (image_test tok),
ili barem prompt mora obavezati model da radi sa zadacima iz OCR-a.

### Stvarno
`_image_test` se ne aktivira (`payload["_image_test"]=False`,
`active_task_kind="practice"`), practice mode blok kaže "daj zadatak" pa model
izmisli svoje. Tačan učenikov odgovor je proglašen netačnim → **direktno obmanjuje dijete**.

### Root cause
`matbot/ai_tutor_service.py::_resolve_image_test_state` (linije ~1149–1212):
za svježu sliku bez eksplicitnog signala (`continue_image_test` potvrda,
"korak po korak", referenca na stavku, "nastavi") funkcija vraća `None`
(linija `else: return None`). Fresh multi-image u practice tako pada u
standardni practice tok čiji prompt traži generisanje zadatka.
Offline dokaz (mock, bez modela): `_image_test=False`, u user promptu nema
direktive "radi zadatke sa slike" za practice granu.

### Regresioni test (prijedlog)
```python
def test_practice_fresh_multi_image_uses_image_tasks(master, tmap):
    payload = {"grade":6,"mode":"practice","student_message":"",
               "image_ocr_text":OCR3,"has_image":True}
    out = svc.handle_chat(payload, fake_chat, master, tmap, model="m", timeout=1)
    # image_test tok mora biti aktivan ILI prompt mora sadržavati OCR zadatke
    assert payload.get("_image_test") or "3/10 + 4/10" in _last_user_prompt(fake_chat)
```

---

## AUD-02 — "prvi je 6/9, drugi 4/8, treći ne znam": multi-odgovor potpuno promašen

| | |
|---|---|
| **Severity** | **High** (čest oblik dječijeg odgovora; gubi date odgovore) |
| **Kategorija** | grading / reference-resolution / deterministic-parsing |
| **Razred/Mod/Tema** | 6 / practice (exam-tip zadatak) / 6-04-031 |
| **Reprodukcija** | direct service, stvarni model |
| **Broj reprodukcija** | **2/2 (sistematski; parsing dokazan offline deterministički)** |
| **Confidence** | visok |

### Koraci
1. Seed višestavkovni zadatak + `task_items {labels:[1,2,3], graded:[]}`:
   ```
   1. Izračunaj: 2/9 + 4/9
   2. Izračunaj: 5/8 - 1/8
   3. Izračunaj: 1/2 + 1/3
   ```
2. Učenik: **`prvi je 6/9, drugi 4/8, treci ne znam`** (phase=answering_practice_task)
3. **Bot (stvarno):** objasni SAMO 1. stavku kao da je učenik pitao pitanje
   ("Dobar zadatak — lako je za naučiti…") — bez ocjene 1. i 2. odgovora,
   bez huka na "treći ne znam". `answer_check=null`,
   `task_items.graded=[]` (ništa upisano).
4. Učenik: **`treci je 5/6`**
5. **Bot (stvarno):** „Postavi cijeli treći zadatak… Ne mogu provjeriti samo
   odlomak ‘5/6' bez izvornog zadatka." — **izgubljen kontekst** iako je
   zadatak u `last_tutor_task` poslan u promptu.

### Offline deterministički dokaz (bez modela)
```
parse_student_answers("prvi je 6/9, drugi 4/8, treci ne znam") → mode=none
detect_referenced_items(...)                                   → set()
parse_student_answers("treci je 5/6")                          → mode=single, refs=set()
# poređenje: "za 2. je 4/8" → mode=numbered {2:4/8}  (radi!)
# poređenje: "3. je 5/6"    → mode=numbered {3:5/6}  (radi!)
```

### Root cause
`matbot/answer_checker.py`:
- `_ANSWER_MARKER_RE` traži cifru + interpunkciju (`1)`, `2.`) — ordinalne riječi ne postoje.
- `detect_referenced_items` ordinal-pattern zahtijeva imenicu iza ordinala
  (`prvi zadatak`) ili prijedlog (`na prvi`); goli `prvi je …` ne pogađa.
Rezultat: `checkable=False` → model bez determinističkog okvira → konfuzija.

### Regresioni test (prijedlog)
```python
def test_ordinal_named_answers_parsed():
    mode, ans = parse_student_answers("prvi je 6/9, drugi 4/8, treci ne znam")
    assert mode == "numbered" and ans[1].raw == "6/9" and ans[2].raw == "4/8"
    assert 3 not in answered  # "ne znam" = nepokušano (postojeći _NONANSWER mehanizam)
```

---

## AUD-03 — LLM topic-klasifikator vraća `unknown` za jasne poruke

| | |
|---|---|
| **Severity** | **Medium** |
| **Kategorija** | routing |
| **Razredi** | 6, 7, 8 (potvrđeno na sva tri) |
| **Reprodukcija** | direct service, stvarni model |
| **Broj reprodukcija** | **3/3 za g7 poruku; po 1/1 za g6 i g8** |
| **Confidence** | visok |

### Koraci / dokaz
| Poruka | Razred | Očekivana tema | `final_topic` |
|---|---|---|---|
| „ne kontam sabiranje cijelih brojeva sa razlicitim predznakom" | 7 | 7-01-00x | `unknown` (3/3) |
| „sta je hipotenuza i kako je izracunam" | 8 | 8-04-025 | `unknown` |
| „objasni mi porcente i kolko je 20% od 50" (typo) | 6 | 6-06-060 | `unknown` |

Heuristika ispravno kaže `unknown` (nazivi tema nemaju te riječi), poruka NIJE
vague (`is_vague_message=False`), LLM klasifikator **jeste pozvan** (dokazano
mock-instrumentacijom `detect_topic`) i vraća `unknown`.

### Posljedica
Odgovor je sadržajno dobar (general-answer put), ali **bez topic konteksta**:
nema video preporuke, nema NPP scope-a u promptu, `activity_log` bez teme,
`recommended_mode`/chips potencijalno siromašniji. Tiha degradacija.

### Root cause (hipoteza, visoka pouzdanost)
`matbot/topic_detector.py::detect_topic_llm` — klasifikatorski prompt daje listu
`(id, tema_ui)` naziva; nazivi tema ne sadrže sinonime dječijeg jezika
("predznak", "hipotenuza" je u nazivu 8-04-025? — provjeriti; "porcenti" typo).
Klasifikator radi doslovno poklapanje pojma umjesto semantike + konzervativan prag.

### Regresioni test (prijedlog)
Offline eval lista (poruka → očekivani topic-prefix) u
`docs/eval/eval_cases.json` + sedmični live smoke sa ≥80% pragom.

---

## AUD-04 — Novi zadatak pregazi višestavkovno stanje dok stavke čekaju

| | |
|---|---|
| **Severity** | **Medium** |
| **Kategorija** | state-machine |
| **Razred/Mod** | 6 / practice (multi-item) |
| **Reprodukcija** | direct service, stvarni model (1/1 zapaženo; mehanizam očit u kodu) |
| **Confidence** | visok za mehanizam; srednji za frekvenciju |

### Koraci
1. Seed 3 stavke + `task_items {labels:[1,2,3], graded:[]}`.
2. Učenik: `za 2. je 4/8` → checker ispravno: stavka 2 correct, 1 i 3 missing.
3. **Bot (stvarno):** ocijeni stavku 2, kaže da 1 i 3 čekaju, **ali doda i novi
   zadatak** „Zadatak: Izračunaj 3/10 + 7/20".
4. Server: `extract_marked_task` nađe marker → novi `last_tutor_task`,
   `_task_items_for_response` vidi novi jednostavni task → **`task_items=None`**.
   Stanje stavki 1 i 3 je IZGUBLJENO; sljedeći odgovor se veže za novi zadatak.

### Root cause
`matbot/ai_tutor_service.py::_finalize_response`: prompt zabranjuje novi zadatak
dok stavke čekaju („Izuzetak: ako sve stavke… nisu odgovorene, prvo zatraži
preostale stavke"), ali **server ne enforc-uje**: marker se prihvata bezuslovno.
(Fix zadržavanja `last_tutor_task` iz 2026-07-11 pokriva samo slučaj kad model
NE da novi zadatak.)

### Regresioni test (prijedlog)
```python
def test_new_task_marker_ignored_while_items_pending(...):
    # grading turn, task_items graded=[2], model vrati "Zadatak: ..." marker
    # → last_tutor_task mora ostati STARI multi zadatak, task_items sačuvan
```

---

## AUD-05 — Deterministički checker: rupe u pokriću (procenti, zagrade, stepeni, Pitagora, jedinice)

| | |
|---|---|
| **Severity** | **Medium** |
| **Kategorija** | missing-deterministic-checks |
| **Reprodukcija** | offline deterministički + 2 live posljedice |
| **Confidence** | visok |

### Offline gap katalog (`derive_expected` → None)
```
---GAP---   Izračunaj 35% od 40.
---GAP---   Izračunaj: (-3) · 4        ← zagrade lome _EXPR_PREFIX_RE ( -3 · 4 RADI)
---GAP---   Koliko je 25% od 80?
---GAP---   Izračunaj: 2^6
---GAP---   Katete su 3 cm i 4 cm. Kolika je hipotenuza?
---GAP---   Riješi sistem: x + y = 4 i x - y = 2
---GAP---   Napiši 1/3 kao decimalan broj zaokružen na dvije decimale.
---GAP---   Koliko je centimetara u 3,5 metara?   (konverzija jedinica)
```

### Live posljedica (2 reprodukcije)
Bez determinističke presude grading_guard nema šta nametnuti pa model krši
label-first kontrakt:
- **D-percent T2** — `1400` za „35% od 40": odgovor počinje
  „Izgleda da si pomnožio 35 sa 40…" (bez „Netačno." na početku).
- **X-units-cm-m T2** — `35` za „cm u 3,5 m": počinje „Čini se da si pomjerio
  decimalnu tačku…" (bez labele; usput „tačku" umjesto „zarez").

Matematika je u svim opažanjima bila TAČNA (model), ali bez mreže — rizik ostaje.

---

## AUD-06 — Osporavanje ocjene veže se kao odgovor na NOVI (auto-dodijeljeni) zadatak

| | |
|---|---|
| **Severity** | **Medium** |
| **Kategorija** | state-machine / intent-routing |
| **Reprodukcija** | direct service, stvarni model. Pogrešno VEZIVANJE deterministički uvijek; tekst „Netačno." ~1/4 |
| **Confidence** | visok |

### Koraci (stvarni transkript)
1. `Izračunaj: 3/4 + 2/5` [novi zadatak koji je bot auto-dodijelio poslije tačnog odgovora]
2. Učenik: **„pogrijesio si, nastavnica kaze da je 2/5"** — priča o PRETHODNOJ ocjeni.
3. `answer_check`: `{n:1, verdict:"incorrect", expected:"23/20", given:"2/5"}` —
   sistem je „2/5" uzeo kao odgovor na aktivni zadatak 3/4+2/5.
4. **Bot:** „**Netačno.** Dobro što si provjerio sa nastavnicom — … tačan zbir je 23/20"
   → dijete dobije „Netačno" za zadatak koji nije ni pokušalo.

### Root cause
`_apply_practice_help_contract` hvata frustraciju/vague-pitanja (fix 2026-07-11),
ali **ne hvata challenge/meta poruke** („pogriješio si", „nije tačno", „nastavnica
kaže") koje sadrže broj → `_has_practice_answer_attempt=True` → grading protiv
aktivnog zadatka.

### Regresioni test (prijedlog)
```python
@pytest.mark.parametrize("msg", ["pogrijesio si, nastavnica kaze da je 2/5",
                                 "nije tacno, meni je ispalo 2/8 i sigurno je tako"])
def test_challenge_message_not_graded_against_active_task(msg): ...
```

---

## AUD-07 — Quick + slika s više zadataka: povremeno riješi SVE umjesto da pita koji

| | |
|---|---|
| **Severity** | **Low-Medium** (stohastički ~25%; krši product pravilo) |
| **Kategorija** | image-flow / prompt-compliance |
| **Reprodukcija** | 1/1 original, 0/3 re-run → stohastički |

Original (stvarno): „1) 7/10, 2) 1/6, 3) 5 km/h. Provjera: …" — svi rezultati
odjednom, bez pitanja koji zadatak. Prompt direktiva postoji
(„pitaj koji broj zadatka… ne rješavaj sve") — model je povremeno ignoriše.
Deterministički gate ne postoji za ovaj put (`_detected_task_count=None`,
`_image_result_available=None` u ovoj grani — offline dokaz).

---

## AUD-08 — „Zadatak:" linija procuri u explain continuation

| | |
|---|---|
| **Severity** | **Low** (vizuelno; server state čist — `last_tutor_task` prazan) |
| **Reprodukcija** | 1/1 original, 0/3 re-run → stohastički ~25% |

B-g6-core-flow T2 („mozes krace i jednostavnije", continuing_explanation):
odgovor je sadržavao red `Zadatak: 2/3+1/6…`. `build_continuation_instructions`
nema anti-„Zadatak:" pravilo (explain mode blok ima; continuation blok dobio
anti-„**Rezultat:**" 2026-07-11, ali ne i anti-„Zadatak:").

---

## AUD-09 — HR leksik promiče kroz jezičku zaštitu

| | |
|---|---|
| **Severity** | **Low** |
| **Reprodukcija** | live: „okomite" (g9 explain), „zbroj" (g7 explain), „Pithagorinim" (g8, typo modela), „decimalnu tačku" (X-units) |

`matbot/bosnian.py` ne pokriva: okomit→normalan/pod pravim uglom, zbroj→zbir
(termin je već zabranjen u prompt pravilima ali bez post-processing zamjene),
„decimalna tačka"→„decimalni zarez".

---

## AUD-10 — Multi-stavke: zbirna labela + dupla numeracija

| | |
|---|---|
| **Severity** | **Low** (stil/čitljivost) |
| **Reprodukcija** | 2 live opažanja |

- E-ordered: odgovor počinje „Tačno. 1. Tačno. …" — zbirna labela na vrhu i
  po-stavkama (pravilo iz 2026-07-11 kaže bez zbirne).
- E-za-2-je: „1. 1. (čekanje)… 2. 2. Tačno…" — dupla numeracija;
  i ponovljena rečenica „Zadatak 2 je tačan. … zadatak 2. je tačan."

---

## Sumnje / opažanja koja NISU klasifikovana kao bugovi

| ID | Opažanje | Zašto nije bug |
|---|---|---|
| S-01 | Ljestvica pohvala postaje repetitivna (X-ladder T4 ~92% sličnо) | stil, ne funkcija |
| S-02 | „sta dalje" u g9 practice → odmah novi zadatak bez pitanja | konzistentno s product odlukom „Vježba daje zadatke" |
| S-03 | 6. razred pitan za Pitagoru — objasni je bez napomene o razredu | product odluka potrebna; nije objективно pogrešno |
| S-04 | D-repeating: „0,33" prihvaćeno za 1/3 na 2 decimale | matematički ispravno |
| S-05 | Novi zadatak poslije `X-units` niza koristi „2 h 15 min" brzinu — teže od nivoa | težina, subjektivno |
