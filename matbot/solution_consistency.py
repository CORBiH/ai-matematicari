"""Da li OZNAČEN odgovor slijedi iz vlastitog rješenja zadatka.

ZAŠTO POSTOJI (produkcijski nalaz, ručni QA, lekcija o brojevnim izrazima s
decimalnim brojevima):

    zadatak:    kupovina 4,75 + 3,25 + 2,50, plaćeno 20,00
    rješenje:   „ukupno 10,50 KM … kusur 9,50 KM“   ← tačno
    označeno:   11,50 KM                            ← netačno
    server je dopisao: „Konačan rezultat je 11,50 KM“

Učenik je izabrao matematički tačnih 9,50 i dobio ocjenu netačno. Rečenica koju
server dopisuje NIJE uzrok — ona je samo učinila protivrječnost vidljivom.
Uzrok je što je paket objavljen iako se označen odgovor NE POJAVLJUJE nigdje u
vlastitom rješenju zadatka.

ŠTA OVAJ MODUL JESTE: uzak, siguran čuvar dosljednosti. Skupi sve vrijednosti
koje rješenje STVARNO navodi (i izračunate lance iz `$...$`, i decimalne
brojeve iz proze), pa provjeri da je među njima i označena vrijednost.

ŠTA OVAJ MODUL NIJE: rješavač zadataka. Ne izvodi odgovor iz teksta zadatka i
ne tvrdi da je rješenje tačno — tvrdi samo da paket sam sebi ne protivrječi.

NESIGURNOST NIKAD NE ZNAČI PREKRŠAJ: kad se označen odgovor ne može pročitati
kao broj, ili rješenje ne navodi nijednu vrijednost, provjera se PRESKAČE.
Decimalno poređenje ide preko `Fraction`, nikad preko binarnog float-a.
"""
import re
from fractions import Fraction

from matbot import mathcheck

# Broj u prozi ili unutar $...$: 9,50 · 9.50 · 1 200 · -3
_NUMBER_RE = re.compile(r"-?\d{1,3}(?:[  ]\d{3})*(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?")
# Zapis koji nije puka vrijednost (razlomak, korijen, stepen, promjenljiva…) —
# tada se označen odgovor ne pokušava čitati kao decimalni broj.
_NON_PLAIN_RE = re.compile(r"\\(?:frac|sqrt|pi|cdot|times|div|pm|in|le|ge|neq)|[a-zA-Z]\s*=|\^")


def _as_number(raw):
    text = (raw or "").strip().replace("\u00a0", " ").replace(" ", "")
    if not text:
        return None
    try:
        return Fraction(text.replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None


def marked_value(marked_text):
    """Vrijednost označene opcije, ili None kad se ne može sigurno pročitati.

    Traži se TAČNO JEDAN broj uz dozvoljenu jedinicu (KM, cm, %), bez razlomaka,
    korijena i promjenljivih — sve ostalo je izvan onoga što ovaj čuvar smije
    tvrditi."""
    bare = (marked_text or "").replace("$", " ").strip()
    if not bare or _NON_PLAIN_RE.search(bare):
        return None
    numbers = _NUMBER_RE.findall(bare)
    if len(numbers) != 1:
        return None
    return _as_number(numbers[0])


def solution_values(solution_text):
    """Sve vrijednosti koje rješenje NAVODI ili IZRAČUNA.

    Dva izvora, oba postojeća: `mathcheck` za izračunate lance u `$...$` i
    doslovni brojevi iz cijelog teksta (rješenje često zaključak piše prozom,
    npr. „kusur je 9,50 KM“)."""
    text = solution_text or ""
    values = set()
    for raw in _NUMBER_RE.findall(text):
        number = _as_number(raw)
        if number is not None:
            values.add(number)
    for segment in mathcheck.math_segments(text):
        for part in re.split(r"=|\\approx", segment):
            try:
                candidates = mathcheck.evaluate_candidates(part)
            except Exception:                                    # noqa: BLE001
                # Nedokaziv izraz (promjenljiva, nepodržana konstrukcija) se
                # PRESKAČE — odsustvo dokaza nikad nije dokaz prekršaja.
                continue
            for candidate in candidates:
                try:
                    values.add(Fraction(str(candidate)).limit_denominator(10 ** 6))
                except (ValueError, ZeroDivisionError, TypeError):
                    continue
    return values


def divergence(marked_text, solution_text):
    """('' | kod, detalj). Prazan kod = nema DOKAZANE protivrječnosti."""
    marked = marked_value(marked_text)
    if marked is None:
        return "", ""
    values = solution_values(solution_text)
    if not values:
        return "", ""            # rješenje ne navodi nijednu vrijednost → nedokazivo
    if any(abs(value - marked) <= Fraction(1, 10 ** 6) for value in values):
        return "", ""
    shown = ", ".join(
        str(float(value)) for value in sorted(values, key=float)[:6])
    return ("solution_answer_divergence",
            f"označena opcija nosi {float(marked)}, a rješenje nigdje ne dobija tu "
            f"vrijednost (u rješenju: {shown})")
