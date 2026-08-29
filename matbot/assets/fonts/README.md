# Vendorovani fontovi — DejaVu Sans

Ovdje stoje BINARNI fontovi koje `matbot/report_pdf.py` koristi za mjesečni
izvještaj roditelju. Namjerno su u repou, a ne u Dockerfileu i ne u sistemu.

## Zašto u repou

Prvo izdanje PDF-a koristilo je `Vera.ttf` koji stiže unutar reportlaba. Vera
nema glif za veliko **`Đ` (U+0110)** i iscrtavala ga je kao prazan kvadratić —
pogađalo je stvarna imena (Đurđević, Đozić, Đemal). Kvar je nađen VIZUELNOM
kontrolom, jer je `ord(znak) in face.charToGlyph` vraćao `True` i kad ključ
pokazuje na glif 0 (`.notdef`), pa su i tekstualni sloj PDF-a i tadašnji test
tvrdili da je slovo tu.

Font se zato vendoruje: lokalni razvoj, testovi, Docker i produkcija moraju
imati BAJT ZA BAJT isti fajl. Sistemski font (`fonts-dejavu-core`) to ne daje —
razlika lokalno/produkcija je upravo klasa greške koja je ovaj kvar i
napravila.

## Porijeklo

| | |
|---|---|
| Projekat | DejaVu Fonts |
| Izdanje | `version_2_37` (`dejavu-fonts-ttf-2.37.zip`) |
| Izvor | https://github.com/dejavu-fonts/dejavu-fonts/releases/tag/version_2_37 |
| SHA256 arhive | `7576310b219e04159d35ff61dd4a4ec4cdba4f35c00e002a136f00e96a908b0a` |

Fajlovi su izvađeni iz te arhive NEIZMIJENJENI:

| Fajl | Izvor u arhivi | SHA256 |
|---|---|---|
| `DejaVuSans.ttf` | `ttf/DejaVuSans.ttf` | `7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954` |
| `DejaVuSans-Bold.ttf` | `ttf/DejaVuSans-Bold.ttf` | `e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724` |
| `LICENSE-DejaVu.txt` | `LICENSE` | `7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93` |

## Licenca

`LICENSE-DejaVu.txt` je izvorni tekst iz same arhive: **Bitstream Vera Fonts
License** + **Arev Fonts License**. Obje izričito dopuštaju umnožavanje i
distribuciju, uključujući „as part of a larger software package", uz uslov da
obavještenje o autorskim pravima ide uz svaku kopiju — zato ovaj fajl stoji
ovdje, pored binarnih fontova.

Fontovi se NE MIJENJAJU i NE PREIMENUJU (licenca to traži za izmijenjene
verzije) i ne prodaju se zasebno.

## Ako se font mijenja

Svaka zamjena mora proći test pokrivenosti glifova u
`tests/test_parent_report.py` — traži se NENULTI identifikator glifa za svako
slovo iz `report_pdf.REQUIRED_GLYPHS`, a ne puko postojanje ključa u
`charToGlyph`.
