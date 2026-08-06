"""Deterministička provjera NUMERIČKE DOSLJEDNOSTI jednakosti u vidljivom tekstu.

ZAŠTO POSTOJI (živi nalaz, Explain, „Pravilni mnogougao“): model je naveo
ISPRAVNU formulu $P=\\frac{3a^2\\sqrt{3}}{2}$, a zatim za $a=4$ napisao lanac

    $P=\\frac{3\\cdot16\\sqrt{3}}{2}=48\\sqrt{3}\\approx83,14$

Tačno je $24\\sqrt{3}\\approx41,57$ — model je „zaboravio“ podijeliti s 2.
Formula je bila tačna, aritmetički lanac nije. Nijedna postojeća provjera
(MathJax sigurnost, terminologija, ugovor porodice) to ne može uhvatiti.

ŠTA OVAJ MODUL JESTE: uski, siguran *čuvar dosljednosti*. Uzima svaki $...$
segment, razbije ga na lanac dijelova razdvojenih znakom `=` ili `\\approx`,
numerički izračuna SAMO one dijelove koje sigurno može izračunati, i uporedi
susjedne izračunate vrijednosti. Neslaganje = odbijanje cijelog odgovora.

ŠTA OVAJ MODUL NIJE: dokazivač matematike. Izraz s promjenljivom
($c^2=a^2+b^2$, $x+7=15$) se PRESKAČE, ne „dokazuje“. Preskočen izraz nije
dokaz ispravnosti — samo odsustvo dokaza greške.

SIGURNOST: nikad `eval()`. Izraz se prevodi u ograničen Python podskup i
izvršava ručnim obilaskom AST-a uz strogu bijelu listu čvorova (samo
aritmetika, `sqrt(...)` i konstanta `PI`). Sve ostalo → nepodržano → preskoči.
"""
import ast
import math
import re
from decimal import Decimal, InvalidOperation

from matbot.mathsegments import INLINE, DISPLAY, TEXT, math_contents, tokenize_math

# Ludolfov broj: škola u BiH računa s π ≈ 3,14 (vidi referentni PDF), pa svaki
# izraz s π vrednujemo OBJEMA vrijednostima i prihvatamo ako se poklopi bilo
# koja — inače bi „$9\\pi \\approx 28,26$“ (tačno uz 3,14) bilo lažno odbijeno.
_PI_VALUES = (math.pi, 3.14)

# ---------------------------------------------------------------------------
# DEKLARISANA APROKSIMACIJA π (živi nalaz D35-2, poziv 19 kampanje od 35)
# ---------------------------------------------------------------------------
# Model je u istom odgovoru NAJAVIO „π\approx3,14“ pa onda napisao
# „$6\pi\approx18,85$“. Uz 3,14 tačan proizvod je 18,84; 18,85 dolazi od punog
# π. Oba broja su „tačna“ svaki za svoju konvenciju — greška je što odgovor
# MIJEŠA dvije konvencije u jednoj rečenici, i učenik koji ponovi račun s 3,14
# dobije drugi broj nego bot.
#
# Provjera je zato dvodijelna: prvo se u CIJELOM tekstu (i proza i matematika —
# deklaracija je u živom nalazu stajala baš u prozi, van svakog $...$) traži
# izričita deklaracija vrijednosti π; kad ona postoji, svi izrazi s π vrednuju
# se ISKLJUČIVO deklarisanom vrijednošću, a permisivno „prihvati bilo koju od
# dvije“ se gasi.
#
# Lookbehind na „nije slovo/cifra“ je bitan: „$2\pi\approx6,28$“ NIJE
# deklaracija vrijednosti π nego običan izračun, i ne smije pomjeriti konvenciju.
_DECLARED_PI_RE = re.compile(
    r"(?<![\w])(?:\\pi|π)\s*(?:\\approx|≈|=)\s*(\d+[.,]\d+)"
)
# Deklaracija se prihvata samo ako je stvarno aproksimacija π. Šire granice ne
# pogađamo — nepoznata vrijednost se ignoriše, ne „ispravlja“.
_PI_DECLARATION_MIN = Decimal("3.1")
_PI_DECLARATION_MAX = Decimal("3.2")


