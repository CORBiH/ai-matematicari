"""„Sutra imam kontrolni“ — serverski vlasnik cijelog testa (v1, 2026-08-15).

PROIZVOD: učenik bira razred + OBLAST (ne lekciju), server sastavi TAČNO 5
MCQ pitanja iz te oblasti, učenik odgovori na svih pet, i tek NAKON predaje
vidi rezultat, procenat, pogrešna pitanja i koje lekcije da ponovi. Poslije
rezultata: lakši / isti / teži test.

ARHITEKTURA (namjerno mala — ovo NIJE drugi Practice motor):

    server bira 5 kanonskih ciljeva (lekcija + težina po slotu)
        ↓
    JEDAN Luna batch poziv (svih 5 slotova, strukturiran izlaz)
        ↓
    deterministički validatori po pitanju (postojeći, motorno-nezavisni)
        ↓
    USLOVNI drugi poziv SAMO za slotove koji su pali (nikad treći)
        ↓
    server miješa opcije, dodjeljuje slova i ČUVA ključ odgovora kod sebe

TVRDA PRAVILA:
  • najviše 2 poziva modela po generisanom testu; bez retry petlje — SDK/mrežna
    greška pada zatvoreno bez ikakvog dodatnog poziva;
  • ključ odgovora (tačna opcija, rješenje, očekivani odgovor) NIKAD ne ide u
    browser prije predaje — /exam/start payload nosi samo tekst i 4 opcije;
  • ocjenjivanje je isključivo serversko i deterministički (bez AI poziva);
  • polovičan test se NIKAD ne objavljuje: 4 validna + 1 sumnjivo = pad;
  • kurikulum je autoritativan server-side: oblast se razrješava kroz
    data/topics.json (matbot/topics.py), model ne smije zamijeniti lekciju.

Validatori po pitanju su NAMJERNO isti primitivi koje Practice objava već
koristi (mathsafe/terminologija kroz package_preflight.safe_visible_text,
mathcheck, geometrycheck, option_equivalence, mcq_integrity, stem_disclosure,
task_identity) — jedna implementacija, bez kopiranja Practice mašinerije
stanja. Dubina „vjernosti lekciji“ se ovdje ne dokazuje recenzentom: cilj
lekcije ulazi u prompt po slotu, echo lesson_id se provjerava, a kampanja
mjerenja drži kvalitet (vidi docs/CURRENT_STATE.md nakon v1 kampanje).
"""
import copy
import logging
import random
import re
import secrets
import threading
import time

from matbot import (config, exactly_one, geometrycheck, linear_system_mcq,
                    mcq_integrity, option_equivalence,
                    point_plane_projection_mcq, root_interval_mcq,
                    square_pyramid_mcq, stem_disclosure, triangle_consistency)
from matbot.llm import LLMError, failure_diagnostics_kv
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsegments import TEXT, tokenize_math
from matbot.prompts import (build_kontrolni_input, build_kontrolni_instructions,
                            kontrolni_repair_hint)
from matbot.topics import oblast_lessons
from matbot.tutor import lesson_context, task_identity
from matbot.tutor.package_preflight import safe_visible_text

logger = logging.getLogger(__name__)

# Poruka učeniku kad test ne može bezbjedno da se pripremi (fail closed).
GENERATION_FAILED_MESSAGE = "Nismo uspjeli pripremiti test. Pokušaj ponovo."

QUESTIONS_PER_TEST = 5
OPTIONS_PER_QUESTION = 4

# Profili TEŽINE TESTA (ne pojedinačnog pogađanja). Raspodjela po slotovima je
# rastuća (zagrijavanje → vrhunac) — konceptualno ~3E+2M / 1E+3M+1H / 1M+3H+1Z.
PROFILES = ("easier", "standard", "harder")
PROFILE_SLOTS = {
    "easier": ("easy", "easy", "easy", "medium", "medium"),
    "standard": ("easy", "medium", "medium", "medium", "hard"),
    "harder": ("medium", "hard", "hard", "hard", "demanding"),
}
DIFFICULTY_LABELS = {
    "easy": "lagan", "medium": "srednji", "hard": "težak", "demanding": "zahtjevan",
}

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")
_OBLAST_ID_RE = re.compile(r"^\d+-\d{2}$")
_OPTION_ID_RE = re.compile(r"^[a-d]$")
_EXAM_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")

# Koliko potpisa nedavnih pitanja sesija pamti preko VIŠE testova istog
# konteksta (3 testa × 5 pitanja) — dovoljno da „Novi test“ ne vrati upravo
# viđeno pitanje, a stanje ne raste (isti princip kao Practice historije).
MAX_RECENT_EXAM_SIGNATURES = 15


class ExamValidationError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# STANJE — server-owned, in-memory (bez baze; gubitak na restart je prihvaćen,
# isti ugovor kao Practice sesije).
# ---------------------------------------------------------------------------

def _fresh_state(session_id, context_key, grade, oblast_id, oblast_name):
    return {
        "session_id": session_id,
        "context_key": context_key,
        "grade": grade,
        "oblast_id": oblast_id,
        "oblast_name": oblast_name,
        "exam_id": "",
        "profile": "standard",
        # Pitanja AKTIVNOG testa — uključuju ključ odgovora i rješenje.
        # Nikad se ne šalju browseru u ovom obliku.
        "questions": [],
        "graded": False,
        "result": None,
        "recent_signatures": [],
        "recent_lessons": [],
    }


