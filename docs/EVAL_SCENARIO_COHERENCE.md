# Koherentnost scenarija evaluacije (korijenski uzrok RC11)

Ovaj dokument je revizija 22 nekoherentna scenarija iz talasa
`discovery-100 @ 8a8f04d` i zapis kako je svaki popravljen za konsolidovani
završni talas. **Nijedno produktno očekivanje nije ublaženo** — mijenjala se
isključivo testna fikstura.

## Šta se dogodilo

Scenario zamrzne poruku učenika i izabranu lekciju. U 22 od 100 slučajeva ta
dva su bila nespojiva: poruka je tražila jednu vrstu zadatka, a lekcija predaje
drugu. Bot je slijedio **lekciju** — što je ispravno produktno ponašanje, jer je
izabrana lekcija vlasnik onoga što se vježba — pa je očekivanje scenarija bilo
nevaljano. Petina talasa je time potrošena na mjerenje nepostojećeg kvara.

## Trajna zaštita

`tools/practice_eval/coherence.py` dokazuje nespojivost **offline**, u
`--dry-run`, prije nego što se potroši ijedan živi poziv. Koristi isključivo
postojeće zatvorene gramatike proizvoda:

| Detektor | Dokaz | Hvata |
|---|---|---|
| `relation_kind_conflict` | kanonski naslov lekcije vs vrsta relacije iz poruke (`mcq_integrity.read_solve_statement`, Task 3) | A007, A013, A022, A024, B010, B011, B012 |
| `lesson_contract_conflict` | semantički ugovor vježbe lekcije (`semantic_practice`, Task 2) primijenjen na poruku | E008, F005, F007, F008 |
| `non_solve_lesson_conflict` | zatvoren spisak naslova koji ne predaju rješavanje | A015 |
| `unsupported_request_family` | scenario traži presudu skupa rješenja, a relacija je van podržane linearne porodice | A016, A017 (uz oznaku `solve_set_adjudicated`) |

**12 od 22** se hvata deterministički. Ostalih 10 (C009, C012, C014, C015,
F001–F004 i granični A016/A017 bez oznake) nisu dokazivi iz naslova lekcije bez
pogađanja kurikuluma; oni se sprječavaju **konstrukcijom** — završni talas ih
nema, jer je svaka poruka uparena s lekcijom koja tu radnju stvarno predaje.
Nedokazivo se **nikad** ne prijavljuje kao nevaljan scenario.

## Deklaracija namjere

Scenario sada nosi polje `request_alignment`:

- `must_follow` (podrazumijevano) — zahtjev je predmet testa i **mora** biti
  ispunjen; dokazano nespojiv zahtjev čini scenario nevaljanim;
- `lesson_overrides` — namjerna sonda: zahtjev je **van** lekcije i bot ga ne
  smije poslušati. Nespojivost je tada svrha scenarija i nikad se ne prijavljuje.

Ovo polje je ispravka za **E006**: lekcija „Podudarnost trouglova - SUS“,
zahtjev van lekcije, bot ostao na lekciji — evaluator je to brojao kao
semantički kvar proizvoda, a to je bio **ispunjen ugovor**.

## Mapa popravki (22 scenarija)