def declared_pi_values(text):
    """Vrati sortiranu torku vrijednosti π koje tekst IZRIČITO deklariše.

    Prazna torka = nema deklaracije, pa vrijedi ranije (permisivno) ponašanje.
    Parsira se preko Decimal-a da „3,14“ ne bi prošao kroz binarni float prije
    nego što uopšte znamo da je validna deklaracija."""
    if not text:
        return ()
    values = set()
    for match in _DECLARED_PI_RE.finditer(text):
        try:
            value = Decimal(match.group(1).replace(",", "."))
        except InvalidOperation:
            continue
        if _PI_DECLARATION_MIN <= value <= _PI_DECLARATION_MAX:
            values.add(float(value))
    return tuple(sorted(values))

# Relativna tolerancija za EGZAKTNO poređenje (oba dijela racionalna, bez
# decimalnog literala) — samo da apsorbuje šum binarnog zapisa.
_EXACT_REL_TOL = 1e-9

# Kad je relacija `\approx`, a nijedan dio nema decimalni literal iz kojeg bi
# se izvela preciznost, koristi se ova relativna toleranicija.
_APPROX_REL_TOL = 0.01


class _Unsupported(Exception):
    """Izraz sadrži nešto što ne umijemo sigurno izračunati — preskoči ga."""


class _MathError(Exception):
    """Izraz je matematički nevaljan (dijeljenje nulom, korijen negativnog)."""


# ---------------------------------------------------------------------------
# 1) IZDVAJANJE MATEMATIČKIH SEGMENATA ($...$ I $$...$$)
# ---------------------------------------------------------------------------
# Živi nalaz (Explain, docs/CURRENT_STATE.md C-5): stariji kod je dijelio
# tekst naivnim alternating-splitom na SVAKI pojedinačan '$', pa je par
# susjednih '$$' uvijek davao PARAN broj dijelova (dvije prazne "text"
# granice), a ova funkcija je tad vraćala PRAZNU listu — numerička provjera
# NIKAD nije ni pogledala sadržaj unutar $$...$$. Sada koristi zajednički
# tokenizator (matbot/mathsegments.py) koji ispravno prepoznaje OBA oblika.


def math_segments(text):
    """Vrati sadržaj svakog matematičkog segmenta (inline $...$ ILI display
    $$...$$), bez delimitera, redoslijedom pojavljivanja."""
    if not text or "$" not in text:
        return []
    return math_contents(tokenize_math(text))


# ---------------------------------------------------------------------------
# 2) LaTeX → ograničen Python izraz
# ---------------------------------------------------------------------------

# Uređeni par „(3,2)“ — zarez tu NIJE decimalni separator. Takav dio se ne
# vrednuje da ne bismo „(3,2)“ pretvorili u broj 3,2.
_ORDERED_PAIR_RE = re.compile(r"\(\s*-?\d+\s*,\s*-?\d+\s*\)")

# Konstrukcije koje ne umijemo (ili ne želimo) tumačiti numerički.
_UNSUPPORTED_RE = re.compile(
    r"\\circ|\\degree|°"          # uglovi
    r"|\\%|%"                     # procenti
    r"|\\sum|\\int|\\lim"         # analiza
    r"|\\begin|\\end"             # okruženja
    r"|\\sqrt\s*\["               # n-ti korijen
    r"|\\log|\\ln|\\sin|\\cos|\\tan"
    r"|\.\.\.|\\dots|\\ldots"
    r"|[<>≤≥≠]|\\le|\\ge|\\ne|\\neq|\\leq|\\geq"  # nejednakosti nisu jednakosti
)


def _strip_units_and_spacing(expr):
    """Ukloni jedinice i razmake koji ne utiču na vrijednost."""
    expr = re.sub(r"\\,|\\;|\\!|\\quad|\\qquad|\\ ", " ", expr)
    expr = re.sub(r"\\left|\\right", "", expr)
    # \text{cm}, \text{cm}^2, \mathrm{...} — jedinica; uklanjamo i eksponent
    # koji joj pripada (npr. \text{cm}^2 cijelo nestaje).
    expr = re.sub(r"\\(?:text|mathrm|mathit)\s*\{[^{}]*\}\s*(?:\^\s*(?:\{[^{}]*\}|\w))?", " ", expr)
    return expr