class KontrolniStore:
    """Thread-safe in-memory stanje kontrolnih testova, po session_id.

    Jedna sesija ima najviše JEDAN aktivan test; novi start prepisuje stari.
    Promjena razreda/oblasti resetuje kontekst (svjež profil i historija)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_session = {}

    def load(self, session_id, grade, oblast_id, oblast_name):
        context_key = f"{grade}|{oblast_id}"
        with self._lock:
            existing = self._by_session.get(session_id)
            if existing is None or existing.get("context_key") != context_key:
                return _fresh_state(session_id, context_key, grade, oblast_id,
                                    oblast_name)
            return copy.deepcopy(existing)

    def get(self, session_id):
        with self._lock:
            state = self._by_session.get(session_id)
            return copy.deepcopy(state) if state else None

    def save(self, state):
        state["recent_signatures"] = \
            state["recent_signatures"][-MAX_RECENT_EXAM_SIGNATURES:]
        with self._lock:
            self._by_session.pop(state["session_id"], None)
            self._by_session[state["session_id"]] = copy.deepcopy(state)
            while len(self._by_session) > config.MAX_SESSIONS_IN_MEMORY:
                oldest = next(iter(self._by_session))
                del self._by_session[oldest]

    def clear(self):
        with self._lock:
            self._by_session.clear()


# ---------------------------------------------------------------------------
# VALIDACIJA ULAZA (klijent se ne pita ništa — server sve provjerava)
# ---------------------------------------------------------------------------

def validate_start_payload(payload):
    """Vrati (session_id, grade, oblast_id, relative) ili baci ExamValidationError."""
    if not isinstance(payload, dict):
        raise ExamValidationError("INVALID_PAYLOAD", "Zahtjev nije prepoznat.")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise ExamValidationError("INVALID_SESSION_ID", "Zahtjev nije prepoznat. Osvježi stranicu.")
    try:
        grade = int(payload.get("grade"))
    except (TypeError, ValueError):
        raise ExamValidationError("INVALID_GRADE", "Razred nije prepoznat.")
    if grade not in (6, 7, 8, 9):
        raise ExamValidationError("INVALID_GRADE", "Razred mora biti 6, 7, 8 ili 9.")
    oblast_id = payload.get("oblast_id")
    if not isinstance(oblast_id, str) or not _OBLAST_ID_RE.match(oblast_id.strip()):
        raise ExamValidationError("INVALID_OBLAST", "Oblast nije prepoznata.")
    relative = payload.get("relative") or ""
    if relative not in ("", "same", "easier", "harder"):
        raise ExamValidationError("INVALID_RELATIVE", "Zahtjev nije prepoznat.")
    return session_id, grade, oblast_id.strip(), relative


def validate_submit_payload(payload):
    """Vrati (session_id, exam_id, answers) ili baci ExamValidationError.

    `answers` se ovdje provjerava samo STRUKTURNO (dict, id-jevi oblika q1..q5,
    vrijednosti a–d, bez viška ključeva) — pripadnost konkretnom testu i
    potpunost provjerava run_submit nad serverskim stanjem."""
    if not isinstance(payload, dict):
        raise ExamValidationError("INVALID_PAYLOAD", "Zahtjev nije prepoznat.")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise ExamValidationError("INVALID_SESSION_ID", "Zahtjev nije prepoznat. Osvježi stranicu.")
    exam_id = payload.get("exam_id")
    if not isinstance(exam_id, str) or not _EXAM_ID_RE.match(exam_id):
        raise ExamValidationError("INVALID_EXAM_ID", "Test nije prepoznat.")
    answers = payload.get("answers")
    if not isinstance(answers, dict) or len(answers) > QUESTIONS_PER_TEST:
        raise ExamValidationError("INVALID_ANSWERS", "Odgovori nisu prepoznati.")
    cleaned = {}
    for key, value in answers.items():
        if not isinstance(key, str) or len(key) > 8:
            raise ExamValidationError("INVALID_ANSWERS", "Odgovori nisu prepoznati.")
        if not isinstance(value, str) or not _OPTION_ID_RE.match(value):
            raise ExamValidationError("INVALID_ANSWERS", "Odgovori nisu prepoznati.")
        cleaned[key] = value
    return session_id, exam_id, cleaned


# ---------------------------------------------------------------------------
# IZBOR CILJEVA — server bira lekcije i težine PRIJE generisanja
# ---------------------------------------------------------------------------

def next_profile(previous, relative):
    """Tranzicija profila testa; kliješteno na krajevima ljestvice."""
    if relative in ("", "same") or previous not in PROFILES:
        return previous if previous in PROFILES else "standard"
    index = PROFILES.index(previous)
    if relative == "easier":
        return PROFILES[max(index - 1, 0)]
    return PROFILES[min(index + 1, len(PROFILES) - 1)]


def select_slots(lessons, profile, avoid_lesson_ids=(), rng=None):
    """Vrati 5 slotova [{"slot", "lesson_id", "lesson_title", "difficulty"}].

    Raznolikost: uz ≥5 lekcija u oblasti bira se 5 RAZLIČITIH, uz preferenciju
    lekcija koje NISU bile u upravo završenom testu (rotacija pokrivenosti).
    Uz <5 lekcija ponavljanje je nužno — ali se lekcije smjenjuju ciklično, a
    prompt + potpisi pitanja garantuju strukturno različite zadatke."""
    rng = rng or random
    difficulties = PROFILE_SLOTS[profile]
    pool = list(lessons)
    if not pool:
        return []
    avoid = set(avoid_lesson_ids or ())
    if len(pool) >= QUESTIONS_PER_TEST:
        fresh = [lesson for lesson in pool if lesson["id"] not in avoid]
        chosen = list(fresh)
        rng.shuffle(chosen)
        chosen = chosen[:QUESTIONS_PER_TEST]
        if len(chosen) < QUESTIONS_PER_TEST:
            rest = [lesson for lesson in pool if lesson not in chosen]
            rng.shuffle(rest)
            chosen += rest[:QUESTIONS_PER_TEST - len(chosen)]
    else:
        rng.shuffle(pool)
        chosen = [pool[i % len(pool)] for i in range(QUESTIONS_PER_TEST)]
    return [
        {"slot": i + 1, "lesson_id": lesson["id"],
         "lesson_title": lesson["title"], "difficulty": difficulties[i]}
        for i, lesson in enumerate(chosen)
    ]


# ---------------------------------------------------------------------------
# VALIDACIJA GENERISANOG PITANJA — postojeći deterministički primitivi
# ---------------------------------------------------------------------------

def _safe_field(raw, limit, allow_wrap=False):
    """(očišćen_tekst | None). None = nebezbjedan zapis ili prekoračena dužina."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > limit:
        return None
    cleaned, safe = safe_visible_text(raw, allow_wrap=allow_wrap)
    if not safe or not cleaned.strip():
        return None
    return cleaned


# ---------------------------------------------------------------------------
# ŽIVA KAMPANJA v1 (27 testova, 135 pitanja) — dvije dokazano objavljene
# greške ISTE klase: rješenje izvodi TAČNU vrijednost, a označena opcija nosi
# DRUGU („$7<\sqrt{70}<8$“ uz rješenje koje kaže $8<\sqrt{70}<9$; sferna
# površina označena 56,52 uz rješenje 113,04). mcq_integrity orakl ne pokriva
# te oblike, pa su ispod TRI uska, dokaziva validatora. Nijedan ne pogađa:
# nedokazivo uvijek ćuti (isti princip kao mathcheck).
# ---------------------------------------------------------------------------

_RELATION_SPLIT_RE = re.compile(r"(\\leq\b|\\geq\b|\\le\b|\\ge\b|<|>|=)")
_RELATION_CANON = {"\\leq": "<=", "\\le": "<=", "\\geq": ">=", "\\ge": ">=",
                   "<": "<", ">": ">", "=": "="}
