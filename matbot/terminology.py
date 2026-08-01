"""Deterministička (ne-AI) normalizacija zabranjenih termina u tekstu VIDLJIVOM
učeniku.

Pokriva PET od šest zabranjenih termina navedenih u matbot/rules.py
(_LANGUAGE_RULES): hrvatski „čimbenik“ (→ „faktor“), „kutomer“ (→ „uglomjer“),
„jednakokračni“ (→ „jednakokraki“), „zbroj“ (→ „zbir“) i „potenciranje“
(→ „stepenovanje“). Prompt pravilo (rules.py) modelu KAŽE da koristi ispravne
termine, ali prompt nije garancija: ovaj modul je deterministička zaštita
izlaza koja se primjenjuje na svaki user-visible tekst (Practice reply/
zadatak/opcije, Explain reply, Quick reply).

ŠESTI zabranjeni termin — „suma“ — NAMJERNO NIJE ovdje pokriven (živi nalaz,
Faza E audita, docs/CURRENT_STATE.md C-8): pravilo u rules.py ga zabranjuje
SAMO „za osnovnoškolski zbir“, ne uopšteno — riječ „suma“ ima potpuno
legitimnu, čestu upotrebu u tekstualnim zadacima koja NEMA veze s matematičkim
zbirom (npr. „Suma od 200 KM podijeljena je na tri dijela.“ — ovdje „suma“
znači „iznos novca“, i zamjena u „Zbir od 200 KM...“ bi bila POGREŠNA i
zvučala bi neprirodno). Bez semantičkog razumijevanja rečenice, deterministička
regex zamjena ne može pouzdano razlikovati ta dva značenja — širenje na „suma“
bi riskiralo kvarenje ispravnog teksta zadatka, što je gore od ostavljanja ovog
jednog termina prompt-only. Ostaje dokumentovan, neriješen rizik (C-8).

Namjerno USKO za preostalih pet: mijenjaju se SAMO padežni/rodni oblici tih
tačno pet riječi, ništa drugo. Zamjena se NIKAD ne izvodi unutar matematičkih
segmenata ($...$ ILI $$...$$) — tamo se ove riječi ne pojavljuju legitimno, a
diranje matematike bi moglo oštetiti MathJax. Razdvajanje teksta ide preko
zajedničkog tokenizatora (matbot/mathsegments.py) — stariji naivni
alternating-split na svaki pojedinačan '$' je parirao pogrešno čim bi se
pojavio par susjednih '$$'.
"""
import re

from matbot.mathsegments import map_text_segments

