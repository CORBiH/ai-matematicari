# Ugovori lekcija (Lesson Contract Engine) — Faza 1

Cilj: **dodavanje obične podržane lekcije mijenja PODATKE, ne Python.**

Prije ovoga je svaka nova lekcija tražila tri izmjene koda: red u mapi porodica
po ID-ju lekcije, granu u validatoru i rečenicu u promptu. Nad 534 lekcije to ne
skalira — 15 od 22 različita ishoda routinga postojalo je zbog 15 ručno
nabrojanih lekcija (5,6 % kurikuluma, 68 % površine routinga).

---

## SMJER GENERISANJA: server računa, model piše

**Nakon Live96 kampanje (pozivi 503–598) smjer je OBRNUT.** Ranije je model
izmišljao matematiku i morao je ponovo opisati u strukturisanom „dokazu“, a
server je iz dokaza rekonstruisao istinu. Rezultat: 11 od 48 pilot poziva
odbijeno — **nijedno zbog pogrešne matematike**, sva zbog reprezentacije:

- pozivi 536 i 548: `evidence_kind_not_in_contract` — serverov **vlastiti**
  prompt je nudio `numeric_expression`, a serverov **vlastiti** ugovor ga nije
  prihvatao. Zadaci su bili potpuno tačni;
- poziv 539: `evidence_invalid` — tačan zadatak „popuni prazninu“, ali dokaz s
  rupom nije imao legalan zapis pod dodijeljenim oblikom;
- pozivi 524 i 532: učenik traži „nađi grešku“, server dodijelio
  `direct_computation`, model poslušao učenika → namjerno pogrešan lanac pao je
  na numeričkoj provjeri.

Sada **server iz ugovora KONSTRUIŠE** operande, operaciju, tačan odgovor,
distraktore i označeni indeks (POVUČENO 2026-08-14: `matbot/contracts/generator.py` je obrisan; ugovor je sada podatak, ne generator). Tačnost,
vjernost lekciji i označeni odgovor su tačni **po konstrukciji**, a ne
naknadnom rekonstrukcijom. Model dobija gotov zadatak i piše samo okolnu
bosansku prozu.

### Vlasništvo (Faza 1)

| Vlasnik | Šta |
|---|---|
| **SERVER** | tekst zadatka, sve četiri opcije, označeni odgovor, očekivani odgovor, jedinice, MathJax (delimiteri i komande), izbor arhetipa, težina, miješanje opcija |
| **MODEL** | okolna bosanska proza: `reply`, `hint`, feedback |

Model **ne smije** izmisliti, prepisati, preurediti ni ponovo izračunati
nijedan broj, operaciju, izraz, opciju ni odgovor. Njegov `new_task` sadržaj se
na motoru **u cijelosti ignoriše** — služi samo kao signal „u ovom turnu se
izdaje novi zadatak“. Prozu čuva `pipeline.verify_prose_fidelity`: broj koji se
ne da objasniti iz samog zadatka obara **taj tekst** (hint pada na siguran
generički), nikad cio turn s mutacijom stanja.

### Cijena odbijanja je sada NULA poziva

Kostur se pravi **prije** jedinog AI poziva. Kad priprema padne na bootstrapu
(nema aktivnog zadatka), turn se odbija bez ijednog potrošenog poziva. Kad
aktivan zadatak postoji, turn smije nastaviti kao razgovor, ali novi zadatak se
u tom turnu ne može izdati.

---

## Kako dodati lekciju

1. Izaberi predložak u `data/contract_templates.json` (ili napravi novi ako
   opisuje **domen**, nikad jednu lekciju).
2. Dodaj red u `data/lesson_contracts.json` — samo ono što se razlikuje.
3. Dodaj fixture/test.
4. `python scripts/build_lesson_contracts.py --report`.

Ništa pod `matbot/` se ne mijenja. Test `test_g3_b_new_lesson_is_enabled_end_to_end_by_data_only`
to i dokazuje: lekcija drugog razreda i drugog domena prelazi na motor samo
dodavanjem reda.

