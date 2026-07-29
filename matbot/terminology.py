"""Deterministička (ne-AI) normalizacija zabranjenih termina u tekstu VIDLJIVOM
učeniku.

Trenutno pokriva JEDNO pravilo: hrvatski „čimbenik“ se nikad ne smije pojaviti —
u bosanskom školskom registru koristi se „faktor“. Prompt pravilo (rules.py)
modelu KAŽE da koristi „faktor“, ali prompt nije garancija: ovaj modul je
deterministička zaštita izlaza koja se primjenjuje na svaki user-visible tekst
(Practice reply/zadatak/opcije, Explain reply, Quick reply).

Namjerno USKO: mijenjaju se samo padežni oblici imenice „čimbenik“, ništa
drugo. Zamjena se NIKAD ne izvodi unutar $...$ segmenata — tamo se ova riječ
ne pojavljuje legitimno, a diranje matematike bi moglo oštetiti MathJax.
"""
import re

# Padežni oblici imenice „čimbenik“ → odgovarajući oblik imenice „faktor“.
# Dvije osnove: „čimbenik-“ (jednina + genitiv množine) i „čimbenic-“ (množina,
# palatalizacija k→c). Duži nastavci moraju biti PRIJE kraćih u alternaciji da
# regex ne odsiječe npr. „čimbenicima“ na „čimbenici“ + „ma“.
_SUFFIX_MAP = {
    # osnova „čimbenic“ (množina)
    "ima": "faktorima",
    "i": "faktori",
    "e": "faktore",
    # osnova „čimbenik“ (jednina)
    "": "faktor",
    "a": "faktora",
    "u": "faktoru",
    "om": "faktorom",
}

_CIMBENIK_RE = re.compile(
    r"čimbenic(ima|i|e)"
    r"|čimbenik(om|a|u)?",
    re.IGNORECASE,
)

_DOLLAR_SPLIT = re.compile(r"(?<!\\)\$")


def _match_capitalization(source: str, replacement: str) -> str:
    """Zadrži oblik pisanja originala tamo gdje je to smisleno: SVE VELIKO →
    SVE VELIKO, Prvo Veliko → Prvo veliko, inače malo."""
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _replace_in_plain_text(text: str) -> str:
    def _sub(match):
        whole = match.group(0)
        # Tačno jedna od dvije grupe je popunjena (druga je None); prazan
        # nastavak jednine ("čimbenik") daje None → normalizuj u "".
        suffix = (match.group(1) if match.group(1) is not None else match.group(2)) or ""
        replacement = _SUFFIX_MAP.get(suffix.lower(), "faktor")
        return _match_capitalization(whole, replacement)

    return _CIMBENIK_RE.sub(_sub, text)


def normalize_terminology(text: str) -> str:
    """Vrati tekst sa zabranjenim terminima zamijenjenim dozvoljenima.

    Zamjena se izvodi ISKLJUČIVO izvan $...$ segmenata — matematički zapis
    ostaje bajt-identičan, pa MathJax ne može biti oštećen ovom normalizacijom.
    """
    if not text:
        return text or ""
    if "čimbeni" not in text.lower():
        return text  # brzi izlaz: ništa za mijenjati

    if "$" not in text:
        return _replace_in_plain_text(text)

    parts = _DOLLAR_SPLIT.split(text)
    for i in range(0, len(parts), 2):  # samo NE-math dijelovi (parni indeksi)
        parts[i] = _replace_in_plain_text(parts[i])
    return "$".join(parts)


def contains_forbidden_term(text: str) -> bool:
    """True ako tekst SADRŽI bilo koji oblik zabranjenog termina (case-insensitive).
    Koristi se u testovima i kao dijagnostika — ne u produkcijskom putu."""
    return bool(_CIMBENIK_RE.search(text or ""))
