"""Deterministički verifikatori: iz DOKAZA izračunaj istinu, pa provjeri opcije.

Nijedan verifikator ne čita prozu. Sve što odlučuje dolazi iz strukturisanog
dokaza (matbot/contracts/evidence.py), a vidljivi tekst se koristi samo za
unakrsnu provjeru na višem nivou.

„Nije se moglo dokazati“ NIKAD ne znači „prošlo je“: svaki rezultat nosi
`engaged` (jesam li uopšte imao šta provjeriti) odvojeno od `valid`. Prazna
lista problema nikad ne označava i „provjereno“ i „preskočeno“ — to je greška
koja je jednom već pustila pogrešan odgovor (D35T-2).
"""
from dataclasses import dataclass, field
from fractions import Fraction

from matbot.contracts import evidence as ev


@dataclass(frozen=True)
class VerifierResult:
    engaged: bool
    valid: bool
    code: str
    details: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.engaged and self.valid


# ---------------------------------------------------------------------------
# exact_rational — egzaktno poređenje vrijednosti opcija s izračunatom istinom
# ---------------------------------------------------------------------------

def verify_exact_rational(ground_truth, option_values, correct_index,
                          require_distinct_from=None):
    """`option_values` su ČVOROVI dokaza (predshuffle redoslijed).

    `require_distinct_from`: kad je zadano, tačna opcija smije imati istu
    VRIJEDNOST, ali ne i isti ZAPIS (npr. „prepoznaj ekvivalentan razlomak“ —
    odgovor mora biti drugi zapis iste vrijednosti, ne prepisan original)."""
    if ground_truth is None:
        return VerifierResult(False, False, "ground_truth_not_computable")
    if not option_values:
        return VerifierResult(False, False, "option_values_missing")

    values = []
    for node in option_values:
        try:
            values.append(ev.evaluate(node))
        except ev.EvidenceError:
            return VerifierResult(False, False, "option_value_not_computable")

    matches = [i for i, value in enumerate(values) if value == ground_truth]
    details = {
        "ground_truth": str(ground_truth),
        "option_values": [str(value) for value in values],
    }
    if len(matches) > 1:
        return VerifierResult(True, False, "multiple_correct_options",
                              dict(details, matches=matches))
    if not matches:
        return VerifierResult(True, False, "no_correct_option", details)
    if matches[0] != correct_index:
        return VerifierResult(True, False, "marked_answer_mismatch",
                              dict(details, derived_index=matches[0]))

    if require_distinct_from is not None:
        answer = option_values[correct_index]
        if not answer.is_literal or not require_distinct_from.is_literal:
            return VerifierResult(False, False, "equivalence_pair_not_literal")
        if (answer.num, answer.den) == (require_distinct_from.num, require_distinct_from.den):
            return VerifierResult(True, False, "answer_repeats_the_reference", details)

    return VerifierResult(True, True, "ok", dict(details, derived_index=matches[0]))


# ---------------------------------------------------------------------------
# error_category — kategorija se IZVODI iz strukture lanca, nikad iz riječi
# ---------------------------------------------------------------------------
#
# Ranije je kategorija pogađana bosanskim ključnim riječima nad tekstom opcije
# („reciproc“, „skrat“, „nazivnik“). To nije determinističko: sinonim ili druga
# formulacija mijenjali su ishod. Sada server sam prepoznaje ŠTA se strukturno
# desilo između dva koraka lanca, a opcije samo DEKLARIŠU kategoriju iz
# zatvorenog skupa — pa se poklapanje provjerava, ne interpretira.

def _literal_pair(node):
    return (node.num, node.den) if node.is_literal else None


def _binary_literals(node):
    """(op, (n1,d1), (n2,d2)) za binarni čvor nad dva literala, inače None."""
    if node.is_leaf or len(node.args) != 2:
        return None
    left, right = node.args
    if not (left.is_literal and right.is_literal):
        return None
    return node.op, _literal_pair(left), _literal_pair(right)


def _combined_denominators(previous, following):
    binary = _binary_literals(previous)
    target = _literal_pair(following)
    if not binary or not target:
        return None
    op, (_, d1), (_, d2) = binary
    if op not in ("add", "subtract"):
        return None
    combined = d1 + d2 if op == "add" else d1 - d2
    return "combined_denominators" if target[1] == combined else None


