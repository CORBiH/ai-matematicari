# Ručni QA nakon deploya — Vježbajmo

14 lekcija, odabranih tako da pokriju svaku rutu i svaki popravak iz ovog
izdanja. Nije potrebno gledati kod: sve se vidi u samoj aplikaciji.

## Šta je promijenjeno u ovom izdanju

1. **Označen odgovor mora slijediti iz rješenja.** Paket u kojem „Uradi ga ti“
   računa jedno, a označeno je drugo, više se ne objavljuje.
2. **Samo jedan tačan odgovor.** Dvije opcije iste matematičke vrijednosti
   (npr. `√12` i `2√3`, ili isti skup rješenja drukčije zapisan) se odbijaju.
3. **Jedan nagovještaj.** „Ne znam — daj mi hint“ daje JEDAN koristan
   nagovještaj. Ponovni klik vraća isti tekst. „Uradi ga ti“ i dalje daje
   cijelo rješenje.
4. **„Daj mi novi zadatak“ mora dati drugu vrstu vježbe**, ne isti zadatak s
   drugim brojevima ili imenima.

## Kako testirati svaku lekciju

Za svaku lekciju ispod uradi ovaj niz i gledaj ono što je u koloni „na šta
paziti“:

```
Daj mi zadatak.  →  Daj mi novi zadatak.  ×3  →  Daj mi teži zadatak.
→  Ne znam — daj mi hint  →  ponovo klikni hint  →  Uradi ga ti
```

| # | lekcija | razred | očekivana ruta | na šta paziti |
|---|---|---|---|---|
| 1 | `6-05-011` Brojevni izrazi i tekstualni zadaci s decimalnim brojevima | 6 | deterministička, 0 poziva (vraćena) | **Ovo je lekcija s bugom kusura.** Provjeri da označeni odgovor odgovara računu i da „Uradi ga ti“ ne kaže drugi broj. Odgovori odmah (bez čekanja). |
| 2 | `8-01-009` Jednačina x²=a, a≥0 | 8 | Luna fast (migrirana) | **Lekcija s dvije tačne opcije.** Traži 4–5 zadataka zaredom i provjeri da NIKAD dvije ponuđene opcije nisu ista vrijednost (`√12` i `2√3`). |
| 3 | `8-03-019` Jednostavni kamatni račun | 8 | Luna fast (migrirana) | Decimalni novac: kamata i iznos moraju se slagati s rješenjem. |
| 4 | `9-07-033` Tekstualni zadaci s površinom i zapreminom | 9 | Luna fast (migrirana) | Višekoračni tekstualni zadatak; provjeri da traženo i označeno budu ista veličina. |
| 5 | `6-04-001` Pojam razlomka, brojnik i nazivnik | 6 | Luna fast | Već si je testirao — provjeri da je i dalje dobra i da „novi zadatak“ mijenja vrstu pitanja. |
| 6 | `6-04-005` Proširivanje razlomaka | 6 | Luna fast (bivša ugovorna) | Zadatak mora tražiti PROŠIRIVANJE (ne skraćivanje), brojevi do 60. |
| 7 | `6-04-006` Skraćivanje i nesvodivi razlomak | 6 | Luna fast (bivša ugovorna) | Tačan odgovor mora biti do kraja skraćen; distraktori smiju biti neskraćeni oblici iste vrijednosti. |
| 8 | `6-02-001` Skupovi N i N0 | 6 | Luna fast (migrirana) | Ranije je davala isto pitanje na sva tri nivoa — provjeri da se sada mijenja. |
| 9 | `7-04-014` Podudarnost trouglova - USU | 7 | Luna fast | Već si je testirao — kontrolna tačka da se ništa nije pokvarilo. |
| 10 | `8-04-001` Pitagorina teorema - formulacija | 8 | Luna fast (migrirana) | Ranije jedna jedina rečenica na svim nivoima; sada traži 4 zadatka i gledaj mijenja li se vrsta. |
| 11 | `9-05-006` Grafička metoda rješavanja sistema | 9 | Luna fast | Provjeri da zadatak ostaje u lekciji (grafička metoda), a ne prelazi na računsku. |
| 12 | `6-01-001` Pojam skupa, elementi i označavanje | 6 | deterministička, 0 poziva | Mora biti trenutna (bez čekanja) i i dalje raznolika. |
| 13 | `9-01-006` Jednaki algebarski razlomci | 9 | Luna fast (migrirana) | Algebarski izrazi; provjeri da nema dvije jednake opcije. |
| 14 | `6-06-003` Razmjera/omjer | 6 | Luna fast (migrirana) | Ranije uvijek „Zapiši razmjeru … u najjednostavnijem obliku“ — sada traži 4 zadatka i gledaj mijenja li se oblik. |

## Šta prijaviti

Prijavi odmah ako vidiš bilo šta od ovoga:

- označen odgovor koji nije matematički tačan,
- dvije ponuđene opcije koje su ista vrijednost,
- „Uradi ga ti“ koje daje drugi rezultat nego što je označeno,
- hint koji odaje konačan odgovor ili tačnu opciju,
- drugi klik na hint koji daje NOVI (dublji) hint,
- „novi zadatak“ koji je isti zadatak s drugim brojevima ili imenima,
- zadatak koji izlazi iz lekcije.

Napomena o brzini: lekcije 1 i 12 su determinističke i odgovaraju odmah;
ostale idu kroz model i traju otprilike 5–15 sekundi.
