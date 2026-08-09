"""Zajednički deterministički tokenizator za MathJax tekst: dijeli string na
uređene segmente teksta i matematike, podržavajući I `$...$` (inline) I
`$$...$$` (display) — bez naivnog alternating-split-a koji parira PRVI sa
DRUGIM `$`, TREĆI sa ČETVRTIM itd. (taj pristup je slijep za `$$`: dva
susjedna `$` znaka razbije na dva odvojena para umjesto da ih prepozna kao
JEDAN display par — vidi živi nalaz u mathsafe.py/mathcheck.py docstringovima).

ALGORITAM (single left-to-right scan, bez regex alternacije):
  - Van matematike: prvi `$` koji NIJE escapeovan (`\\$`) odlučuje tip:
    ako je ODMAH praćen JOŠ JEDNIM `$`, otvara DISPLAY (`$$`); inače
    otvara INLINE (jedan `$`).
  - Unutar INLINE: zatvara ga SLJEDEĆI bilo koji neescapeovan `$` (tačno
    jedan znak) — bez obzira šta ga prati. Ovo je namjerno: kad zatvorimo
    inline na prvom `$` koji naiđe, eventualni ODMAH sljedeći `$` postaje
    novi, zaseban token (spriječava da "$$ " unutar zatvorenog inline-a
    slučajno bude pojedeno kao da je nešto drugo).
  - Unutar DISPLAY: zatvara ga SAMO sljedeći `$$` (dva uzastopna). Usamljen
    pojedinačan `$` UNUTAR display bloka je obični SADRŽAJ, nikad delimiter
    — ovo je TAČNO ono što stari kod nije razlikovao (vidi C-3 u
    docs/CURRENT_STATE.md: `$$P=\\frac{a\\cdot h}{2}$$` se lomilo jer je
    sadržaj unutar `$$...$$` tretiran kao DA JE VAN matematike).
  - Nezatvoren delimiter (nema para do kraja stringa): OTVARAJUĆI delimiter
    se OTPISUJE (ne pojavljuje se u izlazu), a ostatak stringa od te tačke
    nadalje ide kao OBIČAN TEKST bez daljeg parsiranja — isto ponašanje kao
    stari `sanitize_math_text` (uporedi test_odd_number_of_dollars_handled_safely:
    jedan nezatvoren `$` potpuno nestaje iz izlaza, sadržaj ostaje čitljiv).

Vraćeni segmenti: lista (kind, content) parova, kind je "text", "inline" ili
"display". `content` NIKAD ne sadrži delimitere. `join_segments` radi obrnuto:
sastavlja segmente nazad u string, dodajući odgovarajuće delimitere.
"""

TEXT = "text"
INLINE = "inline"
DISPLAY = "display"


def _is_escaped(text, pos):
    """True ako je '$' na poziciji pos neposredno prethodjen jednim '\\'."""
    return pos > 0 and text[pos - 1] == "\\"


def tokenize_math_spans(text):
    """Isti prolaz kao `tokenize_math`, ali vraća i RASPON sadržaja u ULAZU:
    listu (kind, content, start, end) gdje je `text[start:end] == content`.

    ZAŠTO POSTOJI: čitač „riješi“ relacija (matbot/mcq_integrity.py) mora znati
    REDOSLIJED i POZICIJU svake pročitane relacije da bi je mogao pridružiti
    lokalnom tekstualnom kontekstu (npr. „…pa dobijamo $x+2>7$“). Bez raspona
    bi se to moralo raditi drugim, paralelnim parsiranjem delimitera — a ovaj
    modul postoji upravo zato da parsiranje `$…$`/`$$…$$` postoji na TAČNO
    JEDNOM mjestu (vidi živi nalaz C-3 u docs/CURRENT_STATE.md).

    `tokenize_math` je od sada tanak omotač oko ove funkcije, pa se dvije
    implementacije ne mogu razići."""
    if not text:
        return [(TEXT, text or "", 0, 0)]

    segments = []
    buf_start = 0
    buf = []
    i = 0
    n = len(text)

    def flush_text():
        if buf:
            segments.append((TEXT, "".join(buf), buf_start, i))
            buf.clear()

    while i < n:
        ch = text[i]
        if ch == "$" and not _is_escaped(text, i):
            is_display_open = (i + 1 < n and text[i + 1] == "$")
            if is_display_open:
                flush_text()
                j = text.find("$$", i + 2)
                if j == -1:
                    # nezatvoren display: otvarajući "$$" se otpisuje,
                    # ostatak ide kao obični tekst bez daljeg parsiranja.
                    segments.append((TEXT, text[i + 2:], i + 2, n))
                    i = n
                    break
                segments.append((DISPLAY, text[i + 2:j], i + 2, j))
                i = j + 2
                buf_start = i
                continue
            else:
                flush_text()
                close = -1
                j = i + 1
                while j < n:
                    if text[j] == "$" and not _is_escaped(text, j):
                        close = j
                        break
                    j += 1
                if close == -1:
                    # nezatvoren inline: otvarajući "$" se otpisuje.
                    segments.append((TEXT, text[i + 1:], i + 1, n))
                    i = n
                    break
                segments.append((INLINE, text[i + 1:close], i + 1, close))
                i = close + 1
                buf_start = i
                continue
        if not buf:
            buf_start = i
        buf.append(ch)
        i += 1

    flush_text()
    return segments


