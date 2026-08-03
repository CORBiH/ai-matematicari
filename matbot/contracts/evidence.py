"""Strukturisan matematički DOKAZ uz generisan zadatak — i jedini izvor istine.

ZAŠTO POSTOJI: do sada je svaka deterministička provjera ponovo parsirala
BOSANSKU PROZU zadatka regexima („\\frac\\{...\\}\\{...\\}\\s*[+-]\\s*...“). Zbog
toga se svaka nova vrsta lekcije morala opisati novim regexom, a istina o
zadatku živjela je u tekstu koji je model slobodno pisao.

Sada model uz zadatak vraća DOKAZ: mali, ograničen izraz-stablo s TAČNIM
cijelim brojevima. Server iz njega NEZAVISNO izračuna tačan odgovor i tek onda
provjeri da vidljive opcije i označeni indeks tome odgovaraju. Vidljivi tekst i
`expected_answer` ostaju proza za učenika — unakrsno se provjeravaju, ali nikad
nisu izvor istine. (Ista pouka kao D35T-2 kod slike: `visible_problem_text` nije
dokaz.)

Sve je EGZAKTNO nad `fractions.Fraction` — nijedan float ne dodiruje putanju
istine, pa „$\\frac{1}{3}$“ nikad ne postane 0.333….

NEREDUKOVANI num/den se NAMJERNO čuvaju uz vrijednost: struktura zapisa je
dokaz za kategorije greške (npr. „nazivnik rezultata je $d_1+d_2$“ se ne vidi
iz skraćene vrijednosti).
"""
from dataclasses import dataclass
from fractions import Fraction

MAX_DEPTH = 5
MAX_NODES = 32
MAX_ABS_INT = 10 ** 6
MAX_STEPS = 6
MIN_STEPS = 2

_BINARY_OPS = frozenset({"add", "subtract", "multiply", "divide"})

_CONTAINER_KINDS = frozenset({
    "numeric_expression", "rational_expression", "equation", "transformation_steps",
})


class EvidenceError(ValueError):
    """Dokaz je odsutan, neispravan ili izvan granica. Poruka je INTERNA."""


@dataclass(frozen=True)
class Node:
    """Čvor ograničenog izraz-stabla.

    Listovi su ili literal (`num`/`den`, NEREDUKOVANI) ili rupa (`is_hole`).
    Unutrašnji čvorovi su binarne operacije nad tačno dva djeteta."""

    op: str = ""
    args: tuple = ()
    num: int = 0
    den: int = 1
    is_hole: bool = False

    @property
    def is_leaf(self):
        return not self.op

    @property
    def is_literal(self):
        return not self.op and not self.is_hole

    @property
    def value(self):
        """Vrijednost SAMO za literal (bez rekurzije) — Fraction ili None."""
        return Fraction(self.num, self.den) if self.is_literal else None


def _field(raw, name):
    if isinstance(raw, dict):
        return raw.get(name)
    return getattr(raw, name, None)


def parse_node(raw, depth=0, budget=None):
    """Pretvori sirov dokaz (dict ili pydantic objekt) u `Node`, uz granice."""
    if budget is None:
        budget = [MAX_NODES]
    if raw is None:
        raise EvidenceError("nedostaje čvor izraza")
    if depth > MAX_DEPTH:
        raise EvidenceError(f"izraz dublji od {MAX_DEPTH}")
    budget[0] -= 1
    if budget[0] < 0:
        raise EvidenceError(f"izraz ima više od {MAX_NODES} čvorova")

    op = (_field(raw, "op") or "").strip()
    args = _field(raw, "args")
    num = _field(raw, "num")
    den = _field(raw, "den")
    hole = bool(_field(raw, "hole"))

    if op:
        if hole or num is not None or den is not None:
            raise EvidenceError(f"čvor '{op}' ne smije nositi i literal/rupu")
        if op not in _BINARY_OPS:
            raise EvidenceError(f"nepoznata operacija '{op}'")
        if not args or len(args) != 2:
            raise EvidenceError(f"operacija '{op}' traži tačno dva argumenta")
        return Node(op=op, args=tuple(parse_node(a, depth + 1, budget) for a in args))

    if args:
        raise EvidenceError("list ne smije imati argumente")
    if hole:
        if num is not None or den is not None:
            raise EvidenceError("rupa ne smije nositi vrijednost")
        return Node(is_hole=True)

    if num is None:
        raise EvidenceError("list nije ni literal ni rupa")
    if not isinstance(num, int) or (den is not None and not isinstance(den, int)):
        raise EvidenceError("brojnik/nazivnik moraju biti cijeli brojevi")
    den = 1 if den is None else den
    if den == 0:
        raise EvidenceError("nazivnik 0")
    if abs(num) > MAX_ABS_INT or abs(den) > MAX_ABS_INT:
        raise EvidenceError(f"vrijednost izvan granice ±{MAX_ABS_INT}")
    # Znak se drži uz brojnik da bi strukturna poređenja bila jednoznačna.
    if den < 0:
        num, den = -num, -den
    return Node(num=num, den=den)


