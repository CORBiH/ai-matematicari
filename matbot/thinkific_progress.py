"""Thinkific „Student Progress" CSV → normalizovan mjesečni SNIMAK STANJA.

ŠTA OVAJ MODUL JESTE: čisto, determinističko čitanje jednog izvoza. Bez baze,
bez SQL-a, bez mreže i bez ijednog modela. Ulaz su BAJTOVI fajla, izlaz je
provjeren `ParsedProgressFile`.

ŠTA SNIMAK JESTE, A ŠTA NIJE (najvažnija granica ove faze):
izvoz je STANJE NA DAN IZVOZA, a ne dnevnik. On NE dokazuje koja je lekcija
završena kojeg dana ni šta je učenik radio tokom mjeseca. Zato se iz njega NIKAD
ne prave događaji učenja — pamti se jedno stanje po (učenik, kurs, mjesec), a
napredak se dobija POREĐENJEM dva mjeseca. Događaji učenja imaju svoj izvor:
`matbot/activity.py` (Faza 2), i to je jedini autoritet za MAT-BOT ponašanje.

IZMJERENO NAD STVARNIM IZVOZOM (6. razred, 34 reda) — implementacija slijedi
mjerenje, ne opis:

  • kodiranje      UTF-8 BEZ BOM-a; sadrži `č`, `ž` (dakle nikad cp1252)
  • prelomi        goli LF
  • razdvajač      zarez, ALI naziv sekcije `"KRUŽNICA, KRUG, UGAO"` sam sadrži
                   zarez i navodnike — zaglavlje ima 20 zareza a 19 kolona.
                   Zato je pravi CSV parser OBAVEZAN; `split(",")` bi razbio red.
  • procenti       GOLI CIJELI BROJEVI (`31`), BEZ znaka `%`, opseg 0–100
  • vremena        `YYYY-MM-DD HH:MM:SS UTC`
  • prazno         prazan string
  • `Company`      33/34 prazno — administrativno polje, NE sekcija
  • `Matematički bot-MAT BOT` — kolona POSTOJI u izvozu i MORA biti isključena
                   (vidi `EXCLUDED_COLUMNS`)

PII: e-mail i ime prolaze KROZ ovaj modul, ali se ovdje ništa ne loguje i ništa
ne pamti. Sirovi CSV se nikad ne perzistira — u bazu ide samo SHA-256 bajtova,
radi revizije.
"""
import csv
import hashlib
import io
import re

# --- KURSNI SLOTOVI --------------------------------------------------------
# Razred i naziv kursa dolaze ISKLJUČIVO odavde, jer izvoz „Student Progress"
# nema pouzdano polje naziva kursa. Administrator bira slot; ništa se ne izvodi
# iz imena učenika, e-maila ni vrijednosti napretka.
COURSE_SLOTS = {
    "grade_6": {"grade": 6, "course_name": "Matematika za 6. razred"},
    "grade_7": {"grade": 7, "course_name": "Matematika za 7. razred"},
    "grade_8": {"grade": 8, "course_name": "Matematika za 8. razred"},
    "grade_9": {"grade": 9, "course_name": "Matematika za 9. razred"},
}

# --- FIKSNA ZAGLAVLJA ------------------------------------------------------
FIRST_NAME = "First Name"
LAST_NAME = "Last Name"
EMAIL = "Email"
COMPANY = "Company"
COMPLETED_AT = "Completed At"
STARTED_AT = "Started At"
ACTIVATED_AT = "Activated At"
EXPIRES_AT = "Expires At"
LAST_SIGN_IN = "Last Sign In"
PERCENT_VIEWED = "% Viewed"
PERCENT_COMPLETED = "% Completed"

FIXED_COLUMNS = (FIRST_NAME, LAST_NAME, EMAIL, COMPANY, COMPLETED_AT, STARTED_AT,
                 ACTIVATED_AT, EXPIRES_AT, LAST_SIGN_IN, PERCENT_VIEWED,
                 PERCENT_COMPLETED)

