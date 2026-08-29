"""Faza 3C — serverska provjera teksta koji je model napisao za roditelja.

Shema (`ReportNarrativeOutput`) garantuje OBLIK: četiri polja, sami stringovi.
Ne garantuje ništa o SADRŽAJU — model koji poštuje shemu i dalje može napisati
„napredovao je u odnosu na prošli mjesec" za učenika kojem prošli mjesec ne
postoji, ili navesti procenat koji nikad nije izmjeren. Ovaj modul je jedina
tačka koja to hvata.

TRI PROVJERE, SVE DETERMINISTIČKE:

  1. BROJEVI — svaki broj u prozi mora postojati u dopuštenim činjenicama.
     Izmišljen procenat je najopasnija greška u izvještaju za roditelja jer
     izgleda tačno kao izmjeren.
  2. TREND BEZ OSNOVE — kad `previous_available` nije tačno, riječi o promjeni
     kroz vrijeme se odbijaju. Ovo je ista klasa greške kao (1): tvrdnja o
     mjerenju koje nije obavljeno.
  3. OBLIK TEKSTA — HTML/markup i interni nazivi polja nemaju šta u dokumentu
     koji ide roditelju.

ODBIJA SE, NE POPRAVLJA. Prompt je već rekao sva pravila; tekst koji ih krši
znači da poziv nije uspio, a ne da ga treba krpiti — pogotovo jer bi svaka
„popravka" bila serversko pisanje proze o djetetu bez pokrića. Nacrt pada
zatvoreno i administrator vidi sigurnu poruku (Dio 13).
"""
import re
import unicodedata

# Riječi koje tvrde promjenu kroz vrijeme. Traže se kao CIJELE riječi nad
# oblikom bez dijakritike, pa „napredak", „napredovao" i „napredovala" padaju
# pod isti korijen bez lomljenja na padeže.
_TREND_STEMS = (
    "napred",       # napredak, napredovao, napredovala, napredovanje
    "nazad",        # nazadovao, nazadovanje
    "poras",        # porast, porastao
    "rast",         # rast, rastao
    "pad",          # pad, pao (kao cijela riječ)
    "smanj",        # smanjenje, smanjio
    "povec",        # povećanje, povećao
    "poboljs",      # poboljšanje, poboljšao
    "pogors",       # pogoršanje
    "bolje", "losije", "vise nego", "manje nego",
)
# Fraze koje izričito porede s ranijim periodom.
_TREND_PHRASES = (
    "u odnosu na prosli mjesec", "u odnosu na proslli mjesec",
    "proslog mjeseca", "prosli mjesec", "ranije je", "nego ranije",
    "u pooredjenju", "u poredjenju",
)
# Izlaz koji SAM kaže da poređenje nije moguće je ispravan, ne prekršaj.
_TREND_DISCLAIMERS = (
    "nije moguce procijeniti", "nije moguce porediti", "nije dostupan za poredjenje",
    "nema podataka za poredjenje", "nije moguca usporedba", "nema prethodnog mjeseca",
    "prethodni mjesec nije dostupan",
)

# Interni rječnik koji ne smije procuriti u dokument za roditelja.
_INTERNAL_TOKENS = (
    "evidence_level", "low_evidence", "previous_available", "snapshot_missing",
    "percent_viewed", "percent_completed", "tasks_presented", "answers_total",
    "accuracy_percent", "lesson_evidence", "student_id", "course_key",
    "metrics_json", "insufficient", "moderate evidence", "json",
)

_MARKUP_RE = re.compile(r"<[^>]+>|&(?:#\d+|[a-zA-Z]+);|\]\(|\*\*|```")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _fold(text):
    """Mala slova bez dijakritike — poređenje ne smije pasti na „š" vs „s"."""
    lowered = (text or "").lower().replace("đ", "dj").replace("Đ", "dj")
    stripped = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in stripped if not unicodedata.combining(ch))


def collect_text(narrative):
    """Sva proza jednog nacrta kao jedan string."""
    parts = [narrative.get("summary") or ""]
    for key in ("strengths", "focus_areas", "next_month_recommendations"):
        parts.extend(narrative.get(key) or [])
    return "\n".join(parts)


def unsupported_numbers(text, allowed):
    """Brojevi iz proze kojih nema među izmjerenim vrijednostima.

    Godine i mjeseci (npr. „2026") se ne provjeravaju ovdje jer nisu mjere o
    učeniku; sve ostalo mora imati pokriće. Poređenje ide na dvije decimale da
    „58.3" i „58.30" ne bi bili različiti brojevi."""
    found = set()
    for raw in _NUMBER_RE.findall(text or ""):
        value = float(raw.replace(",", "."))
        if value in allowed:
            continue
        if any(abs(value - candidate) < 0.05 for candidate in allowed):
            continue
        found.add(raw)
    return sorted(found)


def trend_violations(text, previous_available):
    """Riječi o promjeni kroz vrijeme kad za promjenu nema osnove."""
    if previous_available:
        return []
    folded = _fold(text)
    if any(disclaimer in folded for disclaimer in _TREND_DISCLAIMERS):
        # Tekst izričito kaže da poređenja nema — to je tačno ono što tražimo.
        return []
    hits = []
    for phrase in _TREND_PHRASES:
        if phrase in folded:
            hits.append(phrase)
    for stem in _TREND_STEMS:
        if re.search(r"\b" + re.escape(stem), folded):
            hits.append(stem)
    return sorted(set(hits))


def markup_violations(text):
    """HTML, markdown i interni nazivi polja."""
    hits = []
    if _MARKUP_RE.search(text or ""):
        hits.append("markup")
    folded = _fold(text)
    for token in _INTERNAL_TOKENS:
        if _fold(token) in folded:
            hits.append("internal:" + token)
    return sorted(set(hits))


def validate_narrative(narrative, facts):
    """Vrati listu INTERNIH kodova. Prazna lista znači: nacrt smije biti sačuvan.

    Kodovi su dijagnostika za log — administrator ih nikad ne vidi (pravilo 7
    projekta), isto kao validatori tutorskog puta."""
    from matbot import report_facts

    problems = []
    text = collect_text(narrative)

    invented = unsupported_numbers(text, report_facts.allowed_numbers(facts))
    if invented:
        problems.append("report_unsupported_number:" + ",".join(invented[:5]))

    previous_available = bool((facts.get("thinkific") or {}).get("previous_available"))
    trend = trend_violations(text, previous_available)
    if trend:
        problems.append("report_trend_without_baseline:" + ",".join(trend[:5]))

    markup = markup_violations(text)
    if markup:
        problems.append("report_markup_or_internal:" + ",".join(markup[:5]))

    # Prazan sažetak nije izvještaj. Liste smiju biti prazne — to je namjerno
    # dopušten ishod kad dokaza nema — ali sažetak mora nešto reći.
    if not (narrative.get("summary") or "").strip():
        problems.append("report_summary_empty")

    return problems