```jsonc
{
  "canonical_topic_id": "6-04-009",
  "grade": 6,
  "status": "enabled",
  "inherits": "rational.add_sub",
  "allowed_task_archetypes": ["direct_computation"],
  "skill": "add_subtract_like_denominators",
  "operand_constraints": {
    "sign_policy": "non_negative",
    "denominator_relation": "equal",
    "integer_range": [1, 60]
  },
  "invariant_constraints": ["denominator_relation", "allowed_operations"],
  "error_category_set": ["combined_denominators", "wrong_numerator", "wrong_operation"]
}
```

`6-04-009` i `6-04-010` razlikuju se u **jednoj vrijednosti**
(`denominator_relation`), `6-04-011` i `6-04-012` u **jednoj**
(`allowed_operations`). Ta jedna vrijednost zamjenjuje cio raniji Python switch.

> **`required_evidence` više NE POSTOJI.** Lekcija zna svoju *matematiku*, ne
> kojim bi JSON kontejnerom model trebao da je opiše. Stari red s tim poljem
> pada na učitavanju s jasnom porukom — nikad tiho kao „bez uslova“.

---

## Stanja ugovora

| Status | Ponašanje Practice moda | Legacy dozvoljen? |
|---|---|---|
| *(nema reda)* | nepromijenjen legacy put | da — to *jeste* legacy |
| `enabled` | **isključivo** motor ugovora; svaki pad je fail-closed | **nikad** |
| *neispravan red* | greška na startu i u CI-ju; aplikacija ne diže | **nikad** |
| `needs_review` | legacy put, ali **posebno prebrojan** | da, svjesno |
| `unsupported` | jasna poruka „vježba nije dostupna“, **bez AI poziva** | **nikad** |
| `legacy_pinned` | legacy put; traži `pinned_reason` + `pinned_at` | da, auditirano |

**Zašto `needs_review` ide na legacy:** to je generatorov prijedlog koji čovjek
nije potvrdio. Kao `enabled` bi objavio nerevidiranu matematiku; kao
`unsupported` bi ugasio Practice na lekciji koja danas radi. Ide na legacy, ali
se u izvještaju **nikad ne stapa** s „nema reda“ — nedovršena migracija se ne
može sakriti iza „još nije počelo“.

**Zašto rollback ide kroz `legacy_pinned`, a ne kroz pokvaren `enabled`:** pad
uključenog ugovora **nema nijedan put** do legacy koda. Jedini način da se
migrirana lekcija vrati jeste eksplicitan status s obaveznim razlogom, koji se
vidi u izvještaju. Time data-rollback ne može zamaskirati defekt motora.

---

## Šta server STVARNO umije konstruisati (Faza 1)

Uključen ugovor smije koristiti samo ovo. Arhetip je „podržan“ **samo ako ga
serverski generator umije napraviti** (`generator.IMPLEMENTED_ARCHETYPES`).
Obećanje oblika bez generatora pada na **učitavanju**
(`archetypes.assert_supported`) — nikad pred učenikom.

| Arhetip | Sposobnost | Verifikator (samoprovjera) | Status |
|---|---|---|---|
| `direct_computation` | **K1** `arithmetic_expression` | `exact_rational` | **implementirano** |
| `identify_equivalent` | **K3** `equivalence_pair` | `exact_rational` | **implementirano** |
| `find_missing_value` | K2 `equation_one_hole` | `exact_rational` | **odgođeno** — nema generatora |
| `identify_error` | K4 `erroneous_chain` | `error_category` | **odgođeno** — nema generatora |
| `word_problem` | K8 | — | **nije uključeno**: vjernost priče kosturu nije mašinski provjerljiva |
| `classify` | — | — | **nepodržano**: nema orakla istine za slobodan bosanski iskaz |
| `apply_formula`, `solve_equation`, `compare`, `order`, … | K5–K7 | — | kasnije |