def evaluate(node, hole_value=None):
    """Egzaktna vrijednost izraza. `hole_value` popunjava sve rupe."""
    if node.is_hole:
        if hole_value is None:
            raise EvidenceError("izraz sadrži rupu bez zadane vrijednosti")
        return Fraction(hole_value)
    if node.is_literal:
        return Fraction(node.num, node.den)
    left = evaluate(node.args[0], hole_value)
    right = evaluate(node.args[1], hole_value)
    if node.op == "add":
        return left + right
    if node.op == "subtract":
        return left - right
    if node.op == "multiply":
        return left * right
    if right == 0:
        raise EvidenceError("dijeljenje nulom u izrazu")
    return left / right


def count_holes(node):
    if node.is_hole:
        return 1
    if node.is_leaf:
        return 0
    return sum(count_holes(child) for child in node.args)


def solve_for_hole(lhs, rhs):
    """Riješi jednačinu s TAČNO jednom rupom, egzaktno i uz provjeru.

    Ne pretpostavlja se linearnost — ona se DOKAZUJE u tri tačke, a dobijeni
    kandidat se na kraju uvrsti nazad. Ako bilo šta od toga ne prođe (npr. rupa
    je u djeliocu, pa izraz nije linearan), dokaz je „nepodržan“ i sve pada
    zatvoreno; nikad se ne pogađa."""
    if count_holes(lhs) + count_holes(rhs) != 1:
        raise EvidenceError("jednačina mora imati tačno jednu rupu")

    def difference(probe):
        return evaluate(lhs, probe) - evaluate(rhs, probe)

    try:
        f0, f1, f2 = difference(0), difference(1), difference(2)
    except EvidenceError:
        raise EvidenceError("rupu nije moguće sondirati (dijeljenje nulom)")

    slope = f1 - f0
    if f2 - f1 != slope:
        raise EvidenceError("jednačina nije linearna po nepoznatoj")
    if slope == 0:
        raise EvidenceError("nepoznata se skraćuje — nema jedinstvenog rješenja")

    candidate = -f0 / slope
    if evaluate(lhs, candidate) != evaluate(rhs, candidate):
        raise EvidenceError("kandidat ne zadovoljava jednačinu")
    return candidate


# ---------------------------------------------------------------------------
# IZVEDENE ČINJENICE — jedini ulaz za generičku provjeru ograničenja lekcije.
# Namjerno ne znaju nijednu lekciju: `constraints.py` ih poredi s ugovorom.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceFacts:
    operations: frozenset
    literals: tuple           # (num, den) NEREDUKOVANO, redoslijedom pojavljivanja
    term_count: int
    has_hole: bool
    max_abs_operand: int
    min_value: Fraction
    binary_denominator_pairs: tuple   # (d_lijevo, d_desno) po binarnom čvoru


def denominator_hint(node):
    """Nazivnik jedne strane računa, kad se može pouzdano očitati.

    Pokriva i zapis s nepoznatim brojnikom — „$\\frac{?}{7}$“ je u dokazu
    `divide(hole, 7)`, pa je nazivnik i dalje 7. Bez ovoga bi ugovor o jednakim
    imeniocima preskočio provjeru na svakom zadatku tipa „dopuni jednakost“."""
    if node.is_hole:
        return None
    if node.is_literal:
        return node.den
    if node.op == "divide" and len(node.args) == 2:
        divisor = node.args[1]
        if divisor.is_literal and divisor.den == 1:
            return divisor.num
    return None


def _walk(node, out):
    if node.is_hole:
        out["has_hole"] = True
        return
    if node.is_literal:
        out["literals"].append((node.num, node.den))
        return
    out["operations"].add(node.op)
    left, right = node.args
    left_den, right_den = denominator_hint(left), denominator_hint(right)
    if left_den is not None and right_den is not None:
        out["pairs"].append((left_den, right_den))
    _walk(left, out)
    _walk(right, out)