_PURE_MATH_OPTION_RE = re.compile(r"^\s*\$[^$]+\$\s*$")
# Uključuje i prozni razlomak („…traženi dio je 7/20.“) — bez toga bi zaključak
# u prozi s razlomkom lažno „protivrječio“ označenoj vrijednosti.
_TEXT_NUMBER_RE = re.compile(r"-?\d+(?:,\d+)?(?:\s*/\s*\d+)?")
# Gole LaTeX riječi u matematici = izgubljena obrnuta kosa crta (živi nalaz
# „$2^xcdot2^3$“ — riječ je ZALIJEPLJENA uz prethodni znak, pa \b ne pali;
# traži se podniz bez prethodne kose crte). Skup je namjerno uzak: samo riječi
# koje se ne mogu pojaviti ni kao niz promjenljivih ni unutar \text{…} sadržaja
# školske matematike 6–9 („pi“ npr. NE ide ovdje — \text{piramide}).
_DAMAGED_LATEX_WORD_RE = re.compile(
    r"(?<!\\)(cdot|frac|sqrt|circ|overline|infty)")


def _unique_value(expression):
    """Jedina numerička vrijednost izraza, ili None (nedokazivo/višeznačno)."""
    candidates = option_equivalence._numeric_candidates(expression)
    if not candidates:
        return None
    first = candidates[0]
    tolerance = 1e-9 * max(1.0, abs(first))
    if any(abs(value - first) > tolerance for value in candidates[1:]):
        return None
    return first


def _statement_chain_truth(bare_option):
    """True/False kad je opcija DOKAZIVO tačan/netačan lanac poređenja
    (npr. „$7<\\sqrt{70}<8$“, „$0,67<0,7<0,76$“), inače None.

    Primjenjuje se SAMO na čisto matematičku opciju: proza uz matematiku
    („Ne, jer je $1=…$“) može negirati vlastitu formulu, pa se tamo ne sudi."""
    parts = _RELATION_SPLIT_RE.split(bare_option)
    if len(parts) < 3 or len(parts) % 2 == 0:
        return None
    operands = parts[0::2]
    relations = [_RELATION_CANON.get(token.strip()) for token in parts[1::2]]
    if any(relation is None for relation in relations):
        return None
    values = [_unique_value(operand) for operand in operands]
    if any(value is None for value in values):
        return None
    provably_true = True
    for left, relation, right in zip(values, relations, values[1:]):
        margin = 1e-9 * max(1.0, abs(left), abs(right))
        if relation == "=":
            holds, fails = abs(left - right) <= margin, abs(left - right) > margin
        elif relation == "<":
            holds, fails = left < right - margin, left >= right - margin
        elif relation == ">":
            holds, fails = left > right + margin, left <= right + margin
        elif relation == "<=":
            holds, fails = left <= right + margin, left > right + margin
        else:
            holds, fails = left >= right - margin, left < right - margin
        if fails:
            return False
        if not holds:
            provably_true = False
    return True if provably_true else None


def _statement_options_failure(option_texts, correct_index):
    """Kod odbijanja kad je OZNAČENA čisto matematička tvrdnja dokazivo
    NETAČNA, ili neki distraktor dokazivo TAČAN — obje su „pogrešno označen
    odgovor“ klase iz žive kampanje. Prazan string = nema nalaza."""
    for index, option in enumerate(option_texts):
        if not _PURE_MATH_OPTION_RE.match(option or ""):
            continue
        truth = _statement_chain_truth(option.strip().strip("$"))
        if truth is False and index == correct_index:
            return "marked_statement_provably_false"
        if truth is True and index != correct_index:
            return "distractor_statement_provably_true"
    return ""


def _solution_numeric_values(solution):
    """Sve numerički dokazive vrijednosti koje rješenje POMINJE: članovi
    lanaca u $…$ segmentima + goli brojevi u prozi (zaključak zna biti u
    prozi: „…najveći zajednički djelilac je 42.“)."""
    values = []
    for kind, content in tokenize_math(solution or ""):
        if kind == TEXT:
            for match in _TEXT_NUMBER_RE.finditer(content):
                value = _unique_value(match.group(0))
                if value is not None:
                    values.append(value)
            continue
        for operand in _RELATION_SPLIT_RE.split(content)[0::2]:
            value = _unique_value(operand)
            if value is not None:
                values.append(value)
    return values


def _solution_contradicts_marked_value(marked_option, solution):
    """True SAMO kad označena opcija ima JEDNU dokazivu numeričku vrijednost,
    rješenje sadrži numeričke vrijednosti, a NIJEDNA se ne poklapa s označenom
    — tačan H12-oblik iz žive kampanje (rješenje izvodi 113,04, označeno
    56,52). Simbolički odgovori i rješenja bez brojeva ćute."""
    bare = (marked_option or "").strip().strip("$")
    if _RELATION_SPLIT_RE.search(bare):
        return False                      # lanci se sude u _statement_options_failure
    marked_value = _unique_value(bare)
    if marked_value is None:
        return False
    mentioned = _solution_numeric_values(solution)
    if not mentioned:
        return False
    tolerance = 1e-6 * max(1.0, abs(marked_value))
    return all(abs(value - marked_value) > tolerance for value in mentioned)


def _damaged_notation_failure(text, option_texts, solution):
    """Uski kozmetički dokazi iz žive kampanje: gola LaTeX riječ unutar $…$
    („2^xcdot2^3“) i karet u prozi van matematike („90^$\\circ$“)."""
    for surface in (text, solution, *option_texts):
        for kind, content in tokenize_math(surface or ""):
            if kind == TEXT:
                if "^" in content:
                    return "caret_outside_math"
            elif _DAMAGED_LATEX_WORD_RE.search(content):
                return "damaged_latex_word_in_math"
    return ""


# ---------------------------------------------------------------------------
# KAMPANJA v1, DRUGI KRUG — dvije nove dokazane klase:
#   • „Koji broj je djeljiv sa 100?“ bez ijedne djeljive opcije: hiljadni
#     razdjelnik `\,` u opcijama ($3\,120$) zaslijepio je mcq_integrity orakl;
#   • „U trouglu je α=β — koji odnos stranica važi?“ s označenim $b=c$:
#     korespondencija ugao↔naspramna stranica (α↔a, β↔b, γ↔c) je čista
#     konvencija i dokaziva bez ikakvog rješavanja.
# ---------------------------------------------------------------------------

_DIVISIBILITY_CLAIM_RE = re.compile(r"(?i)\bdjeljiv\w*\s+sa?\s+\$?(\d+)\$?")
_NEGATED_DIVISIBILITY_RE = re.compile(r"(?i)\bnije\s+djeljiv")
_INTEGER_OPTION_RE = re.compile(r"^-?\d+$")


def _integer_option(option):
    """Cio broj iz opcije, ili None. Skida $, razmake i hiljadni `\\,`."""
    bare = (option or "").strip().strip("$").replace("\\,", "").replace(" ", "")
    if not _INTEGER_OPTION_RE.match(bare):
        return None
    return int(bare)


