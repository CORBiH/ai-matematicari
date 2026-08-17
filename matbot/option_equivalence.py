"""Deterministička provjera SEMANTIČKE (ne samo tekstualne) jednakosti dvije
multiple-choice opcije (Defekt 4, živi produkcijski nalaz — lekcija
„Dijagonala kvadrata“, 8. razred):

  • "$8\\sqrt{2}\\,\\text{cm}$" i "$11,3\\,\\text{cm}$" predstavljaju ISTU
    vrijednost ($8\\sqrt{2}\\approx11,3137$, zaokruženo na jednu decimalu daje
    $11,3$) — dvije "različite" opcije, ista tačna vrijednost.
  • "$d=a\\sqrt{2}$" i "$d=\\sqrt{2}a$" su ALGEBARSKI identični (komutativno
    množenje u drugom poretku).

Nijedna postojeća provjera (mathsafe, mathcheck, FamilyContract) ne hvata ovo:
mathcheck poredi LANAC jednakosti UNUTAR jednog izraza, ne DVIJE odvojene
opcije jedna s drugom; tekstualno poređenje ne prepoznaje ni zaokruživanje ni
komutativnost.

TRI nezavisna, sigurna testa (OR — bilo koji dovoljan da se opcije proglase
ekvivalentnim):
  1. NUMERIČKA jednakost obje vrijednosti (ponovo koristi restricted-AST
     evaluator iz matbot/mathcheck.py — NIKAD eval()), s tolerancijom
     izvedenom iz decimalne preciznosti (isti princip kao mathcheck._tolerance).
  2. SIMBOLIČKA (komutativna) jednakost: izraz se parsira u ograničeno
     kanonsko stablo (broj/promjenljiva/+/-/*//^/\\sqrt) gdje se djeca
     komutativnih operacija (+, *) SORTIRAJU prije poređenja — pa
     "a\\sqrt{2}" i "\\sqrt{2}a" postaju STRUKTURNO identični.
  3. SKUP-RJEŠENJE jednakost (DISC talas, RC1): gola vrijednost, jednočlan
     skup ({11} i \\{11\\}), x=c, relacija i intervalni zapis kanonikalizuju
     se ISTOM zatvorenom solve gramatikom (mcq_integrity.continuous_answer_set)
     u kanonski kontinuirani skup — isti skup je isti odgovor pod svakim
     domenom. Domen-osjetljiva jednakost (x=-1 vs -2<x<0 nad Z) se ovdje
     NAMJERNO ne tvrdi — nju presuđuje evaluate_linear_solve_mcq s domenom.

Kad NIJEDAN test ne može sigurno dokazati jednakost, opcije se smatraju
RAZLIČITIM — modul NIKAD ne "pogađa" da su dvije opcije iste; nedokazivost
nije dokaz jednakosti (isti princip kao mathcheck.py: preskoči, ne pogađaj).
"""
import re
import unicodedata

from matbot import mathcheck, mcq_integrity

_UNIT_STRIP_RE = re.compile(
    r"\\(?:text|mathrm|mathit)\s*\{[^{}]*\}\s*(?:\^\s*(?:\{[^{}]*\}|\w))?"
)
_SPACING_STRIP_RE = re.compile(r"\\,|\\;|\\!|\\quad|\\qquad|\\ |\\left|\\right")
_DISALLOWED_RE = re.compile(
    r"\\circ|\\degree|°"
    r"|\\%|%"
    r"|\\sum|\\int|\\lim"
    r"|\\begin|\\end"
    r"|\\sqrt\s*\["
    r"|\\log|\\ln|\\sin|\\cos|\\tan"
    r"|\.\.\.|\\dots|\\ldots"
    r"|[<>≤≥≠]|\\le|\\ge|\\ne|\\neq|\\leq|\\geq"
)
_NUM_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?")


