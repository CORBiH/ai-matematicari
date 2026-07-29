"""Deterministička provjera da GENERISANI zadatak stvarno pripada porodici koju
je server dodijelio (FamilyContract sloj).

ZAŠTO POSTOJI (živi nalaz, 4 stvarna poziva, lekcija „Proširivanje razlomaka“):
server je ispravno rotirao porodice i ispravno ih poslao u prompt, ali je model
za dvije uzastopne porodice vratio ISTI pedagoški zadatak:

    dodijeljeno: find_expansion_factor
    generisano:  „Proširi razlomak 2/5 tako da nazivnik bude 20.“   ← pogrešno
    dodijeljeno: find_missing_numerator
    generisano:  „Proširi razlomak 3/7 tako da nazivnik bude 35.“   ← pogrešno

Prompt je bila SUGESTIJA. Ovaj modul je pretvara u UGOVOR: zadatak koji ne
odgovara dodijeljenoj porodici odbija se PRIJE ikakve mutacije sesije, bez
drugog AI poziva (pozivalac vraća postojeći SAFE_ERROR_MESSAGE).

DIZAJN — namjerna asimetrija strogosti:
  • Porodice gdje je struktura NEDVOSMISLENA (razlomci: proširi / koji faktor /
    nedostajući brojnik / prepoznaj ekvivalent) dobijaju STROG ugovor: moraju
    pozitivno dokazati svoj oblik.
  • Porodice gdje se oblik ne može dokazati bez matematičkog parsera (tekstualni
    zadaci, direktan račun, obrnuta formula) dobijaju ugovor ZABRANJENIH OBLIKA:
    ne traže dokaz ispravnog oblika, nego odbijaju prepoznato pogrešne.
Razlog: traženje pozitivnog dokaza svuda odbacivalo bi validne kreativne
zadatke i učenik bi prečesto dobio sigurnu poruku o grešci.
"""
import re
from dataclasses import dataclass, field

from matbot.task_families import FAMILY_DESCRIPTIONS


class FamilyContractError(ValueError):
    """Zadatak ne odgovara dodijeljenoj porodici. Poruka je INTERNA (log),
    nikad se ne prikazuje učeniku."""


# ---------------------------------------------------------------------------
# DOZVOLJENE VRIJEDNOSTI internih (server-only) metapodataka koje model
# deklariše uz zadatak. Nikad ne idu u browser — vidi practice._next_state.
# ---------------------------------------------------------------------------

STUDENT_MUST_FIND = (
    "expanded_fraction", "expansion_factor", "missing_numerator", "missing_denominator",
    "equivalent_fraction", "incorrect_step", "variable_value", "ordered_pair",
    "formula", "missing_dimension", "method", "number_of_solutions",
    "value", "statement", "comparison", "unit_value", "next_step",
)

ANSWER_KIND = (
    "integer", "decimal", "fraction", "ordered_pair", "expression",
    "formula", "option_label", "short_text",
)

TASK_FORM = (
    "direct_calculation", "missing_value", "recognition", "error_detection",
    "method_selection", "interpretation", "word_problem", "construction_step",
)


# ---------------------------------------------------------------------------
# PREPOZNAVANJE OBLIKA — uske, determinističke provjere nad VIDLJIVIM tekstom
# ---------------------------------------------------------------------------

# Imperativ „proširi“/„skrati“ = direktiva da učenik SAM proizvede prošireni
# razlomak. Namjerno NE hvata particip „proširen(a)“ — „Razlomak 2/5 proširen
# je na 8/20“ je legitiman UVOD u pitanje o faktoru, ne direktiva.
_EXPAND_DIRECTIVE_RE = re.compile(r"\bpro[šs]iri\b|\bskrati\b|\bproširite\b", re.IGNORECASE)

_DENOMINATOR_WORD_RE = re.compile(r"nazivnik|imenilac|imenitelj", re.IGNORECASE)
_NUMERATOR_WORD_RE = re.compile(r"brojnik|brojilac", re.IGNORECASE)

# „Kojim brojem…“, „Kojim je brojem…“, „Koji faktor…“, „Koliko puta…“
_ASKS_FACTOR_RE = re.compile(
    r"koj(?:im|i)\s+(?:je\s+)?broj(?:em)?\b"
    r"|koj(?:im|i)\s+(?:je\s+)?faktor"
    r"|koliko\s+puta"
    r"|faktor\s+pro[šs]irenja",
    re.IGNORECASE,
)

# Praznina U JEDNAKOSTI (ne bilo koji upitnik!). „Koja opcija je tačna?“ ne
# smije se protumačiti kao nedostajuća vrijednost — zato se traži ? / □ / _
# unutar razlomka ili neposredno iza znaka jednakosti, ili riječ dopuni/nedostaje.
_MISSING_SLOT_RE = re.compile(
    r"\\frac\s*\{\s*[?_□]+\s*\}"
    r"|\\frac\s*\{[^{}]*\}\s*\{\s*[?_□]+\s*\}"
    r"|[?□]\s*/\s*\d"
    r"|\d\s*/\s*[?□]"
    r"|=\s*\$?\s*[?□]"
    r"|\\square"
    r"|nedostaj|dopuni|popuni",
    re.IGNORECASE,
)

