# MAT-BOT — Implementirane promjene (lokalno, jun 2026)

Sve promjene su lokalne; ništa nije deploy-ano niti je produkcija dirana.
Verifikacija: `python -m py_compile app.py` + `pytest` (104 testa, svi prolaze,
svi vanjski servisi mockirani).

## app.py

### Sigurnost
| Promjena | Detalj | Rizik |
|---|---|---|
| Secret key | Čita `FLASK_SECRET_KEY` pa `SECRET_KEY`; ERROR log ako se koristi default van LOCAL_MODE | nizak |
| XSS | Novi `render_model_html()` — JEDINO mjesto gdje output modela postaje HTML (escape → latexify → `<br>`); koriste ga tekstualni, Mathpix i oba Vision toka | nizak–srednji (slikovni odgovori sada renderuju newline kao `<br>`; vizuelno praktično isto) |
| SSRF | `is_safe_external_url()` — samo http(s), blokira loopback/privatne/link-local/metadata adrese (uklj. DNS-rebinding provjeru preko `getaddrinfo`); `_fetch_image_bytes()` sa stream limitom `IMAGE_FETCH_MAX_MB` (20). Escape hatch: `ALLOW_PRIVATE_IMAGE_URLS=1` | nizak |
| Upload limit | `MAX_CONTENT_LENGTH_MB` default 200 → **20** (env-podesivo) | nizak |
| /tmp čišćenje | `cleanup_stale_uploads()` — briše fajlove starije od `UPLOAD_MAX_AGE_S` (3600s) pri svakom novom uploadu | nizak |
| Rate limiting | `flask-limiter` (već u requirements): `/submit` → `RATE_LIMIT_SUBMIT` (default "30 per minute" po IP-u iz X-Forwarded-For); diag endpointi → `RATE_LIMIT_DIAG`; isključivo: `RATE_LIMIT_ENABLED=0`; 429 vraća JSON sa bosanskom porukom | srednji — default 30/min po IP-u; škole iza NAT-a dijele IP → po potrebi povećati env varom |
| Diag endpointi | `/sheets/diag`, `/sheets/selftest`, `/mathpix/selftest`, `/gcs/signed-upload` sada traže LOCAL_MODE ili header `X-Diag-Token: $DIAG_TOKEN` → inače 403 | nizak (frontend ih ne koristi; ops mora postaviti DIAG_TOKEN ako ih treba) |
| CORS | `CORS_ORIGINS` env (zarezom odvojene domene) → restrikcija; bez env-a ponašanje nepromijenjeno (sve domene). Produkcijska odluka u next-steps | nula (default isti) |

### Ispravnost
| Promjena | Detalj |
|---|---|
| Kontekst razgovora | `/submit` sada čita `history_json` (form ili JSON), `sanitize_history()` validira (lista `{user,bot}` stringova, zadnjih `HISTORY_MAX_TURNS=5`, po poruci `HISTORY_MAX_CHARS=2000`, bot HTML → čisti tekst). Ide i u sync i u async (kroz task payload `history`). Sesijska `api_history` ostaje samo kao mali fallback (čisti tekst, 3×600 znakova). |
| Async kontekst | `_process_job_core` čita `payload["history"]` umjesto fiksnog `[]` — slike i teška pitanja sada imaju kontekst ("uradi i b)" radi). |
| Async grafovi | `should_plot`/`add_plot_div_once` se primjenjuju i u `_process_job_core`. |
| Fence-stripping | `strip_ascii_graph_blocks` više ne briše SVE code blokove — samo one koji liče na ASCII graf. |
| Broj zadatka | Bare "N." se ne hvata za decimale ("3.5") ni "N. razred"; `requested_clause()` prevodi `-1` ("zadnji") u "posljednji" umjesto da modelu šalje "-1". |
| Historija — robusnost | Gradnja poruka koristi `.get()` (nema više KeyError na klijentskim podacima); bot poruke se uvijek šalju modelu kao čisti tekst. Konzistentno `HISTORY_CONTEXT_TURNS=5` za tekst i vision (ranije tekst 2, vision 5, komentar tvrdio 5). |
| Env knobs | `OPENAI_TIMEOUT` i `OPENAI_MAX_RETRIES` se sada stvarno čitaju iz env-a (defaulti nepromijenjeni: HARD_TIMEOUT_S / 2). |

