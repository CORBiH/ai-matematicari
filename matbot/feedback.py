"""Determinističko oblikovanje feedbacka na POGREŠAN klik u Practice modu.

Živi nalaz: nakon netačnog odgovora tutor je znao napisati dugačak dokaz zašto
je izabrana opcija pogrešna („Izabrao si $\\frac{3}{24}$, ali to nije tačno
zato što...“). Učenik time dobije zid teksta umjesto sljedećeg koraka.

Ugovor za PRVI pogrešan klik (server ga garantuje, ne prompt):
    Netačno.

    Hint: <jedan konkretan sljedeći korak ili pitanje>

Server je ovdje autoritet iz istog razloga kao kod verdikta: prompt pravilo
može biti zanemareno, a ovo je vidljivo učeniku. Modul NIKAD ne poziva model —
oblikuje tekst koji je model već vratio (hint polje ili reply).
"""
import re

from matbot import config
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsegments import math_contents, tokenize_math

VERDICT_PREFIX = "Netačno."
HINT_PREFIX = "Hint: "

# Kad model procuri tačan odgovor u hint, ne pravimo drugi AI poziv — vraćamo
# ovaj neutralan, uvijek tačan sljedeći korak. Namjerno bez $...$: garantovano
# bezbjedan za HARD skraćivanje (vidi _hard_clip) bez rizika da presiječe MathJax.
GENERIC_HINT = "Vrati se na prvi korak zadatka i provjeri koje pravilo iz ove lekcije treba primijeniti."

# Uvodne fraze kojima model sam počne odgovor — server ih uklanja da ne bismo
# dobili „Netačno. Nije tačno. Hint: ...“.
_LEADING_VERDICT_RE = re.compile(
    r"^\s*(?:"
    r"neta[čc]no|nije ta[čc]no|pogre[šs]no|nije dobro|nažalost(?: ne)?|"
    r"ta[čc]no|bravo|odli[čc]no|to[čc]no"
    r")\b[\s.,!:—-]*",
    re.IGNORECASE,
)

# „Hint:“ / „Savjet:“ koje je model možda već napisao — skida se da ga server
# doda tačno jednom, u kanonskom obliku.
_LEADING_HINT_LABEL_RE = re.compile(r"^\s*(?:hint|savjet|uputa|pomo[ćc])\s*[:—-]\s*", re.IGNORECASE)

_DOLLAR_SPLIT = re.compile(r"(?<!\\)\$")
_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
_WORD_BOUNDARY_RE = re.compile(r"\s+")

# A small deterministic quality floor for a common non-contract task shape.
# It is keyed by the visible mathematical form, never a lesson ID: a hint for
# a remainder modulo a composite divisor cannot stop at one proper factor and
# pretend that this determines the requested remainder.
_REMAINDER_TASK_RE = re.compile(
    r"ostatak\s+pri\s+dijeljenju\s+(?:broja\s+)?\$?\s*(\d+)\s*\$?\s+sa\s+\$?\s*(\d+)",
    re.IGNORECASE,
)
_DIVISOR_MENTION_RE = re.compile(r"\bsa\s+\$?\s*(\d+)\b", re.IGNORECASE)


def _balanced_math(text):
    """True kad su svi $...$ delimiteri upareni (paran broj neescaped '$')."""
    return (len(_DOLLAR_SPLIT.split(text)) % 2) == 1


def strip_leading_verdict(text):
    """Ukloni modelovu vlastitu ocjenu i „Hint:“ oznaku s početka teksta."""
    cleaned = (text or "").strip()
    previous = None
    # Ponavljamo jer model zna napisati i „Netačno. Nije tačno.“ zaredom.
    while cleaned != previous:
        previous = cleaned
        cleaned = _LEADING_VERDICT_RE.sub("", cleaned, count=1).strip()
    cleaned = _LEADING_HINT_LABEL_RE.sub("", cleaned, count=1).strip()
    return cleaned


