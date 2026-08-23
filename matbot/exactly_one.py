"""TAČNO JEDAN TAČAN ODGOVOR — dokaz, ne odsustvo nalaza (kontrolni v1).

ZAŠTO POSTOJI (dva živa nalaza iz kampanja, 2/300 objavljenih pitanja):

  (1) 7. razred, „Konstrukcije izvedenih uglova 15°, 75°, 105°, 120°“
      „Koji slijed konstrukcija … daje ugao od $75^\\circ$?“
        a) 90°, pa njegova simetrala → 45°                    (netačno)
        b) 90°, pa njegova simetrala i dodati 30° → 75°       TAČNO
        c) 60° i 30°, njihov zbir → 90°                       (netačno)
       *d) 60°, pa njegova simetrala i sabrati 15° → 45°      OZNAČENO, netačno
      Označen recept daje 45°, a traženih 75° daje NEOZNAČENA opcija b.

  (2) 7. razred, „Primjena podudarnosti u dokazivanju jednakosti elemenata“
      „U trouglu $ABC$ važi $AB=AC$. $AD$ je simetrala ugla kod $A$ i siječe
       $BC$ u $D$. Koja jednakost se može dokazati podudarnošću $ABD$ i $ACD$?“
       *a) $BD=DC$                     TAČNO (označeno)
        d) $\\angle ABD=\\angle ACD$    TAKOĐE TAČNO — ista podudarnost daje oba
      Dva tačna odgovora u istom pitanju.

ZAŠTO IH NIJEDNA POSTOJEĆA KAPIJA NIJE OBORILA: sve su tražile DOKAZAN DEFEKT.
`option_equivalence` traži jednake VRIJEDNOSTI (ovdje su tvrdnje različite),
`mcq_integrity` orakli rade nad izračunljivim vrijednostima (ovdje ih nema),
a uski šablonski čuvari (recepti, ugao↔stranica) poklapaju TAČNE formulacije —
prvi nalaz je promakao samo zato što je rečenica drukčije složena. Objava je
time tražila „nema nalaza“, a nikad „dokazano tačno jedan“. Ćutanje orakla nije
dokaz ispravnosti.

DOKTRINA (ovaj modul):

  PROVEN_ONE_CORRECT   tačno jedna opcija je nezavisno dokazana tačnom i to je
                       označena  → smije se objaviti
  PROVEN_ZERO_CORRECT  nijedna opcija nije tačna (ili označena nije ona tačna)
  PROVEN_MULTI_CORRECT dvije ili više opcija su tačne
  NOT_PROVABLE         server ne može utvrditi istinitost svih opcija

Za kontrolni v1 NOT_PROVABLE NIJE bezbjedno za objavu, ali SAMO za oblik
pitanja koji bira TVRDNJU (recept, zaključak, jednakost, opis). Pitanja koja
traže KONKRETAN REZULTAT (broj, mjera, izraz, skup) ne prolaze kroz ovu kapiju
— njih štite postojeći orakli vrijednosti, koji su za taj oblik primjenjivi.
Time se ne zabranjuje nijedna lekcija: pali slot ide u popravku s izričitim
zahtjevom da pitanje traži provjerljiv rezultat.

GRANICA: ovdje se NE dokazuje geometrija ni proza. Modul zna samo ono što
projekat već umije izračunati; sve ostalo je pošteno NOT_PROVABLE.
"""
import re

from matbot import option_equivalence

PROVEN_ONE_CORRECT = "proven_one_correct"
PROVEN_ZERO_CORRECT = "proven_zero_correct"
PROVEN_MULTI_CORRECT = "proven_multi_correct"
NOT_PROVABLE = "not_provable"

# ---------------------------------------------------------------------------
# 1) ISTINITOST JEDNE ČISTE MATEMATIČKE TVRDNJE
#    (lanac poređenja/jednakosti nad izračunljivim vrijednostima)
# ---------------------------------------------------------------------------