### Performanse i observability
- Sheets init je lijen (`_init_sheets()` pri prvoj upotrebi, ne na importu) — brži cold start; u LOCAL_MODE se preskače u potpunosti.
- `log_to_sheet` ide u daemon thread (`SHEETS_ASYNC_LOG=1` default) — skida ~0.3–0.8s sa svakog sync odgovora.
- `log.warning` umjesto tihih `except: pass`: Sheets append/init, GCS upload, Mathpix OCR i fallback, parsiranje historije, blokirani URL-ovi, download slika.
- Startup log: koji job store (firestore/memory) + upozorenje ako je memory van LOCAL_MODE; `_enqueue` loguje zašto pada na lokalni thread.

### Uklonjen mrtvi kod
- `_sync_process_once` + `import concurrent.futures as cf` (nikad pozvano)
- top-level `ThreadPoolExecutor`/`FuturesTimeout`/`traceback`/`ImageFont` importi
- `gcs_upload_filestorage` (nikad pozvana)
- `JEDNACINE_NEJEDNACINE_LOGIKA_I_METODOLOGIJA` (nikad u promptu)
- nekorišteni `user_text` parametar `build_system_prompt`; `_vision_clauses` (vraćala prazan string)
- bogus `ETAG_DISABLED` config ključ (ne postoji u Flasku)

## templates/index.html
- `poll()`: toleriše do 3 uzastopne mrežne greške prije odustajanja (ranije: prva greška prekida čekanje).
- Uspješan async odgovor se sada upisuje u localStorage historiju (`pushHistory`) — ranije se upisivala samo greška (obrnuta logika), pa follow-up nakon async odgovora nije imao kontekst.
- Jasnije bosanske poruke pri prekidu veze.

## Novi/izmijenjeni fajlovi
- `tests/` — 104 testa: `conftest.py` (env + anti-network guard + OpenAI mock), `test_utils.py`, `test_prompts_history.py`, `test_ssrf.py`, `test_routes_sync.py`, `test_routes_async.py`, `test_limits_and_gating.py`
- `pytest.ini`, `requirements-dev.txt`
- `.gcloudignore` — ranije sadržavao zalijepljen shell skript umjesto patterna; sada ispravan
- `.gitignore` — dodano `.pytest_cache/`, `.env.*`, `venv/`
- `docs/fable-audit.md`, `docs/fable-changes.md`, `docs/fable-next-steps.md`
- `README.md` — osvježen (lokalno pokretanje, env tabela, testiranje)

## Obrisani fajlovi
- `Procfile.bak` (zastarjeli backup)
- `list_models.py` (importovao `google.generativeai` koji nije ni u requirements — mrtav/pokvaren)
- `test_env.py` (prazan stub; zamijenjen pravim testovima)
- `.deploy-ping` (jednokratni deploy trigger iz 2025)
- `~/matbot-refresh.sh` (slučajno commitovan folder doslovnog imena `~`)

## Namjerno NIJE mijenjano
- `requirements.txt`, `Dockerfile`, `cloudbuild.yaml`, `deploy.sh`, `Procfile`
- modeli, oblici API odgovora, pedagoški promptovi, bosanski tekstovi, legacy `/` ruta

---

# Tutor/practice pouzdanost — audit jul 2026 (lokalno)

Cilj: ukloniti klase grešaka u ocjenjivanju (tačan odgovor proglašen netačnim,
kontradikcije, ignorisane/pogrešno ocijenjene stavke), ponavljanje zadataka,
ekavske oblike i loš auto-scroll. Verifikacija: `pytest` (510 testova) +
`python scripts/eval_tutor.py` (34 slučaja, rutiranje 0 grešaka).