def _divisibility_claim_failure(text, option_texts, correct_index):
    """Kod odbijanja za pitanje „koji (nije) djeljiv sa N“ s cjelobrojnim
    opcijama: označena mora zadovoljiti tvrdnju, a nijedan distraktor ne smije
    — inače pitanje ili nema tačan odgovor ili ih ima više."""
    match = _DIVISIBILITY_CLAIM_RE.search(text or "")
    if not match:
        return ""
    divisor = int(match.group(1))
    if divisor == 0:
        return ""
    negated = bool(_NEGATED_DIVISIBILITY_RE.search(text))
    values = [_integer_option(option) for option in option_texts]
    if any(value is None for value in values):
        return ""
    def satisfies(value):
        divisible = value % divisor == 0
        return (not divisible) if negated else divisible
    if not satisfies(values[correct_index]):
        return "divisibility_marked_option_false"
    if any(satisfies(value) for index, value in enumerate(values)
           if index != correct_index):
        return "divisibility_distractor_also_true"
    return ""


# Treći krug: „Koja cifra u $12,305$ zauzima mjesto stotinki?“ s označenom
# cifrom DESETINKI (3 umjesto 0). Decimalno mjesto je čista konvencija —
# dokazivo bez računanja.
_DECIMAL_IN_TEXT_RE = re.compile(r"\$(\d+),(\d+)\$")
_DECIMAL_PLACE_RE = re.compile(
    r"(?i)mjest\w*\s+(desetink|stotink|stotin|hiljadit|tisu[cć]ink)")
_PLACE_INDEX = {"desetink": 0, "stotink": 1, "stotin": 1,
                "hiljadit": 2, "tisućink": 2, "tisucink": 2}


def _decimal_place_failure(text, option_texts, correct_index):
    """Kod odbijanja kad pitanje traži cifru na decimalnom mjestu, a označena
    jednocifrena opcija NIJE cifra tog mjesta. Sve nejednoznačno ćuti."""
    place_match = _DECIMAL_PLACE_RE.search(text or "")
    numbers = _DECIMAL_IN_TEXT_RE.findall(text or "")
    if not place_match or len(numbers) != 1:
        return ""
    decimals = numbers[0][1]
    index = _PLACE_INDEX[place_match.group(1).lower()]
    if index >= len(decimals):
        return ""
    values = [_integer_option(option) for option in option_texts]
    if any(value is None or not 0 <= value <= 9 for value in values):
        return ""
    if values[correct_index] != int(decimals[index]):
        return "decimal_place_marked_wrong"
    return ""


# Konstrukcijski recepti uglova (živi nalazi, dva kruga): distraktor koji je
# ISPRAVAN alternativni postupak („Sastaviti $45^\circ$ i $30^\circ$“ za cilj
# $75^\circ$; u jednom pitanju su SVA ČETIRI recepta davala 75°). Sudi se SAMO
# opciji koja CIJELA odgovara jednom od dva doslovna šablona — bez ikakvog
# slaganja glagola po tekstu (krhki parser je izričito neprihvatljiv):
#   T1  „sastaviti/sabrati ugao od $X$ i ugao od $Y$“      → X + Y
#   T2  „prepoloviti ugao od $X$ i dodati ugao od $Y$“     → X/2 + Y
_ANGLE_TARGET_RE = re.compile(r"ugao\s+od\s+\$(\d+(?:,\d+)?)\^\\circ\$")
_RECIPE_T1_RE = re.compile(
    r"(?i)^(?:konstruisati\s+)?(?:sastaviti|sabrati|spojiti)\s+ugao\s+od\s+"
    r"\$(\d+(?:,\d+)?)\^\\circ\$\s+i\s+ugao\s+od\s+\$(\d+(?:,\d+)?)\^\\circ\$\.?$")
_RECIPE_T2_RE = re.compile(
    r"(?i)^(?:konstruisati\s+)?prepoloviti\s+ugao\s+od\s+\$(\d+(?:,\d+)?)\^\\circ\$"
    r"\s+i\s+dodati\s+ugao\s+od\s+\$(\d+(?:,\d+)?)\^\\circ\$\.?$")


def _recipe_angle(option):
    """Ugao (u stepenima) koji recept-opcija konstruiše, ili None."""
    text = (option or "").strip()
    match = _RECIPE_T1_RE.match(text)
    if match:
        return (float(match.group(1).replace(",", "."))
                + float(match.group(2).replace(",", ".")))
    match = _RECIPE_T2_RE.match(text)
    if match:
        return (float(match.group(1).replace(",", ".")) / 2
                + float(match.group(2).replace(",", ".")))
    return None


def _construction_recipe_failure(text, option_texts, correct_index):
    """Kod odbijanja kad recept-opcija dokazivo (ne) konstruiše ciljani ugao:
    distraktor koji POGAĐA cilj = drugi tačan odgovor; označeni recept koji
    PROMAŠUJE cilj = pogrešno označen. Sve van šablona ćuti."""
    target_match = _ANGLE_TARGET_RE.search(text or "")
    if not target_match:
        return ""
    target = float(target_match.group(1).replace(",", "."))
    for index, option in enumerate(option_texts):
        value = _recipe_angle(option)
        if value is None:
            continue
        if index == correct_index and abs(value - target) > 1e-9:
            return "construction_marked_recipe_misses_target"
        if index != correct_index and abs(value - target) <= 1e-9:
            return "construction_distractor_reaches_target"
    return ""


_GREEK_SIDE = {"\\alpha": "a", "\\beta": "b", "\\gamma": "c"}
# Dozvoljen i trojni oblik „$\beta=\gamma=50^\circ$“ (živi nalaz: baš taj
# zapis je promakao užem obrascu, pa je pogrešno označen $a=c$ objavljen).
_GREEK_PAIR_RE = re.compile(
    r"\$(\\alpha|\\beta|\\gamma)\s*=\s*(\\alpha|\\beta|\\gamma)"
    r"(?:\s*=\s*\d+(?:,\d+)?\s*\^\\circ)?\$")
_SIDE_PAIR_OPTION_RE = re.compile(r"^([abc])\s*=\s*([abc])$")


def _angle_side_correspondence_failure(text, option_texts, correct_index):
    """Tekst tvrdi jednakost DVA ugla trougla, opcije su čiste jednakosti
    stranica {a,b,c}: označena MORA biti naspramni par (α↔a, β↔b, γ↔c).
    Sve ostalo (nejednakosti, mješovite opcije, više uglova) ćuti."""
    # Zajednički korijen deklinacije: „trougao“ NEMA „trougl“ (živi nalaz —
    # baš ta forma je promakla), a „trouglu/trougla“ imaju.
    if "troug" not in (text or "").lower():
        return ""
    pairs = _GREEK_PAIR_RE.findall(text or "")
    if len(pairs) != 1:
        return ""
    left, right = pairs[0]
    if left == right:
        return ""
    expected_sides = frozenset((_GREEK_SIDE[left], _GREEK_SIDE[right]))
    marked = _SIDE_PAIR_OPTION_RE.match(
        (option_texts[correct_index] or "").strip().strip("$").replace(" ", ""))
    if not marked:
        return ""
    if frozenset(marked.groups()) != expected_sides:
        return "angle_side_correspondence_violated"
    return ""