class _Unknown(Exception):
    """Izraz se ne može sigurno kanonikalizovati/tokenizovati — NIJE dokaz
    nejednakosti, samo signal da se pouzdano ne zna."""


# ---------------------------------------------------------------------------
# DOSLOVNA (tekstualna) JEDINSTVENOST — case-SENSITIVE
#
# ŽIVI NALAZ (poziv 12 fokusiranog live testa, lekcija „Centar, poluprečnik i
# prečnik“): validan `choose_correct_formula` zadatak s opcijama
#     $R=2r$   $r=2R$   $R=r^2$   $O=\pi r^2$
# bio je ODBIJEN kao „duple opcije“ jer je stara provjera radila
# `text.lower()` prije poređenja, pa su `$R=2r$` i `$r=2R$` oba postali
# `$r=2r$`. U ovom projektu VELIČINA SLOVA NOSI ZNAČENJE:
#     r/R (poluprečnik/prečnik), d/D (dijagonala strane/prostorna),
#     P/p, O/o, B/b, H/h
# pa se matematički tekst NIKAD ne smije spuštati u mala slova.
#
# Normalizacija koja OSTAJE (sigurna, ne mijenja značenje):
#   • Unicode NFC (isti znak, isti zapis)
#   • uklanjanje SVIH razmaka — dvije opcije koje se razlikuju samo po
#     razmacima („$R = 2r$“ i „$R=2r$“) jesu ista opcija
# Sve ostalo (velika/mala slova, simboli, MathJax struktura) ostaje netaknuto.
# ---------------------------------------------------------------------------

_ALL_WHITESPACE_RE = re.compile(r"\s+")


def normalized_option_key(text):
    """Ključ za DOSLOVNO poređenje dvije opcije. ČUVA veličinu slova."""
    key = unicodedata.normalize("NFC", text or "").strip()
    return _ALL_WHITESPACE_RE.sub("", key)


def find_textual_duplicate_pairs(option_texts):
    """Vrati [(i, j)] parove opcija koje su DOSLOVNO iste nakon sigurne
    normalizacije. Ovo hvata ono što semantička provjera NE MOŽE — dvije
    identične PROZNE opcije („Sabrao je brojnike.“), jer se proza ne može
    numerički ni simbolički kanonikalizovati."""
    seen = {}
    pairs = []
    for index, text in enumerate(option_texts):
        key = normalized_option_key(text)
        if not key:
            continue
        if key in seen:
            pairs.append((seen[key], index))
        else:
            seen[key] = index
    return pairs


def _strip_math_delimiters(text):
    t = (text or "").strip()
    if len(t) >= 2 and t.startswith("$") and t.endswith("$"):
        return t[1:-1]
    return t


def _value_expression(expr):
    """Ako izraz ima oblik 'nešto=vrijednost' (npr. 'd=a\\sqrt2'), poredi se
    SAMO dio poslije POSLJEDNJEG '=' — to je stvarna formula/vrijednost koju
    opcija predlaže."""
    idx = expr.rfind("=")
    return expr[idx + 1:].strip() if idx != -1 else expr.strip()


def _defined_quantity(expr):
    """Lijeva strana jednakosti — VELIČINA koju opcija definiše, ili None kad
    opcija nije oblika 'nešto=vrijednost'.

    ZAŠTO POSTOJI: raniji komentar uz _value_expression je pretpostavljao da je
    „lijeva strana ista u sve četiri opcije, pa nije predmet poređenja“. Ta
    pretpostavka PADA upravo kod zadataka o notaciji, gdje je lijeva strana ono
    što se ispituje. Posljedica (izmjereno): `$D=a\\sqrt3$` i `$d=a\\sqrt3$`,
    `$P=ab$` i `$p=ab$`, `$B=a^2$` i `$b=a^2$` bili su proglašeni ISTOM
    opcijom jer im je desna strana identična — pa je validan zadatak odbijen.

    Dvije opcije koje definišu RAZLIČITE veličine tvrde RAZLIČITE stvari, čak i
    kad im se desne strane poklapaju. Poređenje čuva veličinu slova."""
    idx = expr.rfind("=")
    if idx == -1:
        return None
    left = expr[:idx].strip()
    return _ALL_WHITESPACE_RE.sub("", left) or None