_RELATION_SPLIT_RE = re.compile(r"(\\leq\b|\\geq\b|\\le\b|\\ge\b|\\neq\b|\\ne\b|<|>|=)")
_RELATION_CANON = {"\\leq": "<=", "\\le": "<=", "\\geq": ">=", "\\ge": ">=",
                   "\\neq": "!=", "\\ne": "!=", "<": "<", ">": ">", "=": "="}
_PURE_MATH_OPTION_RE = re.compile(r"^\s*\$[^$]+\$\s*$")


def _unique_value(expression):
    """Jedina brojčana vrijednost izraza, ili None (nedokazivo/višeznačno)."""
    candidates = option_equivalence._numeric_candidates(expression)
    if not candidates:
        return None
    first = candidates[0]
    tolerance = 1e-9 * max(1.0, abs(first))
    if any(abs(value - first) > tolerance for value in candidates[1:]):
        return None
    return first


def pure_claim_truth(option_text):
    """True/False kad je opcija ČISTA matematička tvrdnja čiju istinitost
    projekat umije izračunati; None inače.

    Proza uz formulu se NAMJERNO ne sudi: rečenica („Ne, jer je $1=2$“) može
    negirati vlastitu formulu, pa bi računanje samo formule dalo pogrešan sud."""
    text = (option_text or "").strip()
    if not _PURE_MATH_OPTION_RE.match(text):
        return None
    bare = text.strip("$")
    parts = _RELATION_SPLIT_RE.split(bare)
    if len(parts) < 3 or len(parts) % 2 == 0:
        return None
    operands = parts[0::2]
    relations = [_RELATION_CANON.get(token.strip()) for token in parts[1::2]]
    if any(relation is None for relation in relations):
        return None
    values = [_unique_value(operand) for operand in operands]
    if any(value is None for value in values):
        return None
    for left, relation, right in zip(values, relations, values[1:]):
        margin = 1e-9 * max(1.0, abs(left), abs(right))
        if relation == "=" and abs(left - right) > margin:
            return False
        if relation == "!=" and abs(left - right) <= margin:
            return False
        if relation == "<" and not left < right - margin:
            return False
        if relation == ">" and not left > right + margin:
            return False
        if relation == "<=" and not left <= right + margin:
            return False
        if relation == ">=" and not left >= right - margin:
            return False
    return True


# ---------------------------------------------------------------------------
# 2) OBLIK „IZABERI TVRDNJU“ — kada se dokaz UOPŠTE zahtijeva
# ---------------------------------------------------------------------------

# Zatvoren skup bosanskih obrazaca kojima pitanje traži IZBOR TVRDNJE, a ne
# konkretan rezultat. Namjerno uzak: „Koliko je…“, „Izračunaj…“, „Odredi…“
# NISU ovdje — to su pitanja vrijednosti i njih sude orakli vrijednosti.
_CLAIM_STEM_RE = re.compile(
    r"(?i)\b("
    r"koja tvrdnja|koje tvrdnje|koja je tvrdnja|koja od (?:navedenih )?tvrdnji|"
    r"koji zaklju[čc]ak|koja jednakost|koja relacija|koja nejednakost|"
    r"[šs]ta se mo[žz]e dokazati|[šs]ta slijedi|koja jednakost slijedi|"
    r"koji odnos|koji je (?:pravilan |ta[čc]an )?(?:odnos|redoslijed|slijed|postupak)|"
    r"koji slijed|koji postupak|koji podaci|koja svojstva|koje svojstvo|"
    r"kako se (?:pravilno )?(?:konstrui[šs]e|dobija|odre[đd]uje)|"
    r"koja tvrdnja (?:ta[čc]no )?opisuje|koja definicija"
    r")\b")

# Opcija je TVRDNJA (a ne vrijednost) kad je rečenica proze, ili relacija među
# IMENOVANIM objektima (npr. `$BD=DC$`, `$b=c$`) — dakle nešto što se ne svodi
# na broj. Vrijednosne opcije („$18$“, „$\frac{5}{8}$“, „$5\,\text{cm}$“) nisu.
_WORD_RE = re.compile(r"[A-Za-zČĆŽŠĐčćžšđ]{3,}")
_LATEX_WORD_RE = re.compile(r"\\[A-Za-z]+|\\text\{[^}]*\}")