## Novi moduli
| Modul | Šta radi |
|---|---|
| `matbot/answer_checker.py` | Deterministička provjera odgovora u KODU (`fractions.Fraction`): komplement razlomka ("koji dio nije/ostaje"), pretvaranje mješoviti↔nepravi, direktan račun (`izračunaj ...` i čisti izrazi), skraćivanje; numerisane stavke ("1) 3/5 2) 1/4"); ekvivalencija (3/5=6/10, 2 1/4=9/4); parsira i LaTeX zapis zadatka (`\frac{a}{b}`). KONZERVATIVNO: kad nije sigurno → `unverified` (nikad izmišljeno "netačno"); komplement bez riječi "dio" smije samo POTVRDITI tačno (positive_only). |
| `matbot/bosnian.py` | `to_ijekavica()` — zadnja linija odbrane za česte ekavske oblike (deo→dio, rešenje→rješenje, vežba→vježba...); word-boundary, čuva veliko slovo, ne dira matematički zapis ni riječi poput "video". |

## matbot/ai_tutor_service.py
- `_prepare_chat`: pri `interaction_phase=answering_practice_task` pokreće checker; presuda ide u prompt (obavezujuća za model) i u response (`answer_check`).
- `_sanitize_payload`: novo polje `recent_tasks` (max 6 × 300 znakova) — anti-ponavljanje.
- `_finalize_response`: `to_ijekavica(answer)` (važi i za stream — klijent finalni render radi iz `done.answer`); `extract_practice_task(..., mode=...)`.
- `extract_practice_task`: za `exam` hvata SVE numerisane zadatke (1., 2., 3.) sa numeracijom — ranije samo prvi, pa provjera "1) ... 2) ..." nije imala stavke 2 i 3.

## Promptovi (matbot/tutor_prompts.py, matbot/prompt_builder.py)
- Nova system sekcija TAČNOST PRI PROVJERI ODGOVORA: prvo sam izračunaj pa presudi; ekvivalentni zapisi su tačni; numerisane stavke posebno; bez kontradikcije; PROVJERA IZ SISTEMA je obavezujuća.
- JEZIK I TON: eksplicitno dio/cijeli/rješenje/vježba/primjer/sljedeći (NIKAD ekavski).
- Practice follow-up: blok PROVJERA IZ SISTEMA (per-stavka TAČNO/NETAČNO/BEZ ODGOVORA/neprovjereno); prva rečenica = konačan sud; neodgovorena stavka se NE ocjenjuje nego traži; poslije kompletnog rješenja NEMA "probaj ponovo" za isti zadatak.
- Practice/exam: blok NEDAVNO DATI ZADACI (iz `recent_tasks`) + pravilo da novi zadatak mora imati druge brojeve i kontekst; tipični zadaci iz mastera su "uzor", ne fiksna lista.

## Frontend (templates/index.html)
- Auto-scroll: `scrollTutorToBottom()` i NAKON `MathJax.typesetPromise` (typeset naknadno mijenja visinu poruke) — za poruke i za finalni render streama.
- Chips: uklonjen "🔁 Ponovi zadatak" (dupliranje chata); tokom čekanja odgovora sada hint + "➕ Novi zadatak".
- `recent_tasks`: localStorage (`matbot_tutor_recent_<cid>`, zadnjih 6) → šalje se u payloadu; čisti se pri resetu konverzacije i "Očisti chat".

## Testovi
- `tests/test_answer_checker.py` (30): primjeri A–D kao regresije opštih klasa + ekvivalencija, mješoviti/nepravi, LaTeX, decimalni zarez, konzervativnost (jedinice, dodatni brojevi, "ne znam").
- `tests/test_tutor_grading_flow.py` (26): presuda u promptu i responsu (sync + stream), anti-ponavljanje, exam multi-task ekstrakcija, ijekavica post-processing, system prompt pravila.
- `tests/test_ai_tutor_widget_template.py` (+3): auto-scroll poslije typeseta, chips bez duplikata, recent_tasks plumbing.
- `docs/eval/eval_cases.json` (+5): grading regresije za LIVE eval (A–D + no-repeat).

## Poznata ograničenja
- Checker pokriva razlomke/cijele/decimalne i 4 klase zadataka; ostalo ocjenjuje model uz stroža pravila (prvo izračunaj, pa presudi).
- Stream može nakratko prikazati ekavski oblik dok traje kucanje; finalni render je ispravljen.
- `temperature` se namjerno NE šalje (gpt-5 familija ne prima custom vrijednost kroz chat completions).

