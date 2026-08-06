"""Deterministički generator porodice `fraction_arithmetic_direct` (Faza 4H).

ZAŠTO POSTOJI (forenzika latencije, Faza 4H): svjež/nov/lakši/teži zadatak je
kroz dvopozivni model-put trajao medijalno ~34 s (dva sekvencijalna poziva s
~1200–1650 izlaznih tokena po pozivu), uz ~2–3 % isteka na 45 s po pozivu.
Za četiri lekcije direktnog računa s razlomcima server može SAM proizvesti
cio paket — operande, egzaktan odgovor, distraktore iz poznatih učeničkih
grešaka, ljestvicu nagovještaja i potpuno rješenje — i dokazati ga postojećim
validatorima, bez ijednog poziva modela.

MATEMATIČKI AUTORITET: isključivo egzaktni `fractions.Fraction`. Binarni
float se nigdje ne koristi kao izvor istine.

GRANICA: generator NE zamjenjuje recenzenta za model-pakete i ne aktivira
nove lekcije. Poziva se samo kroz Practice orkestrator, koji izlaz i dalje
provjerava istim preflight/objavnim validatorima kao svaki drugi paket
(uključujući orakl direktnog računa — nezavisna provjera da je tačno jedna
opcija vrijednost vidljivog izraza).
"""
import random
from dataclasses import dataclass
from fractions import Fraction
from math import gcd, lcm

from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter, TaskPayload,
                                 TaskSignature, TutorOption)

FAMILY_ID = "fraction_arithmetic_direct"
GENERATOR_VERSION = "detfrac-1"

_SUPPORTED_OPERATIONS = frozenset({"add", "subtract", "multiply", "divide"})

# Simboli operacija po projektnim pravilima zapisa (matbot/rules.py):
# množenje uvijek `\cdot`, školsko dijeljenje `:`.
_OPERATOR_SYMBOL = {"add": "+", "subtract": "-", "multiply": "\\cdot", "divide": ":"}


class DeterministicGenerationError(RuntimeError):
    """Ni nakon ograničenog broja pokušaja nije nastao valjan paket."""


def supports(parameters) -> bool:
    """True SAMO kad parametri ugovora u potpunosti staju u ovaj generator."""
    operations = tuple((parameters or {}).get("allowed_operations") or ())
    if not operations or not set(operations) <= _SUPPORTED_OPERATIONS:
        return False
    relation = (parameters or {}).get("denominator_relation") or "any"
    return relation in ("equal", "unlike", "any")


@dataclass(frozen=True)
class FractionPackage:
    """Server-vlasnički strukturisan paket jednog zadatka (Workstream B)."""

    lesson_id: str
    lesson_title: str
    family_id: str
    operation: str                 # add | subtract | multiply | divide
    operands: tuple                # sirovi (brojnik, imenilac) parovi, redom prikaza
    operand_displays: tuple        # MathJax zapisi operanada
    denominator_relation: str      # equal | unlike | any
    level: int
    answer: Fraction               # kanonski egzaktan rezultat
    display_answer: str            # kanonski prikaz (mješovit/razlomak/cio broj)
    accepted_answers: tuple        # ekvivalentni prihvatljivi zapisi
    question: str
    option_texts: tuple            # 4 prikaza, indeks 0 tačan (server kasnije miješa)
    correct_index: int
    hints: tuple                   # tri nivoa nagovještaja
    solution: str
    generator_version: str = GENERATOR_VERSION

    def task_payload(self) -> TaskPayload:
        """Isti oblik paketa kao model-nacrt — da objava i validatori ostanu
        DOSLOVNO isti kod za obje strategije izvršenja."""
        return TaskPayload(
            selected_lesson_id=self.lesson_id,
            selected_lesson_title=self.lesson_title,
            target_difficulty_level=self.level,
            text=self.question,
            task_type="multiple_choice",
            options=[TutorOption(id="abcd"[index], text=text)
                     for index, text in enumerate(self.option_texts)],
            correct_option_index=self.correct_index,
            correct_option_id="abcd"[self.correct_index],
            expected_answer=self.option_texts[self.correct_index],
            solution=self.solution,
            difficulty={1: "easy", 2: "standard", 3: "hard"}[self.level],
            difficulty_evidence=_evidence_for_level(self.level),
            task_signature=TaskSignature(
                task_family=FAMILY_ID,
                operation_or_relation=self.operation,
                normalized_parameters=[
                    SignatureParameter(name=f"operand_{index}",
                                       value=f"{num}/{den}")
                    for index, (num, den) in enumerate(self.operands)
                ] + [SignatureParameter(name="level", value=str(self.level))],
                required_conditions=[f"denominators_{self.denominator_relation}"],
                relevant_objects=["fraction"],
                answer_type="multiple_choice",
            ),
        )