def facts_for(nodes):
    """Sažmi jedan ili više izraza u činjenice koje ugovor može provjeriti."""
    out = {"operations": set(), "literals": [], "pairs": [], "has_hole": False}
    for node in nodes:
        _walk(node, out)
    literals = tuple(out["literals"])
    magnitudes = [abs(n) for n, _ in literals] + [abs(d) for _, d in literals]
    values = [Fraction(n, d) for n, d in literals]
    return EvidenceFacts(
        operations=frozenset(out["operations"]),
        literals=literals,
        term_count=len(literals),
        has_hole=out["has_hole"],
        max_abs_operand=max(magnitudes) if magnitudes else 0,
        min_value=min(values) if values else Fraction(0),
        binary_denominator_pairs=tuple(out["pairs"]),
    )


# ---------------------------------------------------------------------------
# KONTEJNER DOKAZA
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedEvidence:
    kind: str
    expression: Node = None
    lhs: Node = None
    rhs: Node = None
    steps: tuple = ()
    option_values: tuple = ()
    option_categories: tuple = ()

    @property
    def primary_nodes(self):
        """Sve što ulazi u provjeru OGRANIČENJA lekcije (operacije, opseg,
        odnos imenilaca) — za lanac koraka to je cio lanac."""
        if self.kind == "equation":
            return (self.lhs, self.rhs)
        if self.kind == "transformation_steps":
            return tuple(self.steps)
        return (self.expression,)

    @property
    def principal_nodes(self):
        """Ono što opisuje VELIČINU zadatka (broj članova, veličina brojeva).

        Za lanac koraka to je SAMO polazni izraz: „$\\frac{7}{12}+\\frac{3}{12}$“
        je zadatak s dva člana i onda kad je iza njega još tri koraka pogrešnog
        postupka. Bez ove razlike bi svaki zadatak o grešci lažno ispao pretežak."""
        if self.kind == "transformation_steps":
            return (self.steps[0],) if self.steps else ()
        return self.primary_nodes

    def facts(self):
        return facts_for(self.primary_nodes)

    def principal_facts(self):
        return facts_for(self.principal_nodes)


def parse(raw, option_count=4):
    """Provjeri i pretvori cio dokaz. Baca EvidenceError — nikad ne pogađa."""
    if raw is None:
        raise EvidenceError("zadatak nema strukturisan dokaz")
    kind = (_field(raw, "kind") or "").strip()
    if kind not in _CONTAINER_KINDS:
        raise EvidenceError(f"nepodržana vrsta dokaza '{kind}'")

    expression = _field(raw, "expression")
    lhs, rhs = _field(raw, "lhs"), _field(raw, "rhs")
    steps = _field(raw, "steps")
    option_values = _field(raw, "option_values")
    option_categories = _field(raw, "option_categories")

    parsed_expression = parsed_lhs = parsed_rhs = None
    parsed_steps = ()

    if kind == "equation":
        if expression is not None or steps:
            raise EvidenceError("dokaz 'equation' nosi samo lhs/rhs")
        parsed_lhs, parsed_rhs = parse_node(lhs), parse_node(rhs)
    elif kind == "transformation_steps":
        if expression is not None or lhs is not None or rhs is not None:
            raise EvidenceError("dokaz 'transformation_steps' nosi samo steps")
        if not steps or not (MIN_STEPS <= len(steps) <= MAX_STEPS):
            raise EvidenceError(f"lanac koraka mora imati {MIN_STEPS}-{MAX_STEPS} koraka")
        parsed_steps = tuple(parse_node(step) for step in steps)
    else:
        if lhs is not None or rhs is not None or steps:
            raise EvidenceError(f"dokaz '{kind}' nosi samo expression")
        parsed_expression = parse_node(expression)

    parsed_values = ()
    if option_values is not None:
        if len(option_values) != option_count:
            raise EvidenceError(
                f"option_values mora imati tačno {option_count} stavki"
            )
        parsed_values = tuple(parse_node(value) for value in option_values)

    parsed_categories = ()
    if option_categories is not None:
        if len(option_categories) != option_count:
            raise EvidenceError(
                f"option_categories mora imati tačno {option_count} stavki"
            )
        parsed_categories = tuple((c or "").strip() for c in option_categories)

    return ParsedEvidence(
        kind=kind, expression=parsed_expression, lhs=parsed_lhs, rhs=parsed_rhs,
        steps=parsed_steps, option_values=parsed_values,
        option_categories=parsed_categories,
    )