def _bare_option_key(text):
    """Normalizovan ključ opcije BEZ $ delimitera — za doslovno poređenje sa
    matematičkim segmentima teksta zadatka."""
    return option_equivalence.normalized_option_key(
        (text or "").strip().strip("$"))


def _stem_reveals_marked_option(task_text, option_texts, correct_index):
    """True SAMO kad tekst zadatka DOSLOVNO sadrži označenu opciju, a nijedan
    distraktor — tekst je time izdvojio baš tačan odgovor („Rezultat je $2/5$.
    Koji dio…?“). Zadatak koji nabraja VIŠE kandidata („Koji od brojeva $96$,
    $108$ i $121$ …?“) legitimno sadrži i tačan — ne pada.

    Poređenje je namjerno DOSLOVNO (normalizacija razmaka, ne semantika):
    semantička jednakost bi oborila legitimne zadatke oblika „Proširi razlomak
    $\\frac{2}{5}$…“ čiji je odgovor ISTA vrijednost drugog zapisa (6/15).
    Šire klase otkrivanja (izbor entiteta) i dalje pokriva stem_disclosure."""
    stem_keys = {
        _bare_option_key(content)
        for kind, content in tokenize_math(task_text or "")
        if kind != TEXT and content.strip()
    }
    stem_keys.discard("")
    if not stem_keys:
        return False
    marked_in_stem = _bare_option_key(option_texts[correct_index]) in stem_keys
    if not marked_in_stem:
        return False
    distractor_in_stem = any(
        _bare_option_key(option) in stem_keys
        for index, option in enumerate(option_texts) if index != correct_index)
    return not distractor_in_stem


