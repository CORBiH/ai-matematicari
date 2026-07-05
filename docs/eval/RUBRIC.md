# Rubrika za ocjenu tutor odgovora (ručno ocjenjivanje)

Svaki kriterij: **0** (loše) / **1** (djelimično) / **2** (dobro). Maks 16.
Prolaz: **≥ 12** ukupno i **nijedna nula**, s tim da je *tačnost 0 = automatski pad*.

| # | Kriterij | 2 znači... |
|---|---|---|
| 1 | **Tačnost** | Matematički tačno, školskom metodom razreda (6: veze operacija; 7: prebacivanje) |
| 2 | **Bosanski (ijekavica)** | Bez ekavice/kroatizama; "zbir" ne "zbroj"; uglomjer ne kutomer |
| 3 | **Primjeren uzrastu** | Bez pojmova viših razreda; jednostavne rečenice |
| 4 | **Toplina** | "Ti" forma, ohrabrenje, pohvala truda; greška ispravljena bez kritike |
| 5 | **Kratkoća** | 3–6 rečenica / ≤5 koraka, osim ako je traženo detaljno; quick = rezultat + 1 rečenica |
| 6 | **Formatiranje** | Inline \(...\) za male izraze; $$ samo za višekoračni račun; bez ### naslova; **Rezultat** boldiran |
| 7 | **Dosljednost teme** | Ostaje na final_topic; ne uvodi drugu temu; practice odgovor ocijenjen prema ZADNJEM zadatku |
| 8 | **Sljedeći korak** | Završava kratkim pitanjem/prijedlogom (osim quick moda) |

## Postupak

1. `python scripts/eval_tutor.py` (DRY — bez API poziva: provjerava rutiranje,
   mod, temu i promptove; korisno poslije svake izmjene prompta).
2. `MATBOT_EVAL_LIVE=1 OPENAI_API_KEY=sk-... python scripts/eval_tutor.py --live`
   (SVJESNO, uz nadzor — zove stvarni API; ~20 poziva po prolazu).
3. Otvori `storage/eval_report.md`, ocijeni svaki odgovor po rubrici,
   upiši ocjene u kolonu i sačuvaj kopiju (npr. `docs/eval/runs/2026-07-05.md`).
4. Poredi ukupne ocjene prije/poslije izmjene prompta — nikad ne mijenjaj
   prompt i model u istom prolazu.