def _extract_argument(expr, start):
    """Vrati (sadržaj_argumenta, indeks_poslije) za argument LaTeX komande.

    Podržava OBA oblika koja LaTeX dozvoljava:
      • `{...}` s ugniježdenim zagradama — npr. \\sqrt{3}, \\frac{12}{5}
      • JEDAN token bez zagrada — npr. \\sqrt3, \\frac12
    Drugi oblik je bitan: živi model je pisao baš `\\sqrt3`, pa bi bez ovoga
    cijeli izraz bio „nepodržan“ i tiho preskočen umjesto provjeren.
    """
    while start < len(expr) and expr[start].isspace():
        start += 1
    if start >= len(expr):
        raise _Unsupported("nedostaje argument")
    if expr[start] == "{":
        depth = 0
        for i in range(start, len(expr)):
            if expr[i] == "{":
                depth += 1
            elif expr[i] == "}":
                depth -= 1
                if depth == 0:
                    return expr[start + 1:i], i + 1
        raise _Unsupported("nezatvorena '{'")
    if expr[start].isdigit() or expr[start].isalpha():
        return expr[start], start + 1
    raise _Unsupported("neprepoznat argument")


def _latex_to_python(expr):
    """Prevedi ograničen LaTeX podskup u Python izraz (string).

    Podržano: cijeli i decimalni brojevi (zarez ili tačka), zagrade, + - * /,
    stepen `^`, \\frac{}{}, \\sqrt{}, \\cdot, \\times, \\pi, implicitno množenje,
    bosansko školsko dijeljenje `:` (npr. `60:15`, `(24+6):5`, `3,5:0,5`) — vidi
    docs/CURRENT_STATE.md C-4: ovaj zapis je OBAVEZAN po projektnim pravilima
    (matbot/rules.py: "Školsko dijeljenje u običnom zapisu: „:“"), a stariji kod
    ga uopšte nije prepoznavao kao operator (svaki ':' je pao na charset
    provjeri u evaluate_candidates → cio izraz tiho preskočen, nikad provjeren).
    Prevodi se direktno u '/' — identična aritmetika, ista bezbjednosna
    ograničenja (dijeljenje nulom i dalje baca _MathError, ne eval()).
    """
    expr = _strip_units_and_spacing(expr)

    out = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "\\":
            if expr.startswith("\\frac", i):
                num, j = _extract_argument(expr, i + len("\\frac"))
                den, k = _extract_argument(expr, j)
                fraction = "((" + _latex_to_python(num) + ")/(" + _latex_to_python(den) + "))"
                # MJEŠOVITI BROJ: „$1\\frac{3}{20}$“ znači $1+\\frac{3}{20}$, NE
                # $1\\cdot\\frac{3}{20}$ (vidi pravilo zapisa u matbot/rules.py).
                # Cijeli broj neposredno ispred \\frac se zato „povuče“ nazad i
                # sabere. Množenje se u ovom projektu uvijek piše s \\cdot, pa
                # nema dvosmislenosti. (Ovo NE važi za \\sqrt: „$24\\sqrt{3}$“
                # jeste množenje.)
                digits = []
                while out and out[-1].isdigit():
                    digits.append(out.pop())
                if digits:
                    whole = "".join(reversed(digits))
                    out.append("((" + whole + ")+" + fraction + ")")
                else:
                    out.append(fraction)
                i = k
                continue
            if expr.startswith("\\sqrt", i):
                arg, j = _extract_argument(expr, i + len("\\sqrt"))
                out.append("sqrt((" + _latex_to_python(arg) + "))")
                i = j
                continue
            if expr.startswith("\\cdot", i):
                out.append("*")
                i += len("\\cdot")
                continue
            if expr.startswith("\\times", i):
                out.append("*")
                i += len("\\times")
                continue
            if expr.startswith("\\pi", i):
                out.append("PI")
                i += len("\\pi")
                continue
            raise _Unsupported("nepoznata LaTeX komanda")
        if ch == "^":
            out.append("**")
            i += 1
            if i < len(expr) and expr[i] == "{":
                arg, j = _extract_argument(expr, i)
                out.append("(" + _latex_to_python(arg) + ")")
                i = j
            continue
        if ch == "{" or ch == "}":
            # Vitičaste zagrade koje je već potrošio parser za \frac,
            # \sqrt ili stepen jesu LaTeX argumenti. Svaka zagrada koja stigne
            # dovde je SAMOSTALNO grupisanje. U njoj ':' ne prihvatamo: oblik
            # ``{1:2}`` je ujedno Python dict sintaksa i prije podrške za
            # školsko dijeljenje bio je namjerni adversarial test. Pretvaranje
            # takvog oblika u ``(1/2)`` bi code-like sintaksu proglasilo
            # aritmetikom. Sigurno je preskoči; legitimni oblici koriste
            # ``(1:2)`` ili ':' unutar prepoznatog LaTeX argumenta.
            if ch == "{":
                content, _ = _extract_argument(expr, i)
                if ":" in content:
                    raise _Unsupported("dvotačka u samostalnim vitičastim zagradama")
            out.append("(" if ch == "{" else ")")
            i += 1
            continue
        if ch == ":":
            out.append("/")
            i += 1
            continue
        out.append(ch)
        i += 1

    text = "".join(out)
    # Decimalni zarez → tačka (samo između cifara).
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return text


