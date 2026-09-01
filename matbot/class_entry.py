"""Faza 3D+ — BRZI UNOS JEDNOG ČASA ZA VIŠE UČENIKA („Upiši čas").

ZAŠTO POSTOJI: instruktor je do sada morao otvoriti profil svakog učenika da bi
evidentirao jedan te isti čas. Za grupu od osam učenika to je osam stranica i
osam čuvanja. Ovdje se isti čas upisuje jednom.

ŠTA OVO NIJE: nije drugi izvještajni sistem i nije nova tabela. Piše se u
POSTOJEĆI `student_sessions`, kroz POSTOJEĆU validaciju
(`student_sessions.validate_session`), pa red nastao ovim putem mora biti
NERAZLUČIV od reda unesenog kroz profil učenika. Mjesečni sažetak, prosjeci,
imenilac zadaće i zapažanja za roditelje ostaju netaknuti.

TRI STANJA UČEŠĆA, NE DVA. Ovo je proizvodna odluka s razlogom: unutar istog
razreda postoje grupe i različiti termini, pa učenik koji nije bio na OVOM času
nije time odsutan. „Nije na ovom času" zato ne pravi NIJEDAN red — a lažni
izostanci bi mjesecima obarali prisustvo i završili u izvještaju roditelju kao
neredovnost koje nije bilo.

SERVER JE AUTORITET NAD SPISKOM. Klijent šalje `student_id`, ali koji su učenici
uopšte dozvoljeni odlučuje server iz POTVRĐENOG tekućeg razreda. Poslan
osmoškolac u sedmi razred pada zatvoreno, i to bez tihog preskakanja reda.
"""
from matbot import student_sessions

# --- Stanja učešća ---------------------------------------------------------
PARTICIPATION_PRESENT = "present"
PARTICIPATION_ABSENT = "absent"
PARTICIPATION_NOT_SCHEDULED = "not_scheduled"
PARTICIPATION_VALUES = (PARTICIPATION_PRESENT, PARTICIPATION_ABSENT,
                        PARTICIPATION_NOT_SCHEDULED)

# PODRAZUMIJEVANO STANJE. Nikad „prisutan" i nikad „odsutan" — oba bi bila
# tvrdnja koju niko nije unio.
PARTICIPATION_DEFAULT = PARTICIPATION_NOT_SCHEDULED

PARTICIPATION_LABELS = {
    PARTICIPATION_PRESENT: "Prisutan",
    PARTICIPATION_ABSENT: "Odsutan",
    PARTICIPATION_NOT_SCHEDULED: "Nije na ovom času",
}

# ZADAĆA ODSUTNOG UČENIKA — serverska vrijednost, nikad iz formulara.
#
# `student_sessions.homework_status` je NOT NULL (šema v3), pa „bez vrijednosti"
# nije opcija bez migracije. Od tri postojeće vrijednosti jedina ispravna je
# `not_assigned`, i to zbog IMENIOCA: `build_monthly_summary` broji imenilac kao
# `done + not_done`, a `not_assigned` NAMJERNO izostavlja. Odsutan učenik zato
# ne ulazi u postotak urađene zadaće — ni kao uspjeh ni kao neuspjeh.
#
# `not_done` bi bila izmišljena tvrdnja („nije uradio") o učeniku koji nije ni
# bio na času, i mjesecima bi obarala imenilac. Semantika izvještaja se ovim NE
# mijenja: pravilo „not_assigned se ne broji" je zatečeno i ostaje.
ABSENT_HOMEWORK = student_sessions.HOMEWORK_NOT_ASSIGNED

# Podrazumijevana zadaća za PRISUTNOG učenika. Također `not_assigned`: ako
# instruktor ne dodirne polje, ništa se ne tvrdi. Konzervativno po istoj
# doktrini — radije prazna mjera nego izmišljena.
PRESENT_HOMEWORK_DEFAULT = student_sessions.HOMEWORK_NOT_ASSIGNED

VALID_GRADES = (6, 7, 8, 9)

# --- REŽIM TEME ČASA -------------------------------------------------------
# „Iz nastavnog plana" je podrazumijevano; „Ručni unos" postoji jer nije svaki
# stvarni čas lekcija iz plana („Uvodni čas", „Ponavljanje", „Konsultacije").
# REŽIM JE SERVERSKI RAZUMLJIV, ne slijepo preuzeto polje: nepoznata vrijednost
# pada na kurikularni režim, koji je STROŽI — pa podmetnut režim ne može
# zaobići provjeru gradiva, nego je uvijek uključuje.
TOPIC_MODE_LABELS = {
    student_sessions.TOPIC_CURRICULUM: "Iz nastavnog plana",
    student_sessions.TOPIC_CUSTOM: "Ručni unos",
}
TOPIC_MODE_DEFAULT = student_sessions.TOPIC_CURRICULUM


