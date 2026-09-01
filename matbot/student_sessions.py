"""Faza 3D — evidencija časova: validacija, mjesečni sažetak, signali navika.

OVO JE PRVI IZVOR IZVJEŠTAJA KOJI NE DOLAZI S PLATFORME. Thinkific mjeri
pokrivenost sadržaja, MAT-BOT samostalan rad; čas mjeri ono što instruktor
stvarno vidi. Zato je u izvještaju roditelju PRVI, i zato učenik koji nema
nijedan nalog, a ima časove, ipak zaslužuje izvještaj.

TRI SEMANTIČKE GRANICE KOJE SE NE SMIJU POMIJEŠATI:

  1. `activity_rating` (1–5) je ANGAŽMAN NA ČASU, ne ocjena iz matematike.
     Učenik koji uredno prati čas a griješi u zadacima ima visok angažman i
     slab učinak — to su dvije različite tvrdnje o dvije različite stvari.
  2. `attendance` je činjenica, ne moralni sud. Odsutan učenik NEMA angažman:
     `activity_rating` mora biti NULL, jer bi lažna jedinica mjesecima obarala
     prosjek i čitala se kao nezainteresovanost umjesto kao izostanak.
  3. `homework_status` ima TRI stanja. „Nije zadana" NIJE neuspjeh i ne smije
     ući u imenilac — inače bi mjesec u kojem zadaća nije ni zadavana izgledao
     kao mjesec u kojem nije rađena.

SVA ARITMETIKA JE OVDJE, PRIJE MODELA. Model dobija gotove brojeve i signale i
nema šta da sabere ni da procijeni (isto pravilo kao Faza 3C).
"""
import re

# --- Zatvoreni skupovi vrijednosti -----------------------------------------
ATTENDANCE_PRESENT = "present"
ATTENDANCE_ABSENT = "absent"
ATTENDANCE_VALUES = (ATTENDANCE_PRESENT, ATTENDANCE_ABSENT)

HOMEWORK_DONE = "done"
HOMEWORK_NOT_DONE = "not_done"
HOMEWORK_NOT_ASSIGNED = "not_assigned"
HOMEWORK_VALUES = (HOMEWORK_DONE, HOMEWORK_NOT_DONE, HOMEWORK_NOT_ASSIGNED)

ACTIVITY_MIN = 1
ACTIVITY_MAX = 5

# Značenja se drže KAO PODATAK da bi admin UI i testovi imali jedan izvor.
# Nigdje se ne zove „ocjena" — vidi granicu 1 gore.
ACTIVITY_LABELS = {
    1: "nije učestvovao / nije pratio rad",
    2: "slabo aktivan, potrebna stalna pomoć",
    3: "učestvuje uz usmjeravanje",
    4: "aktivan i uglavnom samostalan",
    5: "vrlo aktivan i samostalan",
}

HOMEWORK_LABELS = {
    HOMEWORK_DONE: "Urađena",
    HOMEWORK_NOT_DONE: "Nije urađena",
    HOMEWORK_NOT_ASSIGNED: "Nije zadana",
}

ATTENDANCE_LABELS = {
    ATTENDANCE_PRESENT: "Prisutan",
    ATTENDANCE_ABSENT: "Odsutan",
}

MAX_COMMENT_CHARS = 1000
MAX_LABEL_CHARS = 120

# --- IZVOR TEME ČASA -------------------------------------------------------
# Nije svaki čas lekcija iz plana. „Uvodni čas", „Ponavljanje", „Priprema za
# kontrolni" i „Konsultacije" su stvarni časovi kojih u `topics.json` nema i ne
# smiju se izmišljati kao kurikularna lekcija.
#
# RAZLIKA JE EKSPLICITNA, NE POGOĐENA. Alternativa bi bila da se kasnije traži
# naziv po kurikulumu i zaključuje šta je bilo — ali kurikulum se mijenja, pa bi
# isti red danas bio „lekcija" a sutra „ručna tema". Server zna šta je unijeto i
# to zapisuje.
TOPIC_CURRICULUM = "curriculum"
TOPIC_CUSTOM = "custom"
TOPIC_SOURCES = (TOPIC_CURRICULUM, TOPIC_CUSTOM)

# `topic_source IS NULL` na ZATEČENOM redu čita se kao kurikularni čas, i to
# nije nagađanje: do verzije 5 je `validate_session` svaki čas s gradivom
# provjeravao prema `topics.json`, pa drugačiji red nije mogao ni nastati.
LEGACY_TOPIC_SOURCE = TOPIC_CURRICULUM