---

# image_test tok — state-driven stanje razgovora (jul 2026, lokalno)

Uzrok bugova: active_task_kind/pending_action/last_tutor_task su se izvodili
PARSIRANJEM PROZE odgovora modela. Sada su state-driven.

## Backend (matbot/ai_tutor_service.py)
- `detect_explicit_intent` + `_apply_explicit_intent`: "korak po korak/objasni postupak/rijesi detaljno" → step_by_step (quick→explain); "samo rezultat" → result_only (→quick); "sve zadatke/cijeli test" → solve_all. Čita se iz ORIGINALNE poruke učenika, prije confirmation-rewrite-a; ne dira ocjenjivanje (answering_practice_task).
- `_resolve_image_test_state`: deterministička mašina stanja za zadatke sa slike. Izvor stavki je ISKLJUČIVO OCR (svježa slika ili OCR sekcija iz last_image_context preko `ocr_from_saved_context`); ulaz u koračanje samo na jasan signal (potvrda continue_image_test, solve_all/step intent uz sliku, "nastavi na treći zadatak" referenca, "nastavi" uz postojeće image_test stanje). ≥2 stavke obavezno; answering_practice_task nikad ne otima.
- `_next_state_for_response`: image_test grana ima APSOLUTNU prednost pred prozom — solved/current/next_item se računaju iz stanja; schema: `next_state.image_test = {item_labels, solved, next_item, style}` + `pending_action={type:continue_image_test, source:image_context, next_item}` + `active_task_kind=image_test`. Zadnja stavka riješena → izlaz iz image toka.
- Guard: tokom image_test odgovor NIKAD ne postaje last_tutor_task; `_looks_like_practice_task_text` odbacuje prelazne fraze bez matematičkog signala ("Odlično, idemo na sljedeći zadatak!", "Super, nastavljamo", "Želiš li da nastavimo?").
- `_normalize_next_state` propušta image_test pod-stanje (validirano); next_item podržava i pod-oznake ("5.c").

## Prompt (matbot/prompt_builder.py)
- `build_image_test_instructions`: mode blok koji NADJAČAVA standardne modove — riješi isključivo tekuću stavku (tekst stavke ide u prompt), zadrži numeraciju, stil po stanju, nikad nepovezani zadatak; najavi sljedeću stavku.

## Frontend (templates/index.html)
- `last_image_context` se šalje uz follow-up formulacije (prošireni rječnik: uradi/rijesi/korak/nastavi/sve/dalje), uz SVE potvrde i dok je image_test aktivan.
- `applyTutorResponse`: tokom image_test proza se NE pretvara u last_tutor_task (inImageTest gate + clearAwaitingPracticeTask); greška bez statusa ne briše next_state.
- `looksLikeTransitionText` ogledalo backend filtera; `isNewPracticeTaskRequest` isključuje "nastavi..." fraze.

## Testovi
- tests/test_image_test_state.py (22): start koračanja (korak po korak/sve zadatke), "da" nastavlja stavku 2 (ne vježbu), "nastavi na treći zadatak" → stavka 3, izlaz nakon zadnje, prelazni tekst nije zadatak, stil nadjačava quick, bez konteksta "nastavi" → pojašnjenje/fallback, jedna stavka bez koračanja, ocjenjivanje se ne otima, streaming nosi stanje.
- test_ai_tutor_widget_template.py (+3 image_test kontrakt), eval_cases (+3).
- Verifikacija: pytest 560 passed; node scripts/check_js.mjs OK; dry eval 41/0; git diff --check čist.

## Poznata ograničenja
- Vision-only slike (bez OCR teksta) nemaju image_test koračanje (nema pouzdanih stavki) — kontekst se i dalje čuva.
- Odlazak u nepovezanu vježbu usred image_test toka ne pamti progres (image_test se ne prenosi kroz nepovezan potez); "objasni prvi zadatak sa slike" i dalje radi preko last_image_context.