# ---------------------------------------------------------------------------
# ANOTACIJA „broj: zbir njegovih cifara“ (živi nalaz, release gate b7025e4,
# zadatak o pravilima djeljivosti)
# ---------------------------------------------------------------------------
# Ovo NIJE pravilo neke lekcije nego oblik ZAPISA: model je zbir cifara pisao
# tako da dvotačka OZNAČAVA broj o kojem je riječ:
#
#     $12:\\;1+2=3$        $135:\\;1+3+5=9$        $405:\\;4+0+5=9$
#
# Tu dvotačka nije dijeljenje nego „za broj 12 vrijedi:“. Kako je stariji kod
# SVAKU dvotačku bezuslovno prevodio u '/', lijeva strana je računata kao
# 12/1+2 = 14, pa je TAČAN odgovor odbijen porukom
# `numeric_equality_mismatch: '12:\\;1+2' (14) != '3' (3) [solution]`.
#
# Anotacija se priznaje samo kad je DOKAZIVA, nikad po lekciji ni po nagađanju:
# prefiks je cio broj, iza dvotačke stoji zbir čiji su sabirci TAČNO cifre tog
# broja — svaka zasebno i istim redom. Tada je jedino moguće čitanje „zbir
# cifara“, pa se vrednuje samo zbir. Jednakost se i dalje provjerava do kraja:
# `12:1+2=4` pada jer je 1+2=3, a `12:1+3=4` uopšte nije anotacija (cifre broja
# 12 nisu 1 i 3) pa ostaje dijeljenje i takođe pada.
#
# ZAŠTO BAR DVIJE CIFRE: jednocifren prefiks bi progutao ispravno dijeljenje
# „$5:5=1$“ (cifre `[5]` == sabirci `[5]`) i pretvorio ga u zbir 5 ≠ 1. Uz prag
# od dvije cifre iza dvotačke mora stajati BAR DVA sabirka, pa nijedan oblik
# „a:b“ — `12:3`, `20:5`, `60:15`, `(24+6):5`, `3,5:0,5`, `3:4=6:8` — ne može
# ni ući u ovu granu i školsko dijeljenje ostaje provjereno kao i dosad.
_DIGIT_SUM_ANNOTATION_RE = re.compile(r"^\s*(\d{2,})\s*:\s*(\d(?:\s*\+\s*\d)+)\s*$")


def _strip_digit_sum_annotation(expr):
    """Vrati SAMO zbir kad je `expr` cio oblika „broj: cifra+cifra(+…)“ i kad su
    sabirci baš cifre tog broja. Inače vrati `expr` NEPROMIJENJEN — dvotačka
    tada ostaje školsko dijeljenje.

    `_strip_units_and_spacing` se koristi isključivo kao SONDA za prepoznavanje
    (`\\;` stoji baš između oznake i zbira) i njen rezultat nikad ne izlazi iz
    ove funkcije. Zato izraz koji nije anotacija stiže u `_latex_to_python`
    doslovno onakav kakav je i dosad stizao: nijedan zatečeni put se ne mijenja,
    pa ni onaj koji na zaostaloj dvostrukoj kosoj crti mora pasti kao ranije.
    Kad anotacija JESTE prepoznata, vraćeni zbir su same cifre i `+` — bez
    ijedne LaTeX komande."""
    match = _DIGIT_SUM_ANNOTATION_RE.match(_strip_units_and_spacing(expr))
    if not match:
        return expr
    number, digit_sum = match.group(1), match.group(2)
    addends = [addend.strip() for addend in digit_sum.split("+")]
    if addends != list(number):
        return expr
    return digit_sum