# Vrijeme časa: LOKALNI ZIDNI SAT, bez vremenske zone. Čas u 14:00 je čas u
# 14:00 u učionici; pretvaranje po zoni preglednika bi isti čas prikazivalo
# različito i razbilo identitet grupe.
_TIME_RE = re.compile(r"\A([01]\d|2[0-3]):([0-5]\d)\Z")

# Koliko časova mora postojati prije nego što se smije tvrditi OBRAZAC. Tri je
# najmanji broj kod kojeg „uvijek/nikad" prestaje biti slučajnost; ispod toga se
# ne tvrdi ništa (ista doktrina kao dokazni pragovi Faze 3C).
MIN_SESSIONS_FOR_PATTERN = 3

# Pragovi signala. Namjerno ASIMETRIČNI i s prazninom između: između 60 % i 80 %
# se ne tvrdi ni jedno ni drugo. Bez te praznine bi 79 % postalo „traži pažnju",
# a 80 % „dosljedno" — razlika od jednog časa ne smije prevrnuti poruku
# roditelju.
ATTENDANCE_GOOD_MIN = 0.80
ATTENDANCE_LOW_MAX = 0.60
ACTIVITY_GOOD_MIN = 4.0
ACTIVITY_LOW_MAX = 3.0
HOMEWORK_GOOD_MIN = 0.80
HOMEWORK_LOW_MAX = 0.60

SIGNAL_CONSISTENT_ATTENDANCE = "consistent_attendance"
SIGNAL_ATTENDANCE_NEEDS_ATTENTION = "attendance_needs_attention"
SIGNAL_STRONG_ENGAGEMENT = "strong_class_engagement"
SIGNAL_ENGAGEMENT_NEEDS_SUPPORT = "class_engagement_needs_support"
SIGNAL_CONSISTENT_HOMEWORK = "consistent_homework"
SIGNAL_HOMEWORK_NEEDS_ATTENTION = "homework_needs_attention"

# Koliko zapažanja s časova ide roditelju u PDF. Izvještaj je pregled mjeseca,
# ne dnevnik — puna istorija ostaje administratoru.
MAX_PARENT_COMMENTS = 3

_DATE_RE = re.compile(r"\A(\d{4})-(\d{2})-(\d{2})\Z")