def _wrong_numerator(previous, following):
    binary = _binary_literals(previous)
    target = _literal_pair(following)
    if not binary or not target:
        return None
    op, (n1, d1), (n2, d2) = binary
    if op not in ("add", "subtract") or d1 != d2 or target[1] != d1:
        return None
    expected = n1 + n2 if op == "add" else n1 - n2
    return "wrong_numerator" if target[0] != expected else None


def _missed_reciprocal(previous, following):
    binary = _binary_literals(previous)
    if not binary:
        return None
    op, (n1, d1), (n2, d2) = binary
    if op != "divide":
        return None
    try:
        straight_across = Fraction(n1 * n2, d1 * d2)
    except ZeroDivisionError:
        return None
    try:
        actual = ev.evaluate(following)
    except ev.EvidenceError:
        return None
    return "missed_reciprocal" if actual == straight_across else None


_ALTERNATIVE_OPS = {
    "add": ("multiply", "subtract"),
    "subtract": ("add", "multiply"),
    "multiply": ("add", "divide"),
    "divide": ("multiply", "subtract"),
}


def _wrong_operation(previous, following):
    binary = _binary_literals(previous)
    if not binary:
        return None
    op, (n1, d1), (n2, d2) = binary
    try:
        actual = ev.evaluate(following)
    except ev.EvidenceError:
        return None
    left, right = Fraction(n1, d1), Fraction(n2, d2)
    for alternative in _ALTERNATIVE_OPS.get(op, ()):
        try:
            if alternative == "add" and actual == left + right:
                return "wrong_operation"
            if alternative == "subtract" and actual == left - right:
                return "wrong_operation"
            if alternative == "multiply" and actual == left * right:
                return "wrong_operation"
            if alternative == "divide" and right != 0 and actual == left / right:
                return "wrong_operation"
        except ZeroDivisionError:
            continue
    return None


def _wrong_product(previous, following):
    binary = _binary_literals(previous)
    if not binary:
        return None
    op, _, _ = binary
    return "wrong_product" if op == "multiply" else None


def _incorrect_conversion(previous, following):
    """Oba koraka su račun — dakle prepisivanje (npr. svođenje na zajednički
    nazivnik) koje je promijenilo vrijednost."""
    if previous.is_leaf or following.is_leaf:
        return None
    return "incorrect_conversion"


def _unequal_scaling(previous, following):
    """Brojnik i nazivnik nisu množeni/dijeljeni ISTIM brojem.

    Ime kategorije nosi i SMJER (proširivanje vs skraćivanje) — time ugovor
    lekcije o proširivanju automatski odbija zadatak o grešci pri skraćivanju,
    bez ijedne grane po lekciji."""
    before, after = _literal_pair(previous), _literal_pair(following)
    if not before or not after:
        return None
    n1, d1 = before
    n2, d2 = after
    if (n1, d1) == (n2, d2) or n1 == 0 or d1 == 0:
        return None
    if Fraction(n1, d1) == Fraction(n2, d2):
        return None  # vrijednost očuvana — ovo nije greška skaliranja
    expanding = abs(n2) >= abs(n1) and abs(d2) >= abs(d1)
    reducing = abs(n2) <= abs(n1) and abs(d2) <= abs(d1)
    if expanding and not reducing:
        return "unequal_scaling"
    if reducing and not expanding:
        return "wrong_reduction"
    return None


# Redoslijed je bitan: od najspecifičnije strukture prema najopštijoj.
_DERIVERS = (
    _combined_denominators,
    _wrong_numerator,
    _missed_reciprocal,
    _wrong_operation,
    _wrong_product,
    _unequal_scaling,
    _incorrect_conversion,
)