def validate_generated_question(parsed, slot, context, prior_signatures):
    """Vrati (clean_dict, "") ili (None, kod_odbijanja).

    Redoslijed: jeftino → skupo. Svaki kod je interni (samo za log)."""
    if getattr(parsed, "slot", None) != slot["slot"]:
        return None, "slot_mismatch"
    # Model NE SMIJE zamijeniti ciljanu lekciju drugom (ugovor batch generisanja).
    if (getattr(parsed, "lesson_id", "") or "").strip() != slot["lesson_id"]:
        return None, "lesson_target_replaced"
    if (getattr(parsed, "difficulty", "") or "") != slot["difficulty"]:
        return None, "difficulty_target_replaced"
    options_raw = list(getattr(parsed, "options", []) or [])
    if len(options_raw) != OPTIONS_PER_QUESTION:
        return None, "option_count"
    correct_index = getattr(parsed, "correct_option_index", -1)
    if not isinstance(correct_index, int) or not 0 <= correct_index < OPTIONS_PER_QUESTION:
        return None, "bad_correct_index"

    text = _safe_field(parsed.text, config.MAX_TASK_CHARS)
    if text is None:
        return None, "unsafe_or_long_text"
    option_texts = []
    for raw in options_raw:
        cleaned = _safe_field(raw, config.MAX_OPTION_TEXT_CHARS, allow_wrap=True)
        if cleaned is None:
            return None, "unsafe_or_long_option"
        option_texts.append(cleaned)
    expected = _safe_field(parsed.expected_answer, config.MAX_EXPECTED_ANSWER_CHARS,
                           allow_wrap=True)
    if expected is None:
        return None, "unsafe_or_long_expected"
    solution = _safe_field(parsed.solution, config.MAX_REPLY_CHARS)
    if solution is None:
        return None, "unsafe_or_long_solution"

    # Slova opcija postoje tek POSLIJE serverskog miješanja — svako slovo koje
    # je model napisao je tvrdnja koju nije mogao znati (isti princip kao H12
    # u Practice-u). Ne normalizuje se: batch pitanje bez slova je jeftin
    # zahtjev, pa je svaki pomen slova čist signal nediscipline.
    for field_name, surface in (("text", text), ("solution", solution)):
        if mcq_integrity.option_label_claims(surface):
            return None, f"option_label_claim_{field_name}"

    # Tačna opcija i očekivani odgovor nose PRAVU matematiku; distraktori su
    # namjerno pogrešni i ne provjeravaju se na unutrašnju konzistentnost.
    correct_text = option_texts[correct_index]
    for field_name, surface in (("text", text), ("correct_option", correct_text),
                                ("expected", expected), ("solution", solution)):
        issues = find_numeric_inconsistencies(surface)
        if issues:
            return None, f"numeric_inconsistency_{field_name}"

    for field_name, surface in (("text", text), ("correct_option", correct_text)):
        geometry_issues = geometrycheck.find_geometry_issues(
            surface, context.geometry_scope, list(context.geometry_figures))
        if geometry_issues:
            return None, f"geometry_notation_{field_name}"

    # POSTOJI LI OBJEKAT KOJI ZADATAK OPISUJE (živi nalaz post-deploy 3128968):
    # objavljen je obim trougla čiji su ZADATI podaci međusobno nemogući
    # ($\alpha=50^\circ$, $\beta=60^\circ$, $a=6$, $b=7$ — sinusna teorema
    # promašuje 3,2 %). Notaciju sudi `geometrycheck`, vrijednosti sude orakli
    # iznad; nijedan od njih ne pita postoji li sam objekat. Sudi se ISKLJUČIVO
    # iz teksta zadatka — označena opcija i rješenje su ono što se provjerava,
    # pa ne smiju biti dokaz. Vidi matbot/triangle_consistency.py.
    triangle_failure = triangle_consistency.publication_failure(text)
    if triangle_failure:
        return None, triangle_failure

    if expected.strip() != correct_text.strip():
        return None, "expected_option_mismatch"

    # OBLIK DISTRAKTORA U NUMERIČKOM PITANJU (živi nalaz, verifikacija poslije
    # izdanja): objavljeni su „0, intez“ i „$0,<KANNADA>?85$“ kao opcije u
    # pitanjima čiji su svi ostali odgovori decimalni brojevi. Ključ je bio
    # tačan i ocjena ispravna, ali učenik je vidio besmislicu. Distraktor NE
    # mora biti tačan — mora biti sintaksno moguć odgovor istog tipa. Vidi
    # `mcq_integrity.numeric_option_shape_failure`.
    shape_failure = mcq_integrity.numeric_option_shape_failure(
        option_texts, correct_index)
    if shape_failure:
        return None, shape_failure

    if option_equivalence.find_textual_duplicate_pairs(option_texts):
        return None, "duplicate_options"
    # Semantička ekvivalencija dvije opcije = potencijalno dva tačna odgovora
    # (istorijski defekt: ekvivalentne radikalske opcije). Uvijek pada.
    if option_equivalence.find_equivalent_option_pairs(option_texts):
        return None, "equivalent_options"

    # Uski matematički orakl (rješava zadatak gdje umije) + poklapanje označene
    # opcije s kanonskim rezultatom. Neprimjenjiv oblik ćuti — ne pogađa.
    mcq_failure, _result = mcq_integrity.publication_failure(
        text, option_texts, correct_index, expected)
    if mcq_failure:
        return None, f"mcq_integrity_{mcq_failure}"

    # SISTEM DVIJE LINEARNE JEDNAČINE (živi nalaz P0-1, finalna prijemna
    # kampanja e767cac, K5 q5): za taj oblik nijedan orakl iznad nije bio
    # primjenjiv, pa je objava zavisila SAMO od toga što model sam sebi nije
    # protivrječio (`expected_answer == označena opcija`). Objavljeno je pitanje
    # čije tačno rješenje ($x=\frac{12}{5}$) nije bilo ni među ponuđenim
    # opcijama. Ovdje server sam riješi sistem egzaktno i tek onda ocijeni sve
    # četiri opcije — vidi matbot/linear_system_mcq.py.
    system_failure = linear_system_mcq.publication_failure(
        text, option_texts, correct_index, solution)
    if system_failure:
        return None, f"linear_system_{system_failure}"

    # PRAVILNA ČETVOROSTRANA PIRAMIDA (živi nalaz N-4, post-deploy 70bb514):
    # objavljeno je „$a=10$, apotema $h_a=13$, kolika je bočna ivica $s$?“ s
    # označenim $\sqrt{219}$, a tačno je $\sqrt{194}$ — i ta opcija je bila
    # ponuđena. $\sqrt{219}$ nastaje SAMO ako se apotema upotrijebi kao visina
    # piramide. To je zamjena ULOGA veličina, pa je modelov račun ostao
    # aritmetički tačan i nijedan postojeći validator nije imao za šta da se
    # uhvati. Server sada sam izračuna traženu dužinu egzaktno — vidi
    # matbot/square_pyramid_mcq.py.
    pyramid_failure = square_pyramid_mcq.publication_failure(
        text, option_texts, correct_index)
    if pyramid_failure:
        return None, f"square_pyramid_{pyramid_failure}"

    # ORTOGONALNA PROJEKCIJA DUŽI NA RAVAN (živi nalaz N-6, ista kampanja):
    # objavljeno je „tačke s ISTE strane, udaljenosti 9 i 12, $AB=15$, kolika
    # je projekcija?“ s označenih $12$, a tačno je $\sqrt{216}=6\sqrt6$ — dakle
    # nijedna opcija nije bila tačna. $12$ nastaje samo ako se $9$ uzme kao
    # NORMALNA RAZLIKA umjesto $|12-9|=3$. Opet zamjena uloga, pa je modelov
    # račun ostao aritmetički tačan — vidi matbot/point_plane_projection_mcq.py.
    projection_failure = point_plane_projection_mcq.publication_failure(
        text, option_texts, correct_index)
    if projection_failure:
        return None, f"point_plane_{projection_failure}"


    # ŽIVA KAMPANJA v1: uski dokazi povrh orakla (vidi blokove komentara gore).
    divisibility_failure = _divisibility_claim_failure(text, option_texts,
                                                       correct_index)
    if divisibility_failure:
        return None, divisibility_failure
    place_failure = _decimal_place_failure(text, option_texts, correct_index)
    if place_failure:
        return None, place_failure
    recipe_failure = _construction_recipe_failure(text, option_texts, correct_index)
    if recipe_failure:
        return None, recipe_failure
    correspondence_failure = _angle_side_correspondence_failure(
        text, option_texts, correct_index)
    if correspondence_failure:
        return None, correspondence_failure
    statement_failure = _statement_options_failure(option_texts, correct_index)
    if statement_failure:
        return None, statement_failure

    # KORIJEN IZMEĐU UZASTOPNIH CIJELIH BROJEVA (živi nalaz, verifikacija
    # poslije izdanja): objavljeno je „između koja dva uzastopna prirodna broja
    # se nalazi $\sqrt{70}$?“ s označenim „$7$ i $8$“, a tačno je „$8$ i $9$“ —
    # i to je RJEŠENJE u istom paketu uredno izvelo. `expected_answer` je bio
    # jednak označenoj opciji, pa je paket bio sam sa sobom „dosljedan“ i nijedan
    # postojeći orakl nije bio primjenjiv (označena opcija nosi DVIJE
    # vrijednosti, pa `_solution_contradicts_marked_value` ćuti). Server sada sam
    # izračuna interval egzaktnom cjelobrojnom aritmetikom — vidi
    # matbot/root_interval_mcq.py.
    #
    # REDOSLIJED: namjerno POSLIJE `_statement_options_failure`. Lančani
    # oblik opcije („$7<\sqrt{70}<8$“) već ima svoj uži detektor i svoj
    # kod; ovaj orakl pokriva ono što taj ne vidi — PROZNI par („Između $7$
    # i $8$“), tačno oblik iz živog nalaza.
    root_interval_failure = root_interval_mcq.publication_failure(
        text, option_texts, correct_index)
    if root_interval_failure:
        return None, f"root_interval_{root_interval_failure}"
    if _solution_contradicts_marked_value(correct_text, solution):
        return None, "solution_marked_value_divergence"
    notation_failure = _damaged_notation_failure(text, option_texts, solution)
    if notation_failure:
        return None, notation_failure

    # TAČNO JEDAN TAČAN — DOKAZ, NE ODSUSTVO NALAZA (matbot/exactly_one.py).
    # Za oblik „izaberi tvrdnju“ (recept, zaključak, jednakost, opis) objava
    # traži POZITIVAN dokaz da je tačna tačno jedna opcija. Dva živa nalaza
    # (2/300) prošla su upravo zato što su svi raniji čuvari tražili dokazan
    # defekt, a nijedan nije tražio dokaz ispravnosti. Pitanja koja traže
    # konkretan rezultat ovdje prolaze nedirnuta.
    exactly_one_failure = exactly_one.publication_failure(
        text, option_texts, correct_index)
    if exactly_one_failure:
        return None, exactly_one_failure

    disclosure = stem_disclosure.stem_answer_disclosure(
        text, option_texts, correct_index)
    if disclosure:
        return None, "stem_answer_disclosure"
    if _stem_reveals_marked_option(text, option_texts, correct_index):
        return None, "stem_reveals_marked_option"

    signature = task_identity.canonical_signature(text, option_texts)
    if signature and signature in prior_signatures:
        return None, "repeated_task_signature"

    return {
        "lesson_id": slot["lesson_id"],
        "lesson_title": slot["lesson_title"],
        "difficulty": slot["difficulty"],
        "text": text,
        "option_texts": option_texts,
        "correct_index": correct_index,
        "expected_answer": expected,
        "solution": solution,
        "signature": signature,
    }, ""


