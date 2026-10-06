"""Sigurno prepoznavanje kursa u Thinkific ``Student Progress`` CSV fajlu.

Stvarni pojedinačni izvoz nema kolonu s nazivom ili ID-jem kursa. Ime fajla
također nije autoritet: pri novom izvozu mijenjaju se oba heksadecimalna dijela
imena. Jedina kursna metainformacija koja zaista postoji U CSV-u jeste potpuna,
uređena lista kolona sekcija kursa.

Zato se automatski prepoznaje samo POTPUNO podudaranje s jednom od četiri
izmjerene trenutne strukture. Jedna sekcija, ime učenika, broj u imenu, redoslijed
uploada ili ime fajla nikad nisu dokaz razreda. Ako Thinkific kurs dobije novu
sekciju ili se postojeća preimenuje, rezultat je ``None`` i administrator mora
izričito odabrati razred samo za taj fajl.
"""
import csv
import io

from matbot import thinkific_progress as progress


# Strukture su izmjerene nad četiri važeća izvoza 2026-10-06. Kolona koja
# ugrađuje MAT-BOT nije dio nastavnog potpisa: njen naziv se već mijenjao, a
# njen procenat nije ni autoritet za MAT-BOT aktivnost.
COURSE_SECTION_HEADERS = {
    "grade_6": (
        "SKUPOVI",
        "KRUŽNICA, KRUG, UGAO",
        "N i No SKUPOVI",
        "DJELJIVOST BROJEVA",
        "RAZLOMCI",
        "RAZLOMCI U DECIMALNOM OBLIKU",
        "DECIMALNI BROJEVI - OPERACIJE",
    ),
    "grade_7": (
        "CIJELI BROJEVI",
        "RACIONALNI BROJEVI",
        "VEKTORI",
        "IZOMETRIJSKA PRESLIKAVANJA",
        "OPERACIJE (RAČUNANJE) SA UGLOVIMA (STEPENI, MINUTE, SEKUNDE)",
        "TROUGAO (TRO" "K" "UT)",
        "ČETVEROUGAO",
        "OSNOVNE GEOMETRIJSKE KONSTRUKCIJE I POSTUPCI",
    ),
    "grade_8": (
        "STEPENI",
        "VEKTORI",
        "REALNI BROJEVI",
        "PITAGORINA TEOREMA",
        "CIJELI RACIONALNI IZRAZI (POLINOMI)",
        "ALGEBARSKI RAZLOMCI",
        "KRUŽNICA, KRUG",
        "TALESOVA TEOREMA I SLIČNOST TROUGLOVA",
        "RAZMJERE i PROPORCIJE",
        "PRAVOUGLI KOORDINATNI SISTEM i GRAFIK LINEARNE FUNKCIJE",
        "MNOGOUGAO",
        "GEOMETRIJSKA TIJELA",
    ),
    "grade_9": (
        "CIJELI RACIONALNI IZRAZI (POLINOMI)",
        "RAZLOMLJENI RACIONALNI IZRAZI (ALGEBARSKI RAZLOMCI)",
        "TAČKA, PRAVA I RAVAN",
        "SLIČNOST I TALESOVA TEOREMA",
        "LINEARNA FUNKCIJA",
        "LINEARNE JEDNAČINE SA JEDNOM NEPOZNATOM",
        "LINEARNE NEJEDNAČINE SA JEDNOM NEPOZNATOM",
        "SISTEM LINEARNIH JEDNAČINA SA 2 NEPOZNATE",
        "GEOMETRIJSKA TIJELA",
    ),
}


def _normalized(value):
    return " ".join(str(value or "").split()).casefold()


_FIXED = frozenset(_normalized(name) for name in progress.FIXED_COLUMNS)


def _is_matbot_embed(name):
    compact = _normalized(name).replace("-", " ")
    return "mat bot" in compact


def _signature(header):
    return tuple(
        _normalized(name) for name in header
        if _normalized(name) not in _FIXED and not _is_matbot_embed(name)
    )


_KNOWN_SIGNATURES = {
    key: tuple(_normalized(name) for name in names)
    for key, names in COURSE_SECTION_HEADERS.items()
}


def detect_course_key(raw_bytes, report_month):
    """Vrati sigurno prepoznat ``grade_N`` ili ``None`` za nepoznat potpis.

    Cijeli fajl se uvijek provjeri postojećim strogim parserom. Privremeni slot
    kod nepoznatog potpisa služi isključivo validaciji formata; ne vraća se,
    ne upisuje se i ne utiče na odluku o kursu.
    """
    month = progress.parse_report_month(report_month)
    text = progress.decode(raw_bytes)
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [(name or "").strip() for name in next(reader)]
    except StopIteration:
        raise progress.ProgressFormatError("source_empty") from None
    if header and header[0].startswith("\ufeff"):
        header[0] = header[0].lstrip("\ufeff")

    signature = _signature(header)
    matches = [key for key, known in _KNOWN_SIGNATURES.items()
               if signature == known]
    detected = matches[0] if len(matches) == 1 else None

    progress.parse_progress_csv(
        raw_bytes, detected or "grade_6", month)
    return detected