_IMPLICIT_PATTERNS = (
    (re.compile(r"(\d)\s*\("), r"\1*("),
    (re.compile(r"\)\s*\("), r")*("),
    (re.compile(r"\)\s*(\d)"), r")*\1"),
    (re.compile(r"(\d)\s*(sqrt|PI)"), r"\1*\2"),
    (re.compile(r"\)\s*(sqrt|PI)"), r")*\1"),
    (re.compile(r"(PI)\s*(\d)"), r"\1*\2"),
    (re.compile(r"(PI)\s*(sqrt)"), r"\1*\2"),
    (re.compile(r"(PI)\s*\("), r"\1*("),
)


def _insert_implicit_multiplication(text):
    previous = None
    while text != previous:
        previous = text
        for pattern, replacement in _IMPLICIT_PATTERNS:
            text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# 3) SIGURNO VREDNOVANJE (AST bijela lista — NIKAD eval)
# ---------------------------------------------------------------------------

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)


def _eval_node(node, pi_value):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, pi_value)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _Unsupported("nedozvoljena konstanta")
        return float(node.value)
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise _Unsupported("nedozvoljena operacija")
        left = _eval_node(node.left, pi_value)
        right = _eval_node(node.right, pi_value)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise _MathError("dijeljenje nulom")
            return left / right
        # Pow — ograniči eksponent da spriječimo eksploziju (npr. 9**9**9).
        if abs(right) > 64 or abs(left) > 1e12:
            raise _Unsupported("prevelik stepen")
        try:
            result = left ** right
        except (OverflowError, ValueError, ZeroDivisionError):
            raise _MathError("nevaljan stepen")
        if isinstance(result, complex):
            raise _MathError("kompleksan rezultat")
        return float(result)
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOPS):
            raise _Unsupported("nedozvoljen unarni operator")
        value = _eval_node(node.operand, pi_value)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Call):
        if (not isinstance(node.func, ast.Name) or node.func.id != "sqrt"
                or len(node.args) != 1 or node.keywords):
            raise _Unsupported("nedozvoljen poziv funkcije")
        value = _eval_node(node.args[0], pi_value)
        if value < 0:
            raise _MathError("korijen negativnog broja")
        return math.sqrt(value)
    if isinstance(node, ast.Name):
        if node.id == "PI":
            return pi_value
        raise _Unsupported("promjenljiva u izrazu")
    raise _Unsupported(f"nedozvoljen AST čvor: {type(node).__name__}")


def evaluate_candidates(latex_expr, pi_values=()):
    """Vrati listu mogućih numeričkih vrijednosti izraza (jedna po vrijednosti
    π kad se π pojavljuje, inače jedna). Baca _Unsupported ili _MathError.

    `pi_values`: kad odgovor IZRIČITO deklariše vrijednost π (vidi
    declared_pi_values), koriste se SAMO te vrijednosti — ne i puni math.pi."""
    if _UNSUPPORTED_RE.search(latex_expr) or _ORDERED_PAIR_RE.search(latex_expr):
        raise _Unsupported("nepodržana konstrukcija")

    python_expr = _latex_to_python(_strip_digit_sum_annotation(latex_expr))
    python_expr = _insert_implicit_multiplication(python_expr).strip()
    if not python_expr:
        raise _Unsupported("prazan izraz")
    # Brza odbrana: sve što nije iz očekivanog skupa znakova → preskoči.
    if not re.fullmatch(r"[0-9+\-*/().\s]|[0-9+\-*/().\sA-Za-z]*", python_expr):
        raise _Unsupported("neočekivani znakovi")
    if re.search(r"[A-Za-z]+", python_expr.replace("sqrt", "").replace("PI", "")):
        raise _Unsupported("promjenljiva u izrazu")

    try:
        tree = ast.parse(python_expr, mode="eval")
    except SyntaxError:
        raise _Unsupported("neparsabilan izraz")

    uses_pi = "PI" in python_expr
    if not uses_pi:
        candidates = (math.pi,)
    else:
        candidates = tuple(pi_values) or _PI_VALUES
    return [_eval_node(tree, pi) for pi in candidates]


# ---------------------------------------------------------------------------
# 4) POREĐENJE LANCA JEDNAKOSTI
# ---------------------------------------------------------------------------

_CHAIN_SPLIT = re.compile(r"(\\approx|≈|=)")
_DECIMAL_LITERAL_RE = re.compile(r"\d+[.,](\d+)")
_IRRATIONAL_RE = re.compile(r"\\sqrt|\\pi")