def _is_claim_option(option_text):
    text = (option_text or "").strip()
    if not text:
        return False
    if _PURE_MATH_OPTION_RE.match(text):
        # Čista matematika: tvrdnja je samo ako ima relaciju I nije izračunljiva
        # kao broj (npr. `$BD=DC$` jesu imena, `$2+2=4$` je izračunljivo).
        bare = text.strip("$")
        if not _RELATION_SPLIT_RE.search(bare):
            return False
        return pure_claim_truth(text) is None
    prose = _LATEX_WORD_RE.sub(" ", text)
    return len(_WORD_RE.findall(prose)) >= 2


def is_claim_selection(text, option_texts):
    """Da li pitanje traži IZBOR TVRDNJE (a ne konkretan rezultat)."""
    options = list(option_texts or ())
    if not options:
        return False
    claim_options = sum(1 for option in options if _is_claim_option(option))
    if claim_options == 0:
        return False
    # Dovoljan je JEDAN od dva nezavisna signala: upitna formulacija ILI to da
    # su SVE opcije tvrdnje. Time „Izračunaj…“ s vrijednostima nikad ne upada,
    # a „Koja tvrdnja…“ s prozom uvijek upada.
    return bool(_CLAIM_STEM_RE.search(text or "")) or claim_options == len(options)


# ---------------------------------------------------------------------------
# 3) PRESUDA
# ---------------------------------------------------------------------------

def evaluate(text, option_texts, marked_index):
    """Vrati (presuda, kod). Kod je interni — nikad ne ide u pregledač."""
    options = list(option_texts or ())
    if not options or not isinstance(marked_index, int):
        return NOT_PROVABLE, "unprovable_missing_options"
    if not 0 <= marked_index < len(options):
        return NOT_PROVABLE, "unprovable_bad_marked_index"

    truths = [pure_claim_truth(option) for option in options]
    if any(truth is None for truth in truths):
        # Bar jedna tvrdnja se ne može provjeriti → ne tvrdimo ništa.
        return NOT_PROVABLE, "unprovable_claim_selection"

    true_indexes = [index for index, truth in enumerate(truths) if truth]
    if len(true_indexes) == 0:
        return PROVEN_ZERO_CORRECT, "proven_zero_correct"
    if len(true_indexes) > 1:
        return PROVEN_MULTI_CORRECT, "proven_multi_correct"
    if true_indexes[0] != marked_index:
        return PROVEN_ZERO_CORRECT, "marked_option_provably_false"
    return PROVEN_ONE_CORRECT, "proven_one_correct"


def publication_failure(text, option_texts, marked_index, oracle_result=None):
    """Kod odbijanja za kontrolni objavu, ili prazan string.

    Kapija se PRIMJENJUJE samo na oblik „izaberi tvrdnju“. Pitanja vrijednosti
    prolaze nedirnuta — njih već sude postojeći orakli vrijednosti.

    `oracle_result` je nalaz JAČEG orakla vrijednosti koji je već presudio ovaj
    isti paket. ŽIVI NALAZ (forenzika dostupnosti): za „riješi nejednačinu“
    zadatke server je `evaluate_linear_solve_mcq`-om dokazao da je označena
    opcija jedina tačna, a onda ih je OVAJ sloj odbio kao „nedokazive“, jer
    `pure_claim_truth` ne umije presuditi oblik `$x\leq 2$`. Dokaz mora
    nadjačati apstinenciju — ali samo POZITIVAN dokaz, i to nad ISTIM označenim
    indeksom (vidi `mcq_integrity.proves_marked_option`). Bez takvog dokaza
    ponašanje je nepromijenjeno: nedokazivo se i dalje odbija."""
    if not is_claim_selection(text, option_texts):
        return ""
    verdict, code = evaluate(text, option_texts, marked_index)
    if verdict == PROVEN_ONE_CORRECT:
        return ""
    from matbot import mcq_integrity
    if mcq_integrity.proves_marked_option(oracle_result, marked_index):
        return ""
    return code