# ---------------------------------------------------------------------------
# GENERISANJE — 1 batch poziv + USLOVNA popravka palih slotova (maks. 2)
# ---------------------------------------------------------------------------

def _slot_contexts(grade, slots):
    contexts = {}
    for slot in slots:
        contexts[slot["slot"]] = lesson_context.build(grade, slot["lesson_id"])
    return contexts

def _call_batch(llm, grade, oblast_name, slots, contexts, avoid_texts, request_id,
                stage, timeout_s=None, slot_feedback=None):
    """Jedan batch poziv; vrati (mapa slot→parsed pitanje, latency_ms) ili
    (None, latency) na LLM grešku. NIKAD ne pravi dodatni poziv.

    `timeout_s` sužava rok poziva na ostatak ukupnog roka generisanja.
    `slot_feedback` nosi RAZLOG odbijanja po slotu (samo za popravku)."""
    instructions = build_kontrolni_instructions(grade, oblast_name)
    input_text = build_kontrolni_input(grade, oblast_name, slots, contexts,
                                       avoid_texts, slot_feedback=slot_feedback)
    try:
        result = llm.kontrolni_turn(instructions, input_text, timeout_s=timeout_s)
    except LLMError as error:
        logger.warning("kontrolni_llm_failed request_id=%s stage=%s category=%s %s",
                       request_id, stage, error.category,
                       failure_diagnostics_kv(error))
        return None, 0
    parsed = {}
    for question in result.output.questions:
        # Dupli slot u izlazu = nedisciplinovan paket; zadnji se ignoriše, a
        # slot pada na validaciji identiteta (prvi zapis je autoritativan).
        parsed.setdefault(question.slot, question)
    return parsed, result.latency_ms


def generate_test(llm, grade, oblast_name, slots, recent_signatures,
                  request_id=""):
    """Vrati (pitanja_u_redoslijedu_slotova, meta) ili (None, meta) — fail closed.

    Meta nosi broj poziva, ukupnu latenciju i kodove odbijanja (za log/kampanju).
    """
    contexts = _slot_contexts(grade, slots)
    meta = {"calls": 0, "latency_ms": 0, "reject_codes": [], "repaired_slots": []}
    # UKUPAN ROK generisanja (vidi config.kontrolni_deadline_s): drugi poziv
    # dobija samo ostatak, a kad ostatka nema — preskače se i paket pada
    # zatvoreno. Nikad treći poziv, nikad retry.
    started_at = time.perf_counter()

    parsed_by_slot, latency = _call_batch(
        llm, grade, oblast_name, slots, contexts, (), request_id, "batch")
    meta["calls"] += 1
    meta["latency_ms"] += latency
    if parsed_by_slot is None:
        return None, meta

    accepted = {}
    known_signatures = set(recent_signatures or ())
    failed_slots = []
    # RAZLOG PO SLOTU putuje u popravku (vidi prompts.kontrolni_repair_hint):
    # bez njega je model ponavljao isti defekt (mjereno na `equivalent_options`).
    slot_feedback = {}
    for slot in slots:
        parsed = parsed_by_slot.get(slot["slot"])
        if parsed is None:
            failed_slots.append(slot)
            meta["reject_codes"].append(f"slot{slot['slot']}:missing")
            slot_feedback[slot["slot"]] = kontrolni_repair_hint("missing")
            continue
        clean, code = validate_generated_question(
            parsed, slot, contexts[slot["slot"]], known_signatures)
        if clean is None:
            failed_slots.append(slot)
            meta["reject_codes"].append(f"slot{slot['slot']}:{code}")
            slot_feedback[slot["slot"]] = kontrolni_repair_hint(code)
            continue
        accepted[slot["slot"]] = clean
        if clean["signature"]:
            known_signatures.add(clean["signature"])

    if failed_slots:
        # USLOVNI drugi (i posljednji) poziv: SAMO pali slotovi, uz tekstove
        # prihvaćenih pitanja kao „već iskorišteno — mora se razlikovati“.
        remaining_s = config.kontrolni_deadline_s() - (time.perf_counter() - started_at)
        if remaining_s < config.MIN_STAGE_BUDGET_S:
            # Prvi poziv je pojeo cijeli budžet: popravka se NE pokušava, jer
            # bi probila ukupan rok i vratila 504 umjesto naše poruke.
            meta["reject_codes"].append("deadline_exhausted_before_repair")
            return None, meta
        meta["repaired_slots"] = [slot["slot"] for slot in failed_slots]
        avoid_texts = [accepted[number]["text"] for number in sorted(accepted)]
        parsed_by_slot, latency = _call_batch(
            llm, grade, oblast_name, failed_slots, contexts, avoid_texts,
            request_id, "repair", timeout_s=remaining_s,
            slot_feedback=slot_feedback)
        meta["calls"] += 1
        meta["latency_ms"] += latency
        if parsed_by_slot is None:
            return None, meta
        for slot in failed_slots:
            parsed = parsed_by_slot.get(slot["slot"])
            if parsed is None:
                meta["reject_codes"].append(f"slot{slot['slot']}:missing_after_repair")
                return None, meta
            clean, code = validate_generated_question(
                parsed, slot, contexts[slot["slot"]], known_signatures)
            if clean is None:
                # Poslije drugog poziva nema trećeg: cijeli test pada zatvoreno.
                meta["reject_codes"].append(f"slot{slot['slot']}:{code}_after_repair")
                return None, meta
            accepted[slot["slot"]] = clean
            if clean["signature"]:
                known_signatures.add(clean["signature"])

    return [accepted[slot["slot"]] for slot in slots], meta


def _shuffle_options(option_texts, correct_index, rng=None):
    """Server je JEDINI koji dodjeljuje slova opcijama i pamti tačno (isti
    princip kao Practice objava). Miješa se tačno jednom po pitanju."""
    rng = rng or random
    ids = ["a", "b", "c", "d"]
    pairs = list(enumerate(option_texts))
    rng.shuffle(pairs)
    options, correct_option_id = [], ""
    for position, (original_index, option_text) in enumerate(pairs):
        options.append({"id": ids[position], "text": option_text})
        if original_index == correct_index:
            correct_option_id = ids[position]
    return options, correct_option_id


# ---------------------------------------------------------------------------
# JAVNI TOK: START (generisanje) i SUBMIT (ocjenjivanje)
# ---------------------------------------------------------------------------

