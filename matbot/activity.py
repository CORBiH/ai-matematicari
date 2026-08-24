"""Strukturirani zapis UČENIKOVE aktivnosti — činjenice, ne razgovor.

ŠTA OVAJ MODUL JESTE: tanak, turn-lokalni sabirnik događaja koje server MOŽE
DOKAZATI. Motor (Practice pipeline) ga zove tačno na mjestu gdje činjenica
postaje istinita — kad je zadatak objavljen, kad je klik ocijenjen, kad je
nagovještaj stvarno poslužen. Sloj iznad (`matbot/api.py`) sakupljene događaje
na kraju turna preda izvještajnoj bazi zajedno s već razriješenim `student_id`.

ŠTA NIJE: nije log, nije historija razgovora i nije mjerenje modela.

  • NIKAD ne pamti sirovu poruku učenika, cio odgovor tutora, prompt, sliku ni
    e-mail. Ono što ide u bazu je nabrojiv `event_type` plus nekoliko
    strukturiranih polja koja server ionako posjeduje (razred, lekcija, oblast).
  • Identitet zadatka ulazi kao SHA-256 otisak (`current_task_identity`), koji
    je već serverski izračunat i ne otkriva tekst.
  • Ne bilježi se ništa što učenik NIJE vidio: odbijen model-paket, recenzentska
    eskalacija i interni retry nisu učenikova aktivnost.

ZAŠTO CONTEXTVAR, A NE ARGUMENT KROZ SVE POTPISE: događaj nastaje duboko u
Practice motoru (`_publish_task`, `_run_choice_turn`), a `student_id` postoji
samo na HTTP granici. Provlačenje sabirnika kroz desetak potpisa promijenilo bi
motor koji ova faza NE SMIJE dirati u ponašanju. `ContextVar` je po niti (i po
async zadatku), pa gunicorn s više niti nema dijeljeno stanje; van
`capture()` je `note()` čist no-op, tako da testovi motora i evaluacijski alat
rade bajt za bajt isto.

TVRDA GRANICA: ovaj modul NIKAD ne baca prema pozivaocu i nikad ne dodiruje
mrežu. On samo puni listu u memoriji. Upis (i njegovi kvarovi) žive u
`matbot/reporting_db.py`.
"""
import contextvars
import datetime
import json
import logging
from contextlib import contextmanager

logger = logging.getLogger("matbot.activity")

# Izvor u `learning_activity.source`. Zatvorena vrijednost — druge izvore
# (Thinkific sinhronizacija) piše neki drugi, budući modul.
SOURCE = "matbot"

# --- REČNIK DOGAĐAJA -------------------------------------------------------
# Svaki naziv odgovara TAČNO jednoj činjenici koju server dokazuje. Nema
# sinonima i nema dva događaja za istu činjenicu: verdikt klika je već u imenu
# događaja, pa se ne ponavlja u metapodacima.
PRACTICE_TASK_PRESENTED = "practice_task_presented"
PRACTICE_ANSWER_CORRECT = "practice_answer_correct"
PRACTICE_ANSWER_INCORRECT = "practice_answer_incorrect"
PRACTICE_HINT_USED = "practice_hint_used"
PRACTICE_FULL_SOLUTION_SHOWN = "practice_full_solution_shown"
EXPLAIN_COMPLETED = "explain_completed"
QUICK_COMPLETED = "quick_completed"
KONTROLNI_GENERATED = "kontrolni_generated"
# Predaja testa: JEDINI kontrolni događaj koji nosi stvaran rezultat učenika.
# Postoji zato što `kontrolni.run_submit` ocjenjuje deterministički na serveru —
# nije procjena i nije izvedeno iz modela.
KONTROLNI_COMPLETED = "kontrolni_completed"

EVENT_TYPES = frozenset({
    PRACTICE_TASK_PRESENTED, PRACTICE_ANSWER_CORRECT, PRACTICE_ANSWER_INCORRECT,
    PRACTICE_HINT_USED, PRACTICE_FULL_SOLUTION_SHOWN,
    EXPLAIN_COMPLETED, QUICK_COMPLETED,
    KONTROLNI_GENERATED, KONTROLNI_COMPLETED,
})

