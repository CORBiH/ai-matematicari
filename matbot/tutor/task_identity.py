"""Serverski izveden KANONSKI identitet objavljenog zadatka.

ZAŠTO POSTOJI (produkcijski nalaz, lekcija o pravilima djeljivosti):

    objavljeno : „Koji od sljedećih brojeva je djeljiv sa 25?“ · 322 390 349 375
    učenik     : klikne 390 → crveno, ocijenjeno netačno (sve ispravno)
    učenik     : „Daj mi novi zadatak.“
    objavljeno : ISTI zadatak, ISTE četiri opcije — a jednom i uz uvod
                 „Evo lakšeg zadatka.“

Zaštita od ponavljanja je do tada poredila `task.task_signature` — strukturu
koju MODEL sam deklariše o sebi. Kad model za vizuelno identičan zadatak
deklariše makar malo drugačije `normalized_parameters`, digest se razlikuje i
duplikat prođe. Server nikad nije poredio ono što učenik STVARNO VIDI.

Ovdje se identitet računa ISKLJUČIVO iz vidljivog paketa — teksta zadatka i
vrijednosti opcija — pa nijedna modelova deklaracija ne može ga promijeniti.
Isti potpis ide i u browser, da UI stanje može biti vezano za identitet
zadatka, a ne za njegov tekst.

ŠTA HVATA:
  • doslovno isti tekst i iste opcije,
  • promijenjen REDOSLIJED opcija (opcije se sortiraju),
  • promijenjene ID-jeve opcija uz iste vrijednosti (ID-jevi nisu dio potpisa),
  • kozmetiku: veličinu slova, višestruke razmake, `$…$`, LaTeX razmake,
    navodnike i završnu interpunkciju,
  • promijenjen uvodni tekst odgovora (uvod nije dio teksta zadatka).

ŠTA NAMJERNO NE TVRDI: puni parafrazni identitet. „Koji broj je djeljiv sa 25?“
i „Odaberi broj djeljiv sa 25.“ su za čovjeka isti zadatak, a ovdje nisu isti
potpis. Dokazivanje toga traži semantiku koju server nema — a guard koji ne
može dokazati mora skipovati, ne nagađati (CLAUDE.md).
"""
import hashlib
import json
import re
import unicodedata

# Kozmetika koja NIKAD ne mijenja matematiku zadatka.
_MATH_DELIMITERS_RE = re.compile(r"\$+")
_LATEX_SPACING_RE = re.compile(r"\\[,;:!]|\\quad|\\qquad|\\ ")
_WHITESPACE_RE = re.compile(r"\s+")
# Interpunkcija i navodnici koji ne nose vrijednost. Znakovi koji MOGU nositi
# matematiku (cifre, slova, + - * / = < > ^ _ { } ( ) , .) se NE diraju.
_COSMETIC_RE = re.compile(r"[„“”\"'`]")


def normalize_fragment(text):
    """Kanonski oblik jednog vidljivog fragmenta (pitanje ili opcija)."""
    body = unicodedata.normalize("NFKC", text or "")
    body = _MATH_DELIMITERS_RE.sub(" ", body)
    body = _LATEX_SPACING_RE.sub(" ", body)
    body = _COSMETIC_RE.sub("", body)
    body = _WHITESPACE_RE.sub(" ", body).strip()
    # Završna interpunkcija ne razlikuje zadatke („…sa 25?“ vs „…sa 25 ?“).
    body = re.sub(r"\s*([?!.:;])\s*$", r"\1", body)
    return body.casefold()


def canonical_parts(task_text, option_texts):
    """Kanonski dijelovi potpisa — čitljivo, za dijagnostiku i testove."""
    options = sorted(normalize_fragment(option) for option in (option_texts or ()))
    return {"question": normalize_fragment(task_text), "options": options}