def _decimal_places(*expressions):
    places = 0
    for expr in expressions:
        for match in _DECIMAL_LITERAL_RE.finditer(expr):
            places = max(places, len(match.group(1)))
    return places


def _tolerance(left_expr, right_expr, relation, magnitude):
    """Tolerancija poređenja.

    • Oba dijela racionalna i bez decimalnog literala, relacija „=“ → egzaktno
      (samo šum float-a). Tako „$7/2=3$“ pada, kako i treba.
    • Postoji decimalni literal → tolerancija zaokruživanja iz njegove
      preciznosti (npr. 2 decimale → 0,005). Tako „$\\sqrt{2}=1,41$“ prolazi,
      a „$24\\sqrt{3}\\approx83,14$“ i dalje pada.
    • Iracionalno bez decimala uz „\\approx“ → mala relativna tolerancija.
    """
    places = _decimal_places(left_expr, right_expr)
    if places:
        return 0.5 * (10 ** -places) * 1.1
    has_irrational = bool(_IRRATIONAL_RE.search(left_expr) or _IRRATIONAL_RE.search(right_expr))
    if relation == "approx":
        return max(_APPROX_REL_TOL * abs(magnitude), 1e-9)
    if has_irrational:
        return max(_EXACT_REL_TOL * abs(magnitude), 1e-9)
    return max(_EXACT_REL_TOL * abs(magnitude), 1e-9)


def check_segment(segment, pi_values=()):
    """Vrati listu poruka o nedosljednosti unutar JEDNOG $...$ segmenta."""
    tokens = _CHAIN_SPLIT.split(segment)
    if len(tokens) < 3:
        return []

    parts = tokens[0::2]
    separators = tokens[1::2]

    evaluated = []  # (index, candidates, raw_expr)
    for index, part in enumerate(parts):
        if not part.strip():
            continue
        try:
            evaluated.append((index, evaluate_candidates(part, pi_values), part))
        except _MathError as e:
            return [f"numeric_equality_mismatch: nevaljan izraz ({e})"]
        except _Unsupported:
            continue  # sigurno preskakanje — nije dokaz ispravnosti

    issues = []
    for (left_i, left_vals, left_expr), (right_i, right_vals, right_expr) in zip(evaluated, evaluated[1:]):
        between = separators[left_i:right_i]
        relation = "approx" if any(s in ("\\approx", "≈") for s in between) else "eq"
        magnitude = max(abs(v) for v in left_vals + right_vals) or 1.0
        tol = _tolerance(left_expr, right_expr, relation, magnitude)
        if not any(abs(a - b) <= tol for a in left_vals for b in right_vals):
            issues.append(
                "numeric_equality_mismatch: "
                f"{left_expr.strip()!r} ({left_vals[0]:.6g}) != {right_expr.strip()!r} ({right_vals[0]:.6g})"
            )
    return issues


# ---------------------------------------------------------------------------
# NAMJERNO LAŽNA JEDNAKOST — dokaz kontradikcije (živi release gate 5ac723e,
# scenario grade9, lekcija 9-05-010 „Sistem bez rješenja“)
# ---------------------------------------------------------------------------
# Rješenje sistema bez rješenja MORA prikazati lažnu jednakost da bi dokazalo
# kontradikciju: „…pa bi slijedilo $3=5$, što nije tačno.“ Ovaj modul je takav
# segment tretirao kao aritmetičku grešku (`numeric_equality_mismatch`), pa je
# svaki vjeran odgovor te lekcije padao zatvoreno — i Tutorov nacrt i
# recenzentova ispravka, jer NIJEDNA ispravka koja zadržava smisao lekcije ne
# može ukloniti kontradikciju. Cijela lekcija je time postala neobjavljiva.
#
# Lažna jednakost se priznaje SAMO kad je vidljivi tekst IZRIČITO proglašava
# netačnom, i to usko vezano uz sam segment:
#   • marker u ISTOJ rečenici, prije ili poslije segmenta („…, što nije
#     tačno“, „slijedi netačna jednakost $3=5$“), ili
#   • marker u NEPOSREDNO sljedećoj rečenici koja počinje anaforom („To je
#     kontradikcija…“) — anafora se odnosi na upravo prikazanu jednakost.
#
# Granica rečenice je bitna zbog OCJENE ODGOVORA: „Netačno. Pravilan postupak:
# $17-9=7$.“ — „Netačno.“ je zasebna rečenica o UČENIKOVOM odgovoru, i pogrešan
# lanac modela iza nje mora ostati odbijen. Zato marker iz druge rečenice bez
# anafore nikad ne amnestira segment. Aproksimacije (`\approx`) se ne mogu
# proglasiti „namjerno lažnim“ — pogrešno zaokruživanje ostaje greška — a
# `nevaljan izraz` (dijeljenje nulom i sl.) nikad se ne amnestira.
_FALSE_MARKER_RE = re.compile(
    r"nije\s+tač\w*|netač\w*|nemogu[ćc]\w*|nije\s+mogu[ćc]\w*|kontradikcij\w*"
    r"|protivrječ\w*|protivurječ\w*|protivriječ\w*|ne\s+važi|ne\s+vrijedi"
    r"|apsurd\w*|nema\s+rješenj\w*|ne\s+može",
    re.IGNORECASE)
