# Faza 4G — ručni smoke test spremnosti lekcija

Ponovljiv ručni protokol za sedam lekcija u opsegu Faze 4G. Svaki korak ima
očekivan rezultat i tačne uslove ZAUSTAVLJANJA. Protokol ne zahtijeva pristup
logovima — sve se provjerava iz onoga što je vidljivo u pregledaču.

## Lekcije u opsegu

| ID | Naziv | Razred |
|---|---|---|
| 6-03-004 | Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25 | 6 |
| 6-04-009 | Sabiranje i oduzimanje razlomaka jednakih imenilaca | 6 |
| 6-04-010 | Sabiranje i oduzimanje razlomaka različitih imenilaca | 6 |
| 6-04-011 | Množenje razlomka prirodnim brojem i razlomkom | 6 |
| 6-04-012 | Dijeljenje razlomka prirodnim brojem i razlomkom | 6 |
| 7-03-006 | Upoređivanje racionalnih brojeva | 7 |
| 9-05-010 | Sistem bez rješenja | 9 |

## Komande (provjerene u repou)

```powershell
# ograničeni offline testovi Faze 4G
.venv\Scripts\python.exe -m pytest tests\test_contradiction_solution_gate.py tests\test_divisibility_wording_variants.py tests\test_direct_computation_mcq.py tests\test_comparison_mcq.py tests\test_unsafe_notation_preflight.py tests\test_frontend_dom_behaviour.py -q

# cijeli offline paket (jednom, ~1500+ testova; na Windowsu pokrenuti iz
# Git Bash-a da bi pre-push testovi našli `sh`)
python -m pytest -q

# statička kapija (nula SDK poziva)
python tools/run_live_release_gate.py --static-checks

# lokalno pokretanje aplikacije
python app.py    # http://127.0.0.1:5000
```

## Protokol po lekciji (svaka od sedam lekcija)

Za svaku lekciju uraditi redom:

1. **Izbor lekcije.** Izaberi razred, oblast i tačnu lekciju iz tabele.
2. **Prvi zadatak.** Pošalji „Daj mi zadatak.“
   *Očekivano:* uvod „Evo zadatka.“, pitanje iz TAČNO te lekcije, četiri
   opcije, tačno jedna matematički tačna.
3. **Pogrešan odgovor.** Klikni namjerno pogrešnu opciju.
   *Očekivano:* opcija pocrveni, povratna poruka objašnjava grešku, zadatak i
   opcije OSTAJU na ekranu.
4. **Nagovještaj.** Pošalji „Ne znam.“ (ili chip za pomoć).
   *Očekivano:* nagovještaj se odnosi na AKTIVNI zadatak; zadatak se ne mijenja.
5. **Cijelo rješenje.** Pošalji „Uradi ga ti.“
   *Očekivano:* potpun postupak za aktivni zadatak; svaka jednakost u postupku
   računski tačna; zadatak se ne mijenja.
6. **Novi zadatak.** Pošalji „Daj mi novi zadatak.“
   *Očekivano:* STVARNO drugačiji zadatak (drugi brojevi i druga tačna
   vrijednost); nijedna crvena/zelena oznaka starog zadatka ne ostaje.
7. **Lakši.** Pošalji „Daj mi lakši zadatak.“
   *Očekivano:* ako je već najlakši nivo, uvod kaže „Ovo je već najlakši
   nivo…“; nikad lažno „Evo lakšeg zadatka.“ bez stvarne promjene.
8. **Teži.** Pošalji „Daj mi teži zadatak.“
   *Očekivano:* zahtjevniji zadatak ISTE lekcije; uvod istinit.
9. **Još jedan novi zadatak.** Ponovi korak 6.
   *Očekivano:* opet različit kanonski paket.
10. **Identitet i vizuelno stanje.** Provjeri da su pitanje, opcije i oznake
    uvijek pripadali istom (aktivnom) zadatku kroz sve korake.

## Dodatno po lekciji

### 6-03-004 (djeljivost)

Pošalji doslovno:

> Daj mi MCQ zadatak gdje broj mora biti djeljiv i sa 6 i sa 25.

*Očekivano:*
- tačno jedna tačna opcija;
- tačna opcija je djeljiva i sa 6 i sa 25 (dakle sa 150);
- povratna poruka i rješenje pominju CIO uslov (i 6 i 25), ne samo jedan dio.

Varijante formulacije koje server sada čita kao potpun uslov:
„i sa 6 i sa 25“, „sa 6 i istovremeno sa 25“, „sa 6 i sa brojem 25“,
„sa 6, ali i sa 25“, „sa 6 te sa 25“, „sa 6 i 25“, „sa 2, 3 i 5“.
Negacija („nije djeljiv sa 25“) i disjunkcija („ili“) padaju zatvoreno —
ako takav zahtjev vrati zadatak koji uslov tumači POGREŠNO, to je stop uslov.

### 6-04-009 / 6-04-010 (sabiranje i oduzimanje razlomaka)

- 6-04-009: imenioci u zadatku moraju biti JEDNAKI.
- 6-04-010: imenioci moraju biti RAZLIČITI; rješenje koristi zajednički
  imenilac; konačan rezultat ispravno skraćen ili ekvivalentan.

### 6-04-011 (množenje)

- operacija u zadatku je množenje (`\cdot`), nikad sabiranje/dijeljenje;
- skraćivanje unakrsno, ako se koristi, mora biti računski tačno.

### 6-04-012 (dijeljenje)

- operacija je dijeljenje (`:`); djelilac nikad nula;
- rješenje koristi recipročnu vrijednost i rezultat odgovara originalnom
  izrazu dijeljenja.

### 7-03-006 (upoređivanje)

- zadatak sa znakom: ponuđeni znakovi `<`, `>`, `=` — označeni znak odgovara
  stvarnoj relaciji dva prikazana broja;
- zadatak „najveći/najmanji“: tačna opcija je stvarno ekstremna vrijednost.

### 9-05-010 (sistem bez rješenja)

- zadatak tipa „Odredi koliko rješenja ima sistem…“ s jednakim lijevim
  stranama i različitim desnim;
- rješenje SMIJE prikazati nemoguću jednakost (npr. `3 = 5`) — ali uz nju u
  istoj rečenici mora stajati da nije tačna („što nije tačno“, „nemoguće“,
  „kontradikcija“);
- tačna opcija: sistem nema rješenja.

## Uslovi ZAUSTAVLJANJA (bilo koja lekcija)

Prekini smoke i prijavi nalaz ako se desi bilo šta od:

1. MCQ bez ijedne tačne opcije.
2. Više od jedne matematički tačne opcije.
3. Pogrešna opcija označena kao tačna.
4. Očekivani odgovor se ne slaže s postupkom u rješenju.
5. Nagovještaj mijenja aktivni zadatak.
6. Cijelo rješenje mijenja aktivni zadatak.
7. „Novi zadatak“ vrati kanonski isti paket (isti brojevi, iste opcije).
8. Crvena/zelena oznaka starog zadatka ostane na novom.
9. Sigurna greška ukloni aktivni zadatak s ekrana (server javio da je sačuvan).
10. Kasni odgovor prepiše noviji zadatak (vidljiva „zamjena“ bez zahtjeva).
11. Sadržaj druge lekcije (npr. procenti u lekciji o razlomcima).
12. Generička greška na običan ispravan tok (prvi zadatak, klik, hint).

Jedan izolovan fail-closed odgovor („Nešto je zapelo…“) uz OČUVAN zadatak nije
stop uslov — ponovi poruku; stop je tek ponavljanje na istom koraku.