class SessionValidationError(ValueError):
    """Neispravan zapis časa. `code` je INTERNI kod za log i test, ne za ekran."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _clean_label(raw):
    """Kratka oznaka iz kurikuluma. Bez HTML-a, bez prijeloma, ograničena."""
    text = " ".join((raw or "").split())
    if "<" in text or ">" in text:
        raise SessionValidationError("session_label_markup")
    return text[:MAX_LABEL_CHARS] or None


def parse_session_date(raw):
    """Kanonski `YYYY-MM-DD`, stvarno postojeći datum."""
    import datetime

    match = _DATE_RE.match((raw or "").strip())
    if not match:
        raise SessionValidationError("session_date_format")
    year, month, day = (int(part) for part in match.groups())
    try:
        datetime.date(year, month, day)
    except ValueError:
        raise SessionValidationError("session_date_invalid") from None
    return "%04d-%02d-%02d" % (year, month, day)


def parse_session_time(raw):
    """Kanonsko `HH:MM` (24h), ili `SessionValidationError`.

    PRIMA SAMO VEĆ KANONSKI OBLIK. Namjerno se NE „popravlja" `9`, `9h`,
    `14.30` ni `2 PM`: prikazni niz nije vrijeme, a tiho tumačenje bi značilo da
    server pogađa šta je instruktor mislio. Preglednikov `<input type="time">`
    ionako šalje `HH:MM`.

    Odbija i `24:00` i `12:60` — oba su „skoro ispravna", pa bi ih popustljiv
    parser primio kao ponoć odnosno sljedeći sat."""
    text = " ".join(str(raw or "").split())
    match = _TIME_RE.match(text)
    if not match:
        raise SessionValidationError("session_time_format")
    return "%s:%s" % (match.group(1), match.group(2))


def clean_topic_source(raw):
    """`curriculum` / `custom`; sve ostalo je `None` (= kurikularno)."""
    value = str(raw or "").strip()
    return value if value in TOPIC_SOURCES else None


def validate_session(*, session_date, attendance, activity_rating,
                     homework_status, area_name=None, lesson_name=None,
                     comment=None, grade=None, session_time=None,
                     topic_source=None, require_time=False):
    """Sirov administratorski unos → zapis spreman za bazu.

    SERVER JE AUTORITET. Formular smije onemogućiti polje angažmana za odsutnog
    učenika, ali odluka se donosi OVDJE — klijent se ne provjerava, nego se
    njegov unos ponovo izvodi.

    `grade` uključuje KANONSKU provjeru gradiva: oblast i lekcija moraju
    postojati u kurikulumu tog razreda (`matbot/topics.py`). Bez toga bi
    mjesečno grupisanje po oblastima raslo iz tipfelera, a izvještaj roditelju
    prikazivao dva imena za istu oblast. Administratorska ruta grade prosljeđuje
    UVIJEK; `None` ostaje samo za odbranu pri čitanju zatečenog zapisa.

    `topic_source = "custom"` ISKLJUČUJE kurikularnu provjeru: ručna tema je
    slobodan tekst po definiciji i ne postoji u `topics.json`. Naziv teme je
    tada OBAVEZAN (bez njega red ne bi imao nikakvu temu), a oblast ostaje
    neobavezna — „Uvodni čas" ne pripada nijednoj oblasti, a izmišljena bi bila
    gora od prazne.

    `require_time` je za SVE NOVE zapise: od verzije 5 čas bez vremena ne smije
    nastati, jer se dvije grupe istog razreda istog dana razlikuju samo po
    njemu. Zatečeni redovi bez vremena se i dalje čitaju i mijenjaju."""
    date = parse_session_date(session_date)

    topic = clean_topic_source(topic_source)
    time_text = None
    if session_time is not None and str(session_time).strip():
        time_text = parse_session_time(session_time)
    elif require_time:
        raise SessionValidationError("session_time_required")

    attendance = (attendance or "").strip()
    if attendance not in ATTENDANCE_VALUES:
        raise SessionValidationError("session_attendance_invalid")

    homework = (homework_status or "").strip()
    if homework not in HOMEWORK_VALUES:
        raise SessionValidationError("session_homework_invalid")

    rating = activity_rating
    if isinstance(rating, str):
        rating = rating.strip() or None
    if rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            raise SessionValidationError("session_activity_invalid") from None
        if not ACTIVITY_MIN <= rating <= ACTIVITY_MAX:
            raise SessionValidationError("session_activity_range")

    if attendance == ATTENDANCE_ABSENT and rating is not None:
        # Granica 2: odsutan učenik nema angažman. Ovo se NE „popravlja" tiho na
        # NULL — tihi ispravak bi sakrio da je formular poslao nemoguć par.
        raise SessionValidationError("session_absent_with_activity")

    text = (comment or "").strip()
    if len(text) > MAX_COMMENT_CHARS:
        raise SessionValidationError("session_comment_too_long")

    area = _clean_label(area_name)
    lesson = _clean_label(lesson_name)

    if topic == TOPIC_CUSTOM:
        # RUČNA TEMA: bez kurikularne provjere, ali naziv je obavezan.
        if not lesson:
            raise SessionValidationError("session_topic_required")
    elif grade is not None and (area or lesson):
        from matbot import topics

        # Oba naziva ili nijedan — polovična evidencija („oblast bez lekcije")
        # bi kasnije značila red koji se ne može grupisati.
        if not (area and lesson):
            raise SessionValidationError("session_curriculum_incomplete")
        if not topics.curriculum_pair_valid(grade, area, lesson):
            raise SessionValidationError("session_curriculum_unknown")

    return {
        "session_date": date,
        "session_time": time_text,
        "attendance": attendance,
        "activity_rating": rating,
        "homework_status": homework,
        "area_name": area,
        "lesson_name": lesson,
        "topic_source": topic,
        "comment": text or None,
    }


# ---------------------------------------------------------------------------
# MJESEČNI SAŽETAK — sve determinističko, nijedan broj ne dolazi od modela
# ---------------------------------------------------------------------------
def _ratio(numerator, denominator):
    return (float(numerator) / denominator) if denominator else None


def work_habit_signals(summary):
    """Konzervativni signali radnih navika. Prazna lista je LEGITIMAN ishod.

    NISU OCJENE. Postoje da model ne bi sam odlučivao da je „prisustvo odlično"
    na osnovu dva časa: prag i smjer su serverska odluka, a model samo opisuje
    ono što mu je već zaključeno."""
    signals = []

    total = summary["sessions_total"]
    if total >= MIN_SESSIONS_FOR_PATTERN:
        share = _ratio(summary["present_count"], total)
        if share is not None and share >= ATTENDANCE_GOOD_MIN:
            signals.append(SIGNAL_CONSISTENT_ATTENDANCE)
        elif share is not None and share <= ATTENDANCE_LOW_MAX:
            signals.append(SIGNAL_ATTENDANCE_NEEDS_ATTENTION)

    activity = summary["activity"]
    if activity["rated_sessions"] >= MIN_SESSIONS_FOR_PATTERN:
        average = activity["average"]
        if average is not None and average >= ACTIVITY_GOOD_MIN:
            signals.append(SIGNAL_STRONG_ENGAGEMENT)
        elif average is not None and average < ACTIVITY_LOW_MAX:
            signals.append(SIGNAL_ENGAGEMENT_NEEDS_SUPPORT)

    homework = summary["homework"]
    assigned = homework["assigned_count"]
    if assigned >= MIN_SESSIONS_FOR_PATTERN:
        share = _ratio(homework["done_count"], assigned)
        if share is not None and share >= HOMEWORK_GOOD_MIN:
            signals.append(SIGNAL_CONSISTENT_HOMEWORK)
        elif share is not None and share < HOMEWORK_LOW_MAX:
            signals.append(SIGNAL_HOMEWORK_NEEDS_ATTENTION)

    return signals


def build_monthly_summary(rows):
    """Redovi jednog mjeseca → determinističan sažetak časova.

    `rows` su rječnici iz baze, sortirani po datumu rastuće. Sve što izlazi
    odavde je već izračunato; model ovo samo opisuje."""
    rows = list(rows or [])
    present = [r for r in rows if r["attendance"] == ATTENDANCE_PRESENT]
    absent = [r for r in rows if r["attendance"] == ATTENDANCE_ABSENT]

    # PROSJEK SAMO PREKO OCIJENJENIH PRISUTNIH ČASOVA. Bez ocjene nema mjerenja,
    # a 0/5 bi bila izmišljena mjera o učeniku koji nije bio ocijenjen.
    rated = [r["activity_rating"] for r in present
             if r["activity_rating"] is not None]
    average = round(sum(rated) / float(len(rated)), 1) if rated else None

    done = [r for r in rows if r["homework_status"] == HOMEWORK_DONE]
    not_done = [r for r in rows if r["homework_status"] == HOMEWORK_NOT_DONE]
    # `not_assigned` NAMJERNO nije u imeniocu (granica 3 iz docstringa).
    assigned = len(done) + len(not_done)

    # KURIKULARNO GRADIVO I RUČNE TEME SE NE MIJEŠAJU. „Uvodni čas" jeste
    # stvaran čas i broji se u prisustvu, angažmanu i zadaći — ali NIJE lekcija
    # iz plana i ne smije stajati u istom spisku kao „Sabiranje razlomaka".
    # Inače bi izvještaj roditelju mogao „Uvodni čas" opisati kao gradivo koje
    # treba uvježbati.
    areas, lessons, custom_topics = [], [], []
    for row in rows:
        custom = (row.get("topic_source") or LEGACY_TOPIC_SOURCE) == TOPIC_CUSTOM
        area = (row.get("area_name") or "").strip()
        lesson = (row.get("lesson_name") or "").strip()
        if custom:
            if lesson and lesson not in custom_topics:
                custom_topics.append(lesson)
            continue
        if area and area not in areas:
            areas.append(area)
        if lesson and lesson not in lessons:
            lessons.append(lesson)

    # Zapažanja: najsvježija prva, a pri istom datumu veći `id` prvi — poredak
    # mora biti DETERMINISTIČAN, inače bi dva časa istog dana mijenjala PDF.
    commented = [r for r in rows if (r.get("comment") or "").strip()]
    commented.sort(key=lambda r: (r["session_date"], r.get("id") or 0), reverse=True)
    parent_comments = [{"date": r["session_date"],
                        "comment": (r.get("comment") or "").strip()}
                       for r in commented[:MAX_PARENT_COMMENTS]]

    summary = {
        "available": bool(rows),
        "sessions_total": len(rows),
        "present_count": len(present),
        "absent_count": len(absent),
        "activity": {"rated_sessions": len(rated), "average": average},
        "homework": {"assigned_count": assigned,
                     "done_count": len(done),
                     "not_done_count": len(not_done),
                     "not_assigned_count": len(rows) - assigned},
        "areas_worked": areas,
        "lessons_worked": lessons,
        # Ručne teme ostaju administratoru i PDF-u; u ugovor prema modelu NE
        # ulaze (vidi `report_facts._instruction_facts`).
        "custom_topics": custom_topics,
        # Slobodan tekst instruktora. OSTAJE OVDJE I U PDF-u, a u ugovor prema
        # modelu NE ULAZI (Dio 20) — vidi `report_facts.build_ai_facts`.
        "parent_comments": parent_comments,
    }
    summary["signals"] = work_habit_signals(summary)
    return summary