# PROJEKTNI (server-owned) tekst opcije po kategoriji greške.
#
# ZAŠTO POSTOJI: kategorija se izvodi strukturno i nosi TAČNOST, ali je učenik ne
# vidi — on čita prozu. Da model piše tu prozu slobodno, tačnost bi zavisila od
# skrivenog metapodatka, a značenje koje učenik čita od teksta koji niko nije
# provjerio. To dvoje smije se razići, pa se vidljivi tekst NE UZIMA od modela
# nego se renderuje ovdje — iz iste kategorije koja odlučuje o tačnosti.
#
# Jezik je bosanski (ijekavica) i prolazi kroz matbot/terminology.py kao i svaki
# drugi vidljivi tekst.
ERROR_CATEGORY_LABELS = {
    "combined_denominators":
        "Sabrao je i nazivnike, a nazivnik je trebao ostati isti.",
    "wrong_numerator":
        "Nazivnik je zadržao ispravno, ali je brojnik pogrešno izračunao.",
    "missed_reciprocal":
        "Pri dijeljenju nije pomnožio recipročnom vrijednošću drugog razlomka.",
    "wrong_operation":
        "Primijenio je pogrešnu računsku operaciju.",
    "wrong_product":
        "Proizvod razlomaka je pogrešno izračunao.",
    "unequal_scaling":
        "Brojnik i nazivnik nije pomnožio istim brojem.",
    "wrong_reduction":
        "Brojnik i nazivnik nije podijelio istim brojem.",
    "incorrect_conversion":
        "Razlomke je pogrešno sveo na zajednički nazivnik.",
}

# Kategorije za koje POSTOJI strukturni izvođač I projektni tekst. Ugovor koji
# navede bilo šta izvan ovoga pada na UČITAVANJU (archetypes.assert_supported) —
# jer bez teksta server ne bi mogao sam sastaviti opciju.
DERIVABLE_ERROR_CATEGORIES = frozenset({
    "combined_denominators", "wrong_numerator", "missed_reciprocal",
    "wrong_operation", "wrong_product", "unequal_scaling", "wrong_reduction",
    "incorrect_conversion",
})


def render_error_options(categories):
    """Vidljivi tekstovi opcija iz VALIDIRANIH kategorija, istim redoslijedom.

    Vraća None ako makar jedna kategorija nema projektni tekst — pozivalac tada
    odbija zadatak umjesto da objavi neprovjerenu prozu."""
    rendered = []
    for category in categories:
        label = ERROR_CATEGORY_LABELS.get(category)
        if not label:
            return None
        rendered.append(label)
    return tuple(rendered)


def derive_error_category(previous, following):
    for deriver in _DERIVERS:
        category = deriver(previous, following)
        if category:
            return category
    return None


def verify_error_category(steps, option_categories, correct_index, allowed_categories):
    """Lanac se PONOVO izračuna; tražimo tačno jedan nedosljedan korak."""
    try:
        values = [ev.evaluate(step) for step in steps]
    except ev.EvidenceError:
        return VerifierResult(False, False, "error_chain_not_computable")

    mismatches = [i for i in range(len(values) - 1) if values[i] != values[i + 1]]
    details = {"chain": [str(value) for value in values], "mismatches": mismatches}
    if len(mismatches) > 1:
        return VerifierResult(True, False, "multiple_defensible_errors", details)
    if not mismatches:
        return VerifierResult(True, False, "no_demonstrated_error", details)

    index = mismatches[0]
    derived = derive_error_category(steps[index], steps[index + 1])
    if derived is None:
        return VerifierResult(False, False, "error_kind_not_supported", details)
    details["derived_category"] = derived
    if derived not in set(allowed_categories):
        # Ovdje se, bez ijedne grane po lekciji, hvata i „greška pri
        # skraćivanju u lekciji o proširivanju“.
        return VerifierResult(True, False, "error_category_outside_contract", details)

    declared = list(option_categories or ())
    if len(declared) < 2:
        return VerifierResult(False, False, "option_categories_missing", details)
    unknown = sorted(set(declared) - DERIVABLE_ERROR_CATEGORIES)
    if unknown:
        return VerifierResult(True, False, "unknown_error_category",
                              dict(details, unknown=unknown))
    if len(set(declared)) != len(declared):
        return VerifierResult(True, False, "ambiguous_error_options",
                              dict(details, declared=declared))

    matching = [i for i, category in enumerate(declared) if category == derived]
    if len(matching) != 1:
        return VerifierResult(True, False, "ambiguous_error_options",
                              dict(details, declared=declared, matching=matching))
    if matching[0] != correct_index:
        return VerifierResult(True, False, "marked_error_option_mismatch",
                              dict(details, derived_index=matching[0]))
    return VerifierResult(True, True, "ok", dict(details, derived_index=matching[0]))


# Verifikatori implementirani u ovoj fazi. `archetypes.py` bira koji se koristi;
# ugovor ne smije imenovati verifikator kojeg ovdje nema.
IMPLEMENTED = frozenset({"exact_rational", "error_category"})