def run_start(store, llm, payload):
    """Vrati (http_status, response_dict). Najviše 2 poziva modela; svaki drugi
    ishod je fail closed s kontrolisanom porukom."""
    request_id = secrets.token_hex(6)
    started = time.perf_counter()
    session_id, grade, oblast_id, relative = validate_start_payload(payload)

    lessons = oblast_lessons(grade, oblast_id)
    if not lessons:
        raise ExamValidationError("UNKNOWN_OBLAST",
                                  "Izabrana oblast nije prepoznata za ovaj razred.")
    oblast_name = lessons[0]["oblast"]

    state = store.load(session_id, grade, oblast_id, oblast_name)
    # Prvi test konteksta je UVIJEK standard; easier/same/harder koračaju od
    # profila POSTOJEĆEG testa. `relative` bez aktivnog testa (npr. replay
    # zahtjeva poslije restarta) bezbjedno pada na standard.
    if relative in ("easier", "same", "harder") and state.get("exam_id"):
        profile = next_profile(state.get("profile", "standard"), relative)
    else:
        profile = "standard"
    slots = select_slots(lessons, profile,
                         avoid_lesson_ids=state.get("recent_lessons", ()))

    questions, meta = generate_test(
        llm, grade, oblast_name, slots, state.get("recent_signatures", ()),
        request_id=request_id)
    total_ms = int((time.perf_counter() - started) * 1000)
    if questions is None:
        logger.warning(
            "kontrolni_generation_failed request_id=%s grade=%s oblast=%s "
            "profile=%s calls=%s total_ms=%s codes=%s",
            request_id, grade, oblast_id, profile, meta["calls"], total_ms,
            ",".join(meta["reject_codes"])[:400])
        return 200, {"status": "failed", "message": GENERATION_FAILED_MESSAGE}

    exam_id = secrets.token_urlsafe(12)
    stored_questions, client_questions = [], []
    for ordinal, question in enumerate(questions, start=1):
        options, correct_option_id = _shuffle_options(
            question["option_texts"], question["correct_index"])
        question_id = f"q{ordinal}"
        stored_questions.append({
            "id": question_id,
            "ordinal": ordinal,
            "lesson_id": question["lesson_id"],
            "lesson_title": question["lesson_title"],
            "difficulty": question["difficulty"],
            "text": question["text"],
            "options": options,
            "correct_option_id": correct_option_id,
            "expected_answer": question["expected_answer"],
            "solution": question["solution"],
            "signature": question["signature"],
        })
        # KLIJENTSKI payload: NIKAD correct_option_id / expected / solution /
        # lesson metapodaci — browser prije predaje ne smije moći pročitati ključ.
        client_questions.append({
            "id": question_id,
            "ordinal": ordinal,
            "text": question["text"],
            "options": [{"id": o["id"], "text": o["text"]} for o in options],
        })

    state.update({
        "exam_id": exam_id,
        "profile": profile,
        "questions": stored_questions,
        "graded": False,
        "result": None,
        "recent_lessons": [q["lesson_id"] for q in stored_questions],
    })
    state["recent_signatures"] = (
        list(state.get("recent_signatures", []))
        + [q["signature"] for q in stored_questions if q["signature"]])
    store.save(state)

    logger.info(
        "kontrolni_start request_id=%s grade=%s oblast=%s profile=%s calls=%s "
        "repaired=%s llm_ms=%s total_ms=%s",
        request_id, grade, oblast_id, profile, meta["calls"],
        ",".join(str(s) for s in meta["repaired_slots"]) or "-",
        meta["latency_ms"], total_ms)
    return 200, {
        "status": "ready",
        "exam_id": exam_id,
        "difficulty": profile,
        "oblast_name": oblast_name,
        "question_count": len(client_questions),
        "questions": client_questions,
    }


def _recommendation(wrong_questions, score):
    """Deterministička preporuka: server ZNA ciljne lekcije pogrešnih pitanja —
    nikakav model se ne pita šta učenik treba ponoviti."""
    lessons = []
    for question in wrong_questions:
        title = question["lesson_title"]
        if title not in lessons:
            lessons.append(title)
    if score == 5:
        message = "Odlično — spreman/na si za ovu oblast."
    elif score == 4:
        message = "Vrlo dobro. Ponovi lekciju iz pitanja koje si pogriješio/la."
    elif score >= 2:
        message = "Preporučujemo da ponoviš navedene lekcije prije kontrolnog."
    else:
        message = ("Vrati se na osnovne lekcije ove oblasti i pokušaj lakši test.")
    return {"lessons": lessons, "message": message}


def run_submit(store, payload):
    """Serversko ocjenjivanje — NULA poziva modela, deterministički.

    Idempotentno: već ocijenjen test vraća POHRANJEN rezultat i ignoriše nove
    odgovore (browser ne može promijeniti odgovore poslije predaje)."""
    session_id, exam_id, answers = validate_submit_payload(payload)
    state = store.get(session_id)
    if state is None or not state.get("exam_id") or state["exam_id"] != exam_id:
        # Replay starog/tuđeg exam_id-ja ili restart servera: bez ključa nema
        # ocjenjivanja — kontrolisana poruka, nikad pogađanje.
        raise ExamValidationError("UNKNOWN_EXAM",
                                  "Test nije pronađen. Pokreni novi test.")

    if state.get("graded") and state.get("result"):
        logger.info("kontrolni_submit_repeat session_context=%s", state["context_key"])
        return 200, state["result"]

    questions = state.get("questions", [])
    known_ids = {question["id"] for question in questions}
    if set(answers) - known_ids:
        raise ExamValidationError("INVALID_ANSWERS", "Odgovori nisu prepoznati.")
    unanswered = [q["id"] for q in questions if q["id"] not in answers]
    if unanswered:
        return 200, {
            "status": "incomplete",
            "remaining": len(unanswered),
            "message": (f"Nisi odgovorio/la na {len(unanswered)} "
                        f"{'pitanje' if len(unanswered) == 1 else 'pitanja'}."),
        }

    graded_questions, wrong = [], []
    for question in questions:
        selected = answers[question["id"]]
        correct = selected == question["correct_option_id"]
        correct_text = next(o["text"] for o in question["options"]
                            if o["id"] == question["correct_option_id"])
        graded_questions.append({
            "id": question["id"],
            "ordinal": question["ordinal"],
            "correct": correct,
            "selected_option_id": selected,
            "correct_option_id": question["correct_option_id"],
            "correct_text": correct_text,
            "lesson_title": question["lesson_title"],
        })
        if not correct:
            wrong.append(question)

    score = sum(1 for g in graded_questions if g["correct"])
    result = {
        "status": "graded",
        "exam_id": exam_id,
        "score": score,
        "total": len(graded_questions),
        "percentage": int(round(100 * score / max(len(graded_questions), 1))),
        "questions": graded_questions,
        "recommendation": _recommendation(wrong, score),
    }
    state["graded"] = True
    state["result"] = result
    store.save(state)
    logger.info("kontrolni_submit grade=%s oblast=%s profile=%s score=%s/5",
                state["grade"], state["oblast_id"], state["profile"], score)
    return 200, result