# Gornje granice — zaštita od toga da neispravan poziv upiše smeće u tabelu iz
# koje se kasnije crta roditeljski izvještaj.
MAX_EVENT_KEY_CHARS = 200
MAX_TEXT_FIELD_CHARS = 200
MAX_METADATA_CHARS = 500
# Koliko događaja jedan turn uopšte smije proizvesti. Normalan turn ima 1–2;
# granica postoji da greška u petlji ne može napuniti ni memoriju ni bazu.
MAX_EVENTS_PER_TURN = 12

_buffer = contextvars.ContextVar("matbot_activity_buffer", default=None)


# --- VRIJEME DOGAĐAJA, NE VRIJEME UPISA -----------------------------------
# ŽIVI DEFEKT (nađen prije commita): i `learning_activity.occurred_at` i
# `assessment_attempts.started_at/completed_at` su se punili bazinim
# `CURRENT_TIMESTAMP`, dakle u trenutku kad radna nit STIGNE da piše. Kako su
# upisi asinhroni i smiju stići obrnutim redom, to je moglo proizvesti
# logički nemoguć zapis: `completed_at < started_at` za test koji je očito prvo
# generisan pa tek onda predan.
#
# Ispravka je semantička, ne kozmetička: vrijeme se hvata TAMO GDJE ČINJENICA
# POSTAJE ISTINITA (kad `status` postane `ready`/`graded`, kad motor objavi
# zadatak), i putuje kroz payload do baze. Raspored niti više ne može pomjeriti
# hronologiju.
#
# FORMAT je namjerno BAJT ZA BAJT isti kao SQLite `CURRENT_TIMESTAMP`:
# `YYYY-MM-DD HH:MM:SS`, 19 znakova, UTC, BEZ milisekundi (izmjereno na
# libsql 0.1.11 — `'2026-08-24 16:26:44'`). Time leksikografsko poređenje,
# `date()`, `MIN()` i sortiranje rade isto nad našim vrijednostima i nad
# `created_at` kolonom koja i dalje koristi bazin default.
#
# Milisekunde se NE izmišljaju: dodavanje preciznosti koju izvor nema stvorilo
# bi lažan utisak tačnosti i razišlo bi format s `created_at`.
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def event_timestamp(moment=None):
    """UTC pečat trenutka u kojem je događaj DOKAZAN. Nikad ne baca."""
    try:
        current = moment or datetime.datetime.now(datetime.timezone.utc)
        if current.tzinfo is not None:
            current = current.astimezone(datetime.timezone.utc)
        return current.strftime(TIMESTAMP_FORMAT)
    except Exception:                      # pragma: no cover — defanzivno
        return None


