"""Generička provjera: da li DOKAZ zadovoljava ograničenja izabrane lekcije.

Nijedna funkcija ovdje ne zna nijednu lekciju. Sve odluke dolaze iz vrijednosti
ugovora, pa ista funkcija provjerava i „imenioci moraju biti jednaki“ (6. razred,
razlomci) i „brojevi smiju biti negativni“ (7. razred, cijeli brojevi).

Ovaj modul je zamjena za raniji `elif kind == "add_equal"` niz grana po imenu
lekcije: ono što je bilo Python grana sada je vrijednost `denominator_relation`.
"""
from dataclasses import dataclass, field
from fractions import Fraction
from math import gcd

from matbot.contracts import evidence as ev


@dataclass(frozen=True)
class ConstraintResult:
    valid: bool
    code: str = "ok"
    details: dict = field(default_factory=dict)
    # `engaged=False` znači „nisam imao šta provjeriti“, a NE „prošlo je“.
    # Razlika je bitna: bez nje bi dijagnostika tvrdila da je ograničenje
    # provjereno i palo, iako ga dokaz uopšte nije mogao pokazati.
    engaged: bool = True


_OK = ConstraintResult(True)


def _fail(code, **details):
    return ConstraintResult(False, code, details)


def _unprovable(code, **details):
    return ConstraintResult(False, code, details, engaged=False)


# ---------------------------------------------------------------------------
# OGRANIČENJA NAD CIJELIM DOKAZOM
# ---------------------------------------------------------------------------

def check_operations(contract, facts):
    allowed = set(contract.allowed_operations)
    if not allowed:
        return _OK
    extra = sorted(facts.operations - allowed)
    if extra:
        return _fail("operation_not_allowed", used=extra, allowed=sorted(allowed))
    return _OK


def check_sign_policy(contract, facts):
    policy = contract.constraint("sign_policy", "any")
    if policy == "any" or not facts.literals:
        return _OK
    values = [Fraction(n, d) for n, d in facts.literals]
    if policy == "non_negative" and min(values) < 0:
        return _fail("negative_operand_not_allowed", minimum=str(min(values)))
    if policy == "positive" and min(values) <= 0:
        return _fail("non_positive_operand_not_allowed", minimum=str(min(values)))
    return _OK


def check_integer_range(contract, facts):
    bounds = contract.constraint("integer_range")
    if not bounds:
        return _OK
    low, high = bounds
    denominator_limit = max(abs(low), abs(high))
    for numerator, denominator in facts.literals:
        if not low <= numerator <= high:
            return _fail("operand_out_of_range", numerator=numerator, bounds=[low, high])
        if not 1 <= abs(denominator) <= denominator_limit:
            return _fail("denominator_out_of_range", denominator=denominator,
                         limit=denominator_limit)
    return _OK


def check_denominator_relation(contract, facts):
    """Jedna vrijednost ugovora umjesto cijele grane po lekciji.

    Semantika je namjerno asimetrična i preslikava raniju provjeru: „equal“
    traži da SVI parovi budu jednaki, dok „different“ traži da BAR JEDAN par
    stvarno zahtijeva zajednički nazivnik."""
    relation = contract.constraint("denominator_relation", "any")
    if relation == "any":
        return _OK
    pairs = facts.binary_denominator_pairs
    if not pairs:
        # Dokaz ne pokazuje nijedan par imenilaca — uslov se ne može ni
        # provjeriti. Fail closed, ali izričito NEPROVJERENO.
        return _unprovable("denominator_relation_not_provable", relation=relation)
    if relation == "equal":
        if not all(left == right for left, right in pairs):
            return _fail("denominators_must_be_equal", pairs=list(pairs))
        return _OK
    if relation == "different":
        if not any(left != right for left, right in pairs):
            return _fail("denominators_must_differ", pairs=list(pairs))
        return _OK
    if relation == "one_divides_another":
        for left, right in pairs:
            if left == 0 or right == 0 or (left % right and right % left):
                return _fail("denominators_must_divide", pairs=list(pairs))
        return _OK
    return _fail("unknown_denominator_relation", relation=relation)


def check_fraction_form(contract, facts):
    form = contract.constraint("fraction_form", "any")
    if form == "any" or not facts.literals:
        return _OK
    for numerator, denominator in facts.literals:
        proper = abs(numerator) < abs(denominator)
        if form == "proper" and not proper:
            return _fail("fraction_must_be_proper", literal=[numerator, denominator])
        if form == "improper" and proper:
            return _fail("fraction_must_be_improper", literal=[numerator, denominator])
    return _OK


def check_term_count(contract, facts):
    bounds = contract.constraint("term_count")
    if not bounds:
        return _OK
    low, high = bounds
    if not low <= facts.term_count <= high:
        return _fail("term_count_out_of_range", term_count=facts.term_count,
                     bounds=[low, high])
    return _OK


_EVIDENCE_CHECKS = (
    check_operations,
    check_sign_policy,
    check_integer_range,
    check_denominator_relation,
    check_fraction_form,
    check_term_count,
)


def check_evidence(contract, facts):
    """Svi generički uslovi lekcije nad izvedenim činjenicama dokaza."""
    for check in _EVIDENCE_CHECKS:
        result = check(contract, facts)
        if not result.valid:
            return result
    return _OK


# ---------------------------------------------------------------------------
# ODNOS ZADANOG I TAČNOG ODGOVORA (arhetip `identify_equivalent`)
# ---------------------------------------------------------------------------

_DIRECTION_FOR_CATEGORY = {
    "unequal_scaling": "expand",
    "wrong_reduction": "reduce",
}


def _scaling_direction(reference, answer):
    if abs(answer.num) > abs(reference.num) and abs(answer.den) > abs(reference.den):
        return "expand"
    if abs(answer.num) < abs(reference.num) and abs(answer.den) < abs(reference.den):
        return "reduce"
    return "none"


def check_answer_relation(contract, reference, answer):
    """Smjer skaliranja i traženi oblik odgovora — oboje iz ugovora.

    Time lekcija o proširivanju i lekcija o skraćivanju dijele ISTI arhetip i
    ISTI verifikator, a razlikuju se samo vrijednošću `scaling_direction`."""
    if not (reference.is_literal and answer.is_literal):
        return _fail("answer_relation_not_literal")

    required = contract.constraint("scaling_direction", "any")
    if required != "any":
        actual = _scaling_direction(reference, answer)
        if actual != required:
            return _fail("scaling_direction_mismatch",
                         required=required, actual=actual,
                         reference=[reference.num, reference.den],
                         answer=[answer.num, answer.den])

    if contract.representation("answer_form", "any") == "irreducible":
        if gcd(abs(answer.num), abs(answer.den)) != 1:
            return _fail("answer_must_be_irreducible",
                         answer=[answer.num, answer.den])
    return _OK


def check_error_direction(contract, previous, following, derived_category):
    """Kategorija greške mora pripadati smjeru koji lekcija uči.

    Odbrambeni sloj povrh `error_category_set`: ugovor koji bi (greškom) naveo
    obje smjerne kategorije i dalje ne može pustiti zadatak iz pogrešnog smjera."""
    required = contract.constraint("scaling_direction", "any")
    if required == "any":
        return _OK
    direction = _DIRECTION_FOR_CATEGORY.get(derived_category)
    if direction is None:
        return _OK
    if direction != required:
        return _fail("error_direction_mismatch", required=required, actual=direction,
                     category=derived_category)
    return _OK


def facts_for_evidence(parsed):
    return ev.facts_for(parsed.primary_nodes)