_ANAPHOR_START_RE = re.compile(r"^\s*(?:to|ovo|a\s+to)\b", re.IGNORECASE)
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?\n]")
# Ograničen prozor: marker mora stajati uz segment, ne bilo gdje u tekstu.
_FALSE_CONTEXT_WINDOW = 140


def _declares_false(before_text, after_text):
    """True ako okolna proza izričito proglašava susjedni segment netačnim."""
    tail = _SENTENCE_BOUNDARY_RE.split(before_text[-_FALSE_CONTEXT_WINDOW:])[-1]
    if _FALSE_MARKER_RE.search(tail):
        return True
    sentences = _SENTENCE_BOUNDARY_RE.split(after_text[:_FALSE_CONTEXT_WINDOW])
    if sentences and _FALSE_MARKER_RE.search(sentences[0]):
        return True
    if (len(sentences) > 1 and _ANAPHOR_START_RE.match(sentences[1])
            and _FALSE_MARKER_RE.search(sentences[1])):
        return True
    return False


def _segment_may_be_declared_false(segment, segment_issues):
    """Segment smije biti amnestiran samo ako je čista (ne-approx) jednakost
    čiji su svi nalazi obična vrijednosna neslaganja."""
    if "\\approx" in segment or "≈" in segment:
        return False
    return all("nevaljan izraz" not in issue for issue in segment_issues)


def find_numeric_inconsistencies(text):
    """Glavna ulazna tačka. Vrati listu INTERNIH razloga (prazno = nema
    dokazane nedosljednosti). Nikad ne mijenja tekst i nikad ne poziva model."""
    issues = []
    # Deklaracija se traži u CIJELOM tekstu, ne po segmentu: u živom nalazu je
    # „π\approx3,14“ stajalo u prozi, a nedosljedan izraz u $...$ ispod nje.
    pi_values = declared_pi_values(text or "")
    tokens = tokenize_math(text or "")
    for index, (kind, content) in enumerate(tokens):
        if kind not in (INLINE, DISPLAY):
            continue
        found = check_segment(content, pi_values)
        if not found:
            continue
        if _segment_may_be_declared_false(content, found):
            before = tokens[index - 1][1] if (
                index > 0 and tokens[index - 1][0] == TEXT) else ""
            after = tokens[index + 1][1] if (
                index + 1 < len(tokens) and tokens[index + 1][0] == TEXT) else ""
            if _declares_false(before, after):
                continue
        issues.extend(found)
    return issues


def is_numerically_consistent(text):
    return not find_numeric_inconsistencies(text)


def safe_numeric_value(expression):
    """Javna, sigurna vrijednost JEDNOG izraza za druge uske orakle (Faza 4G).

    Vraća ("value", broj) kad je izraz jednoznačno izračunljiv postojećim
    restricted-AST evaluatorom; ("invalid", None) kad je matematički nevaljan
    (dijeljenje nulom i sl.); ("unsupported", None) za sve što se ne može
    sigurno izračunati — uključujući izraze s π čije dvije konvencionalne
    vrijednosti daju različit rezultat (jednoznačnost je uslov, ne pogađa se)."""
    try:
        values = evaluate_candidates(expression or "")
    except _MathError:
        return "invalid", None
    except _Unsupported:
        return "unsupported", None
    if not values or max(values) - min(values) > 1e-12:
        return "unsupported", None
    return "value", values[0]