_ASKS_EQUIVALENT_RE = re.compile(r"jednak|ekvivalent|iste\s+vrijednosti", re.IGNORECASE)
_ASKS_COMPARE_RE = re.compile(
    r"uporedi|upored|ve[ćc]i|manji|pore[đd]aj|rastu[ćc]|opadaju[ćc]|koji\s+je\s+ve|koji\s+je\s+ma",
    re.IGNORECASE,
)
_ASKS_METHOD_RE = re.compile(
    r"koj(?:a|u|om)\s+metod|koj(?:i|im)\s+postup|koj(?:a|i)\s+na[čc]in|najprikladnij|"
    r"metod(?:a|u|om)\s+je\s+najbolj",
    re.IGNORECASE,
)
_ASKS_SOLUTION_COUNT_RE = re.compile(
    r"koliko\s+rje[šs]enj|broj\s+rje[šs]enj|ima\s+li\s+rje[šs]enj|"
    r"beskona[čc]no\s+mnogo|nema\s+rje[šs]enj|jedno\s+rje[šs]enje",
    re.IGNORECASE,
)
_ASKS_VERIFY_RE = re.compile(
    r"da\s+li\s+je|je\s+li\b|provjeri\s+da\s+li|zadovoljava|"
    r"da\s+li\s+ure[đd]eni\s+par|da\s+li\s+par",
    re.IGNORECASE,
)
_ASKS_ERROR_RE = re.compile(
    r"gre[šs]k|pogrije[šs]|netа?[čc]n|nije\s+ta[čc]n|[šs]ta\s+je\s+pogre|"
    r"koji\s+korak\s+je|gdje\s+je\s+gre|ispravk|isprav(?:i|ka|no)",
    re.IGNORECASE,
)
_ASKS_FORMULA_RE = re.compile(
    r"koj(?:a|om|u)\s+formul|formula\s+se\s+koristi|koj(?:i|im)\s+izraz\w*\s+ra[čc]una",
    re.IGNORECASE,
)
_ASKS_NEXT_STEP_RE = re.compile(
    r"sljede[ćc]i\s+korak|naredni\s+korak|[šs]ta\s+(?:se\s+)?radi\s+dalje|koji\s+korak\s+slijedi|"
    r"prvi\s+korak",
    re.IGNORECASE,
)
_ASKS_STATEMENT_RE = re.compile(
    r"koja\s+tvrdnj|koja\s+je\s+tvrdnj|koja\s+re[čc]enica|koji\s+iskaz|"
    r"koja\s+od\s+navedenih\s+tvrdnj|[šs]ta\s+je\s+ta[čc]no",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(
    r"\b(?:mm|cm|dm|km|m|mm²|cm²|dm²|m²|mm³|cm³|dm³|m³|litar|litr|ml|kg|g|t)\b"
    r"|\\text\{\s*(?:mm|cm|dm|km|m|l|ml|kg|g)\s*\}"
    r"|pretvor|mjern(?:a|e|ih)\s+jedinic",
    re.IGNORECASE,
)
# Kontekst „iz života“: bar jedna imenica/glagol iz svakodnevice. Uzorci su
# STEMOVI (bez \b na kraju) jer bosanska deklinacija mijenja nastavak —
# „torta/torte/tortu“, „bašta/bašte“, „učenik/učenici/učenika“.
_CONTEXT_WORD_RE = re.compile(
    r"baš[tč]\w*|vrt\w*|sob[ae]\w*|zid\w*|ograd\w*|bazen\w*|kutij\w*|torb\w*|"
    r"nov(?:ac|ca|c[ue])\w*|km/h|brzin\w*|vrijeme|sat\w*|minut\w*|"
    r"kola[čc]\w*|tort\w*|pic\w*|pizz\w*|"
    r"u[čc]eni\w*|razred\w*|prodavnic\w*|cijen\w*|popust\w*|"
    r"vod[ae]\w*|mlijek\w*|sok\w*|zemlji[šs]t\w*|njiv\w*|"
    r"radni\w*|posao|posla|putuj\w*|putovanj\w*|automobil\w*|bicikl\w*|"
    r"vozi\w*|autobus\w*|kupi\w*|kupov\w*|proda\w*|potro[šs]\w*|"
    r"\bKM\b|maraka|marke|marku",
    re.IGNORECASE,
)


def _strip_math_wrapper(text):
    """Skini $...$, \\, i \\text{...} da bi ostala gola vrijednost opcije."""
    value = (text or "").strip()
    value = value.replace("\\,", " ").replace("\\;", " ").replace("\\!", "")
    value = re.sub(r"\\text\s*\{([^{}]*)\}", r"\1", value)
    value = value.strip().strip("$").strip()
    return value


_FRACTION_VALUE_RE = re.compile(
    r"^\\d?frac\s*\{[^{}]*\}\s*\{[^{}]*\}$|^-?\d+\s*/\s*-?\d+$|^-?\d+\s*\\frac\s*\{[^{}]*\}\s*\{[^{}]*\}$"
)
_INTEGER_VALUE_RE = re.compile(r"^-?\d+$")
_DECIMAL_VALUE_RE = re.compile(r"^-?\d+[.,]\d+$")
_ORDERED_PAIR_RE = re.compile(r"^\(?\s*-?\d+\s*,\s*-?\d+\s*\)?$|^\(\s*x\s*,\s*y\s*\)\s*=")


def is_fraction_option(text):
    value = _strip_math_wrapper(text)
    if re.fullmatch(r"\\frac\s*\{[^{}]*\}\s*\{[^{}]*\}", value):
        return True
    if re.fullmatch(r"-?\d+\s*/\s*-?\d+", value):
        return True
    # mješoviti broj: 2\frac{1}{3}
    if re.fullmatch(r"-?\d+\s*\\frac\s*\{[^{}]*\}\s*\{[^{}]*\}", value):
        return True
    return False


def is_integer_option(text):
    return bool(_INTEGER_VALUE_RE.fullmatch(_strip_math_wrapper(text)))


def is_numeric_option(text):
    value = _strip_math_wrapper(text)
    return bool(_INTEGER_VALUE_RE.fullmatch(value) or _DECIMAL_VALUE_RE.fullmatch(value))


def is_ordered_pair_option(text):
    return bool(_ORDERED_PAIR_RE.match(_strip_math_wrapper(text)))


def detected_answer_kind(text):
    """Objektivno prepoznaj TIP vrijednosti iz vidljivog teksta — vraća jedan
    od "fraction"/"integer"/"decimal"/"ordered_pair", ili None kad tip nije
    mehanički prepoznatljiv (proza, formula, izraz, oznaka opcije). Koristi se
    da se `answer_kind` provjeri protiv STVARNOG sadržaja, ne protiv statične
    liste po porodici — vidi napomenu uz `validate_task_family`."""
    if is_fraction_option(text):
        return "fraction"
    value = _strip_math_wrapper(text)
    # Decimal PRIJE ordered_pair: "2,5" (bosanski decimalni zarez) inače pogrešno
    # upada u ordered_pair regex jer su zagrade tamo opcionalne.
    if _DECIMAL_VALUE_RE.fullmatch(value):
        return "decimal"
    if is_ordered_pair_option(text):
        return "ordered_pair"
    if is_integer_option(text):
        return "integer"
    return None


def is_prose_option(text):
    """Opcija koja NIJE gola vrijednost — opisuje metodu, tvrdnju, korak ili
    grešku. Prepoznaje se po tome što sadrži bar dvije riječi od slova."""
    value = _strip_math_wrapper(text)
    if is_fraction_option(text) or is_numeric_option(text) or is_ordered_pair_option(text):
        return False
    words = re.findall(r"[A-Za-zČčĆćĐđŠšŽž]{2,}", value)
    return len(words) >= 2


def _count(options, predicate):
    return sum(1 for o in options if predicate(o))


def _majority(options, predicate):
    """Bar 3 od 4 opcije zadovoljavaju uslov — dopušta jedan distraktor drugog
    oblika (npr. jedna prozna zamka među brojevima)."""
    return options and _count(options, predicate) >= max(1, len(options) - 1)


# ---------------------------------------------------------------------------
# UGOVOR PORODICE
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FamilyContract:
    """Deterministički ugovor jedne porodice.

    required:  lista (naziv, predikat(TaskView)->bool) — SVI moraju proći.
    forbidden: lista (naziv, predikat(TaskView)->bool) — nijedan ne smije proći.
    Prazna `required` lista = ugovor zabranjenih oblika (vidi docstring modula).

    POVJERENJE U DEKLARISANE METAPODATKE MODELA — DVA živa nalaza redom:

      1. Pet ISPRAVNIH find_expansion_factor zadataka lažno odbijeno jer je
         model deklarisao task_form="direct_calculation" dok je ugovor
         dozvoljavao samo "recognition"/"missing_value" (provjera svih 31
         porodice pokazala je ≤2 od mogućih 8 vrijednosti task_form svuda —
         sistemski propust, ne izolovan slučaj).
      2. Ispravan verify_ordered_pair zadatak (konkretan par, pitanje da li
         zadovoljava sistem, opcije su tvrdnje o provjeri) lažno odbijen jer
         je model deklarisao student_must_find="ordered_pair" dok je ugovor
         dozvoljavao samo "statement" — ISTA klasa propusta: i student_must_find
         je interpretativna oznaka (predmet provjere JESTE uređeni par, čak i
         kad je forma odgovora tvrdnja), ne objektivno mjerljiva činjenica.

    Konačna hijerarhija povjerenja (nakon oba nalaza):

      • task_family: STROGO — dodijeljena porodica je autoritet servera, pa
        deklarisano neslaganje je uvijek odbijanje. Ovo se NIKAD ne slabi.
      • VIDLJIV ugovor (required/forbidden nad stvarnim tekstom pitanja/
        opcija/tačnog odgovora): AUTORITATIVAN — jedini stvarni dokaz da
        zadatak pripada dodijeljenoj porodici.
      • answer_kind: strogo SAMO kad je objektivno mehanički prepoznatljivo iz
        stvarnog tačnog odgovora (detected_answer_kind) — fraction/integer/
        decimal/ordered_pair. Kontradikcija (npr. deklarisano "integer" dok je
        tačna opcija razlomak ili uređeni par) je odbijanje; kad tip nije
        mehanički prepoznatljiv (proza/formula/izraz), provjera se preskače.
      • student_must_find: SAMO INFORMATIVNO — više NIKAD ne odbija sam po
        sebi. `canonical_student_must_find` je server-derived vrijednost
        (iz ugovora, ne od modela) — dostupna za interno logovanje/dijagnostiku,
        nikad za odbijanje niti za browser.
      • task_form: SAMO INFORMATIVNO — isto kao gore, `canonical_task_form` je
        server-derived, nikad za odbijanje.

      Model i dalje SMIJE vratiti student_must_find/task_form (ostaju u šemi
      radi dijagnostike — npr. da se uoči kad model sistematski bira neočekivan
      opis za neku porodicu), ali TA vrijednost više NIKAD ne može sama
      odbaciti zadatak koji je VEĆ prošao vidljivi ugovor i objektivnu
      answer_kind provjeru.

    NUMERIČKA DOSLJEDNOST (matbot/mathcheck.py) — POITICA PO PORODICI SAMO za
    TEKST PITANJA:
      • question_numeric_policy="check" (podrazumijevano): pitanje predstavlja
        ČINJENICE kao date — dokazana nedosljedna jednakost u pitanju je
        odbijanje.
      • question_numeric_policy="allow_intentional_mismatch": pitanje smije
        NAMJERNO prikazati pogrešan korak/tvrdnju/formulu/par kao PREDMET koji
        učenik ispituje (npr. „Učenik je napisao $3\\cdot16/2=48$. Šta je
        pogriješio?“) — numerička provjera se PRESKAČE za tekst pitanja.

    Ovo NIKAD ne utiče na opcije ni na tačan odgovor:
      • TAČNA opcija (i expected_answer) se UVIJEK numerički provjerava, bez
        obzira na porodicu — ona predstavlja PRAVU, ne izmišljenu, matematiku.
      • POGREŠNE opcije (distraktori) se NIKAD numerički ne provjeravaju, bez
        obzira na porodicu — namjerno su pogrešne po dizajnu multiple-choice
        zadatka (živi nalaz: distraktor „$3\\cdot16/2=48$“ ne smije srušiti
        cio zadatak).
    """
    family_id: str
    objective: str
    student_must_find: tuple = ()
    answer_kind: tuple = ()
    task_form: tuple = ()
    canonical_task_form: str = ""
    canonical_student_must_find: str = ""
    question_numeric_policy: str = "check"
    required: tuple = ()
    forbidden: tuple = ()
    prompt_must_be_unknown: str = ""
    prompt_options_must_be: str = ""
    prompt_positive_example: str = ""
    prompt_forbidden_example: str = ""

    def __post_init__(self):
        if not self.canonical_task_form and self.task_form:
            object.__setattr__(self, "canonical_task_form", self.task_form[0])
        if not self.canonical_student_must_find and self.student_must_find:
            object.__setattr__(self, "canonical_student_must_find", self.student_must_find[0])


@dataclass
class TaskView:
    """Sve što validator smije gledati — VIDLJIV tekst (nakon sanitizacije i
    normalizacije terminologije) plus interno očekivano rješenje."""
    question: str
    options: list
    correct_option_text: str
    expected_answer: str
    difficulty: str = ""

    @property
    def haystack(self):
        return f"{self.question}"


# --- predikati nad TaskView ------------------------------------------------

def _q(pattern):
    return lambda t: bool(pattern.search(t.question))


def _opts_fraction(t):
    return _majority(t.options, is_fraction_option)


def _opts_integer(t):
    return _majority(t.options, is_integer_option)


def _opts_numeric(t):
    return _majority(t.options, is_numeric_option)


def _opts_prose(t):
    return _majority(t.options, is_prose_option)


def _correct_is_fraction(t):
    return is_fraction_option(t.correct_option_text)


def _correct_is_integer(t):
    return is_integer_option(t.correct_option_text)


def _correct_is_numeric(t):
    return is_numeric_option(t.correct_option_text)


def _correct_is_prose(t):
    return is_prose_option(t.correct_option_text)


def _has_context(t):
    return bool(_CONTEXT_WORD_RE.search(t.question)) and len(t.question) >= 60


# ---------------------------------------------------------------------------
# KATALOG UGOVORA
# ---------------------------------------------------------------------------

CONTRACTS = {}


def _register(contract):
    CONTRACTS[contract.family_id] = contract


# ===== RAZLOMCI — STROGI ugovori (ovdje se desio živi propust) =============

_register(FamilyContract(
    family_id="expand_to_given_denominator",
    objective="učenik sam proizvodi prošireni razlomak do zadanog nazivnika",
    student_must_find=("expanded_fraction",),
    answer_kind=("fraction",),
    task_form=("direct_calculation", "missing_value"),
    required=(
        ("ima_direktivu_prosirivanja", _q(_EXPAND_DIRECTIVE_RE)),
        ("spominje_nazivnik", _q(_DENOMINATOR_WORD_RE)),
        ("opcije_su_razlomci", _opts_fraction),
        ("tacan_odgovor_je_razlomak", _correct_is_fraction),
    ),
    forbidden=(
        ("pita_za_faktor", _q(_ASKS_FACTOR_RE)),
        ("prazno_mjesto_u_jednakosti", _q(_MISSING_SLOT_RE)),
    ),
    prompt_must_be_unknown="cijeli prošireni razlomak",
    prompt_options_must_be="četiri RAZLOMKA",
    prompt_positive_example="Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.",
    prompt_forbidden_example="Kojim brojem je proširen razlomak $\\frac{3}{8}$? (to je porodica find_expansion_factor)",
))

_register(FamilyContract(
    family_id="find_expansion_factor",
    objective="učenik određuje BROJ kojim je razlomak proširen/skraćen",
    student_must_find=("expansion_factor",),
    answer_kind=("integer",),
    task_form=("recognition", "missing_value"),
    required=(
        ("pita_za_faktor", _q(_ASKS_FACTOR_RE)),
        ("opcije_su_brojevi", _opts_numeric),
        ("tacan_odgovor_je_broj", _correct_is_numeric),
    ),
    forbidden=(
        ("ima_direktivu_prosirivanja", _q(_EXPAND_DIRECTIVE_RE)),
        ("opcije_su_razlomci", _opts_fraction),
    ),
    prompt_must_be_unknown="BROJ (faktor) kojim je razlomak proširen ili skraćen",
    prompt_options_must_be="četiri BROJA (npr. 2, 3, 4, 5) — NIKAD razlomci",
    prompt_positive_example="Razlomak $\\frac{2}{5}$ proširen je na $\\frac{8}{20}$. Kojim brojem je proširen?",
    prompt_forbidden_example="Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$. (to je porodica expand_to_given_denominator)",
))

_register(FamilyContract(
    family_id="find_missing_numerator",
    objective="učenik popunjava JEDNU nedostajuću vrijednost u jednakosti razlomaka",
    student_must_find=("missing_numerator", "missing_denominator"),
    answer_kind=("integer",),
    task_form=("missing_value",),
    required=(
        ("ima_prazno_mjesto_u_jednakosti", _q(_MISSING_SLOT_RE)),
        ("opcije_su_brojevi", _opts_numeric),
        ("tacan_odgovor_je_broj", _correct_is_numeric),
    ),
    forbidden=(
        ("ima_direktivu_prosirivanja", _q(_EXPAND_DIRECTIVE_RE)),
        ("opcije_su_razlomci", _opts_fraction),
        ("pita_za_faktor", _q(_ASKS_FACTOR_RE)),
    ),
    prompt_must_be_unknown="SAMO nedostajući brojnik (ili nazivnik) u jednakosti",
    prompt_options_must_be="četiri BROJA — NIKAD cijeli razlomci",
    prompt_positive_example="Dopuni jednakost: $\\frac{3}{7} = \\frac{?}{35}$.",
    prompt_forbidden_example="Proširi razlomak $\\frac{3}{7}$ tako da nazivnik bude $35$. (to je porodica expand_to_given_denominator)",
))

_register(FamilyContract(
    family_id="recognize_equivalent_fraction",
    objective="učenik BIRA koji je od ponuđenih razlomaka jednak zadanom",
    student_must_find=("equivalent_fraction",),
    answer_kind=("fraction",),
    task_form=("recognition",),
    required=(
        ("pita_za_jednakost", _q(_ASKS_EQUIVALENT_RE)),
        ("opcije_su_razlomci", _opts_fraction),
        ("tacan_odgovor_je_razlomak", _correct_is_fraction),
    ),
    forbidden=(
        ("ima_direktivu_prosirivanja", _q(_EXPAND_DIRECTIVE_RE)),
        ("pita_za_faktor", _q(_ASKS_FACTOR_RE)),
        ("prazno_mjesto_u_jednakosti", _q(_MISSING_SLOT_RE)),
    ),
    prompt_must_be_unknown="koji od PONUĐENIH razlomaka je jednak zadanom",
    prompt_options_must_be="četiri RAZLOMKA, od kojih je tačno jedan jednak zadanom",
    prompt_positive_example="Koji od navedenih razlomaka je jednak razlomku $\\frac{4}{9}$?",
    prompt_forbidden_example="Proširi razlomak $\\frac{4}{9}$ na nazivnik $36$. (to je porodica expand_to_given_denominator)",
))

_register(FamilyContract(
    family_id="compare_fractions",
    objective="učenik upoređuje ili poređa razlomke po veličini",
    student_must_find=("comparison",),
    answer_kind=("fraction", "short_text"),
    task_form=("recognition", "direct_calculation"),
    required=(
        ("pita_za_poredjenje", _q(_ASKS_COMPARE_RE)),
    ),
    forbidden=(
        ("ima_direktivu_prosirivanja", _q(_EXPAND_DIRECTIVE_RE)),
        ("pita_za_faktor", _q(_ASKS_FACTOR_RE)),
    ),
    prompt_must_be_unknown="koji je razlomak veći/manji ili koji je ispravan poredak",
    prompt_options_must_be="razlomci ili kratki opisi poretka",
    prompt_positive_example="Koji je razlomak veći: $\\frac{3}{5}$ ili $\\frac{5}{8}$?",
    prompt_forbidden_example="Proširi $\\frac{3}{5}$ na nazivnik $40$.",
))

_register(FamilyContract(
    family_id="fraction_operation",
    objective="učenik izvodi računsku operaciju s razlomcima",
    student_must_find=("value", "expanded_fraction"),
    answer_kind=("fraction", "integer", "decimal"),
    task_form=("direct_calculation",),
    required=(),
    forbidden=(
        ("pita_za_faktor", _q(_ASKS_FACTOR_RE)),
        ("prazno_mjesto_u_jednakosti", _q(_MISSING_SLOT_RE)),
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
    ),
    prompt_must_be_unknown="rezultat računske operacije",
    prompt_options_must_be="rezultati (razlomci ili brojevi)",
    prompt_positive_example="Izračunaj $\\frac{2}{7} + \\frac{3}{7}$.",
    prompt_forbidden_example="Kojim brojem je proširen $\\frac{2}{7}$?",
))

_register(FamilyContract(
    family_id="fraction_word_problem",
    objective="učenik primjenjuje razlomke u životnom kontekstu",
    student_must_find=("value",),
    answer_kind=("fraction", "integer", "decimal"),
    task_form=("word_problem",),
    required=(
        ("ima_zivotni_kontekst", _has_context),
    ),
    forbidden=(
        ("ima_direktivu_prosirivanja", _q(_EXPAND_DIRECTIVE_RE)),
        ("pita_za_faktor", _q(_ASKS_FACTOR_RE)),
    ),
    prompt_must_be_unknown="tražena veličina u opisanoj situaciji",
    prompt_options_must_be="moguće vrijednosti odgovora",
    prompt_positive_example="Amar je pojeo $\\frac{2}{8}$ torte, a Lejla $\\frac{3}{8}$. Koliko su pojeli zajedno?",
    prompt_forbidden_example="Proširi razlomak $\\frac{2}{8}$ na nazivnik $24$.",
))


# ===== SISTEMI JEDNAČINA ===================================================

_register(FamilyContract(
    family_id="solve_system",
    objective="učenik rješava sistem i nalazi uređeni par",
    student_must_find=("ordered_pair",),
    answer_kind=("ordered_pair",),
    task_form=("direct_calculation",),
    required=(),
    forbidden=(
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
        ("pita_broj_rjesenja", _q(_ASKS_SOLUTION_COUNT_RE)),
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
    ),
    prompt_must_be_unknown="uređeni par $(x,y)$ koji rješava sistem",
    prompt_options_must_be="četiri uređena para",
    prompt_positive_example="Riješi sistem: $x+y=7$ i $x-y=1$.",
    prompt_forbidden_example="Koja metoda je najprikladnija za ovaj sistem?",
))

_register(FamilyContract(
    family_id="verify_ordered_pair",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik PROVJERAVA dati uređeni par, ne rješava sistem od nule",
    student_must_find=("statement", "ordered_pair"),
    canonical_student_must_find="verification_result",
    answer_kind=("short_text", "option_label"),
    task_form=("recognition",),
    required=(
        ("nudi_par_na_provjeru", lambda t: bool(_ORDERED_PAIR_RE.search(_strip_math_wrapper(t.question))
                                                or re.search(r"\(\s*-?\d+\s*,\s*-?\d+\s*\)", t.question))),
        ("pita_za_provjeru", _q(_ASKS_VERIFY_RE)),
    ),
    forbidden=(
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
        ("pita_broj_rjesenja", _q(_ASKS_SOLUTION_COUNT_RE)),
    ),
    prompt_must_be_unknown="da li KONKRETAN dati par zadovoljava sistem",
    prompt_options_must_be="kratke tvrdnje (zadovoljava / ne zadovoljava i zašto)",
    prompt_positive_example="Da li je par $(2,3)$ rješenje sistema $x+y=5$ i $x-y=-1$?",
    prompt_forbidden_example="Riješi sistem $x+y=5$ i $x-y=-1$.",
))

_register(FamilyContract(
    family_id="choose_method",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik bira METODU, ne konačan broj",
    student_must_find=("method",),
    answer_kind=("short_text",),
    task_form=("method_selection",),
    required=(
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
        ("opcije_su_opisi", _opts_prose),
        ("tacan_odgovor_je_opis", _correct_is_prose),
    ),
    forbidden=(
        ("opcije_su_uredjeni_parovi", lambda t: _majority(t.options, is_ordered_pair_option)),
    ),
    prompt_must_be_unknown="koja je metoda/postupak najprikladniji",
    prompt_options_must_be="nazivi metoda ili kratki opisi postupka — NIKAD brojevi",
    prompt_positive_example="Koja metoda je najprikladnija za sistem $y=2x$ i $x+y=9$?",
    prompt_forbidden_example="Riješi sistem $y=2x$ i $x+y=9$.",
))

_register(FamilyContract(
    family_id="determine_number_of_solutions",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik određuje KOLIKO rješenja sistem ima",
    student_must_find=("number_of_solutions",),
    answer_kind=("short_text",),
    task_form=("recognition",),
    required=(
        ("pita_broj_rjesenja", _q(_ASKS_SOLUTION_COUNT_RE)),
    ),
    forbidden=(
        ("opcije_su_uredjeni_parovi", lambda t: _majority(t.options, is_ordered_pair_option)),
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
    ),
    prompt_must_be_unknown="broj rješenja (nijedno / jedno / beskonačno mnogo)",
    prompt_options_must_be="slučajevi broja rješenja — NIKAD konkretan uređeni par",
    prompt_positive_example="Koliko rješenja ima sistem $x+y=3$ i $2x+2y=6$?",
    prompt_forbidden_example="Riješi sistem $x+y=3$ i $2x+2y=6$.",
))

_register(FamilyContract(
    family_id="identify_equivalent_system",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik prepoznaje ekvivalentan sistem",
    student_must_find=("statement",),
    answer_kind=("expression",),
    task_form=("recognition",),
    required=(
        ("pita_za_ekvivalenciju", _q(_ASKS_EQUIVALENT_RE)),
    ),
    forbidden=(
        ("pita_broj_rjesenja", _q(_ASKS_SOLUTION_COUNT_RE)),
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
    ),
    prompt_must_be_unknown="koji je ponuđeni sistem ekvivalentan zadanom",
    prompt_options_must_be="sistemi jednačina",
    prompt_positive_example="Koji je sistem ekvivalentan sistemu $x+y=4$ i $x-y=2$?",
    prompt_forbidden_example="Koliko rješenja ima sistem $x+y=4$?",
))

_register(FamilyContract(
    family_id="system_word_problem",
    objective="učenik modelira životnu situaciju sistemom",
    student_must_find=("value", "ordered_pair"),
    answer_kind=("ordered_pair", "integer", "short_text"),
    task_form=("word_problem",),
    required=(
        ("ima_zivotni_kontekst", _has_context),
    ),
    forbidden=(
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
    ),
    prompt_must_be_unknown="tražene veličine u opisanoj situaciji",
    prompt_options_must_be="moguće vrijednosti/parovi",
    prompt_positive_example="U razredu ima 30 učenika. Djevojčica je 4 više nego dječaka. Koliko je dječaka?",
    prompt_forbidden_example="Riješi sistem $x+y=30$ i $x-y=4$.",
))


# ===== JEDNAČINE I NEJEDNAČINE =============================================

_register(FamilyContract(
    family_id="solve_equation",
    objective="učenik rješava jednačinu i nalazi vrijednost nepoznate",
    student_must_find=("variable_value",),
    answer_kind=("integer", "decimal", "fraction"),
    task_form=("direct_calculation",),
    required=(),
    forbidden=(
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
        ("pita_za_sljedeci_korak", _q(_ASKS_NEXT_STEP_RE)),
    ),
    prompt_must_be_unknown="vrijednost nepoznate",
    prompt_options_must_be="moguće vrijednosti nepoznate",
    prompt_positive_example="Riješi jednačinu $x+5=12$.",
    prompt_forbidden_example="Koji je sljedeći korak u rješavanju $x+5=12$?",
))

_register(FamilyContract(
    family_id="verify_solution",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik provjerava da li je DATI broj rješenje",
    student_must_find=("statement", "variable_value"),
    canonical_student_must_find="verification_result",
    answer_kind=("short_text", "option_label"),
    task_form=("recognition",),
    required=(
        ("pita_za_provjeru", _q(_ASKS_VERIFY_RE)),
    ),
    forbidden=(
        ("pita_za_sljedeci_korak", _q(_ASKS_NEXT_STEP_RE)),
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
    ),
    prompt_must_be_unknown="da li je KONKRETAN dati broj rješenje",
    prompt_options_must_be="kratke tvrdnje (jeste / nije i zašto)",
    prompt_positive_example="Da li je $x=4$ rješenje jednačine $2x-3=5$?",
    prompt_forbidden_example="Riješi jednačinu $2x-3=5$.",
))

_register(FamilyContract(
    family_id="identify_next_step",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik bira SLJEDEĆI ispravan korak postupka",
    student_must_find=("next_step",),
    answer_kind=("short_text",),
    task_form=("construction_step", "method_selection"),
    required=(
        ("pita_za_sljedeci_korak", _q(_ASKS_NEXT_STEP_RE)),
        ("opcije_su_opisi", _opts_prose),
    ),
    forbidden=(),
    prompt_must_be_unknown="koji je sljedeći ispravan korak",
    prompt_options_must_be="kratki opisi radnji — NIKAD samo brojevi",
    prompt_positive_example="Koji je sljedeći korak nakon što obje strane podijeliš sa 2?",
    prompt_forbidden_example="Izračunaj konačno rješenje jednačine.",
))

_register(FamilyContract(
    family_id="translate_to_equation",
    objective="učenik prevodi rečenicu u jednačinu",
    student_must_find=("formula",),
    answer_kind=("expression",),
    task_form=("interpretation",),
    required=(),
    forbidden=(
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
    ),
    prompt_must_be_unknown="koja jednačina opisuje opisanu situaciju",
    prompt_options_must_be="jednačine — NIKAD konačan broj",
    prompt_positive_example="Koja jednačina opisuje: broj uvećan za 7 daje 19?",
    prompt_forbidden_example="Izračunaj taj broj.",
))


# ===== GEOMETRIJA ==========================================================

_register(FamilyContract(
    family_id="direct_formula_application",
    objective="učenik uvrštava date podatke u formulu i računa",
    student_must_find=("value",),
    answer_kind=("integer", "decimal", "expression"),
    task_form=("direct_calculation",),
    required=(),
    forbidden=(
        ("pita_koja_formula", _q(_ASKS_FORMULA_RE)),
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
    ),
    prompt_must_be_unknown="brojčani rezultat (obim, površina, zapremina...)",
    prompt_options_must_be="brojčani rezultati s ispravnom jedinicom",
    prompt_positive_example="Izračunaj površinu pravougaonika sa $a=5\\,\\text{cm}$ i $b=3\\,\\text{cm}$.",
    prompt_forbidden_example="Koja formula se koristi za površinu pravougaonika?",
))

_register(FamilyContract(
    family_id="find_missing_dimension",
    objective="učenik iz poznatog rezultata računa nepoznatu dimenziju",
    student_must_find=("missing_dimension",),
    answer_kind=("integer", "decimal"),
    task_form=("missing_value",),
    required=(),
    forbidden=(
        ("pita_koja_formula", _q(_ASKS_FORMULA_RE)),
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
    ),
    prompt_must_be_unknown="nepoznata dužina/dimenzija",
    prompt_options_must_be="moguće dužine s jedinicom",
    prompt_positive_example="Površina pravougaonika je $20\\,\\text{cm}^2$, a stranica $a=4\\,\\text{cm}$. Kolika je stranica $b$?",
    prompt_forbidden_example="Koja formula povezuje površinu i stranice?",
))

_register(FamilyContract(
    family_id="inverse_formula_problem",
    objective="učenik iz površine/zapremine ide nazad do ivice ili poluprečnika",
    student_must_find=("missing_dimension",),
    answer_kind=("integer", "decimal", "expression"),
    task_form=("missing_value",),
    required=(),
    forbidden=(
        ("pita_koja_formula", _q(_ASKS_FORMULA_RE)),
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
    ),
    prompt_must_be_unknown="ivica/poluprečnik izračunat iz date površine ili zapremine",
    prompt_options_must_be="moguće dužine s jedinicom",
    prompt_positive_example="Zapremina kocke je $27\\,\\text{cm}^3$. Kolika je njena ivica?",
    prompt_forbidden_example="Koja formula daje zapreminu kocke?",
))

_register(FamilyContract(
    family_id="choose_correct_formula",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik bira ISPRAVNU FORMULU, ne računa rezultat",
    student_must_find=("formula",),
    answer_kind=("formula", "expression", "short_text"),
    task_form=("recognition", "method_selection"),
    required=(
        ("pita_koja_formula_ili_korak",
         lambda t: bool(_ASKS_FORMULA_RE.search(t.question) or _ASKS_NEXT_STEP_RE.search(t.question)
                        or _ASKS_STATEMENT_RE.search(t.question))),
    ),
    forbidden=(
        ("opcije_su_goli_brojevi", lambda t: _majority(t.options, is_numeric_option)),
    ),
    prompt_must_be_unknown="koja formula/postupak odgovara zadanoj figuri ili tijelu",
    prompt_options_must_be="formule ili kratki opisi — NIKAD goli brojevi",
    prompt_positive_example="Koja formula daje zapreminu piramide?",
    prompt_forbidden_example="Izračunaj zapreminu piramide sa $B=12$ i $H=5$.",
))

_register(FamilyContract(
    family_id="unit_conversion",
    objective="učenik pretvara mjerne jedinice",
    student_must_find=("unit_value",),
    answer_kind=("integer", "decimal"),
    task_form=("direct_calculation",),
    required=(
        ("spominje_jedinice", _q(_UNIT_RE)),
    ),
    forbidden=(
        ("pita_koja_formula", _q(_ASKS_FORMULA_RE)),
    ),
    prompt_must_be_unknown="vrijednost izražena u drugoj mjernoj jedinici",
    prompt_options_must_be="vrijednosti s jedinicama",
    prompt_positive_example="Koliko je $2{,}5\\,\\text{dm}^2$ izraženo u $\\text{cm}^2$?",
    prompt_forbidden_example="Koja formula daje površinu kvadrata?",
))

_register(FamilyContract(
    family_id="compare_figures",
    objective="učenik upoređuje dvije figure/tijela",
    student_must_find=("comparison",),
    answer_kind=("short_text", "integer"),
    task_form=("recognition",),
    required=(
        ("pita_za_poredjenje", _q(_ASKS_COMPARE_RE)),
    ),
    forbidden=(
        ("pita_koja_formula", _q(_ASKS_FORMULA_RE)),
    ),
    prompt_must_be_unknown="koja figura ima veći obim/površinu/zapreminu",
    prompt_options_must_be="kratke tvrdnje ili vrijednosti",
    prompt_positive_example="Koji lik ima veću površinu: kvadrat stranice $4\\,\\text{cm}$ ili pravougaonik $5\\times3\\,\\text{cm}$?",
    prompt_forbidden_example="Koja formula daje površinu kvadrata?",
))

_register(FamilyContract(
    family_id="detect_formula_error",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik pronalazi grešku u ponuđenom postupku/formuli",
    student_must_find=("incorrect_step",),
    answer_kind=("short_text",),
    task_form=("error_detection",),
    required=(
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
        ("opcije_su_opisi", _opts_prose),
    ),
    forbidden=(),
    prompt_must_be_unknown="gdje je greška u prikazanom postupku",
    prompt_options_must_be="opisi greške ili ispravke — NIKAD samo četiri broja",
    prompt_positive_example="Učenik je zapreminu kocke računao kao $V=6a^2$. Šta je pogriješio?",
    prompt_forbidden_example="Izračunaj zapreminu kocke ivice $3\\,\\text{cm}$.",
))

_register(FamilyContract(
    family_id="practical_geometry_problem",
    objective="učenik primjenjuje geometriju u životnoj situaciji",
    student_must_find=("value",),
    answer_kind=("integer", "decimal"),
    task_form=("word_problem",),
    required=(
        ("ima_zivotni_kontekst", _has_context),
    ),
    forbidden=(
        ("pita_koja_formula", _q(_ASKS_FORMULA_RE)),
    ),
    prompt_must_be_unknown="tražena veličina u opisanoj situaciji",
    prompt_options_must_be="vrijednosti s ispravnom jedinicom",
    prompt_positive_example="Bašta je pravougaonik $8\\times5\\,\\text{m}$. Koliko metara ograde treba?",
    prompt_forbidden_example="Izračunaj obim pravougaonika $a=8$, $b=5$.",
))


# ===== OPŠTE (fallback) ====================================================

_register(FamilyContract(
    family_id="direct_computation",
    objective="učenik direktno primjenjuje pravilo iz lekcije",
    student_must_find=("value",),
    answer_kind=("integer", "decimal", "fraction", "expression"),
    task_form=("direct_calculation",),
    required=(),
    forbidden=(
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
        ("pita_za_tvrdnju", _q(_ASKS_STATEMENT_RE)),
        ("pita_za_metodu", _q(_ASKS_METHOD_RE)),
    ),
    prompt_must_be_unknown="rezultat direktne primjene pravila",
    prompt_options_must_be="moguće vrijednosti rezultata",
    prompt_positive_example="Izračunaj $12 \\cdot 4$.",
    prompt_forbidden_example="Koja tvrdnja o množenju je tačna?",
))

_register(FamilyContract(
    family_id="find_missing_value",
    objective="učenik pronalazi nedostajuću vrijednost u zadanoj vezi",
    student_must_find=("value", "missing_numerator", "missing_denominator"),
    answer_kind=("integer", "decimal", "fraction"),
    task_form=("missing_value",),
    required=(),
    forbidden=(
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
        ("pita_za_tvrdnju", _q(_ASKS_STATEMENT_RE)),
    ),
    prompt_must_be_unknown="nedostajuća vrijednost",
    prompt_options_must_be="moguće vrijednosti",
    prompt_positive_example="Dopuni: $7 + \\square = 15$.",
    prompt_forbidden_example="Koja tvrdnja o sabiranju je tačna?",
))

_register(FamilyContract(
    family_id="recognize_correct_statement",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik bira TAČNU TVRDNJU o pojmu iz lekcije",
    student_must_find=("statement",),
    answer_kind=("short_text",),
    task_form=("recognition",),
    required=(
        ("pita_za_tvrdnju_ili_prepoznavanje",
         lambda t: bool(_ASKS_STATEMENT_RE.search(t.question) or _ASKS_EQUIVALENT_RE.search(t.question))),
        ("opcije_su_opisi", _opts_prose),
    ),
    forbidden=(),
    prompt_must_be_unknown="koja je od ponuđenih tvrdnji tačna",
    prompt_options_must_be="cijele tvrdnje/rečenice — NIKAD goli brojevi",
    prompt_positive_example="Koja tvrdnja o uniji skupova je tačna?",
    prompt_forbidden_example="Izračunaj $A \\cup B$ za date skupove.",
))

_register(FamilyContract(
    family_id="detect_student_error",
    question_numeric_policy="allow_intentional_mismatch",
    objective="učenik pronalazi grešku u TUĐEM riješenom postupku",
    student_must_find=("incorrect_step",),
    answer_kind=("short_text",),
    task_form=("error_detection",),
    required=(
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
        ("opcije_su_opisi", _opts_prose),
    ),
    forbidden=(),
    prompt_must_be_unknown="gdje je greška u prikazanom rješenju",
    prompt_options_must_be="opisi greške ili ispravke — NIKAD samo četiri rezultata",
    prompt_positive_example="Učenik je napisao $\\frac{1}{2}+\\frac{1}{3}=\\frac{2}{5}$. Šta je pogriješio?",
    prompt_forbidden_example="Izračunaj $\\frac{1}{2}+\\frac{1}{3}$.",
))

_register(FamilyContract(
    family_id="compare_or_order",
    objective="učenik upoređuje ili poređa vrijednosti",
    student_must_find=("comparison",),
    answer_kind=("short_text", "integer", "fraction"),
    task_form=("recognition",),
    required=(
        ("pita_za_poredjenje", _q(_ASKS_COMPARE_RE)),
    ),
    forbidden=(
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
    ),
    prompt_must_be_unknown="koja je vrijednost veća/manja ili koji je ispravan poredak",
    prompt_options_must_be="vrijednosti ili opisi poretka",
    prompt_positive_example="Koji je broj veći: $-5$ ili $-3$?",
    prompt_forbidden_example="Izračunaj $-5 + (-3)$.",
))

_register(FamilyContract(
    family_id="word_problem",
    objective="učenik rješava tekstualni zadatak iz lekcije",
    student_must_find=("value",),
    answer_kind=("integer", "decimal", "fraction"),
    task_form=("word_problem",),
    required=(
        ("ima_zivotni_kontekst", _has_context),
    ),
    forbidden=(
        ("pita_za_gresku", _q(_ASKS_ERROR_RE)),
        ("pita_za_tvrdnju", _q(_ASKS_STATEMENT_RE)),
    ),
    prompt_must_be_unknown="tražena veličina u opisanoj situaciji",
    prompt_options_must_be="moguće vrijednosti odgovora",
    prompt_positive_example="Amar ima 12 KM i kupi svesku za 5 KM. Koliko mu ostaje?",
    prompt_forbidden_example="Izračunaj $12-5$.",
))


# ---------------------------------------------------------------------------
# JAVNI API
# ---------------------------------------------------------------------------

def contract_for(family_id):
    return CONTRACTS.get(family_id)


def missing_contracts():
    """Porodice iz kataloga koje NEMAJU ugovor — mora biti prazno (test)."""
    return sorted(set(FAMILY_DESCRIPTIONS) - set(CONTRACTS))


def question_numeric_policy(family_id):
    """Server-derived politika numeričke provjere za TEKST PITANJA date
    porodice — "check" ili "allow_intentional_mismatch" (vidi FamilyContract
    docstring). Nepoznata/prazna porodica → "check" (sigurniji podrazumijevani
    izbor: bolje odbiti sumnjivo nego pustiti dokazano pogrešnu činjenicu)."""
    contract = CONTRACTS.get(family_id)
    if contract is None:
        return "check"
    return contract.question_numeric_policy


def validate_task_family(family_id, question, option_texts, correct_option_index,
                         expected_answer="", difficulty="", declared=None):
    """Baca FamilyContractError ako zadatak ne odgovara dodijeljenoj porodici.

    `option_texts` su sanitizovani tekstovi PRIJE miješanja, `correct_option_index`
    je indeks tačne opcije u toj listi. `declared` je dict internih metapodataka
    koje je model vratio (task_family/student_must_find/answer_kind/task_form) —
    može biti None (stariji odgovori) i tada se preskače samo unakrsna provjera
    metapodataka, dok STRUKTURNA provjera i dalje važi.

    Nepoznata porodica (bez ugovora) prolazi — bolje pustiti nego blokirati
    lekciju zbog nedostajućeg ugovora; test missing_contracts() to sprječava.
    """
    if not family_id:
        return
    contract = CONTRACTS.get(family_id)
    if contract is None:
        return

    if not option_texts or not (0 <= correct_option_index < len(option_texts)):
        raise FamilyContractError(f"{family_id}: neispravan indeks tačne opcije")

    view = TaskView(
        question=question or "",
        options=list(option_texts),
        correct_option_text=option_texts[correct_option_index],
        expected_answer=expected_answer or "",
        difficulty=difficulty or "",
    )

    # 1) Unakrsna provjera deklarisanih metapodataka — vidi hijerarhiju
    #    povjerenja u docstringu FamilyContract. task_form I student_must_find
    #    su NAMJERNO izostavljeni iz ove provjere: oba su opisne pedagoške
    #    oznake bez objektivnog kriterija istinitosti (živi nalazi: model je
    #    ispravno klasifikovao zadatak, samo drugom validnom riječju, za obje
    #    — task_form="direct_calculation" umjesto "recognition", i
    #    student_must_find="ordered_pair" umjesto "statement") i NIKAD ne
    #    smiju same odbiti zadatak koji je već prošao strukturnu (vidljivu)
    #    provjeru ispod. Samo TASK_FAMILY (identitet) i ANSWER_KIND (objektivno
    #    provjerljivo) ostaju autoritativni.
    if declared:
        declared_family = (declared.get("task_family") or "").strip()
        if declared_family and declared_family != family_id:
            raise FamilyContractError(
                f"{family_id}: model deklarisao drugu porodicu ({declared_family})"
            )

        # answer_kind: STROGO samo kad je stvarno kontradiktorno objektivno
        # prepoznatom tipu tačnog odgovora — ne protiv statične liste po
        # porodici (ista klasa propusta kao stari task_form/student_must_find
        # ugovor bi se ponovila da smo ovdje samo provjeravali članstvo u tuple-u).
        declared_ak = (declared.get("answer_kind") or "").strip()
        if declared_ak:
            actual_kind = detected_answer_kind(view.correct_option_text)
            if actual_kind and declared_ak != actual_kind:
                raise FamilyContractError(
                    f"{family_id}: answer_kind={declared_ak} u suprotnosti sa stvarnim "
                    f"tipom tačnog odgovora ({actual_kind})"
                )

    # 2) Zabranjeni oblici — nijedan ne smije biti prisutan.
    for name, predicate in contract.forbidden:
        if predicate(view):
            raise FamilyContractError(f"{family_id}: zabranjen oblik '{name}'")

    # 3) Obavezni oblici — svi moraju biti prisutni (prazno za fallback ugovore).
    for name, predicate in contract.required:
        if not predicate(view):
            raise FamilyContractError(f"{family_id}: nedostaje obavezan oblik '{name}'")


def prompt_block(family_id):
    """Kratke, konkretne instrukcije za DODIJELJENU porodicu (samo za nju —
    prompt nikad ne nosi cijeli katalog)."""
    contract = CONTRACTS.get(family_id)
    if contract is None:
        return ""
    lines = [
        f"CILJ PORODICE: {contract.objective}.",
        f"UČENIK MORA ODREDITI: {contract.prompt_must_be_unknown}.",
        f"OPCIJE MORAJU BITI: {contract.prompt_options_must_be}.",
    ]
    if contract.prompt_positive_example:
        lines.append(f"ISPRAVAN PRIMJER: {contract.prompt_positive_example}")
    if contract.prompt_forbidden_example:
        lines.append(f"ZABRANJEN PRIMJER (to NIJE ova porodica): {contract.prompt_forbidden_example}")
    return "\n".join(lines)