| Izvorni | Nespojivost | Popravka u završnom talasu |
|---|---|---|
| A007 | nejednačina `x>-2` na lekciji jednačina (7-02-016) | ista nejednačina na 7-02-019 „Nejednačine sa sabiranjem i oduzimanjem u Z“ → **FW-D04** |
| A013 | interval `-1/2<=x<3/4` na lekciji jednačina sa zagradama | interval na 7-03-019 „Nejednačine u Q“ → **FW-D06** |
| A015 | rješavanje `2x-5=0` na lekciji „Skupovi N, Z, Q, I i R“ | rješavanje na 9-04-002 „Ekvivalentne jednačine“ → **FW-D01/FW-D02** |
| A016 | `x^2=2` (nelinearno) na „Realni brojevi i brojevna osa“ | linearna relacija u istom domenu → **FW-D07** (R ostaje kontinuiran) |
| A017 | `x^2=2` (nelinearno) na „Uređenost i poređenje realnih brojeva“ | linearna relacija u istom domenu → **FW-D07** |
| A022 | nejednačina `-4x<=8` na „Jednačina sa razlomcima“ | nejednačina na 9-04-015 „Nejednačina sa razlomcima“ → **FW-D08** |
| A024 | jednačina `2(x-1)=2x+1` na „Nejednačina sa razlomcima“ | jednačina na 9-04-004 „Jednačina sa razlomcima“ → **FW-S02/S03/S04** |
| B010 | nejednačina `x-4<1` na „Ekvivalentne jednačine“ | nejednačina na 9-04-014 „Nejednačina sa zagradama“ → **FW-D07/FW-S05** |
| B011 | lanac `-3<2x+1<5` na „Jednačina sa zagradama“ | lanac na 7-02-019 → **FW-D04/FW-D10** |
| B012 | lanac `-4<=3x-1<8` na „Jednačina sa razlomcima“ | **jednačina** s razlomkom na istoj lekciji → **FW-S02** (dokaz o paketu `2` vs `{2}` sačuvan zasebno) |
| C009 | koordinatni zadatak na „Tabele, stupčasti i kružni dijagrami“ | zadatak o tačkama na 6-10-007 „Prikaz funkcije tabelom i grafički“ → **FW-F01…F06** |
| C012 | osna simetrija na „Simetrala ugla i konstrukcija“ | zadatak o simetrali ugla i centru upisane kružnice → **FW-G05/FW-G06** |
| C014 | zahtjev mimo „Centralno simetrične tačke i figure“ | zamijenjen ciljanim geometrijskim scenarijem na 6-09-001 → **FW-G01…G04** |
| C015 | zahtjev mimo „Translacija“ | isto → **FW-G01…G04** |
| E008 | nejednačina na „Podudarnost trouglova - SSU“ | nejednačina na 7-02-019 → **FW-D04**; sonda van lekcije zadržana **zasebno** kao `lesson_overrides` → **FW-X01** |
| F001 | zahtjev mimo „Udaljenost tačaka u koordinatnom sistemu“ | zamijenjen koherentnim geometrijskim scenarijem → **FW-G01…G06** |
| F002 | zahtjev mimo „Konstrukcija iracionalnih tačaka na osi“ | isto |
| F003 | zahtjev mimo „Konstruktivni zadaci sa površinama kvadrata“ | isto |
| F004 | zahtjev mimo „Pravilna uspravna trostrana piramida“ | isto |
| F005 | koordinatni zadatak na „Mreža trostrane prizme“ | ugovor `net_semantics` traži zadatak o mreži — scenario izostavljen iz završnog talasa (pokriven postojećim F5K talasom) |
| F007 | isto, „Mreža trostrane piramide“ | isto |
| F008 | isto, „Mreža četverostrane piramide“ | isto |

## Politika brojevnih domena

Prikovana i uz proizvod i uz evaluator (`coherence.DOMAIN_POLICY`, test to
dokazuje protiv `mcq_integrity._SOLVE_DISCRETE_DOMAIN_MIN`):

```
N  = {1, 2, 3, ...}      NULA NIJE PRIRODAN BROJ
N0 = {0, 1, 2, 3, ...}
```

Dvosmislena formulacija („prirodni brojevi s nulom“) se u očekivanjima
scenarija **odbija**. Ovo je izravno iz živog A020: traženo `N`, objavljeno `Z`,
i označeni `{0}` je tačan nad Z a nad N je skup **prazan**.

## Klasifikacija ishoda

`tools/practice_eval/classify.py` razdvaja ono što sirovi PASS/FAIL ne može:

`PRODUCT_CORRECTNESS_FAILURE` · `COVERAGE_GAP` · `SAFE_FAIL_CLOSED` ·
`HARNESS_INVALID_SCENARIO` · `CASCADE_ONLY` · `EVALUATOR_MISCLASSIFICATION` ·
`INFRA_SDK` · `TIMEOUT` · `CLEAN`

Tri pravila koja se ne smiju izgubiti:

1. **Dokaz o paketu preživljava nevaljan scenario** (živi B012). Nekoherentnost
   obara očekivanje scenarija, nikad mjerenje objavljenog sadržaja.
2. **Sigurno odbijanje objave nikad ne postaje „pogrešan sadržaj“.**
3. **Poslije prvog sigurnog odbijanja** kasnija odstupanja stanja/težine su
   `CASCADE_ONLY`, ne nezavisni kvarovi.

Prekoračenje granice od dva poziva je uvijek produktni kvar i **nikad** se ne
sakriva iza nevaljanog scenarija.