**Zašto su K2/K4 odgođeni, a ne pokvareni:** Live96 ih nikad nije ni izvršio
(rotacija na svježoj sesiji uvijek daje prvi arhetip), a ranija kampanja
final14 ih je forsirala i oni su padali. Nisu popravljeni — bili su
neispitani. Vraćaju se **isključivo** kad im server dobije generator; do tada
su suženi **u podacima** (`allowed_task_archetypes` u svakom pilot redu), pa
ih ni rotacija ni intent tabela ne mogu izabrati.

`error_category_set` **ostaje** u podacima lekcija: to su podaci koje K4
koristi unaprijed i suženje arhetipa ih ne smije obrisati.

### Disciplina za `identify_error` (mašinerija spremna za K4)

Kategorija greške se **izvodi strukturno** iz lanca koraka (server ponovo
izračuna lanac egzaktnim `Fraction`-ima, nađe jedini nedosljedan korak i
prepozna ŠTA se strukturno desilo). Proza se ne čita. Kad K4 stigne, server će
lanac i **konstruisati** unaprijed — isti katalog grešaka koji `verifiers.py`
umije prepoznati unazad.

Kategorija nosi i **smjer** (`unequal_scaling` = proširivanje,
`wrong_reduction` = skraćivanje), pa lekcija o proširivanju automatski odbija
zadatak o grešci pri skraćivanju — bez ijedne grane po lekciji.

Kategorija nosi i **smjer** (`unequal_scaling` = proširivanje,
`wrong_reduction` = skraćivanje), pa lekcija o proširivanju automatski odbija
zadatak o grešci pri skraćivanju — bez ijedne grane po lekciji.

**VIDLJIVI TEKST OPCIJE JE SERVER-OWNED** — u Fazi 1 to više nije izuzetak za
jedan arhetip nego **pravilo za sve**: server renderuje svaki vidljivi
matematički sadržaj, a za kategorije greške tekst dolazi iz
`verifiers.ERROR_CATEGORY_LABELS`.

Posljedice, sve pokrivene testovima
(`tests/test_contract_error_option_fidelity.py`):

- svaka izvodiva kategorija mora imati projektni bosanski tekst — ugovor koji
  navede kategoriju bez teksta pada na **učitavanju**;
- označena opcija je uvijek tekst **izvedene** kategorije;
- duple kategorije, nepoznate kategorije i kategorija izvan ugovora odbijaju;
- odbijanje ne mijenja stanje i ne troši drugi model poziv.

---

## Poznato ograničenje: „lakše“ nema prostora u sadašnjim podacima

Svih šest pilot ugovora ima `default == min` na obje **mjerljive** dimenzije
težine, pa zahtjev za lakšim zadatkom nema šta da spusti — mijenja se samo
oznaka težine u sesiji, ne i sam zadatak. Motor radi ispravno („kad prostora
nema, cilj ostaje na granici“); uzrok su **vrijednosti** u
`data/contract_templates.json`, ne kod.

Popravak je odluka o kurikulumu (npr. `operand_magnitude` default 2, pa lakše
ide na 1), jer mijenja i to šta je „normalna“ težina lekcije. Dok se ne donese,
stanje je zaključano testom
`test_33_easier_has_no_headroom_in_the_current_pilot_data` — podizanje defaulta
obara test i traži svjesnu odluku.

`distractor_similarity` je i dalje **samo cilj u promptu**: ne izvodi se iz
zadatka i nikad se ne tvrdi da je provjeren.

---

## Nasljeđivanje: samo sužavanje

Dijete smije **izbaciti** operaciju ili arhetip iz roditeljskog skupa, ali ga
nikad **proširiti**, niti vratiti ograničenje na `"any"`. Bez toga bi „mali
override“ tiho otključao matematiku koju predložak nije odobrio, a niko ne bi
ponovo pregledao cijelu naslijeđenu površinu. Lanac je najviše 3 nivoa; ciklus i
nepoznato polje su greška na učitavanju.

---

## Učenikova molba za oblik zadatka (intent tabela)

`matbot/contracts/intent.py` je **zatvorena, server-owned tabela fraza** (bez
modela, bez slobodnog pogađanja). Postoji jer je sukob između učenikove molbe i
dodijeljenog oblika bio najveći pojedinačni uzrok odbijanja u Live96.