def _evidence_for_level(level):
    """Iskren, fiksan dokaz težine po nivou — zadovoljava ISTE pragove
    (`difficulty_evidence_errors`) koje server primjenjuje i na model-pakete."""
    if level == 1:
        return DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    if level == 2:
        return DifficultyEvidence(
            reasoning_steps=2, condition_count=1, operation_count=2,
            representation_change_count=1, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    return DifficultyEvidence(
        reasoning_steps=3, condition_count=1, operation_count=3,
        representation_change_count=1, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


# ---------------------------------------------------------------------------
# PRIKAZ — kanonski zapis vrijednosti
# ---------------------------------------------------------------------------

def value_display(value: Fraction) -> str:
    """Kanonski prikaz vrijednosti: cio broj, pravi razlomak ili mješovit broj.

    Mješovit zapis `K\\frac{p}{q}` je projektna konvencija (mathcheck ga čita
    kao K + p/q)."""
    if value.denominator == 1:
        return str(value.numerator)
    whole, remainder = divmod(value.numerator, value.denominator)
    if whole >= 1:
        return f"{whole}\\frac{{{remainder}}}{{{value.denominator}}}"
    return f"\\frac{{{value.numerator}}}{{{value.denominator}}}"


def _raw_display(numerator, denominator):
    """Prikaz SIROVOG para bez skraćivanja — operand `\\frac{9}{12}` ostaje
    9/12, jer je skraćivanje upravo vještina koju učenik vježba."""
    if denominator == 1:
        return str(numerator)
    return f"\\frac{{{numerator}}}{{{denominator}}}"


def _mixed_raw_display(whole, numerator, denominator):
    return f"{whole}\\frac{{{numerator}}}{{{denominator}}}"


# ---------------------------------------------------------------------------
# GENERISANJE OPERANADA PO NIVOU (Workstream C)
# ---------------------------------------------------------------------------
# Nivoi su EKSPLICITNI parametarski rasponi — nikad prozna procjena:
#   1: mali brojevi, pravi razlomci, bez skraćivanja rezultata;
#   2: potrebno skraćivanje ili mješovit rezultat / uzajamno prosti imenioci;
#   3: mješoviti operandi (odnosno tri faktora kod množenja).

def _pick(rng, low, high):
    return rng.randint(low, high)


def _like_operands(rng, operation, level):
    """Jednaki imenioci (6-04-009)."""
    if level == 1:
        for _ in range(200):
            den = _pick(rng, 5, 10)
            if operation == "add":
                a = _pick(rng, 1, den - 2)
                b = _pick(rng, 1, den - 1 - a)
                total = a + b
            else:
                a = _pick(rng, 2, den - 1)
                b = _pick(rng, 1, a - 1)
                total = a - b
            if total > 0 and gcd(total, den) == 1:
                return ((a, den), (b, den))
        raise DeterministicGenerationError("like level 1")
    if level == 2:
        for _ in range(200):
            den = _pick(rng, 6, 12)
            if operation == "add":
                a = _pick(rng, 2, den + 3)
                b = _pick(rng, 2, den + 3)
                total = a + b
            else:
                a = _pick(rng, 4, 2 * den)
                b = _pick(rng, 1, a - 1)
                total = a - b
            # Nivo 2: rezultat traži skraćivanje ILI je mješovit zapis.
            if total > 0 and (gcd(total, den) > 1 or total > den):
                return ((a, den), (b, den))
        raise DeterministicGenerationError("like level 2")
    for _ in range(200):
        den = _pick(rng, 6, 12)
        whole_a, whole_b = _pick(rng, 1, 3), _pick(rng, 1, 3)
        pa, pb = _pick(rng, 1, den - 1), _pick(rng, 1, den - 1)
        a = whole_a * den + pa
        b = whole_b * den + pb
        if operation == "subtract":
            if Fraction(a, den) <= Fraction(b, den):
                continue
            return (("mixed", whole_a, pa, den), ("mixed", whole_b, pb, den))
        return (("mixed", whole_a, pa, den), ("mixed", whole_b, pb, den))
    raise DeterministicGenerationError("like level 3")


def _unlike_operands(rng, operation, level):
    """Različiti imenioci (6-04-010) — nikad slučajno jednaki."""
    if level == 1:
        for _ in range(300):
            d1 = _pick(rng, 2, 6)
            factor = _pick(rng, 2, 3)
            d2 = d1 * factor
            if d2 > 12 or d1 == d2:
                continue
            a = _pick(rng, 1, d1 - 1)
            b = _pick(rng, 1, d2 - 1)
            value = (Fraction(a, d1) + Fraction(b, d2) if operation == "add"
                     else Fraction(a, d1) - Fraction(b, d2))
            if 0 < value < 1:
                return ((a, d1), (b, d2))
        raise DeterministicGenerationError("unlike level 1")
    if level == 2:
        for _ in range(300):
            d1 = _pick(rng, 2, 9)
            d2 = _pick(rng, 2, 9)
            if d1 == d2 or gcd(d1, d2) != 1:
                continue
            a = _pick(rng, 1, d1 - 1)
            b = _pick(rng, 1, d2 - 1)
            value = (Fraction(a, d1) + Fraction(b, d2) if operation == "add"
                     else Fraction(a, d1) - Fraction(b, d2))
            if value > 0:
                return ((a, d1), (b, d2))
        raise DeterministicGenerationError("unlike level 2")
    for _ in range(300):
        d1 = _pick(rng, 3, 12)
        d2 = _pick(rng, 3, 12)
        if d1 == d2:
            continue
        whole_a, whole_b = _pick(rng, 1, 3), _pick(rng, 1, 2)
        pa, pb = _pick(rng, 1, d1 - 1), _pick(rng, 1, d2 - 1)
        left = Fraction(whole_a * d1 + pa, d1)
        right = Fraction(whole_b * d2 + pb, d2)
        if operation == "subtract" and left <= right:
            continue
        return (("mixed", whole_a, pa, d1), ("mixed", whole_b, pb, d2))
    raise DeterministicGenerationError("unlike level 3")


def _multiply_operands(rng, level):
    """Množenje (6-04-011): nivo 1 prirodan broj puta razlomak, nivo 2 razlomak
    puta razlomak uz skraćivanje, nivo 3 tri faktora."""
    if level == 1:
        for _ in range(200):
            n = _pick(rng, 2, 5)
            den = _pick(rng, 3, 9)
            num = _pick(rng, 1, den - 1)
            if gcd(num, den) == 1:
                return ((n, 1), (num, den))
        raise DeterministicGenerationError("multiply level 1")
    if level == 2:
        for _ in range(300):
            d1, d2 = _pick(rng, 2, 9), _pick(rng, 2, 9)
            a = _pick(rng, 1, d1 - 1)
            b = _pick(rng, 1, d2 - 1)
            # Skraćivanje mora postojati — inače je ovo nivo 1 s dva razlomka.
            if gcd(a * b, d1 * d2) > 1:
                return ((a, d1), (b, d2))
        raise DeterministicGenerationError("multiply level 2")
    for _ in range(400):
        pairs = []
        for _ in range(3):
            den = _pick(rng, 2, 6)
            num = _pick(rng, 1, den - 1)
            pairs.append((num, den))
        if gcd(pairs[0][0] * pairs[1][0] * pairs[2][0],
               pairs[0][1] * pairs[1][1] * pairs[2][1]) > 1:
            return tuple(pairs)
    raise DeterministicGenerationError("multiply level 3")


def _divide_operands(rng, level):
    """Dijeljenje (6-04-012): djelilac je uvijek strogo pozitivan."""
    if level == 1:
        for _ in range(200):
            den = _pick(rng, 2, 9)
            num = _pick(rng, 1, den - 1)
            n = _pick(rng, 2, 5)
            if gcd(num, den) == 1:
                return ((num, den), (n, 1))
        raise DeterministicGenerationError("divide level 1")
    if level == 2:
        for _ in range(200):
            d1, d2 = _pick(rng, 2, 9), _pick(rng, 2, 9)
            a = _pick(rng, 1, d1 - 1)
            c = _pick(rng, 1, d2 - 1)
            return ((a, d1), (c, d2))
        raise DeterministicGenerationError("divide level 2")
    for _ in range(200):
        den = _pick(rng, 2, 8)
        whole = _pick(rng, 1, 3)
        p = _pick(rng, 1, den - 1)
        d2 = _pick(rng, 2, 8)
        c = _pick(rng, 1, d2 - 1)
        return (("mixed", whole, p, den), (c, d2))
    raise DeterministicGenerationError("divide level 3")


def _normalize_operand(raw):
    """(vrijednost: Fraction, sirovi improper par (num, den), prikaz)."""
    if raw[0] == "mixed":
        _tag, whole, num, den = raw
        return (Fraction(whole * den + num, den), (whole * den + num, den),
                _mixed_raw_display(whole, num, den))
    num, den = raw
    return Fraction(num, den), (num, den), _raw_display(num, den)


# ---------------------------------------------------------------------------
# DISTRAKTORI (Workstream D) — poznate učeničke greške, egzaktno izračunate
# ---------------------------------------------------------------------------

def _distractor_values(operation, values, raw_pairs, answer):
    """Kandidati pogrešnih VRIJEDNOSTI, redom pedagoške vjerovatnoće."""
    candidates = []
    if operation in ("add", "subtract"):
        (a1, d1), (a2, d2) = raw_pairs[0], raw_pairs[1]
        if d1 + d2 > 0:
            # „Sabrao/oduzeo i imenioce“ — najčešća greška.
            numerator = a1 + a2 if operation == "add" else a1 - a2
            if numerator > 0:
                candidates.append(Fraction(numerator, d1 + d2))
        common = lcm(d1, d2)
        e1, e2 = a1 * (common // d1), a2 * (common // d2)
        if d1 != d2:
            # „Zaboravio proširiti prvi razlomak.“
            numerator = a1 + e2 if operation == "add" else a1 - e2
            if numerator > 0:
                candidates.append(Fraction(numerator, common))
        exact = e1 + e2 if operation == "add" else e1 - e2
        for delta in (1, -1, 2):
            if exact + delta > 0:
                candidates.append(Fraction(exact + delta, common))
    elif operation == "multiply":
        pairs = raw_pairs
        num_product = 1
        den_product = 1
        for num, den in pairs:
            num_product *= num
            den_product *= den
        num_sum = sum(num for num, _den in pairs)
        den_sum = sum(den for _num, den in pairs)
        if den_sum > 0:
            candidates.append(Fraction(num_sum, den_sum))     # sabrao umjesto množio
        if len(pairs) == 2:
            (a1, d1), (a2, d2) = pairs
            if d1 * a2 > 0:
                candidates.append(Fraction(a1 * d2, d1 * a2))  # množio recipročnim
        for delta in (1, -1):
            if num_product + delta > 0:
                candidates.append(Fraction(num_product + delta, den_product))
    else:  # divide
        (a, b), (c, d) = raw_pairs
        candidates.append(Fraction(a * c, b * d))              # množio bez recipročnog
        if a * d > 0:
            candidates.append(Fraction(b * c, a * d))          # obrnuo pogrešnu stranu
        exact_num, exact_den = a * d, b * c
        for delta in (1, -1):
            if exact_num + delta > 0:
                candidates.append(Fraction(exact_num + delta, exact_den))
    # Sigurnosna rezerva — uvijek dovoljno kandidata, i dalje egzaktno.
    step = Fraction(1, answer.denominator if answer.denominator > 1 else 2)
    for factor in (1, 2, 3, 4):
        candidates.append(answer + factor * step)
        if answer - factor * step > 0:
            candidates.append(answer - factor * step)
    return candidates


def _pick_distractors(operation, values, raw_pairs, answer):
    chosen = []
    for candidate in _distractor_values(operation, values, raw_pairs, answer):
        if candidate <= 0 or candidate == answer or candidate in chosen:
            continue
        chosen.append(candidate)
        if len(chosen) == 3:
            return tuple(chosen)
    raise DeterministicGenerationError("nedovoljno različitih distraktora")


# ---------------------------------------------------------------------------
# TEKSTOVI: pitanje, nagovještaji, rješenje (Workstreams F/G)
# ---------------------------------------------------------------------------

def _question(operation, displays):
    expression = f" {_OPERATOR_SYMBOL[operation]} ".join(displays)
    return f"Izračunaj: ${expression}$"


_RULE_HINT = {
    ("add", "equal"): "Imenioci su jednaki — sabiraju se samo brojnici, a imenilac ostaje isti.",
    ("subtract", "equal"): "Imenioci su jednaki — oduzimaju se samo brojnici, a imenilac ostaje isti.",
    ("add", "unlike"): "Imenioci su različiti — prvo nađi zajednički imenilac, pa tek onda sabiraj brojnike.",
    ("subtract", "unlike"): "Imenioci su različiti — prvo nađi zajednički imenilac, pa tek onda oduzmi brojnike.",
    ("multiply", "any"): "Razlomci se množe tako da se pomnože brojnik s brojnikom i imenilac s imeniocem.",
    ("divide", "any"): "Dijeljenje razlomkom je množenje njegovom recipročnom vrijednošću.",
}


def _sum_chain(operation, raw_pairs, answer):
    """Lanac jednakosti za jednake imenioce — svaka jednakost je egzaktna."""
    (a1, den), (a2, _den) = raw_pairs
    symbol = "+" if operation == "add" else "-"
    total = a1 + a2 if operation == "add" else a1 - a2
    chain = (f"\\frac{{{a1}}}{{{den}}}{symbol}\\frac{{{a2}}}{{{den}}}"
             f"=\\frac{{{a1}{symbol}{a2}}}{{{den}}}")
    if Fraction(total, den) != answer or answer.denominator == 1 or total > den:
        chain += f"={value_display(answer)}"
    return chain


def _common_denominator_chain(operation, raw_pairs, answer):
    (a1, d1), (a2, d2) = raw_pairs
    common = lcm(d1, d2)
    e1, e2 = a1 * (common // d1), a2 * (common // d2)
    symbol = "+" if operation == "add" else "-"
    total = e1 + e2 if operation == "add" else e1 - e2
    chain = (f"\\frac{{{e1}}}{{{common}}}{symbol}\\frac{{{e2}}}{{{common}}}"
             f"=\\frac{{{total}}}{{{common}}}")
    if Fraction(total, common) != answer or answer.denominator == 1:
        chain += f"={value_display(answer)}"
    return chain, common, (e1, d1, a1), (e2, d2, a2)


def _build_texts(operation, relation, level, raw_pairs, displays, values, answer):
    """(hints, solution) — sav vidljiv sadržaj je izveden iz egzaktnih brojeva."""
    rule_key = (operation, relation if operation in ("add", "subtract") else "any")
    rule = _RULE_HINT[rule_key]
    answer_text = value_display(answer)

    if operation in ("add", "subtract") and relation == "equal":
        chain = _sum_chain(operation, raw_pairs, answer)
        hint2 = (f"Imenilac ostaje ${raw_pairs[0][1]}$ — "
                 f"izračunaj ${raw_pairs[0][0]}{_OPERATOR_SYMBOL[operation]}"
                 f"{raw_pairs[1][0]}$ za novi brojnik.")
        hint3 = (f"Dakle: $\\frac{{{raw_pairs[0][0]}}}{{{raw_pairs[0][1]}}}"
                 f"{_OPERATOR_SYMBOL[operation]}"
                 f"\\frac{{{raw_pairs[1][0]}}}{{{raw_pairs[1][1]}}}"
                 f"=\\frac{{{raw_pairs[0][0]}{_OPERATOR_SYMBOL[operation]}{raw_pairs[1][0]}}}"
                 f"{{{raw_pairs[0][1]}}}$ — još samo sredi rezultat.")
        solution = (f"{rule} Računamo: ${chain}$. "
                    f"Rezultat je ${answer_text}$.")
        return (rule, hint2, hint3), solution

    if operation in ("add", "subtract") and relation == "unlike":
        chain, common, first, second = _common_denominator_chain(
            operation, raw_pairs, answer)
        e1, d1, a1 = first
        e2, d2, a2 = second
        hint2 = (f"Zajednički imenilac je ${common}$: "
                 f"$\\frac{{{a1}}}{{{d1}}}=\\frac{{{e1}}}{{{common}}}$ i "
                 f"$\\frac{{{a2}}}{{{d2}}}=\\frac{{{e2}}}{{{common}}}$.")
        hint3 = (f"Sada računaj: $\\frac{{{e1}}}{{{common}}}"
                 f"{_OPERATOR_SYMBOL[operation]}\\frac{{{e2}}}{{{common}}}$ "
                 "— još samo sredi rezultat.")
        solution = (f"{rule} Proširimo razlomke: "
                    f"$\\frac{{{a1}}}{{{d1}}}=\\frac{{{e1}}}{{{common}}}$, "
                    f"$\\frac{{{a2}}}{{{d2}}}=\\frac{{{e2}}}{{{common}}}$. "
                    f"Zatim: ${chain}$. Rezultat je ${answer_text}$.")
        return (rule, hint2, hint3), solution

    if operation == "multiply":
        numerators = "\\cdot ".join(str(num) for num, _den in raw_pairs)
        denominators = "\\cdot ".join(str(den) for _num, den in raw_pairs)
        product_num = 1
        product_den = 1
        for num, den in raw_pairs:
            product_num *= num
            product_den *= den
        chain = (" \\cdot ".join(displays)
                 + f"=\\frac{{{numerators}}}{{{denominators}}}"
                 + f"=\\frac{{{product_num}}}{{{product_den}}}")
        if Fraction(product_num, product_den) != answer or answer.denominator == 1:
            chain += f"={answer_text}"
        hint2 = (f"Pomnoži brojnike (${numerators}$) i imenioce "
                 f"(${denominators}$), pa skrati ako se može.")
        hint3 = (f"Dakle: $\\frac{{{numerators}}}{{{denominators}}}"
                 f"=\\frac{{{product_num}}}{{{product_den}}}$ — još samo skrati.")
        solution = f"{rule} Računamo: ${chain}$. Rezultat je ${answer_text}$."
        return (rule, hint2, hint3), solution

    # divide
    (a, b), (c, d) = raw_pairs
    chain = (f"{displays[0]}:{displays[1]}"
             f"=\\frac{{{a}}}{{{b}}}\\cdot\\frac{{{d}}}{{{c}}}"
             f"=\\frac{{{a * d}}}{{{b * c}}}")
    if Fraction(a * d, b * c) != answer or answer.denominator == 1:
        chain += f"={answer_text}"
    hint2 = (f"Recipročna vrijednost djelioca je $\\frac{{{d}}}{{{c}}}$ — "
             "pomnoži njome umjesto dijeljenja.")
    hint3 = (f"Dakle: $\\frac{{{a}}}{{{b}}}\\cdot\\frac{{{d}}}{{{c}}}"
             f"=\\frac{{{a * d}}}{{{b * c}}}$ — još samo sredi rezultat.")
    solution = f"{rule} Računamo: ${chain}$. Rezultat je ${answer_text}$."
    return (rule, hint2, hint3), solution


# ---------------------------------------------------------------------------
# GLAVNA ULAZNA TAČKA
# ---------------------------------------------------------------------------

def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    """Sastavi kompletan deterministički paket za JEDNU lekciju porodice.

    `parameters` su parametri kompajliranog semantičkog ugovora lekcije —
    generator NIKAD ne grana po ID-ju lekcije."""
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = min(max(int(level), 1), 3)
    operations = tuple(parameters.get("allowed_operations"))
    relation = parameters.get("denominator_relation") or "any"

    for _ in range(40):
        operation = rng.choice(list(operations))
        if operation in ("add", "subtract"):
            raw = (_like_operands(rng, operation, level) if relation == "equal"
                   else _unlike_operands(rng, operation, level))
        elif operation == "multiply":
            raw = _multiply_operands(rng, level)
        else:
            raw = _divide_operands(rng, level)

        normalized = [_normalize_operand(item) for item in raw]
        values = tuple(entry[0] for entry in normalized)
        raw_pairs = tuple(entry[1] for entry in normalized)
        displays = tuple(entry[2] for entry in normalized)

        if operation == "add":
            answer = values[0] + values[1]
        elif operation == "subtract":
            answer = values[0] - values[1]
        elif operation == "multiply":
            answer = values[0]
            for value in values[1:]:
                answer = answer * value
        else:
            if values[1] == 0:
                continue
            answer = values[0] / values[1]
        if answer <= 0:
            continue

        try:
            distractors = _pick_distractors(operation, values, raw_pairs, answer)
        except DeterministicGenerationError:
            continue

        option_texts = tuple(f"${value_display(value)}$"
                             for value in (answer, *distractors))
        if len(set(option_texts)) != 4:
            continue

        hints, solution = _build_texts(
            operation, relation, level, raw_pairs, displays, values, answer)
        display_answer = value_display(answer)
        improper = f"\\frac{{{answer.numerator}}}{{{answer.denominator}}}"
        accepted = tuple(dict.fromkeys(
            (display_answer, improper, str(answer))))

        return FractionPackage(
            lesson_id=lesson_id, lesson_title=lesson_title, family_id=FAMILY_ID,
            operation=operation, operands=raw_pairs, operand_displays=displays,
            denominator_relation=relation, level=level, answer=answer,
            display_answer=display_answer, accepted_answers=accepted,
            question=_question(operation, displays), option_texts=option_texts,
            correct_index=0, hints=hints, solution=solution,
        )
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")