def ensure_hint_makes_progress(task_text, hint):
    """Replace a provably insufficient composite-remainder hint.

    This is intentionally narrow. It leaves ordinary hints untouched, but if
    the active task asks for a remainder modulo a composite divisor and the
    proposed hint names only a proper factor (for example, modulo 5 for modulo
    15), the student receives a valid next step for the actual requested
    divisor. No answer is revealed and no model call is made.
    """
    candidate = (hint or "").strip()
    match = _REMAINDER_TASK_RE.search(task_text or "")
    if not candidate or match is None:
        return candidate
    dividend, divisor = (int(match.group(1)), int(match.group(2)))
    if divisor <= 1:
        return candidate
    mentioned = {int(value) for value in _DIVISOR_MENTION_RE.findall(candidate)}
    proper_factor_only = any(1 < value < divisor and divisor % value == 0
                             for value in mentioned)
    if divisor in mentioned or not proper_factor_only:
        return candidate
    return (
        "Za ostatak pri dijeljenju broja $" + str(dividend) + "$ sa $" + str(divisor)
        + "$, pronađi najveći višekratnik broja $" + str(divisor)
        + "$ koji nije veći od $" + str(dividend) + "$, pa ga oduzmi od broja."
    )


def _sentence_boundary_clip(text, limit):
    """Pokušaj 1: najduža cjelovita REČENICA unutar `limit` znakova iza koje su
    $...$ delimiteri uravnoteženi. Vraća None ako takva granica ne postoji."""
    best = None
    for match in _SENTENCE_END_RE.finditer(text):
        end = match.end()
        if end > limit:
            break
        if _balanced_math(text[:end]):
            best = end
    if best is None:
        return None
    return text[:best].strip()


def _word_boundary_clip(text, limit):
    """Pokušaj 2 (kad nema kraja rečenice unutar limita): najdulji prefiks do
    granice RIJEČI unutar `limit` znakova, iza kojeg su $...$ delimiteri
    uravnoteženi (tj. prefiks se nikad ne završava USRED $...$ segmenta).
    Vraća None ako ni ovo nije moguće — poziva se GENERIC_HINT."""
    best = None
    for match in _WORD_BOUNDARY_RE.finditer(text):
        end = match.start()
        if end == 0:
            continue
        if end > limit:
            break
        if _balanced_math(text[:end]):
            best = end
    if best is None:
        return None
    return text[:best].rstrip(" ,;:—-").strip()