TIMESTAMP_COLUMNS = (COMPLETED_AT, STARTED_AT, ACTIVATED_AT, EXPIRES_AT, LAST_SIGN_IN)

# Bez ovih izvoz nije „Student Progress" i ne smijemo nagađati šta jeste.
REQUIRED_COLUMNS = (EMAIL, PERCENT_VIEWED, PERCENT_COMPLETED)

# --- KOLONE KOJE NISU KURIKULARNE SEKCIJE ----------------------------------
# `Company` je administrativno polje.
#
# `Matematički bot-MAT BOT` je poseban i NAJVAŽNIJI slučaj: to je Thinkific
# „lekcija" koja samo ugrađuje MAT-BOT iframe, pa njen procenat mjeri da li je
# učenik OTVORIO stranicu — nikad šta je s MAT-BOT-om uradio. Kad bi ušla u
# `thinkific_progress_sections`, izvještaj bi imao DVA nesaglasna izvora o
# MAT-BOT-u, a slabiji (posjeta stranici) bi izgledao kao upotreba. Autoritet za
# MAT-BOT je isključivo Faza 2 (`learning_activity`, `assessment_*`).
MATBOT_EMBED_COLUMN = "Matematički bot-MAT BOT"
EXCLUDED_COLUMNS = frozenset({COMPANY, MATBOT_EMBED_COLUMN})

# Ime kolone se poredi NORMALIZOVANO (bez viška razmaka, bez razlike u veličini
# slova), da izvoz s drugačijim razmakom ne provuče MAT-BOT kolonu kao sekciju.
_EXCLUDED_NORMALIZED = frozenset(name.strip().casefold() for name in EXCLUDED_COLUMNS)

MAX_SECTION_NAME_CHARS = 200
MAX_DISPLAY_NAME_CHARS = 120

_REPORT_MONTH_RE = re.compile(r"\A(\d{4})-(\d{2})\Z")
# Izmjereno: goli cio broj. Sufiks `%` se prihvata DEFANZIVNO (drugi izvoz ga
# može imati) — to ne može ništa pogrešno pročitati, a sve ostalo pada.
_PERCENT_RE = re.compile(r"\A(\d+(?:[.,]\d+)?)\s*%?\Z")
_TIMESTAMP_RE = re.compile(
    r"\A(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\s*(UTC|Z))?\Z")


class ProgressFormatError(ValueError):
    """Fajl se ne može bezbjedno pročitati. `code` je interni dijagnostički kod
    (nikad PII), `row` je 1-bazirani red izvora kad je poznat."""

    def __init__(self, code, row=None, column=""):
        super().__init__(code)
        self.code = code
        self.row = row
        self.column = column

    def describe(self):
        """Poruka za administratora — BEZ e-maila i imena, samo koordinate."""
        where = []
        if self.row is not None:
            where.append("red %d" % self.row)
        if self.column:
            where.append("kolona '%s'" % self.column[:60])
        return "%s (%s)" % (self.code, ", ".join(where)) if where else self.code


def parse_report_month(value):
    """`YYYY-MM` → isti string, ili `ProgressFormatError`.

    NIKAD se ne izvodi iz imena fajla, vremena uploada ni iz ijednog datuma u
    izvozu: mjesec izvještaja je administrativna odluka, a ne svojstvo podataka.
    """
    if not isinstance(value, str):
        raise ProgressFormatError("report_month_invalid")
    match = _REPORT_MONTH_RE.match(value.strip())
    if not match:
        raise ProgressFormatError("report_month_invalid")
    year, month = int(match.group(1)), int(match.group(2))
    if not (2000 <= year <= 2100) or not (1 <= month <= 12):
        raise ProgressFormatError("report_month_out_of_range")
    return "%04d-%02d" % (year, month)


def parse_course_key(value):
    if not isinstance(value, str) or value.strip() not in COURSE_SLOTS:
        raise ProgressFormatError("course_key_invalid")
    return value.strip()


