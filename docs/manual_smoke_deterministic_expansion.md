# Ručni smoke test — deterministička kapacitetna ekspanzija

Vrijedi za stanje u kojem su aktivne obje release zastavice
(`MATBOT_PRACTICE_DIFFICULTY_LEVELS=enabled`) i deterministička strategija
(podrazumijevano uključena; rollback: `MATBOT_DETERMINISTIC_PRACTICE=disabled`).

**Šta se općenito očekuje na determinističkoj lekciji** (svaka stavka ispod):

- klik na „Vježbaj” / „Daj mi novi zadatak” / „Daj mi lakši/teži zadatak”,
  „Ne znam.” (hint), „Uradi ga ti.” (rješenje) i klik na opciju —
  **sve ispod ~1 sekunde** i **0 poziva modela** (u logu:
  `route=deterministic_package` / `deterministic_hint` / `deterministic_solution`
  / `deterministic_grading`, `calls=0`);
- zadatak je uvijek MCQ sa 4 opcije, tačno jedna tačna;
- „teži” diže nivo 1→2→3 (uvod govori istinu i na granici), „lakši” spušta;
- hint 1 nikad ne otkriva rezultat; rješenje otkriva tačnu opciju i završava
  tačnim rezultatom;
- slobodno pitanje („Zašto ovo pravilo radi?”) i dalje ide na model
  (odgovor traje duže — to je očekivano).

## Reprezentativne determinističke lekcije (po jedna po novom kapacitetu)

| # | Lekcija | Šta klikati | Očekivani zadatak | Matematička invarijanta |
|---|---------|-------------|-------------------|-------------------------|
| 1 | 6.r → Prirodni brojevi → **Redoslijed računskih operacija i zagrade** (6-02-007) | novi → teži → teži | `Izračunaj: $(a + b) : c \cdot d$`-oblik | prioritet operacija ispravan; dijeljenje uvijek egzaktno |
| 2 | 6.r → Djeljivost → **Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25** (6-03-004) | novi → teži | „Koji od ponuđenih brojeva je djeljiv sa $d$ (i sa $d_2$)?” | tačno jedan broj prolazi SVA pravila; nivo 2+ traži dva pravila |
| 3 | 6.r → Djeljivost → **NZD** (6-03-008) | novi → hint → rješenje | „Koliki je najveći zajednički djelilac …?” | rješenje dokazuje dijeljenja bez ostatka |
| 4 | 6.r → Djeljivost → **Prosti i složeni brojevi** (6-03-005) | novi ×3 | „Koji od ponuđenih brojeva je prost/složen?” | na nivou 2 „varljivi” složeni (51, 91…) |
| 5 | 6.r → Razlomci → **Upoređivanje razlomaka** (6-04-008) | novi → teži | „Koji je od ponuđenih brojeva najveći/najmanji?” | tačno jedan ekstrem; opcije nikad iste vrijednosti |
| 6 | 6.r → Decimalni → **Množenje decimalnih brojeva** (6-05-009) | novi → lakši | `Izračunaj: $a,b \cdot c,d$` | rezultat egzaktan, zarez na pravom mjestu |
| 7 | 6.r → Postotak → **Procenat broja** (6-06-002) | novi → teži ×2 | „Izračunaj 20 % od broja $250$.” (nivo 3: traženje cjeline) | znak % samo u prozi, nikad u `$...$` |
| 8 | 6.r → Postotak → **Aritmetička sredina** (6-06-004) | novi | „Izračunaj aritmetičku sredinu brojeva …” | (zbir) : broj podataka, egzaktno |
| 9 | 7.r → Cijeli brojevi → **Množenje cijelih brojeva i pravilo znakova** (7-02-011) | novi ×3 | `Izračunaj: $(-a) \cdot b$` | pravilo znakova; distraktor sa pogrešnim znakom |
| 10 | 7.r → Cijeli brojevi → **Jednačine sa sabiranjem i oduzimanjem u Z** (7-02-016) | novi → hint → rješenje | `Riješi jednačinu: $x + a = b$` | rješenje sadrži provjeru uvrštavanjem |
| 11 | 7.r → Racionalni → **Sabiranje racionalnih brojeva** (7-03-009) | novi → teži | zbir razlomaka s predznacima | negativan mješovit broj se NE piše mješovito |
| 12 | 7.r → Racionalni → **Apsolutna vrijednost racionalnog broja** (7-03-005) | novi → teži | `$|-a|$`, na nivou 2+: `$|-a| + |b|$` | rezultat nikad negativan na nivou 1 |
| 13 | 8.r → Stepeni → **Množenje i dijeljenje stepena jednakih osnova** (8-01-015) | novi → teži | „Zapiši u obliku jednog stepena: $a^m \cdot a^n$” | izložioci se sabiraju/oduzimaju; opcije različitih vrijednosti |
| 14 | 8.r → Korijeni → **Kvadratni korijen…** (8-01-008) | novi → teži | `Izračunaj: $\sqrt{81}$` | uvijek savršen kvadrat, bez približnih vrijednosti |
| 15 | 8.r → Podaci → **Povoljni ishodi i klasična vjerovatnoća** (8-06-012) | novi → teži ×2 | kuglice u vreći → razlomak | vjerovatnoća u (0,1]; nivo 3 komplement („NE bude…”) |
| 16 | 9.r → Jednačine → **Jednačina sa zagradama** (9-04-003) | novi → hint → rješenje | `Riješi jednačinu: $a(x + b) = c$` | koraci: podijeli → prebaci → provjera |
| 17 | 9.r → Podaci → **Aritmetička sredina i interpretacija podataka** (9-08-010) | novi | sredina 3–5 brojeva | egzaktan decimalan rezultat na nivou 3 |