class ClassEntryError(ValueError):
    """Neispravan unos časa. `code` je INTERNI kod za log i test, ne za ekran."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def clean_grade(raw):
    """Razred časa. Zatvoren skup 6–9; sve ostalo je `None`."""
    try:
        grade = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return None
    return grade if grade in VALID_GRADES else None


def clean_topic_mode(raw):
    """Nepoznat režim pada na KURIKULARNI, jer je stroži (vidi konstante)."""
    value = str(raw or "").strip()
    return value if value in student_sessions.TOPIC_SOURCES else TOPIC_MODE_DEFAULT


def clean_participation(raw):
    """Nepoznata vrijednost pada na PODRAZUMIJEVANO, ne na „prisutan".

    Namjerno tolerantno u OVOM smjeru: red koji ne nastane je bezopasan, a
    izmišljen red nije. Zaista neispravan `student_id` i dalje pada zatvoreno u
    `build_class_records`."""
    value = str(raw or "").strip()
    return value if value in PARTICIPATION_VALUES else PARTICIPATION_DEFAULT


def _require(condition, code):
    if not condition:
        raise ClassEntryError(code)


def build_class_records(*, session_date, session_time, grade, area_name,
                        lesson_name, roster_ids, submissions,
                        topic_mode=TOPIC_MODE_DEFAULT):
    """Zajednička polja časa + red po učeniku → zapisi spremni za bazu.

    `roster_ids` je SERVERSKI izveden skup dozvoljenih `student_id` (aktivni
    učenici s POTVRĐENIM tekućim razredom jednakim `grade`). `submissions` je
    `{student_id: {"participation", "activity_rating", "homework_status",
    "comment"}}` iz formulara.

    Vraća listu `(student_id, zapis)` SAMO za prisutne i odsutne — „nije na ovom
    času" ne pravi red. Prazna lista je legitiman ishod i pozivalac je odbija
    kao „nijedan učenik nije označen", ne kao grešku podataka.

    PADA ZATVORENO NA CIJELI ČAS. Jedan neispravan red ruši cijeli poziv i ništa
    se ne upisuje — djelimično sačuvan čas je gori od nesačuvanog, jer izgleda
    kao potpuna evidencija."""
    _require(clean_grade(grade) is not None, "class_grade_invalid")
    grade = clean_grade(grade)

    # Datum, vrijeme i tema se provjeravaju JEDNOM za cijeli čas: svi redovi
    # dijele iste vrijednosti, pa bi provjera po učeniku bila isti posao osam
    # puta. Sve prije prvog upisa — vidi atomičnost u `save_class_sessions`.
    date = student_sessions.parse_session_date(session_date)
    # VRIJEME JE OBAVEZNO ZA NOV ČAS (verzija 5): dvije grupe istog razreda
    # istog dana razlikuju se samo po njemu.
    time_text = student_sessions.parse_session_time(session_time)

    mode = clean_topic_mode(topic_mode)
    area = str(area_name or "").strip()
    lesson = str(lesson_name or "").strip()

    if mode == student_sessions.TOPIC_CUSTOM:
        # RUČNA TEMA: naziv je obavezan, oblast NIJE. „Uvodni čas" ne pripada
        # nijednoj oblasti, a izmišljena oblast bi bila gora od prazne.
        _require(lesson, "class_topic_required")
        _require(len(lesson) <= student_sessions.MAX_LABEL_CHARS,
                 "class_topic_too_long")
        _require(len(area) <= student_sessions.MAX_LABEL_CHARS,
                 "class_area_too_long")
    else:
        _require(area and lesson, "class_curriculum_incomplete")

        from matbot import topics

        _require(topics.curriculum_pair_valid(grade, area, lesson),
                 "class_curriculum_unknown")

    allowed = {int(sid) for sid in roster_ids}
    records = []
    for raw_id, fields in sorted((submissions or {}).items(),
                                 key=lambda item: int(item[0])):
        student_id = int(raw_id)
        participation = clean_participation((fields or {}).get("participation"))
        if participation == PARTICIPATION_NOT_SCHEDULED:
            # Ne pravi red i NE provjerava se dalje: učenik kojeg nema na času
            # ne mora imati ispravan angažman ni zadaću.
            continue

        # TEK OVDJE se traži pripadnost spisku. Provjera prije ovoga bi odbila
        # čas zbog učenika koji u njemu i ne učestvuje.
        _require(student_id in allowed, "class_student_not_in_roster")

        if participation == PARTICIPATION_ABSENT:
            # Odsutan: bez angažmana i sa SERVERSKOM zadaćom (vidi konstantu).
            rating = None
            homework = ABSENT_HOMEWORK
        else:
            rating = (fields or {}).get("activity_rating")
            # PRISUTAN MORA IMATI ANGAŽMAN. Prazno polje nije „nula" nego
            # nedovršen unos, pa čas pada dok ga instruktor ne dopuni.
            _require(str(rating or "").strip() != "", "class_activity_required")
            homework = str((fields or {}).get("homework_status")
                           or PRESENT_HOMEWORK_DEFAULT).strip()

        record = student_sessions.validate_session(
            session_date=date,
            session_time=time_text,
            attendance=(student_sessions.ATTENDANCE_PRESENT
                        if participation == PARTICIPATION_PRESENT
                        else student_sessions.ATTENDANCE_ABSENT),
            activity_rating=rating,
            homework_status=homework,
            area_name=(area or None),
            lesson_name=lesson,
            topic_source=mode,
            comment=(fields or {}).get("comment"),
            grade=grade,
            require_time=True)
        records.append((student_id, record))
    return records


def summarize_saved(records):
    """Brojevi za stranicu potvrde. Bez imena i bez slobodnog teksta."""
    present = sum(1 for _, record in records
                  if record["attendance"] == student_sessions.ATTENDANCE_PRESENT)
    return {"rows": len(records), "present": present,
            "absent": len(records) - present}