_UNIT_CAPTURE_RE = re.compile(
    r"\\(?:text|mathrm|mathit)\s*\{([^{}]*)\}\s*(\^\s*(?:\{[^{}]*\}|\w))?"
)


def _extract_units(expr):
    """Vrati normalizovan tuple mjernih jedinica (s eksponentom, npr. 'cm^2')
    prisutnih u izrazu — koristi se da "16 cm" i "16 cm^2" NIKAD ne budu
    proglašeni ekvivalentnim samo zato što je brojčani dio isti."""
    units = []
    for m in _UNIT_CAPTURE_RE.finditer(expr):
        unit = m.group(1).strip()
        exp = (m.group(2) or "").replace(" ", "").replace("{", "").replace("}", "")
        units.append(unit + exp)
    return tuple(units)


def _clean_expression(expr):
    cleaned = _UNIT_STRIP_RE.sub(" ", expr)
    cleaned = _SPACING_STRIP_RE.sub(" ", cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# 1) NUMERIČKA jednakost — ponovo koristi mathcheck restricted-AST evaluator
# ---------------------------------------------------------------------------

def _numeric_candidates(expr):
    cleaned = _clean_expression(expr)
    if _DISALLOWED_RE.search(cleaned):
        return None
    try:
        return mathcheck.evaluate_candidates(cleaned)
    except Exception:
        return None


# Tolerancija zaokruživanja se računa iz BROJA DECIMALA ZAPISA, pa je
# APSOLUTNA — a vrijednosti mogu biti proizvoljno male. Za `7,2 \cdot 10^{-4}`
# zapis ima jednu decimalu, pa je tolerancija 0,055, dok su same vrijednosti
# reda 0,0007: tada su SVE male vrijednosti međusobno „jednake“.
#
# MJERENO (lekcija „Naučni zapis broja“, 12 živih turnova): četiri očito
# različite opcije — $7,2\cdot 10^{-4}$, $7,2\cdot 10^{-5}$, $7,2\cdot 10^{-3}$,
# $6,2\cdot 10^{-4}$ — proglašavane su svih šest parova jednakim, pa je paket
# odbijan kao „više tačnih opcija“. To je bio uzrok 5 od 6 odbijanja te lekcije.
# Deterministički generator je isti problem odavno zaobilazio time što NE nudi
# negativne izložioce (vidi komentar u matbot/deterministic/powers.py), ali
# model-put to ne može — mali brojevi su upravo gradivo te lekcije.
#
# POPRAVKA NE POPUŠTA PROVJERU, NEGO JE ČINI TAČNIJOM: tolerancija zaokruživanja
# ne smije biti reda veličine samih vrijednosti. Dva broja koja se razlikuju za
# više od 5 % vlastite veličine nisu „isti broj drukčije zaokružen“. Jednake
# vrijednosti (razlika 0) ostaju jednake pod bilo kojom tolerancijom.
_ROUNDING_MAGNITUDE_SHARE = 0.05


def _numeric_tolerance(expr_a, expr_b, magnitude):
    relative_floor = max(1e-9 * abs(magnitude), 1e-9)
    places = mathcheck._decimal_places(expr_a, expr_b)
    if not places:
        return relative_floor
    rounding = 0.5 * (10 ** -places) * 1.1
    magnitude_cap = _ROUNDING_MAGNITUDE_SHARE * abs(magnitude)
    return max(min(rounding, magnitude_cap), relative_floor)


# ---------------------------------------------------------------------------
# 2) SIMBOLIČKA (komutativna) jednakost — uzak, siguran tokenizator/parser
#    (NIKAD eval/ast.parse nad proizvoljnim kodom — samo naš vlastiti,
#    ograničen rekurzivni spust nad whitelist gramatikom).
# ---------------------------------------------------------------------------

# NIZ VELIKIH SLOVA JE OZNAKA, NE PROIZVOD (živi release gate
# `release_gate_grade7_rotating`, lekcija o podudarnosti trouglova — SSU).
# Tokenizator je svako slovo pretvarao u zasebnu promjenljivu, pa je opcija
# `SUS` bila proizvod S·U·S, a `SSU` proizvod S·S·U — komutativno JEDNAKI. Time
# je server DOKAZIVAO `semantically_duplicate_options (symbolic_commutative)`
# nad dva različita kriterija podudarnosti i lekcija nije mogla objaviti nijedan
# ispravan MCQ. Recenzent je s pravom vraćao `correct` s nepromijenjenim
# paketom: ukloniti jednu od te dvije opcije znači uništiti zadatak.
#
# Ista greška je proglašavala duplikatima i oznake tačaka: `ABC`/`ACB`/`BAC`/
# `CAB` bile su svih šest parova „dokazano ekvivalentne“.
#
# Množenje se u ovom projektu UVIJEK piše sa `\cdot` (matbot/rules.py), pa
# implicitno množenje susjednih VELIKIH slova nije zapis nego pogađanje; niz
# velikih slova je ime objekta (`AB`, `ABC`, `SUS`) — isto pravilo koje
# `mathsafe.prose_words_in_expression` već koristi kad ih izuzima iz provjere
# proze. Mala slova (`ab` vs `ba`) ostaju komutativan proizvod, kako i jesu u
# algebri, a `$AB \cdot CD$` vs `$CD \cdot AB$` ostaje dokazan duplikat.
_UPPERCASE_LABEL_RE = re.compile(r"[A-Z]{2,}")


def _tokenize(expr):
    tokens = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if expr.startswith("\\sqrt", i):
            tokens.append(("SQRT", None)); i += 5; continue
        if expr.startswith("\\frac", i):
            tokens.append(("FRAC", None)); i += 5; continue
        if expr.startswith("\\cdot", i):
            tokens.append(("OP", "*")); i += 5; continue
        if expr.startswith("\\times", i):
            tokens.append(("OP", "*")); i += 6; continue
        if expr.startswith("\\pi", i):
            tokens.append(("PI", None)); i += 3; continue
        if ch == "\\":
            raise _Unknown("nepoznata LaTeX komanda")
        if ch == "(":
            tokens.append(("LP", ch)); i += 1; continue
        if ch == ")":
            tokens.append(("RP", ch)); i += 1; continue
        if ch == "{":
            tokens.append(("LB", ch)); i += 1; continue
        if ch == "}":
            tokens.append(("RB", ch)); i += 1; continue
        if ch in "+-*/^":
            tokens.append(("OP", ch)); i += 1; continue
        m = _NUM_TOKEN_RE.match(expr, i)
        if m:
            tokens.append(("NUM", m.group(0).replace(",", "."))); i = m.end(); continue
        m = _UPPERCASE_LABEL_RE.match(expr, i)
        if m:
            tokens.append(("VAR", m.group(0))); i = m.end(); continue
        if ch.isalpha():
            tokens.append(("VAR", ch)); i += 1; continue
        raise _Unknown(f"neočekivan znak {ch!r}")
    return tokens


_FACTOR_START_KINDS = ("NUM", "VAR", "LP", "LB", "SQRT", "FRAC", "PI")


class _TokenParser:
    """Rekurzivni spust nad tokenima -> kanonsko (hashable) ugniježdeno
    tuple-stablo. + i * su komutativni: njihova djeca se SORTIRAJU (po
    repr-u kanonskog podstabla) prije poređenja, čime "a\\sqrt2" i "\\sqrt2a"
    (oba: mul(var(a), sqrt(num(2)))) postaju STRUKTURNO identični bez obzira
    na originalni poredak pisanja. - i / NISU komutativni — ostaju uređeni
    (lijevo-desno) binarni čvorovi."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def _advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect(self, kind):
        tok = self._advance()
        if tok[0] != kind:
            raise _Unknown(f"očekivano {kind}, dobijeno {tok}")
        return tok

    def parse_expr(self):
        terms = [self.parse_term()]
        signs = []
        while self._peek()[0] == "OP" and self._peek()[1] in ("+", "-"):
            signs.append(self._advance()[1])
            terms.append(self.parse_term())
        if not signs:
            return terms[0]
        canon = [terms[0]]
        for sign, t in zip(signs, terms[1:]):
            canon.append(t if sign == "+" else ("neg", t))
        return ("add", tuple(sorted(canon, key=repr)))

    def _starts_factor(self):
        return self._peek()[0] in _FACTOR_START_KINDS

    def parse_term(self):
        factors = [self.parse_signed_factor()]
        ops = []
        while True:
            kind, val = self._peek()
            if kind == "OP" and val in ("*", "/"):
                ops.append(self._advance()[1])
                factors.append(self.parse_signed_factor())
            elif self._starts_factor():
                ops.append("*")  # implicitno množenje (npr. "2a", "a\\sqrt2")
                factors.append(self.parse_signed_factor())
            else:
                break
        if not ops:
            return factors[0]
        result = factors[0]
        mul_group = [factors[0]]
        for op, f in zip(ops, factors[1:]):
            if op == "*":
                mul_group.append(f)
            else:  # "/" — nekomutativno, isprazni prikupljenu * grupu prvo
                result = mul_group[0] if len(mul_group) == 1 else (
                    "mul", tuple(sorted(mul_group, key=repr)))
                result = ("div", result, f)
                mul_group = [result]
        if len(mul_group) == 1:
            return mul_group[0]
        return ("mul", tuple(sorted(mul_group, key=repr)))

    def parse_signed_factor(self):
        negate = False
        while self._peek()[0] == "OP" and self._peek()[1] in ("+", "-"):
            if self._advance()[1] == "-":
                negate = not negate
        base = self.parse_power()
        return ("neg", base) if negate else base

    def parse_power(self):
        base = self.parse_atom()
        if self._peek()[0] == "OP" and self._peek()[1] == "^":
            self._advance()
            exp = self.parse_atom()
            return ("pow", base, exp)
        return base

    def parse_atom(self):
        kind, val = self._advance()
        if kind == "NUM":
            return ("num", float(val))
        if kind == "VAR":
            return ("var", val)
        if kind == "PI":
            return ("var", "PI")
        if kind in ("LP", "LB"):
            inner = self.parse_expr()
            self._expect("RP" if kind == "LP" else "RB")
            return inner
        if kind == "SQRT":
            return ("sqrt", self.parse_atom())
        if kind == "FRAC":
            num = self.parse_atom()
            den = self.parse_atom()
            return ("div", num, den)
        raise _Unknown(f"neočekivan token {kind!r}")


def canonicalize_expression(latex_expr):
    """Vrati kanonski oblik izraza, ili None ako se ne može sigurno
    kanonikalizovati (nepodržana/nejednoznačna konstrukcija) — None znači
    "nepoznato", NIKAD "dokazano različito"."""
    try:
        cleaned = _clean_expression(latex_expr or "")
        if not cleaned.strip() or _DISALLOWED_RE.search(cleaned):
            return None
        tokens = _tokenize(cleaned)
        if not tokens:
            return None
        parser = _TokenParser(tokens)
        result = parser.parse_expr()
        if parser.pos != len(tokens):
            return None  # ostatak neparsiran — ne riskiraj pogrešnu kanonikalizaciju
        return result
    except _Unknown:
        return None
    except (ZeroDivisionError, RecursionError, IndexError):
        return None


# ---------------------------------------------------------------------------
# 3) SKUP-RJEŠENJE ekvivalencija (DISC talas, RC1) — ponovo koristi zatvorenu
#    solve gramatiku iz mcq_integrity, NIKAD novi paralelni parser.
# ---------------------------------------------------------------------------

def _solve_sets_equivalent(bare_a, bare_b):
    """True SAMO kad su oba zapisa pročitana kao kanonski KONTINUIRANI skup i
    skupovi su identični. Nepročitljivo = False (nepoznato, ne „različito“).
    Relacije u dvije RAZLIČITE nepoznate se ne proglašavaju istim odgovorom."""
    parsed_a = mcq_integrity.continuous_answer_set(bare_a)
    if parsed_a is None:
        return False
    parsed_b = mcq_integrity.continuous_answer_set(bare_b)
    if parsed_b is None:
        return False
    set_a, variable_a = parsed_a
    set_b, variable_b = parsed_b
    if (variable_a is not None and variable_b is not None
            and variable_a != variable_b):
        return False
    return set_a == set_b


# ---------------------------------------------------------------------------
# JAVNI API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 4) KONAČAN SKUP RJEŠENJA (produkcijski nalaz, ručni QA, „Jednačina x²=a“)
# ---------------------------------------------------------------------------
# Objavljen zadatak za $x^2=12$ ponudio je i $x \in \{-\sqrt{12}, \sqrt{12}\}$
# i $x \in \{-2\sqrt{3}, 2\sqrt{3}\}$, pa je učenik imao DVA tačna odgovora, a
# samo jedan je bio označen. Gola vrijednost $\sqrt{12}$ i $2\sqrt{3}$ su se već
# prepoznavale, ali skup se nikad nije rastavljao — čak ni puko preslagivanje
# $\{\sqrt{12}, -\sqrt{12}\}$ nije bilo prepoznato. `continuous_answer_state`
# pokriva KONTINUIRANE skupove (intervale), ne konačne.
#
# Elementi se NE porede novim parserom: koristi se ISTI dokazani primitiv
# (`options_are_equivalent` nad jednim elementom), pa konačan skup ne može
# tvrditi ekvivalenciju jaču od one koju projekat već ume dokazati.
_SET_PREFIX_RE = re.compile(
    r"^\s*(?:([A-Za-z]\w*)\s*(?:\\in|=|\\subseteq)\s*)?", re.IGNORECASE)
_PM_RE = re.compile(r"^\s*(?:([A-Za-z]\w*)\s*=\s*)?\\pm\s*(.+)$")


def _split_top_level(body):
    """Podijeli po zarezima na NULTOJ dubini vitičastih/oblih zagrada."""
    parts, depth, current = [], 0, []
    for char in body:
        if char in "{([":
            depth += 1
        elif char in "})]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def finite_answer_set(text):
    """Kanonski KONAČAN skup rješenja, ili None kad se ne može sigurno pročitati.

    None znači „nepoznato“, nikad „različito“ — isti princip nesigurnosti kao
    u ostatku modula."""
    bare = _strip_math_delimiters(text or "")
    if not bare.strip():
        return None
    prefix = _SET_PREFIX_RE.match(bare)
    # NEPOZNATA SE NE GUBI: „x ∈ {-2,2}“ i „y ∈ {-2,2}“ su tvrdnje o RAZLIČITIM
    # veličinama, dakle različiti odgovori — isti oprez koji već ima
    # `_solve_sets_equivalent`.
    variable = (prefix.group(1) if prefix else None) or None
    body = (bare[prefix.end():] if prefix else bare).strip()
    plus_minus = _PM_RE.match(bare)
    if plus_minus:
        element = (plus_minus.group(2) or "").strip()
        if not element:
            return None
        return (plus_minus.group(1) or None,
                tuple(sorted({f"+{element}", f"-{element}"})))
    for opening, closing in ((r"\{", r"\}"), ("{", "}")):
        if body.startswith(opening) and body.endswith(closing):
            inner = body[len(opening):-len(closing)]
            elements = _split_top_level(inner)
            if len(elements) < 2:
                return None       # jednočlan skup već pokriva continuous_answer_set
            return (variable, tuple(elements))
    return None


def _finite_sets_equivalent(text_a, text_b):
    """True SAMO kad su oba zapisa KONAČNI skupovi iste veličine i postoji
    bijekcija u kojoj je svaki par elemenata DOKAZANO ista vrijednost."""
    parsed_a = finite_answer_set(text_a)
    parsed_b = finite_answer_set(text_b)
    if parsed_a is None or parsed_b is None:
        return False
    variable_a, set_a = parsed_a
    variable_b, set_b = parsed_b
    if variable_a is not None and variable_b is not None and variable_a != variable_b:
        return False
    if len(set_a) != len(set_b):
        return False
    remaining = list(set_b)
    for element in set_a:
        match = None
        for candidate in remaining:
            if element.strip() == candidate.strip() or options_are_equivalent(
                    element, candidate):
                match = candidate
                break
        if match is None:
            return False
        remaining.remove(match)
    return not remaining


def options_are_equivalent(text_a, text_b):
    """Vrati True SAMO kad je SIGURNO DOKAZANO da dvije MC opcije predstavljaju
    istu vrijednost/izraz (numerički ili simbolički). Vraća False i kad su
    dokazano različite i kad se ne može dokazati — nikad se ne "pogađa"
    jednakost (isti princip nesigurnosti kao matbot/mathcheck.py)."""
    bare_a = _strip_math_delimiters(text_a)
    bare_b = _strip_math_delimiters(text_b)

    # Različita DEFINISANA veličina → različite tvrdnje, bez obzira na to što
    # im se desne strane poklapaju ($D=a\sqrt3$ vs $d=a\sqrt3$). Poređenje je
    # case-sensitive jer u ovom projektu r/R, d/D, P/p, O/o, B/b, H/h nose
    # različita značenja (vidi matbot/geometry_rules.py).
    lhs_a = _defined_quantity(bare_a)
    lhs_b = _defined_quantity(bare_b)
    if lhs_a is not None and lhs_b is not None and lhs_a != lhs_b:
        return False

    val_a = _value_expression(bare_a)
    val_b = _value_expression(bare_b)
    if not val_a or not val_b:
        return False

    units_a = _extract_units(val_a)
    units_b = _extract_units(val_b)
    if units_a and units_b and units_a != units_b:
        return False  # različite jedinice (npr. cm vs cm^2) — nikad "ista vrijednost"

    cands_a = _numeric_candidates(val_a)
    cands_b = _numeric_candidates(val_b)
    if cands_a is not None and cands_b is not None and cands_a and cands_b:
        magnitude = max(abs(v) for v in cands_a + cands_b) or 1.0
        tol = _numeric_tolerance(val_a, val_b, magnitude)
        return any(abs(a - b) <= tol for a in cands_a for b in cands_b)

    canon_a = canonicalize_expression(val_a)
    canon_b = canonicalize_expression(val_b)
    if canon_a is not None and canon_b is not None and canon_a == canon_b:
        return True
    # TREĆI nezavisni test (DISC talas, RC1): SKUP-RJEŠENJE ekvivalencija.
    # Živi nalazi B007/B012/B014: `$11$` i `$\{11\}$` (escaped vitičaste!) su
    # isti jednočlan odgovor, a nijedan od gornja dva testa ih ne čita.
    # `mcq_integrity.continuous_answer_set` kanonikalizuje golu vrijednost,
    # jednočlan skup, x=c, relaciju i intervalni zapis u ISTI kanonski skup —
    # dva zapisa istog KONTINUIRANOG skupa su isti odgovor pod svakim domenom,
    # pa je test siguran i bez domena zadatka. Domen-osjetljiva jednakost
    # (x=-1 vs -2<x<0 nad Z) se ovdje NAMJERNO ne tvrdi (nema dokaza domena).
    if _solve_sets_equivalent(bare_a, bare_b):
        return True
    # ČETVRTI test: KONAČAN skup rješenja (npr. dva korijena kvadratne).
    return _finite_sets_equivalent(text_a, text_b)


def find_equivalent_option_pairs(option_texts):
    """Vrati listu (i, j) indeksa parova opcija za koje je DOKAZANO da su
    semantički ekvivalentne (i < j). Prazna lista = sve četiri opcije su
    dokazano međusobno različite (ili se ne mogu dokazati različitim, što se
    OVDJE tretira kao razlika — vidi options_are_equivalent)."""
    pairs = []
    n = len(option_texts)
    for i in range(n):
        for j in range(i + 1, n):
            if options_are_equivalent(option_texts[i], option_texts[j]):
                pairs.append((i, j))
    return pairs


# ---------------------------------------------------------------------------
# DIJAGNOSTIKA (server-only logging) — KOJA vrsta ekvivalencije je dokazana,
# za strukturisane logove pri odbijanju (nikad se ne šalje u browser).
# ---------------------------------------------------------------------------

_FRACTION_SHAPE_RE = re.compile(
    r"^\\frac\s*\{[^{}]*\}\s*\{[^{}]*\}$|^-?\d+\s*/\s*-?\d+$"
)


def _looks_like_fraction_shape(expr):
    return bool(_FRACTION_SHAPE_RE.match((expr or "").strip()))


def _has_decimal_literal(expr):
    return bool(re.search(r"\d[.,]\d", expr or ""))


def classify_equivalence(text_a, text_b):
    """Vrati string koji opisuje KOJU vrstu dokazane ekvivalencije objašnjava
    options_are_equivalent(text_a, text_b) == True, ili None ako par NIJE
    dokazano ekvivalentan (uključujući "nepoznato"). SAMO za dijagnostiku/log
    — nikad ne utiče na samu odluku o odbijanju.

    Moguće vrijednosti: "textual" (identičan tekst opcije), "equivalent_fraction"
    (oba oblik razlomka, ista vrijednost), "numeric_exact_vs_rounded" (jedna
    strana ima decimalni zapis koji zaokružuje drugu), "numeric_exact"
    (obje strane brojčano jednake, bez razlike u zaokruživanju),
    "symbolic_commutative" (dokazano preko kanonskog stabla, ne brojčano)."""
    if not options_are_equivalent(text_a, text_b):
        return None
    if (text_a or "").strip() == (text_b or "").strip():
        return "textual"

    val_a = _value_expression(_strip_math_delimiters(text_a))
    val_b = _value_expression(_strip_math_delimiters(text_b))

    if _looks_like_fraction_shape(val_a) and _looks_like_fraction_shape(val_b):
        return "equivalent_fraction"

    cands_a = _numeric_candidates(val_a)
    cands_b = _numeric_candidates(val_b)
    if cands_a is not None and cands_b is not None and cands_a and cands_b:
        if _has_decimal_literal(val_a) != _has_decimal_literal(val_b):
            return "numeric_exact_vs_rounded"
        return "numeric_exact"

    canon_a = canonicalize_expression(val_a)
    canon_b = canonicalize_expression(val_b)
    if canon_a is not None and canon_a == canon_b:
        return "symbolic_commutative"
    # Jedini preostali način da options_are_equivalent bude True (DISC RC1).
    return "equivalent_solution_set"


def find_equivalent_option_pairs_with_types(option_texts):
    """Kao find_equivalent_option_pairs, ali svaki par nosi i klasifikaciju
    (i, j, tip) — koristi se ISKLJUČIVO za interne strukturisane logove."""
    result = []
    for i, j in find_equivalent_option_pairs(option_texts):
        result.append((i, j, classify_equivalence(option_texts[i], option_texts[j])))
    return result