## Kontrolne model-lekcije (moraju i dalje ići na model, sporije)

| Lekcija | Zašto je model |
|---|---|
| 6.r → Djeljivost → **Tekstualni zadaci iz djeljivosti** (6-03-010) | tekstualni zadaci — nema ugovora |
| 6.r → Razlomci → **Brojevni izrazi s razlomcima** (6-04-014) | višeoperacijski izrazi izvan ugovora |
| 6.r → Skupovi → **Pojam skupa…** (6-01-001) | konceptualna lekcija |
| 9.r → Jednačine → **Tekstualni zadatak koji se svodi na linearnu jednačinu** (9-04-011) | modelovanje teksta |
| 8.r → Podaci → **Čitanje i kritičko tumačenje dijagrama** (8-06-008) | vizuelna lekcija |

Na svakoj od njih: „Daj mi zadatak” traje duže (dva poziva modela) i log kaže
`route=model_tutor_reviewer` — to je ispravno ponašanje, ne regresija.

## Dodatno provjeriti na bilo kojoj determinističkoj lekciji

1. Odgovori TAČNO na zadatak → „Tačno — rezultat je …”; zatim „Daj mi novi
   zadatak” → nov, kanonski različit zadatak.
2. Odgovori POGREŠNO dvaput → drugi pogrešan klik otkriva tačan rezultat.
3. Postavi slobodno pitanje usred zadatka → model odgovara, zadatak ostaje
   aktivan i klik poslije toga i dalje radi deterministički.
4. Prebaci lekciju pa se vrati → svjež zadatak, nivo kreće od 1.

## Batch #2 — dodatne reprezentativne determinističke lekcije

| # | Lekcija | Šta klikati | Očekivani zadatak | Invarijanta |
|---|---------|-------------|-------------------|-------------|
| 18 | 6.r → Prirodni brojevi → **Dijeljenje s ostatkom** (6-02-005) | novi → teži ×2 | ostatak/količnik; nivo 3 traži djeljenik | rješenje pokazuje zapis a = b·q + r |
| 19 | 6.r → Decimalni → **Pretvaranje razlomka u decimalni broj i obratno** (6-05-003) | novi ×3 | oba smjera pretvaranja | opcije nikad ne miješaju zapise; razlomak uvijek skraćen |
| 20 | 6.r → Decimalni → **Zaokruživanje decimalnih brojeva** (6-05-007) | novi → teži | zaokruži na dato mjesto | školsko half-up; nikad "a = zaokruženo" |
| 21 | 6.r → Postotak → **Razmjera/omjer** (6-06-003) | novi | skrati razmjeru | opcije različitih količnika |
| 22 | 6.r → Jednačine u Q+ → **x ± a = b i a ± x = b** (6-07-002) | novi → hint → rješenje | jednačina s razlomcima, pozitivna rješenja | provjera uvrštavanjem |
| 23 | 6.r → Mjerenje → **Preračunavanje mjernih jedinica** (6-13-005) | novi ×3 | dužina/masa/vrijeme/površina | m² ide s faktorom 100 po koraku |
| 24 | 7.r → Cijeli → **Nejednačine s množenjem i dijeljenjem u Z** (7-02-020) | novi ×3 | opcije su skupovi rješenja | negativan koeficijent OBRĆE znak |
| 25 | 8.r → Realni → **Naučni zapis broja** (8-01-017) | novi → teži | a·10^n, 1 ≤ a < 10 | samo pozitivni izložioci u opcijama |
| 26 | 8.r → Funkcija → **Pojam linearne funkcije y=kx+n** (8-02-005) | novi → teži | vrijednost/koeficijent | provjera uvrštavanjem u rješenju |
| 27 | 8.r → Proporcionalnost → **Nepoznati član proporcije** (8-03-003) | novi → hint | a : b = c : x | unakrsni proizvodi jednaki |
| 28 | 8.r → Podaci → **Frekvencija** (8-06-002) | novi | niz ocjena → frekvencija | prebrojano egzaktno iz niza |
| 29 | 8.r → Polinomi → **Množenje polinoma** (8-07-008) | novi → rješenje | (x+a)(x+b) | provjera vrijednošću za x = 2 |
| 30 | 9.r → Funkcija → **Jednačina prave kroz dvije tačke** (9-03-014) | novi → hint ×2 | T_1, T_2 → y = kx + n | k iz razlike koordinata, n uvrštavanjem |
| 31 | 9.r → Jednačine → **Promjena znaka nejednakosti…** (9-04-016) | novi ×3 | negativan koeficijent | smjer se uvijek obrće |
| 32 | 9.r → Kvadratne → **ax² + bx = 0** (9-06-013) | novi → rješenje | izlučivanje x | uvijek dva rješenja (jedno je 0) |
| 33 | 9.r → Podaci → **Pretvaranje brzine m/s i km/h** (9-08-013) | novi → teži | množenje/dijeljenje sa 3,6 | egzaktan faktor 18/5 |