def clip_preserving_math(text, limit):
    """Skrati tekst na najviše `limit` znakova bez ikad presijecanja unutar
    $...$. Redoslijed pokušaja:
      1. granica rečenice (najčitljivije)
      2. granica riječi (i dalje čitljivo, ako rečenica ne staje)
      3. None — pozivalac MORA pasti na kraći, unaprijed bezbjedan tekst
         (GENERIC_HINT); ovaj modul namjerno NIKAD ne vraća tekst duži od
         `limit` niti hard-siječe usred $...$.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text if _balanced_math(text) else None

    clipped = _sentence_boundary_clip(text, limit)
    if clipped is not None:
        return clipped

    clipped = _word_boundary_clip(text, limit)
    if clipped is not None:
        return clipped

    return None


def _normalized(text):
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


def _normalize_value(text):
    """Normalizuj kandidat vrijednosti (tačan odgovor/opcija ili ono što je
    hint eksplicitno naveo kao rješenje) radi poređenja bez obzira na $...$,
    razmake, tačku na kraju ili LaTeX zapis broja pi."""
    value = (text or "").strip()
    value = value.strip("$").strip()
    value = value.rstrip(".,!?;: ")
    value = value.replace("\\pi", "π").replace(" ", "")
    return value.lower()


# Vrijednost koju fraza otkriva: SAMO kratki, prepoznatljivi oblici (broj,
# π, jedno slovo opcije, ili kompletan $...$ izraz) — NIKAD generičko "sve do
# kraja rečenice". Ovo je ključno: fraza "Zato je 5 tačan odgovor." mora
# uhvatiti SAMO "5", ne cijeli rep rečenice, jer bi inače poređenje s kratkim
# tačnim odgovorom uvijek promašilo.
_VALUE_TOKEN = r"(?:\$[^$]+\$|-?\d+(?:[.,]\d+)?|\\pi|π|[A-Za-z])"

# Eksplicitne fraze kojima hint OTKRIVA konačan odgovor — svaka hvata
# frazu + KRATKU vrijednost odmah iza nje. Provjera je NAMJERNO uska (fraza
# mora doslovno postojati) da se ne bi lažno okinula na legitimne hintove poput
# „Podijeli obje strane sa 2.“ ili „Provjeri znak nakon dijeljenja sa -2.“ —
# te rečenice ne sadrže nijednu od ovih fraza.
_REVEAL_PHRASE_RE = re.compile(
    r"(?:"
    r"ta[čc]an\s+rezultat\s+je|ta[čc]no\s+rje[šs]enje\s+je|"
    r"rezultat\s+je|rje[šs]enje\s+je|odgovor\s+je|"
    r"ta[čc]no\s+je|to[čc]no\s+je|"
    r"dobije[šs]|dobija[šs]|"
    r"zato\s+je|"
    r"ta[čc]na\s+opcija\s+je|iza?beri\s+opciju"
    # Separator je SAMO dvotačka — crtica se namjerno isključuje jer bi se
    # inače pojela kao "separator" ispred negativnog broja (npr. "je -3"),
    # ostavljajući value token bez predznaka i lažno promašujući poređenje.
    r")\s*:?\s*"
    r"(?P<value>" + _VALUE_TOKEN + r")",
    re.IGNORECASE,
)

# „x = 2“ (ili bilo koje jednoslovno slovo) kad je to CIJELA poenta hinta —
# hvata i „x=2“ bez razmaka. Zahtijeva da odmah IZA vrijednosti slijedi kraj
# rečenice/stringa, ne novi operator (isključuje npr. „a = b + c“ nastavke).
_VARIABLE_EQUALS_RE = re.compile(
    r"(?<![a-zA-Z0-9])[a-zA-Zα-ωΑ-Ω]\s*=\s*(?P<value>-?\d+(?:[.,]\d+)?|π|\\pi|[A-Za-z])"
    r"(?=[.!?\s]|$)"
)


def _explicitly_reveals(hint, *candidates):
    """True ako hint sadrži EKSPLICITNU frazu koja otkriva vrijednost, I ta
    vrijednost se poklapa s jednim od `candidates` (tačna opcija/odgovor).

    Namjerno usko: fraza mora postojati (ne svaki kratak broj) — tako
    „Podijeli nazivnik 8 sa 3.“ nikad ne pada u ovu provjeru (nema fraze), a
    kratki odgovori poput „2“, „-3“, „π“, „A“ i dalje bivaju uhvaćeni KAD ih
    hint eksplicitno predstavi kao rješenje.
    """
    normalized_candidates = {_normalize_value(c) for c in candidates if c}
    normalized_candidates.discard("")
    if not normalized_candidates:
        return False

    for pattern in (_REVEAL_PHRASE_RE, _VARIABLE_EQUALS_RE):
        for match in pattern.finditer(hint):
            value = _normalize_value(match.group("value"))
            if value and value in normalized_candidates:
                return True
    return False


# ---------------------------------------------------------------------------
# SLOJ 3 — VRIJEDNOST UGRAĐENA U RAČUN (živi run postStabilityFixes, B53)
# ---------------------------------------------------------------------------
# Zadatak „Riješi jednačinu: $2(x)=8$“, committed odgovor `$x=4$`. Na pogrešan
# pokušaj tutor je napisao:
#
#     „… Dakle, $x=\frac{8}{2}=4$. Možeš to provjeriti: $2\cdot4=8$.“
#     „… Dakle $x=\frac{8}{2}=4$. Provjera: $2(4)=8$, što je tačno.“
#
# Nijedan postojeći sloj to nije vidio: sloj 1 traži DOSLOVAN niz „x=4“, a
# između `x=` i `4` stoji `\frac{8}{2}`; sloj 2 poredi s kandidatom „x=4“, pa
# gola vrijednost iza fraze („rješenje je 4“, „dobiješ 4“) nikad ne poklapa.
#
# Ovo NIJE CAS i ne rješava nikakvu matematiku. Radi samo ono što se može
# dokazati: kad je committed odgovor GOL BROJ, taj broj ne smije se pojaviti
# kao broj unutar `$…$` segmenta odgovora — jer se tamo može naći isključivo
# ako ga je tutor izračunao ili uvrstio. Brojevi u PROZI se namjerno ne broje
# („Korak 1: …“), a broj koji već stoji u samom zadatku ništa ne dokazuje.
_NUMBER_TOKEN_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def committed_numeric_value(candidate):
    """Gola brojevna vrijednost committed odgovora, ili '' kad je nema.

    `$x=4$` → `4` · `$40$` → `40` · `$\\frac{3}{5}$` → `''` (nije gol broj,
    pa se sloj 3 preskače i vrijede samo doslovni slojevi)."""
    value = _normalize_value(candidate)
    if "=" in value:
        value = value.rsplit("=", 1)[1]
    value = value.strip()
    return value if value and _NUMBER_TOKEN_RE.fullmatch(value) else ""


def _math_numbers(text):
    """Brojevi koji stoje UNUTAR $…$ / $$…$$ segmenata, decimalni zarez → tačka."""
    return {number.replace(",", ".")
            for content in math_contents(tokenize_math(text or ""))
            for number in _NUMBER_TOKEN_RE.findall(content)}


# OFFLINE AUDIT istog runa: prva verzija sloja 3 brojala je SVAKI broj u svakom
# `$…$` segmentu, bez obzira na ulogu, pa je novo blokirala i dva odgovora koja
# su prošla sve provjere (`failed_checks: []`):
#   • B46 „…završava se sa $00$, $25$, $50$ ili $75$“ uz committed `75` —
#     nabrajanje PRAVILA lekcije, ne tvrdnja o rješenju;
#   • B59 „$25\\%=\\frac{25}{100}=\\frac{1}{4}$ … (ne izračunavaj još rezultat)“
#     uz committed `$4$` — četvorka je NAZIVNIK.
# Dvije determinističke razlike vraćaju sloj na ono što se može dokazati:
#   1. broj u STRUKTURNOJ poziciji (eksponent, indeks, argument u vitičastim
#      zagradama) je parametar zapisa, nikad tvrđena vrijednost;
#   2. objavljen rezultat i provjera committed rješenja uvijek stoje u TVRĐENOJ
#      JEDNAKOSTI (`x=\\frac{8}{2}=4`, `2\\cdot4=8`, `2(4)=8`). Segment bez `=`
#      je operacija koju učenik tek treba izvesti (`$8:2$`) — ovaj modul je
#      nikad ne računa.
# B53 ostaje blokiran po obje tačke: četvorka tamo stoji izvan vitičastih
# zagrada, u lancu jednakosti.
_STRUCTURAL_RE = re.compile(r"[\^_]\s*\{[^{}]*\}|[\^_]\s*-?\d+|\{[^{}]*\}")


def _strip_structural_positions(content):
    """Ukloni eksponente, indekse i argumente u vitičastim zagradama."""
    previous = None
    while content != previous:
        previous = content
        content = _STRUCTURAL_RE.sub(" ", content)
    return content


def _asserted_numbers(text):
    """Brojevi koje tekst TVRDI: samo iz math segmenata s jednakošću, i samo
    izvan strukturnih pozicija."""
    numbers = set()
    for content in math_contents(tokenize_math(text or "")):
        if "=" not in content:
            continue
        numbers.update(number.replace(",", ".") for number
                       in _NUMBER_TOKEN_RE.findall(_strip_structural_positions(content)))
    return numbers


def _reveals_value_inside_math(text, task_text, *candidates):
    needles = {committed_numeric_value(candidate) for candidate in candidates}
    needles.discard("")
    if not needles:
        return False
    # Zadatak se čita ŠIRE (svaki broj) — sve što učenik ionako vidi je izuzeto.
    provable = {needle.replace(",", ".") for needle in needles} - _math_numbers(task_text)
    return bool(provable & _asserted_numbers(text))


def _has_numeric_inconsistency(text):
    """True ako hint sadrži DOKAZANO nedosljedan lanac jednakosti
    (matbot/mathcheck.py) — npr. model napiše ispravnu formulu ali pogrešnu
    supstituciju u samom hintu. Tretira se isto kao curenje odgovora: server
    NE odbija cio turn, nego tiho pada na GENERIC_HINT (bez drugog AI poziva)
    da bi ostatak razgovora ("Netačno." + koristan sljedeći korak) i dalje
    stigao do učenika."""
    return bool(find_numeric_inconsistencies(text))


def leaks_answer(text, correct_option_text="", expected_answer="", task_text=""):
    """True ako tekst otkriva tačan odgovor.

    TRI nezavisna sloja:
    1. Doslovno navođenje DUŽE vrijednosti (>=3 znaka nakon normalizacije) bilo
       gdje u tekstu — pokriva izraze poput „$\\frac{9}{24}$“ ili „16/60“ koji
       se rijetko pojave slučajno u legitimnom hintu.
    2. Eksplicitna otkrivajuća fraza (vidi _explicitly_reveals) — pokriva
       KRATKE odgovore („2“, „-3“, „π“, „A“, „x=2“) koje sloj 1 namjerno
       preskače da ne bi blokirao legitimne hintove koji ponavljaju brojeve iz
       samog zadatka (npr. „izračunaj $24:8$“ kad je odgovor „8“). Uz sam
       kandidat poredi se i njegova GOLA vrijednost, pa „rješenje je 4“ i
       „dobiješ 4“ poklapaju committed `$x=4$` (živi nalaz B53).
    3. Vrijednost TVRĐENA u računu unutar `$…$` (živi nalaz B53) — vidi
       `_reveals_value_inside_math`. Traži `task_text` da bi broj koji već
       stoji u zadatku bio izuzet; bez njega se sloj ponaša konzervativnije.
       Broj u operaciji bez jednakosti (`$8:2$`) i broj u strukturnoj poziciji
       (eksponent, indeks, nazivnik) NISU tvrdnja o rješenju.

    `task_text` je OPCIONI dodatak — legacy Practice put koji ga ne prosljeđuje
    ponaša se kao i ranije.
    """
    haystack = _normalized(text)
    if not haystack:
        return False

    for candidate in (correct_option_text, expected_answer):
        needle = _normalize_value(candidate)
        if len(needle) >= 3 and needle in haystack.replace(" ", ""):
            return True

    bare_values = [committed_numeric_value(candidate)
                   for candidate in (correct_option_text, expected_answer)]
    if _explicitly_reveals(text, correct_option_text, expected_answer, *bare_values):
        return True

    return _reveals_value_inside_math(text, task_text, correct_option_text, expected_answer)


def shape_first_wrong_feedback(model_hint, model_reply, correct_option_text="",
                                expected_answer="", limit=None):
    """Sastavi vidljiv odgovor za PRVI pogrešan klik.

    Uvijek počinje tačno s „Netačno.“, zatim jedan sažet hint, i UVIJEK ostaje
    unutar `limit` znakova — ako sigurno skraćivanje (rečenica pa riječ, oboje
    bez sijecanja $...$) nije moguće, koristi se kratak GENERIC_HINT umjesto
    vraćanja predugog originala. Ako model nije dao upotrebljiv hint ili je u
    njemu procurio tačan odgovor, također se koristi GENERIC_HINT — u oba
    slučaja bez drugog AI poziva.
    """
    limit = config.MAX_FIRST_WRONG_FEEDBACK_CHARS if limit is None else limit

    hint = strip_leading_verdict(model_hint) or strip_leading_verdict(model_reply)
    if (not hint or leaks_answer(hint, correct_option_text, expected_answer)
            or _has_numeric_inconsistency(hint)):
        hint = GENERIC_HINT

    # Hint se mjeri BEZ fiksnog prefiksa da granica opisuje stvarni sadržaj.
    budget = max(limit - len(VERDICT_PREFIX) - len(HINT_PREFIX) - 2, 40)  # 2 = prazan red
    clipped = clip_preserving_math(hint, budget)
    if (clipped is None or leaks_answer(clipped, correct_option_text, expected_answer)
            or _has_numeric_inconsistency(clipped or "")):
        clipped = GENERIC_HINT
        if len(clipped) > budget:  # teorijski nemoguće s trenutnim config limitima
            clipped = clipped[:budget].rstrip()

    return f"{VERDICT_PREFIX}\n\n{HINT_PREFIX}{clipped}"


def shape_final_wrong_prefix(model_reply):
    """Drugi pogrešan klik: zadržava se postojeće ponašanje otkrivanja rješenja,
    ali odgovor i dalje POČINJE s „Netačno.“ i bez modelove duple ocjene.
    Tekst se ne skraćuje — tu je puno objašnjenje legitimno."""
    body = strip_leading_verdict(model_reply)
    if not body:
        return VERDICT_PREFIX
    return f"{VERDICT_PREFIX}\n\n{body}"