Odluka, tim redom:

1. poklopi li se **tačno jedan** arhetip → to je tražen oblik;
2. je li u `effective_archetypes` **i** ima li generator → plan ga koristi;
3. inače (nema poklapanja, više poklapanja, ugovor ne dozvoljava, generator ne
   postoji) → normalna serverska rotacija. **Nikad greška, nikad drugi poziv.**

Izabrani plan (`archetype`, `source`, `requested`) ide u log svakog turna.

---

## Šta ugovor NE smije dodirivati

Ugovor opisuje **matematiku lekcije**. Sve ovo ostaje u generičkom serverskom
sloju i nijedno polje ugovora ga ne može promijeniti:

otisak kurikuluma · invalidacija aktivnog zadatka · zaštita starog odgovora ·
nemogućnost obnove zadatka iz browsera · odbacivanje zakašnjelog odgovora ·
izolacija na reload · copy-on-write stanje · jedan model poziv po turnu ·
bez retryja i skrivene regeneracije · sigurno odbijanje bez mutacije stanja ·
autentikacija, rate limit, turn lock.

---

## Registar izuzetaka (mora ostati prazan ili obrazložen)

Grana po **imenu ili ID-ju lekcije** u generičkom motoru dozvoljena je samo za
matematiku koja se stvarno ne može izraziti postojećim ugovorom, i mora biti
navedena ovdje.

| Datum | Lekcija | Izuzetak | Obrazloženje |
|---|---|---|---|
| — | — | *(nema)* | Faza 1 nema nijedan izuzetak; `test_g1_*` i `test_g2_*` to čuvaju. |

## Registar `legacy_pinned` povrataka

| Datum | Lekcija | Razlog |
|---|---|---|
| — | — | *(nema)* |

---

## Privremene kontrole koje MORAJU nestati

| Oznaka | Šta je | Ukloniti u |
|---|---|---|
| ~~`MATBOT_CONTRACT_ENGINE=off`~~ | **UKLONJENO 2026-08-14** zajedno s povučenim K1/K3 motorom koji je gasilo. Ugovor je sada isključivo PODATAK za Luna prompt; nema prekidača. | — |
| `stage_a_only=True` u `schema.build` | ograničava uključene ugovore na deterministički podržane arhetipe | kad se skup sposobnosti proširi |
| suženje `allowed_task_archetypes` u 6 pilot redova | drži K2/K4 van rotacije dok nemaju generator | kad K2/K4 dobiju generator |

---

## Granica legacy koda

`matbot/legacy/practice_routing.py` drži zatečeno routiranje po **ID-ju lekcije**
za lekcije koje još nemaju ugovor. To je jedino mjesto pod `matbot/` gdje ID
lekcije smije postojati; arhitektonska kapija ga izuzima, a
`test_13_topic_ids_live_only_in_the_legacy_boundary_or_fixtures` čuva da se ne
proširi. **Ne dodavati nove unose** — nova lekcija ide kroz ugovor.

Pet porodica (`fraction_add_subtract_equal`, `_unlike`, `fraction_multiplication`,
`fraction_division`, `fraction_expression`) postoji isključivo zbog te granice.
`fraction_expression` još ima nemigriranog potrošača; ostale četiri zadržane su
da bi zatečeno ponašanje ostalo doslovno isto. Brišu se tek kad parnost dokaže
da nikoga ne mijenjaju.

## Kapija parnosti (528 lekcija)

`tests/fixtures/legacy_routing_baseline.json` zamrzava PRE-Stage-A routiranje za
svih 528 lekcija bez ugovora — porodice u tačnom redoslijedu, prvu porodicu i
ponašanje teže/lakše. Generiše ga `scripts/freeze_legacy_routing.py`, koji
algoritam **ponovo implementira** iz istorijskog stanja umjesto da poziva kod
koji se testira. `tests/test_legacy_routing_parity.py` traži 528/528 poklapanje.

Fixture **smije** sadržavati ID-jeve lekcija — to su podaci migracije, ne kod
generičkog motora.