class ActivityEvent:
    """Jedan dokazan događaj. Namjerno „glup" nosilac podataka bez logike."""

    __slots__ = ("event_type", "event_key", "mode", "grade", "area_name",
                 "lesson_id", "lesson_name", "metadata", "occurred_at")

    def __init__(self, event_type, event_key, mode="", grade=None, area_name="",
                 lesson_id="", lesson_name="", metadata=None, occurred_at=None):
        self.event_type = event_type
        self.event_key = event_key
        self.mode = mode
        self.grade = grade
        self.area_name = area_name
        self.lesson_id = lesson_id
        self.lesson_name = lesson_name
        self.metadata = metadata or {}
        # Vrijeme se hvata PRI NASTANKU događaja — dakle na mjestu gdje ga je
        # server dokazao — a ne kad ga radna nit upiše.
        self.occurred_at = occurred_at or event_timestamp()

    def metadata_json(self):
        """Kompaktan JSON ili `None`. Prazan rječnik NE postaje `"{}"` —
        nepostojeći podatak se u bazi vidi kao NULL, ne kao prazan objekat."""
        if not self.metadata:
            return None
        try:
            encoded = json.dumps(self.metadata, ensure_ascii=False,
                                 sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return None
        return encoded[:MAX_METADATA_CHARS] if encoded else None

    def __repr__(self):   # pragma: no cover - dijagnostika
        return "ActivityEvent(%s, %s)" % (self.event_type, self.event_key)


def _clean_text(value, limit=MAX_TEXT_FIELD_CHARS):
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _clean_grade(value):
    try:
        grade = int(value)
    except (TypeError, ValueError):
        return None
    return grade if 1 <= grade <= 12 else None


@contextmanager
def capture():
    """Otvori turn-lokalni sabirnik. Van njega je `note()` no-op.

    Vraća listu koja se puni tokom turna. Pozivalac je odgovoran da je preda
    izvještajnom sloju — i to TEK kad zna da je turn zaista uspio."""
    events = []
    token = _buffer.set(events)
    try:
        yield events
    finally:
        _buffer.reset(token)


def active():
    """Da li se trenutno sakuplja (za jeftin izlaz iz vrućih putanja)."""
    return _buffer.get() is not None


def note(event_type, event_key, *, mode="", grade=None, area_name="",
         lesson_id="", lesson_name="", metadata=None):
    """Zabilježi DOKAZANU činjenicu. Nikad ne baca.

    Nepoznat `event_type`, prazan ključ ili pun sabirnik se tiho odbacuju:
    izvještavanje nema pravo da obori tutorski turn ni u jednom slučaju, pa ni
    kroz vlastitu grešku u pozivu."""
    events = _buffer.get()
    if events is None:
        return
    try:
        if event_type not in EVENT_TYPES:
            logger.info("activity_event_rejected code=unknown_event_type")
            return
        key = _clean_text(event_key, MAX_EVENT_KEY_CHARS)
        if not key:
            logger.info("activity_event_rejected code=empty_event_key")
            return
        if len(events) >= MAX_EVENTS_PER_TURN:
            logger.info("activity_event_rejected code=turn_event_limit")
            return
        events.append(ActivityEvent(
            event_type=event_type,
            event_key=key,
            mode=_clean_text(mode, 20),
            grade=_clean_grade(grade),
            area_name=_clean_text(area_name),
            lesson_id=_clean_text(lesson_id, 64),
            lesson_name=_clean_text(lesson_name),
            metadata=metadata if isinstance(metadata, dict) else None,
        ))
    except Exception:                      # pragma: no cover — defanzivno
        logger.info("activity_event_rejected code=unexpected")


# --- KLJUČEVI IDEMPOTENTNOSTI ----------------------------------------------
# `UNIQUE(source, event_key)` u bazi je jedini pravi arbitar, ali ključ mora
# biti DETERMINISTIČAN da bi ta zaštita uopšte imala šta da uhvati. Zato se
# gradi ISKLJUČIVO od serverskih identifikatora:
#
#   session_id            — anoniman UUID iz browsera; NIJE identitet učenika,
#                           ovdje služi samo da razdvoji paralelne razgovore
#   task identity         — SHA-256 vidljivog zadatka (`task_identity.py`)
#   client_turn_id        — monotoni ID akcije koji frontend već šalje
#   exam_id               — serverski UUID kontrolnog testa
#
# NIJEDAN ključ ne sadrži tekst učenika, e-mail ni tekst zadatka.
def practice_task_key(session_id, task_identity):
    return "practice:%s:%s:presented" % (session_id, task_identity[:32])


def practice_answer_key(session_id, task_identity, attempt_token):
    return "practice:%s:%s:answer:%s" % (session_id, task_identity[:32], attempt_token)


def practice_hint_key(session_id, task_identity, hint_level):
    return "practice:%s:%s:hint:%s" % (session_id, task_identity[:32], hint_level)


def practice_solution_key(session_id, task_identity):
    return "practice:%s:%s:solution" % (session_id, task_identity[:32])


def explain_key(session_id, client_turn_id):
    return "explain:%s:%s" % (session_id, client_turn_id)


def quick_key(session_id, client_turn_id):
    return "quick:%s:%s" % (session_id, client_turn_id)


def kontrolni_generated_key(exam_id):
    return "kontrolni:%s:generated" % exam_id


def kontrolni_completed_key(exam_id):
    return "kontrolni:%s:completed" % exam_id