def parse_percent(raw, *, column="", row=None):
    """Procenat → float u [0,100], ili `None` za prazno.

    NAMJERNO NEMA POPRAVLJANJA: prazno je NEPOZNATO (NULL), a neispravna
    vrijednost je GREŠKA. Tiho pretvaranje u 0 bi u izvještaju roditelju
    izgledalo kao izmjeren nulti napredak, što je gore od priznanja da podatak
    nedostaje. Vrijednost van [0,100] se ne siječe nego odbija."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    match = _PERCENT_RE.match(text)
    if not match:
        raise ProgressFormatError("percent_malformed", row=row, column=column)
    value = float(match.group(1).replace(",", "."))
    if value < 0 or value > 100:
        raise ProgressFormatError("percent_out_of_range", row=row, column=column)
    return value


def parse_timestamp(raw, *, column="", row=None):
    """`YYYY-MM-DD HH:MM:SS UTC` → `YYYY-MM-DD HH:MM:SS`, prazno → `None`.

    Thinkific izvozi UTC i to izričito piše. Vrijednost se zato NE pomjera u
    lokalno vrijeme — cijeli izvještajni sloj (Faza 2 uključena) pamti UTC u
    istom obliku kao `CURRENT_TIMESTAMP`, pa poređenje i sortiranje rade nad
    jednim vremenskim prostorom."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    match = _TIMESTAMP_RE.match(text)
    if not match:
        raise ProgressFormatError("timestamp_malformed", row=row, column=column)
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24
            and minute < 60 and second < 60):
        raise ProgressFormatError("timestamp_out_of_range", row=row, column=column)
    return "%04d-%02d-%02d %02d:%02d:%02d" % (year, month, day, hour, minute, second)


def build_display_name(first, last):
    """Ime za prikaz — konzervativno i NIKAD identitet.

    Spaja se samo ono što stvarno postoji, unutrašnji višak razmaka se sažima, a
    prazno daje `None` (pozivalac tada NE dira postojeću vrijednost). Iz e-maila
    se ime NE izvodi."""
    parts = []
    for value in (first, last):
        if isinstance(value, str) and value.strip():
            parts.append(" ".join(value.split()))
    if not parts:
        return None
    return " ".join(parts)[:MAX_DISPLAY_NAME_CHARS]


def section_columns(header):
    """Zaglavlje → [(ordinal, naziv)] za DINAMIČKE kurikularne sekcije.

    Sekcija je sve što nije fiksno polje i nije izričito isključeno. Spisak
    sekcija 6. razreda se NAMJERNO ne ugrađuje: izvoz 7/8/9. razreda ima druge
    nazive, pa bi tvrd spisak značio novu Python izmjenu po razredu.

    `ordinal` je REDOSLIJED KOLONA U IZVOZU, jer je to kurikularni redoslijed
    koji Thinkific već poznaje; abecedno sortiranje bi ga uništilo."""
    columns = []
    ordinal = 0
    seen = set()
    for name in header:
        clean = (name or "").strip()
        if not clean or clean in FIXED_COLUMNS:
            continue
        if clean.casefold() in _EXCLUDED_NORMALIZED:
            continue
        if clean.casefold() in seen:
            continue
        seen.add(clean.casefold())
        ordinal += 1
        columns.append((ordinal, clean[:MAX_SECTION_NAME_CHARS]))
    return columns


class ProgressRow:
    """Jedan validan red izvoza. `email` je PII i postoji SAMO da ga sloj
    identiteta pretvori u `students.id` — dalje se ne prosljeđuje."""

    __slots__ = ("source_row", "email", "display_name", "percent_viewed",
                 "percent_completed", "started_at", "completed_at",
                 "activated_at", "expires_at", "last_sign_in", "sections")

    def __init__(self, **fields):
        for key in self.__slots__:
            setattr(self, key, fields.get(key))


class ParsedProgressFile:
    """Rezultat čitanja jednog izvoza."""

    def __init__(self, course_key, report_month, source_sha256, header,
                 section_names, rows, row_count):
        self.course_key = course_key
        self.report_month = report_month
        self.source_sha256 = source_sha256
        self.header = header
        self.section_names = section_names      # [(ordinal, name)]
        self.rows = rows                        # [ProgressRow]
        self.row_count = row_count              # svi redovi podataka u izvoru

    @property
    def grade(self):
        return COURSE_SLOTS[self.course_key]["grade"]

    @property
    def course_name(self):
        return COURSE_SLOTS[self.course_key]["course_name"]


