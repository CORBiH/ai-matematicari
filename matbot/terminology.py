"""Deterministička (ne-AI) normalizacija zabranjenih termina u tekstu VIDLJIVOM
učeniku.

Pokriva SEDAM od osam zabranjenih termina navedenih u matbot/rules.py
(_LANGUAGE_RULES): hrvatski „čimbenik“ (→ „faktor“), „kutomer“ (→ „uglomjer“),
„jednakokračni“ (→ „jednakokraki“), „zbroj“ (→ „zbir“), „potenciranje“
(→ „stepenovanje“) i — od kampanje od 35 poziva — „trokut“ (→ „trougao“) i
„točan“ (→ „tačan“). Prompt pravilo (rules.py) modelu KAŽE da koristi ispravne
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

Namjerno USKO za pokrivenih sedam: mijenjaju se SAMO padežni/rodni oblici tih
tačno sedam riječi, ništa drugo. Zamjena se NIKAD ne izvodi unutar matematičkih
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
# 6) TROKUT → TROUGAO (živi nalaz D35-3, poziv 20 kampanje od 35)
# ---------------------------------------------------------------------------
# Model je u ispravnom odgovoru o zbiru uglova napisao „svaki trokut ima
# unutrašnji zbir 180°“ — hrvatski oblik usred bosanskog teksta. Prompt pravilo
# ga nije spriječilo, pa ide u determinističku normalizaciju kao i ostali.
#
# Deklinacija se NE poklapa nastavak-za-nastavak: „trokut“ ima pravilnu
# o-osnovu, a „trougao“ ima nepostojano „a“ (trougao → trougla, trouglu) i
# množinu na „-ovi“. Zato eksplicitna mapa. Negativan lookahead na slovo štiti
# izvedenice koje NISU ista riječ (npr. „trokutni“) — njih radije ne diramo
# nego da napravimo „trougaoni“.
_TROKUT_SUFFIX_MAP = {
    "": "trougao",
    "a": "trougla",
    "u": "trouglu",
    "om": "trouglom",
    "i": "trouglovi",
    "e": "trouglove",
    "ima": "trouglovima",
    "ovi": "trouglovi",
    "ova": "trouglova",
    "ove": "trouglove",
    "ovima": "trouglovima",
}
_TROKUT_RE = re.compile(
    r"trokut(ovima|ovi|ova|ove|ima|om|a|u|e|i)?(?![A-Za-zčćžšđČĆŽŠĐ])",
    re.IGNORECASE,
)


def _trokut_suffix(match):
    return match.group(1) or ""


# ---------------------------------------------------------------------------
# 7) TOČAN → TAČAN (živi nalaz D35-2/D35-3, pozivi 19 i 20)
# ---------------------------------------------------------------------------
# Razlika je ISKLJUČIVO u samoglasniku osnove (toč- → tač-); svi nastavci su
# identični, pa se prenose nepromijenjeni. Nastavak MORA biti „an“ (nepostojano
# a: točan) ili počinjati na „n“ (točna, točno, točni, točnost) — to je ono što
# ovo pravilo drži uskim: „točka“ i „točak“ (sasvim druge riječi) ne prolaze, a
# granica riječi na početku štiti „potočni“ i slično.
_TOCAN_RE = re.compile(r"\btoč(an|n[A-Za-zčćžšđČĆŽŠĐ]*)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 8) KUT → UGAO (živi nalaz kampanje od 14 poziva, poziv 5)
# ---------------------------------------------------------------------------
# Objašnjenje zbira uglova trougla je bilo tačno, ali je koristilo hrvatsko
# „kut/kutovi/kuta“ umjesto bosanskog „ugao/uglovi/ugla“.
#
# Ovo je NAJUŽE moguće pravilo u modulu, jer je „kut“ kratak niz koji se
# pojavljuje unutar sasvim drugih riječi: „kutija“, „kutak“, „skuter“, pa i
# „kutomer“ (koji ima SVOJE pravilo iznad i izvršava se PRIJE ovog). Zato se
# traži granica riječi i TAČNO poznat nastavak — ništa se ne pogađa. „Kutija“ i
# „kutak“ nemaju nijedan od ovih nastavaka i nikad se ne diraju.
_KUT_SUFFIX_MAP = {
    "": "ugao",
    "a": "ugla",
    "u": "uglu",
    "om": "uglom",
    "ovi": "uglovi",
    "ova": "uglova",
    "ove": "uglove",
    "ovima": "uglovima",
}
_KUT_RE = re.compile(
    r"\bkut(ovima|ovi|ova|ove|om|a|u)?(?![A-Za-zčćžšđČĆŽŠĐ])",
    re.IGNORECASE,
)


def _kut_suffix(match):
    return match.group(1) or ""


# ---------------------------------------------------------------------------
# 9) PRESEK → PRESJEK (živi nalaz Live96, poziv 551)
# ---------------------------------------------------------------------------
# U objavljenom zadatku o skupovima model je napisao ekavsko „presek“ umjesto
# ijekavskog „presjek“. Ista struktura kao „čimbenik“: dvije osnove — „presek-“
# (jednina) i „presec-“ (množina, palatalizacija k→c) — s eksplicitnom mapom
# nastavaka. Granica riječi na početku i lookahead na kraju drže pravilo uskim
# (izvedenice se ne pogađaju).
_PRESEK_SUFFIX_MAP = {
    # osnova „presec“ (množina)
    "ima": "presjecima",
    "i": "presjeci",
    "e": "presjeke",
    # osnova „presek“ (jednina)
    "": "presjek",
    "a": "presjeka",
    "u": "presjeku",
    "om": "presjekom",
}
_PRESEK_RE = re.compile(
    r"\bpresec(ima|i|e)(?![A-Za-zčćžšđČĆŽŠĐ])"
    r"|\bpresek(om|a|u)?(?![A-Za-zčćžšđČĆŽŠĐ])",
    re.IGNORECASE,
)


def _presek_suffix(match):
    return (match.group(1) if match.group(1) is not None else match.group(2)) or ""


# ---------------------------------------------------------------------------
# 10) IMENILAC → NAZIVNIK (audit ovlašćenja pravila, odluka K — SAMO
#     normalizacija, NE zabranjen termin)
# ---------------------------------------------------------------------------
# Kanonski termin projekta je „nazivnik“ (rules.py, obavezni termini), a audit
# je našao ~120 determinističkih instrukcijskih stringova s „imenilac“. Par se
# normalizuje u SVEM proizvedenom tekstu, ali NAMJERNO NIJE u `_RULES` (dakle
# ni u `contains_forbidden_term`): kanonski KURIKULARNI podaci
# (data/topics.json) sami sadrže „imenilaca“ u zvaničnim naslovima lekcija
# („Sabiranje i oduzimanje razlomaka jednakih imenilaca“), pa bi tretiranje
# termina kao zabranjenog oborilo repo-sken nad podacima koje ne smijemo
# prepravljati — kurikulum je autoritet za NASLOVE, a projekat za VLASTITU
# prozu. Dvije osnove: „imenilac/imenilaca“ (nepostojano a) i „imenioc-“.
_IMENILAC_SUFFIX_MAP = {
    # osnova „imenioc“
    "ima": "nazivnicima",
    "em": "nazivnikom",
    "i": "nazivnici",
    "e": "nazivnike",
    "u": "nazivniku",
    # zajedničko: imenioca/imenilaca → nazivnika
    "a": "nazivnika",
    # osnova „imenilac“
    "": "nazivnik",
}
_IMENILAC_RE = re.compile(
    r"\bimenilac(a)?(?![A-Za-zčćžšđČĆŽŠĐ])"
    r"|\bimenioc(ima|em|e|i|a|u)(?![A-Za-zčćžšđČĆŽŠĐ])",
    re.IGNORECASE,
)


def _imenilac_suffix(match):
    return (match.group(1) if match.group(1) is not None else match.group(2)) or ""


# ---------------------------------------------------------------------------
# 11) ČINILAC → FAKTOR (živi QA nalaz, direktor škole — SAMO normalizacija,
#     NE zabranjen termin)
# ---------------------------------------------------------------------------
# KURIKULARNI DOKAZ (reference/curriculum/semantics/MATBOT_Faza1_KS_RS_NPP):
# u KS_2018 (bosanski plan koji projekat prati) „faktor“ se pojavljuje 56 puta
# u punom tekstu stranica i 13 puta u stavkama ishoda — a „činilac“ NIJEDNOM.
# „Činilac“ dolazi isključivo iz RS_2014 (8 pojava, npr. RS_2014-0104
# „Rastavljanje polinoma na činioce“). Kanonski termin projekta je zato
# „faktor“ (rules.py, obavezni termini).
#
# Kao i kod para imenilac/nazivnik, ovo je NORMALIZACIJA, ne zabrana: riječ je
# potpuno legitimna u srpskom registru i doslovno stoji u kurikularnom izvoru,
# pa je `contains_forbidden_term` NE smije prijavljivati — inače bi repo-sken
# pao nad podacima koje ne prepravljamo. Deklinacija je ista kao kod
# „imenilac“ (nepostojano a): osnove „činilac/činilaca“ i „činioc-“.
_CINILAC_SUFFIX_MAP = {
    # osnova „činioc“
    "ima": "faktorima",
    "em": "faktorom",
    "i": "faktori",
    "e": "faktore",
    "u": "faktoru",
    # zajedničko: činioca/činilaca → faktora
    "a": "faktora",
    # osnova „činilac“
    "": "faktor",
}
_CINILAC_RE = re.compile(
    r"\bčinilac(a)?(?![A-Za-zčćžšđČĆŽŠĐ])"
    r"|\bčinioc(ima|em|e|i|a|u)(?![A-Za-zčćžšđČĆŽŠĐ])",
    re.IGNORECASE,
)


def _cinilac_suffix(match):
    return (match.group(1) if match.group(1) is not None else match.group(2)) or ""


# ---------------------------------------------------------------------------
# ZAŠTITA SANKCIONISANOG DVOJNOG NAZIVA
# ---------------------------------------------------------------------------
# Par „nazivnik (imenilac)“ je KURIKULARNO SANKCIONISAN: KS_2018 ga u sheetu
# `Terminologija` vodi kao eksplicitni alias (str. 8), a KS_2018-0046 nabraja
# „brojilac (brojnik), imenilac (nazivnik)“. Koristi se pri UVOĐENJU pojma.
# Bez ove zaštite normalizacija bi ga pretvorila u „nazivnik (nazivnik)“ —
# tačno onaj pokvareni oblik zbog kojeg se slijepa zamjena i zabranjuje.
#
# DVA OBLIKA ISTOG PARA (živi nalaz, Edin-feedback): zaštita je prvo pokrivala
# SAMO zagradu, a kanonski KURIKULARNI naslovi par pišu KOSOM CRTOM:
#
#     „Pojam razlomka, brojnik/brojilac i nazivnik/imenilac“
#     „Svođenje razlomaka na zajednički nazivnik/imenilac“
#
# pa je normalizacija baš te naslove pretvarala u „nazivnik/nazivnik“ — isti
# pokvareni oblik zbog kojeg zaštita i postoji, samo s drugim razdvojnikom.
# Zato razdvojnik ulazi u obrazac, a ne novi spisak fraza.
#
# ISTI PAR ZA FAKTOR (terminološka direktiva: „FAKTOR (ČINILAC)“): kurikularni
# dokaz ispod i dalje stoji — „činilac“ nema nijednu pojavu u KS_2018 — pa GOLI
# „činilac“ i dalje postaje „faktor“ (prihvatanje ulaza → kanonski izlaz). Ali
# kad je dvojni naziv NAMJERNO napisan uz vlastiti kanonski termin, on je uvod
# pojma, a ne greška, i ne smije se sažeti u „faktor (faktor)“.
_ALIAS_PAIR_SEPARATOR = r"(?:\(|/)"
_ALIAS_PAIR_PREFIX_RE = re.compile(
    rf"nazivnik\s*{_ALIAS_PAIR_SEPARATOR}\s*$", re.IGNORECASE)
_FAKTOR_ALIAS_PAIR_PREFIX_RE = re.compile(
    rf"faktor\s*{_ALIAS_PAIR_SEPARATOR}\s*$", re.IGNORECASE)


def _protect_alias_pair(match) -> bool:
    """True kad je pogodak drugi član sankcionisanog para — ostavi ga."""
    return bool(_ALIAS_PAIR_PREFIX_RE.search(match.string[:match.start()]))


def _protect_faktor_alias_pair(match) -> bool:
    """True kad „činilac“ stoji kao dvojni naziv uz vlastiti kanonski termin."""
    return bool(_FAKTOR_ALIAS_PAIR_PREFIX_RE.search(match.string[:match.start()]))


# ---------------------------------------------------------------------------
# Zajednička primjena — svako pravilo je (regex, suffix_extractor, suffix_map
# | None). suffix_map=None znači "osnova + isti nastavak kao izvor" (potpuno
# dijeljena deklinacija, npr. potenciranje/stepenovanje).
# ---------------------------------------------------------------------------
_RULES = (
    (_CIMBENIK_RE, _cimbenik_suffix, _CIMBENIK_SUFFIX_MAP, "faktor", None),
    (_KUTOMER_RE, _kutomer_suffix, _KUTOMER_SUFFIX_MAP, "uglomjer", None),
    (_JEDNAKOKRACNI_RE, _jednakokracni_suffix, _JEDNAKOKRACNI_SUFFIX_MAP, "jednakokraki", None),
    (_ZBROJ_RE, _zbroj_suffix, _ZBROJ_SUFFIX_MAP, "zbir", None),
    (_POTENCIRANJE_RE, lambda m: m.group(1), None, "stepenovanj", None),
    (_TROKUT_RE, _trokut_suffix, _TROKUT_SUFFIX_MAP, "trougao", None),
    (_TOCAN_RE, lambda m: m.group(1), None, "tač", None),
    # MORA ostati POSLIJE _KUTOMER_RE: „kutomer“ se prvo pretvori u „uglomjer“,
    # pa ovo pravilo nema šta da dohvati unutar njega.
    (_KUT_RE, _kut_suffix, _KUT_SUFFIX_MAP, "ugao", None),
    (_PRESEK_RE, _presek_suffix, _PRESEK_SUFFIX_MAP, "presjek", None),
)

# Pravila koja se PRIMJENJUJU pri normalizaciji, ali NE čine termin
# „zabranjenim“ za repo-sken (`contains_forbidden_term`) — vidi obrazloženje
# uz IMENILAC iznad (kurikularni naslovi legitimno sadrže izvornu riječ).
_NORMALIZE_ONLY_RULES = (
    (_IMENILAC_RE, _imenilac_suffix, _IMENILAC_SUFFIX_MAP, "nazivnik",
     _protect_alias_pair),
    (_CINILAC_RE, _cinilac_suffix, _CINILAC_SUFFIX_MAP, "faktor",
     _protect_faktor_alias_pair),
)

_ALL_REPLACEMENT_RULES = _RULES + _NORMALIZE_ONLY_RULES

# Brzi predizlaz: bilo koja od ovih podniski MORA biti prisutna (case-fold)
# da bi ijedno pravilo uopšte imalo šansu da se poklopi — izbjegava 7 regex
# pretraga na svakom pozivu kad ništa od ovoga nije u tekstu.
_TRIGGER_SUBSTRINGS = (
    "čimbeni", "kut", "jednakokrač", "zbroj", "potenciranj", "trokut", "toč",
    "presek", "presec",
)

# Okidači za CIJELU normalizaciju (uklj. normalize-only parove).
_ALL_TRIGGER_SUBSTRINGS = _TRIGGER_SUBSTRINGS + ("imenil", "imenioc",
                                                  "činil", "činioc")


def _match_capitalization(source: str, replacement: str) -> str:
    """Zadrži oblik pisanja originala tamo gdje je to smisleno: SVE VELIKO →
    SVE VELIKO, Prvo Veliko → Prvo veliko, inače malo."""
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _replace_in_plain_text(text: str) -> str:
    for pattern, suffix_of, suffix_map, base_replacement, protect in _ALL_REPLACEMENT_RULES:
        if not pattern.search(text):
            continue

        def _sub(match, suffix_of=suffix_of, suffix_map=suffix_map,
                 base_replacement=base_replacement, protect=protect):
            whole = match.group(0)
            if protect is not None and protect(match):
                return whole
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
    if not any(trigger in lowered for trigger in _ALL_TRIGGER_SUBSTRINGS):
        return text  # brzi izlaz: ništa za mijenjati

    if "$" not in text:
        return _replace_in_plain_text(text)

    return map_text_segments(text, _replace_in_plain_text)


def contains_forbidden_term(text: str) -> bool:
    """True ako tekst SADRŽI bilo koji oblik BILO KOJEG od sedam pokrivenih
    zabranjenih termina (case-insensitive). Koristi se u testovima i kao
    dijagnostika — ne u produkcijskom putu."""
    haystack = text or ""
    return any(pattern.search(haystack) for pattern, *_ in _RULES)