def tokenize_math(text):
    """Vrati listu (kind, content) segmenata za dati tekst. Nikad ne baca
    izuzetak; prazan/None ulaz vraća [("text", "")]."""
    return [(kind, content)
            for kind, content, _start, _end in tokenize_math_spans(text)]


def join_segments(segments):
    """Obrnuto od tokenize_math: sastavi segmente nazad u string, dodajući
    odgovarajuće delimitere oko inline/display sadržaja."""
    out = []
    for kind, content in segments:
        if kind == INLINE:
            out.append("$" + content + "$")
        elif kind == DISPLAY:
            out.append("$$" + content + "$$")
        else:
            out.append(content)
    return "".join(out)


def math_contents(segments):
    """Vrati listu sadržaja SVIH matematičkih segmenata (inline I display),
    redoslijedom pojavljivanja — pogodno za module koji samo trebaju da
    PROČITAJU matematiku (mathcheck), bez potrebe da sami razlikuju tip."""
    return [content for kind, content in segments if kind in (INLINE, DISPLAY)]


def map_math_segments(text, transform):
    """Primijeni transform(content) na SVAKI matematički segment (inline i
    display), ostavi tekstualne segmente netaknute, i vrati ponovo sastavljen
    string. Pogodno za jednostavne popravke koje ne mijenjaju tip segmenta."""
    segments = tokenize_math(text)
    new_segments = [
        (kind, transform(content) if kind in (INLINE, DISPLAY) else content)
        for kind, content in segments
    ]
    return join_segments(new_segments)


def map_text_segments(text, transform):
    """Primijeni transform(content) na SVAKI tekstualni (ne-matematički)
    segment, ostavi matematiku netaknutu bajt-za-bajt, i vrati ponovo
    sastavljen string. Koristi terminology.py — zamjena termina se NIKAD ne
    smije dogoditi unutar $...$ ili $$...$$."""
    segments = tokenize_math(text)
    new_segments = [
        (kind, transform(content) if kind == TEXT else content)
        for kind, content in segments
    ]
    return join_segments(new_segments)


def ensure_even_dollar_count(text):
    """Sigurnosna mreža: ako iz nekog razloga (patološki/fuzz ulaz) izlaz i
    dalje ima NEPARAN broj neescapeovanih '$' znakova, ukloni POSLJEDNJI takav
    znak. Za sav realan/dobro-oblikovan ulaz ovo se nikad ne aktivira — vidi
    testove za tokenize_math koji dokazuju parnost za normalne slučajeve. Ovo
    je zadnja linija odbrane protiv ekstremnih fuzz ulaza (npr. "$"*51)."""
    count = sum(1 for idx, ch in enumerate(text) if ch == "$" and not _is_escaped(text, idx))
    if count % 2 == 0:
        return text
    for idx in range(len(text) - 1, -1, -1):
        if text[idx] == "$" and not _is_escaped(text, idx):
            return text[:idx] + text[idx + 1:]
    return text