def source_sha256(raw_bytes):
    """SHA-256 nad ORIGINALNIM bajtovima. Jedini trag fajla koji se pamti."""
    return hashlib.sha256(raw_bytes).hexdigest()


def decode(raw_bytes):
    """UTF-8 (uz toleranciju BOM-a). Drugo kodiranje se ODBIJA umjesto da se
    pogađa: pogrešno dekodiran naziv sekcije tiho pravi drugu sekciju i razbija
    poređenje mjeseci."""
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise ProgressFormatError("source_not_bytes")
    if not raw_bytes.strip():
        raise ProgressFormatError("source_empty")
    try:
        return bytes(raw_bytes).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ProgressFormatError("source_encoding_unsupported") from None


def parse_progress_csv(raw_bytes, course_key, report_month):
    """Bajtovi izvoza → `ParsedProgressFile`. Baca `ProgressFormatError`.

    STROG JE NAMJERNO (Dio 17): prvi neispravan red obara CIJELI fajl. Za v1 je
    to bolje od preskakanja, jer administrator dobije jasno „red 12, kolona X"
    i ponovi izvoz, umjesto da mu izvještaj tiho nedostaje za nekoliko učenika.
    Djelimično uvezen mjesec je najgori ishod: izgleda potpuno, a nije."""
    course_key = parse_course_key(course_key)
    report_month = parse_report_month(report_month)
    digest = source_sha256(raw_bytes)
    text = decode(raw_bytes)

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise ProgressFormatError("source_empty") from None

    header = [(name or "").strip() for name in header]
    if header and header[0].startswith("﻿"):
        header[0] = header[0].lstrip("﻿")
    missing = [name for name in REQUIRED_COLUMNS if name not in header]
    if missing:
        raise ProgressFormatError("required_column_missing", column=",".join(missing))

    index = {name: position for position, name in enumerate(header) if name}
    sections = section_columns(header)

    rows = []
    row_count = 0
    for offset, raw_row in enumerate(reader):
        source_row = offset + 2          # 1-baziran, zaglavlje je red 1
        if not any((cell or "").strip() for cell in raw_row):
            continue                     # potpuno prazan red nije podatak
        row_count += 1
        if len(raw_row) != len(header):
            raise ProgressFormatError("row_width_mismatch", row=source_row)

        def cell(name):
            position = index.get(name)
            return raw_row[position] if position is not None else ""

        email = (cell(EMAIL) or "").strip()
        if not email:
            # IDENTITETSKI KVAR: bez adrese red se ne može vezati ni za kog.
            raise ProgressFormatError("email_missing", row=source_row, column=EMAIL)

        values = {}
        for column in TIMESTAMP_COLUMNS:
            values[column] = parse_timestamp(cell(column), column=column, row=source_row)

        section_values = []
        for ordinal, name in sections:
            section_values.append((ordinal, name,
                                   parse_percent(cell(name), column=name, row=source_row)))

        rows.append(ProgressRow(
            source_row=source_row,
            email=email,
            display_name=build_display_name(cell(FIRST_NAME), cell(LAST_NAME)),
            percent_viewed=parse_percent(cell(PERCENT_VIEWED),
                                         column=PERCENT_VIEWED, row=source_row),
            percent_completed=parse_percent(cell(PERCENT_COMPLETED),
                                            column=PERCENT_COMPLETED, row=source_row),
            started_at=values[STARTED_AT],
            completed_at=values[COMPLETED_AT],
            activated_at=values[ACTIVATED_AT],
            expires_at=values[EXPIRES_AT],
            last_sign_in=values[LAST_SIGN_IN],
            sections=section_values,
        ))

    return ParsedProgressFile(course_key=course_key, report_month=report_month,
                              source_sha256=digest, header=header,
                              section_names=sections, rows=rows, row_count=row_count)