def canonical_signature(task_text, option_texts):
    """Stabilan sha256 identitet vidljivog paketa. Prazan kad nema teksta."""
    parts = canonical_parts(task_text, option_texts)
    if not parts["question"]:
        return ""
    encoded = json.dumps(parts, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_same_task(first_signature, second_signature):
    """True samo kad su OBA potpisa poznata i jednaka.

    Nepoznat potpis nikad nije dokaz jednakosti — isti princip kao svaki drugi
    verifikator u projektu."""
    return bool(first_signature) and first_signature == second_signature


# ---------------------------------------------------------------------------
# STRUKTURNI OTISAK — „isti zadatak s drugim brojevima“ (zahtjev iz produkcije)
# ---------------------------------------------------------------------------
# `canonical_signature` iznad namjerno hvata samo DOSLOVNO isti paket. Učenik
# međutim ne doživljava kao nov zadatak ni ovo:
#
#     „Ana ima 12 jabuka…“   →   „Haris ima 19 jabuka…“
#     „Koliko je 12 + 7?“    →   „Koliko je 45 + 8?“
#
# Zato postoji DRUGI, grublji otisak: iz vidljivog teksta se maskiraju brojevi i
# vlastita imena, pa ostaje REČENIČNA STRUKTURA zadatka. Dva zadatka istog
# strukturnog otiska su za učenika ista vježba.
#
# GRANICA: ovo NIJE mjera semantičke istosti i ne koristi se za odbijanje
# TAČNOSTI — samo za raznolikost. Nepoznat otisak nikad ne znači „isto“.
_NUMBER_TOKEN_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_LATEX_COMMAND_RE = re.compile(r"\[a-zA-Z]+")
# Vlastito ime: veliko slovo usred rečenice (ne prva riječ, ne iza tačke).
_PROPER_NOUN_RE = re.compile(r"(?<=[a-zšđčćž,])\s+([A-ZŠĐČĆŽ][a-zšđčćž]{2,})")
# Riječ kojom zadatak POČINJE, a nije ime: imperativ ili upitna riječ. Sve
# ostalo na početku rečenice tretira se kao vlastito ime („Ana ima…“ vs
# „Haris ima…“ je ista vježba). Popis je zatvoren i služi SAMO raznolikosti.
# Zamjenice koje se mijenjaju ZAJEDNO s imenom („Ana … joj“ / „Haris … mu“).
# Zatvoren popis; „je“ se NAMJERNO izostavlja jer je i pomoćni glagol.
_PRONOUN_RE = re.compile(
    r"\b(joj|mu|im|ju|ga|ih|njoj|njemu|njima|njega|nje)\b", re.IGNORECASE)
_TASK_STARTERS = frozenset("""
koliko koji koja koje kojih kojeg izračunaj izracunaj odredi zapiši zapisi
skrati proširi prosiri riješi rijesi nađi nadji prikaži prikazi izaberi
odaberi dopuni poredaj uporedi pretvori razlomak broj ako za sve dat data
dato date koliki kolika koliko čemu cemu šta sta gdje kada zaokruži zaokruzi
provjeri nacrtaj konstruiši konstruisi dokaži dokazi napiši napisi
""".split())


def structural_fragment(text):
    """Rečenična struktura fragmenta: bez brojeva i bez vlastitih imena."""
    body = unicodedata.normalize("NFKC", text or "")
    body = _MATH_DELIMITERS_RE.sub(" ", body)
    body = _LATEX_SPACING_RE.sub(" ", body)
    body = _COSMETIC_RE.sub("", body)
    body = _PROPER_NOUN_RE.sub(" «ime»", body)
    first = body.split(None, 1)
    if first and first[0][:1].isupper() and first[0].strip(".,:;").casefold()             not in _TASK_STARTERS:
        body = "«ime»" + (" " + first[1] if len(first) > 1 else "")
    body = _LATEX_COMMAND_RE.sub("«izraz»", body)
    body = _NUMBER_TOKEN_RE.sub("«n»", body)
    body = _PRONOUN_RE.sub("«zam»", body)
    body = _WHITESPACE_RE.sub(" ", body).strip()
    return body.casefold()


def structural_signature(task_text, option_texts=()):
    """Otisak STRUKTURE zadatka. Prazan kad nema teksta.

    Opcije ulaze samo svojim BROJEM i oblikom (ne vrijednostima): dva zadatka
    koja se razlikuju samo ponuđenim brojevima nisu različite vježbe."""
    skeleton = structural_fragment(task_text)
    if not skeleton:
        return ""
    shapes = sorted({structural_fragment(option) for option in (option_texts or ())})
    encoded = json.dumps({"q": skeleton, "o": shapes}, ensure_ascii=True,
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _content_words(text):
    return {word for word in re.findall(r"[^\W\d_]{3,}", text)}


def _jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def structural_overlap(first_text, second_text):
    """Udio zajedničkih sadržajnih riječi dvije strukture (0.0–1.0)."""
    return _jaccard(_content_words(structural_fragment(first_text)),
                    _content_words(structural_fragment(second_text)))


# ---------------------------------------------------------------------------
# ZAHTJEV ZADATKA — „šta se traži“, odvojeno od „o čemu je priča“
# ---------------------------------------------------------------------------
# NALAZ KOJI JE OVO IZNUDIO (kalibracija nad 852 stvarno objavljena zadatka):
# preklapanje riječi mjeri TEMU, ne vježbu. Kanonski par koji MORA pasti —
#
#     „Ana kupi hljeb, mlijeko i sok. Koliki kusur dobije od «n» KM?“
#     „Haris kupi kiflu, jogurt i vodu. Koliki kusur dobije od «n» KM?“
#
# — dijeli samo 45 % riječi, jer se zamijenila roba. Ali ZAHTJEV je doslovno
# isti. Zato se posebno mjeri završna upitna/imperativna klauza: ona nosi ono
# što učenik mora uraditi, dok imena i predmeti nose samo kulisu.
_SENTENCE_RE = re.compile(r"[^.?!]+[.?!]?")

# Pragovi su MJERENI, ne procijenjeni (skripta `scratchpad/diversity2/`):
#   • u pojasu cjelovitog preklapanja ≥ 0.70 svaki pregledani par iz stvarnog
#     korpusa bio je ista vježba s drugim vrijednostima;
#   • ispod 0.70 se pojavljuju stvarno različite vježbe (npr. „odredi nagib“
#     naspram „odredi međusobni položaj“), pa je to donja granica.
_SAME_ASK_MIN = 0.8         # isti zahtjev…
_SAME_ASK_CONTEXT_MIN = 0.3  # …uz prepoznatljivo isti postav
_SAME_WHOLE_MIN = 0.7       # ili je cijela struktura ista (predmet u pitanju)


def ask_fragment(text):
    """Kanonska završna klauza: ono što zadatak TRAŽI, bez brojeva i imena."""
    skeleton = structural_fragment(text)
    sentences = [part.strip() for part in _SENTENCE_RE.findall(skeleton) if part.strip()]
    if not sentences:
        return skeleton
    questions = [part for part in sentences if part.endswith("?")]
    return questions[-1] if questions else sentences[-1]


def ask_overlap(first_text, second_text):
    """Koliko se poklapa ONO ŠTO SE TRAŽI u dva zadatka (0.0–1.0)."""
    return _jaccard(_content_words(ask_fragment(first_text)),
                    _content_words(ask_fragment(second_text)))


def same_exercise(first_text, second_text):
    """True kad su dva zadatka ISTA VJEŽBA s kozmetičkim zamjenama.

    Namjerno konzervativno: dokazuje se SAMO istost, nikad različitost. Kad
    mjera ne može dokazati istost, vraća False — guard koji ne može dokazati
    mora skipovati, ne nagađati (CLAUDE.md). Zato promjena VRSTE zahtjeva
    („izračunaj obim“ → „iz obima nađi stranicu“) ovdje nikad nije ista vježba,
    a promjena imena, robe ili brojeva jeste."""
    if not (first_text or "").strip() or not (second_text or "").strip():
        return False
    whole = structural_overlap(first_text, second_text)
    if whole >= _SAME_WHOLE_MIN:
        return True
    return (ask_overlap(first_text, second_text) >= _SAME_ASK_MIN
            and whole >= _SAME_ASK_CONTEXT_MIN)