# ---------------------------------------------------------------------------
# 1) ČIMBENIK → FAKTOR
# ---------------------------------------------------------------------------
# Padežni oblici imenice „čimbenik“ → odgovarajući oblik imenice „faktor“.
# Dvije osnove: „čimbenik-“ (jednina + genitiv množine) i „čimbenic-“ (množina,
# palatalizacija k→c). Duži nastavci moraju biti PRIJE kraćih u alternaciji da
# regex ne odsiječe npr. „čimbenicima“ na „čimbenici“ + „ma“.
_CIMBENIK_SUFFIX_MAP = {
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


def _cimbenik_suffix(match):
    # Tačno jedna od dvije grupe je popunjena (druga je None); prazan
    # nastavak jednine ("čimbenik") daje None → normalizuj u "".
    return (match.group(1) if match.group(1) is not None else match.group(2)) or ""


# ---------------------------------------------------------------------------
# 2) KUTOMER → UGLOMJER
# ---------------------------------------------------------------------------
# „kutomer“ završava na tvrd suglasnik „r“ (bez palatalizacije, za razliku od
# „čimbenik“), pa ista sekvenca nastavaka važi za oba oblika (izvor i cilj) —
# jednostavno mapiranje nastavak-po-nastavak.
_KUTOMER_SUFFIX_MAP = {
    "": "uglomjer",
    "a": "uglomjera",
    "u": "uglomjeru",
    "om": "uglomjerom",
    "i": "uglomjeri",
    "e": "uglomjere",
    "ima": "uglomjerima",
}
_KUTOMER_RE = re.compile(r"kutomer(ima|om|e|i|a|u)?", re.IGNORECASE)


def _kutomer_suffix(match):
    return match.group(1) or ""


# ---------------------------------------------------------------------------
# 3) JEDNAKOKRAČNI → JEDNAKOKRAKI (pridjev, "isosceles")
# ---------------------------------------------------------------------------
# Pogrešan (hrvatski) oblik gradi se na osnovi "jednakokrač-" + nastavci
# "-ni/-na/-no/-nog/-nom/-nim/-nih" (an-pridjev); ispravan oblik je
# "jednakokrak-" + "-i/-a/-o/-og/-om/-im/-ih" (i-pridjev) — razlika je UVIJEK
# tačno u vodećem "n" nastavka, pa se cijeli nastavak zajedno s njim mapira.
# Duži nastavci ("nih"/"nim"/"nog"/"nom") MORAJU biti prije kraćih ("ni"/"na"/
# "no") u alternaciji jer ih ovi kraći prefiksiraju.
_JEDNAKOKRACNI_SUFFIX_MAP = {
    "nih": "jednakokrakih", "nim": "jednakokrakim",
    "nog": "jednakokrakog", "nom": "jednakokrakom",
    "ni": "jednakokraki", "na": "jednakokraka", "no": "jednakokrako",
}
_JEDNAKOKRACNI_RE = re.compile(
    r"jednakokrač(nih|nim|nog|nom|ni|na|no)", re.IGNORECASE
)


def _jednakokracni_suffix(match):
    return match.group(1)


# ---------------------------------------------------------------------------
# 4) ZBROJ → ZBIR
# ---------------------------------------------------------------------------
# „zbroj“ završava na „j“ (mek suglasnik → instrumental „-em“: „zbrojem“), a
# „zbir“ na „r“ (tvrd → instrumental „-om“: „zbirom“) — nastavci se RAZLIKUJU
# između izvora i cilja, pa (za razliku od kutomera) treba eksplicitna mapa
# nastavak-po-nastavak, ne dijeljen nastavak. Množina („zbrojevi“ i sl.) je
# rijetka u ovom kontekstu, ali pokrivena radi potpunosti.
_ZBROJ_SUFFIX_MAP = {
    "": "zbir",
    "a": "zbira",
    "u": "zbiru",
    "em": "zbirom",
    "evi": "zbirovi",
    "eva": "zbirova",
    "evima": "zbirovima",
}
_ZBROJ_RE = re.compile(r"zbroj(evima|evi|eva|em|a|u)?", re.IGNORECASE)


def _zbroj_suffix(match):
    return match.group(1) or ""


# ---------------------------------------------------------------------------
# 5) POTENCIRANJE → STEPENOVANJE (glagolska imenica, srednji rod)
# ---------------------------------------------------------------------------
# Oba oblika su regularne imenice na "-nje" s IDENTIČNIM nastavcima
# (-e/-a/-u/-em/-ima) — čista zamjena osnove, nastavak se prenosi nepromijenjen.
_POTENCIRANJE_RE = re.compile(r"potenciranj(ima|em|e|a|u)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Zajednička primjena — svako pravilo je (regex, suffix_extractor, suffix_map
# | None). suffix_map=None znači "osnova + isti nastavak kao izvor" (potpuno
# dijeljena deklinacija, npr. potenciranje/stepenovanje).
# ---------------------------------------------------------------------------
_RULES = (
    (_CIMBENIK_RE, _cimbenik_suffix, _CIMBENIK_SUFFIX_MAP, "faktor"),
    (_KUTOMER_RE, _kutomer_suffix, _KUTOMER_SUFFIX_MAP, "uglomjer"),
    (_JEDNAKOKRACNI_RE, _jednakokracni_suffix, _JEDNAKOKRACNI_SUFFIX_MAP, "jednakokraki"),
    (_ZBROJ_RE, _zbroj_suffix, _ZBROJ_SUFFIX_MAP, "zbir"),
    (_POTENCIRANJE_RE, lambda m: m.group(1), None, "stepenovanj"),
)

# Brzi predizlaz: bilo koja od ovih podniski MORA biti prisutna (case-fold)
# da bi ijedno pravilo uopšte imalo šansu da se poklopi — izbjegava 5 regex
# pretraga na svakom pozivu kad ništa od ovoga nije u tekstu.
_TRIGGER_SUBSTRINGS = ("čimbeni", "kutomer", "jednakokrač", "zbroj", "potenciranj")


def _match_capitalization(source: str, replacement: str) -> str:
    """Zadrži oblik pisanja originala tamo gdje je to smisleno: SVE VELIKO →
    SVE VELIKO, Prvo Veliko → Prvo veliko, inače malo."""
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _replace_in_plain_text(text: str) -> str:
    for pattern, suffix_of, suffix_map, base_replacement in _RULES:
        if not pattern.search(text):
            continue

        def _sub(match, suffix_of=suffix_of, suffix_map=suffix_map, base_replacement=base_replacement):
            whole = match.group(0)
            suffix = suffix_of(match)
            if suffix_map is None:
                replacement = base_replacement + suffix
            else:
                replacement = suffix_map.get(suffix.lower(), base_replacement)
            return _match_capitalization(whole, replacement)

        text = pattern.sub(_sub, text)
    return text


def normalize_terminology(text: str) -> str:
    """Vrati tekst sa zabranjenim terminima zamijenjenim dozvoljenima.

    Zamjena se izvodi ISKLJUČIVO van matematičkih segmenata ($...$ i
    $$...$$) — matematički zapis ostaje bajt-identičan, pa MathJax ne može
    biti oštećen ovom normalizacijom.
    """
    if not text:
        return text or ""
    lowered = text.lower()
    if not any(trigger in lowered for trigger in _TRIGGER_SUBSTRINGS):
        return text  # brzi izlaz: ništa za mijenjati

    if "$" not in text:
        return _replace_in_plain_text(text)

    return map_text_segments(text, _replace_in_plain_text)


def contains_forbidden_term(text: str) -> bool:
    """True ako tekst SADRŽI bilo koji oblik BILO KOJEG od pet pokrivenih
    zabranjenih termina (case-insensitive). Koristi se u testovima i kao
    dijagnostika — ne u produkcijskom putu."""
    haystack = text or ""
    return any(pattern.search(haystack) for pattern, *_ in _RULES)
