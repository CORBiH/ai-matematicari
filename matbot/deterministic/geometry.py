"""Deterministički generator formula-geometrije (bez potrebnog crteža).

Tri semantičke porodice, jedan modul i jedan generički izvršilac nad TABELOM
VRSTA ZADATAKA (vrsta = matematika, lekcija = podaci):

  • geometry_formula_2d  — obim/površina/srednja linija ravnih figura, krug,
    luk/isječak/prsten, uglovi i dijagonale mnogougla;
  • pythagoras_direct    — hipotenuza/kateta, provjera trojke i standardne
    primjene (dijagonale, visine, romb, trapez, tetiva);
  • solid_geometry_direct — kocka/kvadar/prizma/piramida/valjak/kupa/lopta:
    M, P, V, dijagonale, apotema, izvodnica, osni presjek, odnosi zapremina,
    masa iz gustine, broj elemenata poliedra.

AUTORITET: egzaktan račun — `Fraction` za racionalno, `RadicalValue` (q·√n)
za kvadratne iracionalnosti, a π-vrijednosti se nose SIMBOLIČKI kao
racionalan koeficijent uz π (prikaz `12\\pi`). Decimalna aproksimacija nikad
nije autoritet i ne pojavljuje se u odgovorima.

NOTACIJA (matbot/geometry_rules.py — projektno NESTANDARDNA i obavezna):
P = površina, O = obim, V = zapremina, B = površina baze, M = omotač,
H = visina tijela, h = visina figure, h_a = apotema piramide, d = dijagonala
(NIKAD prečnik), D = prostorna dijagonala, r = poluprečnik, R = PREČNIK
(R = 2r), s = izvodnica kupe. Mjerne jedinice stoje u PROZI (cm, cm², cm³),
nikad unutar `$...$`.

Sve formule su doslovno kanonske formule iz geometry_rules.FORMULE blokova.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.deterministic.radicals import RadicalValue
from matbot.tutor.schema import DifficultyEvidence

FAMILY_IDS = ("geometry_formula_2d", "pythagoras_direct",
              "solid_geometry_direct")
GENERATOR_VERSION = "detgeo-1"


# ---------------------------------------------------------------------------
# VRIJEDNOSTI I PRIKAZ
# ---------------------------------------------------------------------------

class PiValue:
    """Racionalan koeficijent uz π — egzaktno, bez decimalne aproksimacije."""

    __slots__ = ("coefficient",)

    def __init__(self, coefficient):
        self.coefficient = Fraction(coefficient)

    def __eq__(self, other):
        return isinstance(other, PiValue) and self.coefficient == other.coefficient

    def __hash__(self):
        return hash(("pi", self.coefficient))

    def display(self):
        if self.coefficient == 1:
            return "\\pi"
        return f"{core.fraction_display(self.coefficient)}\\pi"


def show(value):
    if isinstance(value, PiValue):
        return value.display()
    if isinstance(value, RadicalValue):
        return value.display()
    return core.fraction_display(Fraction(value))


def _positive(value):
    if isinstance(value, PiValue):
        return value.coefficient > 0
    if isinstance(value, RadicalValue):
        return value.coefficient > 0
    return Fraction(value) > 0


def _distinct(values):
    seen, out = set(), []
    for value in values:
        if isinstance(value, (PiValue, RadicalValue, Fraction, int)) and                 not _positive(value):
            continue
        key = (value if isinstance(value, (PiValue,)) else
               (value.coefficient, value.radicand)
               if isinstance(value, RadicalValue) else Fraction(value))
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _scaled_pool(answer):
    """Generički distraktori ISTE vrste vrijednosti (ista jedinica)."""
    if isinstance(answer, PiValue):
        c = answer.coefficient
        return [PiValue(c * 2), PiValue(c / 2), PiValue(c + 1), PiValue(c - 1),
                PiValue(c + 2), PiValue(c * 4)]
    if isinstance(answer, RadicalValue) and not answer.is_rational:
        c, n = answer.coefficient, answer.radicand
        return [RadicalValue(c * 2, n), RadicalValue(c / 2, n),
                RadicalValue(c + 1, n), RadicalValue.of(c, n + 1) if n + 1 > 1
                else RadicalValue(c * 3, n), RadicalValue(c * 4, n)]
    value = answer.rational() if isinstance(answer, RadicalValue) else Fraction(answer)
    pool = [value * 2, value / 2, value + 1, value - 1, value + 2, value - 2,
            value + 10, value * 4]
    return [candidate for candidate in pool if candidate > 0]


# ---------------------------------------------------------------------------
# GENERIČKI IZVRŠILAC — vrsta zadatka opisuje sebe rječnikom
# ---------------------------------------------------------------------------
# builder(rng, level) vraća rječnik:
#   question, answer, unit ("cm"/"cm²"/"cm³"/"°"/""), formula (latex, hint 1),
#   substitution (latex, hint 2/3), chain (latex, rješenje), distractors
#   (vrijednosti iste vrste), signature ((ime, vrijednost), ...), operation
# Jedinica ide u PROZU pitanja/opcija; `chain` je čist broj/π/korijen zapis.

def _int_dim(rng, level, small=(2, 9), mid=(4, 15), big=(6, 24)):
    low, high = small if level == 1 else (mid if level == 2 else big)
    return rng.randint(low, high)


def _evidence_formula(level):
    """Nivo 1 = jedna direktna primjena formule (do dvije operacije)."""
    if level == 1:
        return DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=2,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    return core.evidence_for_level(level)


def _package_from_spec(spec, family_id, lesson_id, lesson_title, level):
    answer = spec["answer"]
    unit = spec.get("unit", "")
    unit_suffix = f" {unit}" if unit else ""
    answer_display = show(answer)
    option_values = _distinct([answer] + list(spec["distractors"]))
    if len(option_values) < 4:
        for extra in _scaled_pool(answer):
            option_values = _distinct(option_values + [extra])
            if len(option_values) >= 4:
                break
    if len(option_values) < 4:
        raise DeterministicGenerationError("nedovoljno različitih opcija")
    option_texts = tuple(f"${show(value)}${unit_suffix}"
                         for value in option_values[:4])
    formula = spec["formula"]
    substitution = spec["substitution"]
    chain = spec["chain"]
    hints = (
        f"Formula: ${formula}$.",
        f"Uvrsti poznate vrijednosti: ${substitution}$.",
        f"Dakle: ${chain}$ — još samo pročitaj rezultat i jedinicu.",
    )
    if answer_display in hints[0]:
        # Prikaz odgovora ne smije biti sadržan u prvoj uputi (npr. odgovor
        # "2" u formuli "n(n-3)/2" ili odgovor "\pi" u formuli s π) — novi
        # pokušaj bira druge vrijednosti.
        raise DeterministicGenerationError("prikaz odgovora sadržan u prvoj uputi")
    solution = (f"{spec.get('rule', 'Primijenimo formulu')}: ${formula}$. "
                f"Računamo: ${chain} = {answer_display}$"
                f"{unit_suffix}." if spec.get("append_answer", True) else
                f"{spec.get('rule', 'Primijenimo formulu')}: ${formula}$. "
                f"Računamo: ${chain}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title, family_id=family_id,
        operation=spec["operation"], level=level, question=spec["question"],
        answer_value=answer, answer_display=answer_display,
        distractor_values=(), hints=hints, solution=solution,
        signature_parameters=list(spec["signature"]) + [("kind", spec["operation"])],
        required_conditions=[spec["operation"]],
        relevant_objects=["geometry"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="",
        evidence=spec.get("evidence") or _evidence_formula(level))


# ---------------------------------------------------------------------------
# 2D VRSTE
# ---------------------------------------------------------------------------

def _k_square_perimeter(rng, level):
    a = _int_dim(rng, level)
    if level >= 2 and rng.random() < 0.5:
        # Inverzno: iz obima nađi stranicu.
        o = 4 * a
        return {
            "question": f"Obim kvadrata je $O = {o}$ cm. Kolika je stranica kvadrata?",
            "answer": Fraction(a), "unit": "cm",
            "formula": "O = 4a", "substitution": f"{o} = 4a",
            "chain": f"a = {o} : 4 = {a}",
            "distractors": [Fraction(o), Fraction(a) * 2, Fraction(a) + 2],
            "signature": [("O", str(o))], "operation": "square_side_from_perimeter",
        }
    return {
        "question": f"Kvadrat ima stranicu $a = {a}$ cm. Koliki je obim kvadrata?",
        "answer": Fraction(4 * a), "unit": "cm",
        "formula": "O = 4a", "substitution": f"O = 4 \\cdot {a}",
        "chain": f"O = 4 \\cdot {a} = {4 * a}",
        "distractors": [Fraction(a * a), Fraction(2 * a), Fraction(a + 4)],
        "signature": [("a", str(a))], "operation": "square_perimeter",
    }


def _k_square_area(rng, level):
    a = _int_dim(rng, level)
    if level >= 2 and rng.random() < 0.5:
        p = a * a
        return {
            "question": f"Površina kvadrata je $P = {p}$ cm². Kolika je stranica kvadrata?",
            "answer": Fraction(a), "unit": "cm",
            "formula": "P = a^2", "substitution": f"{p} = a^2",
            "chain": f"a = \\sqrt{{{p}}} = {a}",
            "distractors": [Fraction(p) / 2, Fraction(p) / 4, Fraction(a) * 2],
            "signature": [("P", str(p))], "operation": "square_side_from_area",
        }
    return {
        "question": f"Kvadrat ima stranicu $a = {a}$ cm. Kolika je površina kvadrata?",
        "answer": Fraction(a * a), "unit": "cm²",
        "formula": "P = a^2", "substitution": f"P = {a}^2",
        "chain": f"P = {a} \\cdot {a} = {a * a}",
        "distractors": [Fraction(4 * a), Fraction(2 * a), Fraction(a * a) * 2],
        "signature": [("a", str(a))], "operation": "square_area",
    }


def _rect_dims(rng, level):
    a = _int_dim(rng, level, (3, 9), (5, 15), (8, 25))
    b = _int_dim(rng, level, (2, 7), (3, 12), (5, 20))
    if a == b:
        b += 1
    return max(a, b), min(a, b)


def _k_rectangle_perimeter(rng, level):
    a, b = _rect_dims(rng, level)
    o = 2 * (a + b)
    if level >= 2 and rng.random() < 0.5:
        return {
            "question": (f"Obim pravougaonika je $O = {o}$ cm, a jedna stranica "
                         f"$a = {a}$ cm. Kolika je druga stranica?"),
            "answer": Fraction(b), "unit": "cm",
            "formula": "O = 2(a+b)", "substitution": f"{o} = 2({a} + b)",
            "chain": f"b = {o} : 2 - {a} = {o // 2} - {a} = {b}",
            "distractors": [Fraction(o - a), Fraction(o) / 2, Fraction(b) + 2],
            "signature": [("O", str(o)), ("a", str(a))],
            "operation": "rectangle_side_from_perimeter",
        }
    return {
        "question": (f"Pravougaonik ima stranice $a = {a}$ cm i $b = {b}$ cm. "
                     "Koliki je obim pravougaonika?"),
        "answer": Fraction(o), "unit": "cm",
        "formula": "O = 2(a+b)", "substitution": f"O = 2({a} + {b})",
        "chain": f"O = 2 \\cdot {a + b} = {o}",
        "distractors": [Fraction(a * b), Fraction(a + b), Fraction(o) * 2],
        "signature": [("a", str(a)), ("b", str(b))],
        "operation": "rectangle_perimeter",
    }


def _k_rectangle_area(rng, level):
    a, b = _rect_dims(rng, level)
    p = a * b
    if level >= 2 and rng.random() < 0.5:
        return {
            "question": (f"Površina pravougaonika je $P = {p}$ cm², a stranica "
                         f"$a = {a}$ cm. Kolika je stranica $b$?"),
            "answer": Fraction(b), "unit": "cm",
            "formula": "P = ab", "substitution": f"{p} = {a} \\cdot b",
            "chain": f"b = {p} : {a} = {b}",
            "distractors": [Fraction(p - a), Fraction(b) * 2, Fraction(a)],
            "signature": [("P", str(p)), ("a", str(a))],
            "operation": "rectangle_side_from_area",
        }
    return {
        "question": (f"Pravougaonik ima stranice $a = {a}$ cm i $b = {b}$ cm. "
                     "Kolika je površina pravougaonika?"),
        "answer": Fraction(p), "unit": "cm²",
        "formula": "P = ab", "substitution": f"P = {a} \\cdot {b}",
        "chain": f"P = {a} \\cdot {b} = {p}",
        "distractors": [Fraction(2 * (a + b)), Fraction(a + b), Fraction(p) * 2],
        "signature": [("a", str(a)), ("b", str(b))],
        "operation": "rectangle_area",
    }


def _k_triangle_perimeter(rng, level):
    while True:
        a = _int_dim(rng, level, (3, 9), (5, 16), (8, 30))
        b = _int_dim(rng, level, (3, 9), (5, 16), (8, 30))
        c = rng.randint(abs(a - b) + 1, a + b - 1)
        if c >= 2:
            break
    o = a + b + c
    return {
        "question": (f"Trougao ima stranice $a = {a}$ cm, $b = {b}$ cm i "
                     f"$c = {c}$ cm. Koliki je obim trougla?"),
        "answer": Fraction(o), "unit": "cm",
        "formula": "O = a+b+c", "substitution": f"O = {a} + {b} + {c}",
        "chain": f"O = {a} + {b} + {c} = {o}",
        "distractors": [Fraction(o - c), Fraction(a * b), Fraction(o) * 2],
        "signature": [("a", str(a)), ("b", str(b)), ("c", str(c))],
        "operation": "triangle_perimeter",
    }


def _k_triangle_area(rng, level):
    a = _int_dim(rng, level, (4, 10), (6, 18), (8, 30))
    h = _int_dim(rng, level, (2, 8), (4, 14), (6, 20))
    if (a * h) % 2 and level == 1:
        h += 1
    p = Fraction(a * h, 2)
    return {
        "question": (f"Trougao ima stranicu $a = {a}$ cm i njoj odgovarajuću "
                     f"visinu $h_a = {h}$ cm. Kolika je površina trougla?"),
        "answer": p, "unit": "cm²",
        "formula": "P = \\frac{a \\cdot h_a}{2}",
        "substitution": f"P = \\frac{{{a} \\cdot {h}}}{{2}}",
        "chain": f"P = \\frac{{{a * h}}}{{2}} = {core.fraction_display(p)}",
        "distractors": [Fraction(a * h), Fraction(a + h), p * 2],
        "signature": [("a", str(a)), ("h", str(h))],
        "operation": "triangle_area",
    }


def _k_parallelogram_area(rng, level):
    a = _int_dim(rng, level, (4, 10), (6, 18), (9, 30))
    h = _int_dim(rng, level, (2, 8), (4, 14), (6, 20))
    p = a * h
    if level >= 2 and rng.random() < 0.4:
        return {
            "question": (f"Površina paralelograma je $P = {p}$ cm², a stranica "
                         f"$a = {a}$ cm. Kolika je visina $h_a$?"),
            "answer": Fraction(h), "unit": "cm",
            "formula": "P = a \\cdot h_a", "substitution": f"{p} = {a} \\cdot h_a",
            "chain": f"h_a = {p} : {a} = {h}",
            "distractors": [Fraction(p - a), Fraction(h) * 2, Fraction(a)],
            "signature": [("P", str(p)), ("a", str(a))],
            "operation": "parallelogram_height_from_area",
        }
    return {
        "question": (f"Paralelogram ima stranicu $a = {a}$ cm i visinu "
                     f"$h_a = {h}$ cm. Kolika je površina paralelograma?"),
        "answer": Fraction(p), "unit": "cm²",
        "formula": "P = a \\cdot h_a", "substitution": f"P = {a} \\cdot {h}",
        "chain": f"P = {a} \\cdot {h} = {p}",
        "distractors": [Fraction(a * h, 2), Fraction(2 * (a + h)), Fraction(p) * 2],
        "signature": [("a", str(a)), ("h", str(h))],
        "operation": "parallelogram_area",
    }


def _k_parallelogram_perimeter(rng, level):
    a, b = _rect_dims(rng, level)
    o = 2 * (a + b)
    return {
        "question": (f"Paralelogram ima stranice $a = {a}$ cm i $b = {b}$ cm. "
                     "Koliki je obim paralelograma?"),
        "answer": Fraction(o), "unit": "cm",
        "formula": "O = 2(a+b)", "substitution": f"O = 2({a} + {b})",
        "chain": f"O = 2 \\cdot {a + b} = {o}",
        "distractors": [Fraction(a + b), Fraction(a * b), Fraction(o) * 2],
        "signature": [("a", str(a)), ("b", str(b))],
        "operation": "parallelogram_perimeter",
    }


def _k_trapezoid_area(rng, level):
    a = _int_dim(rng, level, (6, 12), (8, 20), (10, 30))
    c = rng.randint(2, a - 2)
    h = _int_dim(rng, level, (2, 8), (3, 12), (5, 18))
    if (a + c) % 2 and level == 1:
        c += 1 if c + 1 < a else -1
    p = Fraction((a + c) * h, 2)
    return {
        "question": (f"Trapez ima osnovice $a = {a}$ cm i $c = {c}$ cm i "
                     f"visinu $h = {h}$ cm. Kolika je površina trapeza?"),
        "answer": p, "unit": "cm²",
        "formula": "P = \\frac{(a+c) \\cdot h}{2}",
        "substitution": f"P = \\frac{{({a} + {c}) \\cdot {h}}}{{2}}",
        "chain": f"P = \\frac{{{a + c} \\cdot {h}}}{{2}} = {core.fraction_display(p)}",
        "distractors": [Fraction((a + c) * h), Fraction(a * h), p * 2],
        "signature": [("a", str(a)), ("c", str(c)), ("h", str(h))],
        "operation": "trapezoid_area",
    }


def _k_trapezoid_midline(rng, level):
    a = _int_dim(rng, level, (6, 14), (8, 22), (10, 30))
    c = rng.randint(2, a - 2)
    if (a + c) % 2:
        c += 1 if c + 1 < a else -1
    m = Fraction(a + c, 2)
    return {
        "question": (f"Trapez ima osnovice $a = {a}$ cm i $c = {c}$ cm. "
                     "Kolika je srednja linija trapeza?"),
        "answer": m, "unit": "cm",
        "formula": "m = \\frac{a+c}{2}",
        "substitution": f"m = \\frac{{{a} + {c}}}{{2}}",
        "chain": f"m = \\frac{{{a + c}}}{{2}} = {core.fraction_display(m)}",
        "distractors": [Fraction(a + c), Fraction(a - c), m * 2],
        "signature": [("a", str(a)), ("c", str(c))],
        "operation": "trapezoid_midline",
    }


def _k_triangle_midline(rng, level):
    a = 2 * _int_dim(rng, level, (2, 9), (4, 15), (6, 24))
    return {
        "question": (f"Stranica trougla je $a = {a}$ cm. Kolika je srednja "
                     "linija trougla paralelna toj stranici?"),
        "answer": Fraction(a, 2), "unit": "cm",
        "formula": "m = \\frac{a}{2}", "substitution": f"m = \\frac{{{a}}}{{2}}",
        "chain": f"m = {a} : 2 = {a // 2}",
        "distractors": [Fraction(a) * 2, Fraction(a) - 1, Fraction(a, 4)],
        "signature": [("a", str(a))], "operation": "triangle_midline",
    }


def _k_rhombus_area(rng, level):
    d1 = 2 * _int_dim(rng, level, (2, 6), (3, 9), (4, 12))
    d2 = 2 * _int_dim(rng, level, (1, 5), (2, 8), (3, 11))
    if d1 == d2:
        d2 += 2
    p = Fraction(d1 * d2, 2)
    return {
        "question": (f"Romb ima dijagonale $d_1 = {d1}$ cm i $d_2 = {d2}$ cm. "
                     "Kolika je površina romba?"),
        "answer": p, "unit": "cm²",
        "formula": "P = \\frac{d_1 \\cdot d_2}{2}",
        "substitution": f"P = \\frac{{{d1} \\cdot {d2}}}{{2}}",
        "chain": f"P = \\frac{{{d1 * d2}}}{{2}} = {core.fraction_display(p)}",
        "distractors": [Fraction(d1 * d2), Fraction(d1 + d2), p * 2],
        "signature": [("d1", str(d1)), ("d2", str(d2))],
        "operation": "rhombus_area_diagonals",
    }


def _k_rhombus_perimeter(rng, level):
    a = _int_dim(rng, level)
    return {
        "question": f"Romb ima stranicu $a = {a}$ cm. Koliki je obim romba?",
        "answer": Fraction(4 * a), "unit": "cm",
        "formula": "O = 4a", "substitution": f"O = 4 \\cdot {a}",
        "chain": f"O = 4 \\cdot {a} = {4 * a}",
        "distractors": [Fraction(2 * a), Fraction(a * a), Fraction(4 * a + 4)],
        "signature": [("a", str(a))], "operation": "rhombus_perimeter",
    }


def _k_deltoid_area(rng, level):
    d1 = 2 * _int_dim(rng, level, (2, 6), (3, 9), (4, 12))
    d2 = 2 * _int_dim(rng, level, (1, 5), (2, 8), (3, 11))
    if d1 == d2:
        d1 += 2
    p = Fraction(d1 * d2, 2)
    return {
        "question": (f"Deltoid ima dijagonale $d_1 = {d1}$ cm i $d_2 = {d2}$ cm. "
                     "Kolika je površina deltoida?"),
        "answer": p, "unit": "cm²",
        "formula": "P = \\frac{d_1 \\cdot d_2}{2}",
        "substitution": f"P = \\frac{{{d1} \\cdot {d2}}}{{2}}",
        "chain": f"P = \\frac{{{d1 * d2}}}{{2}} = {core.fraction_display(p)}",
        "distractors": [Fraction(d1 * d2), Fraction(d1 + d2), p * 2],
        "signature": [("d1", str(d1)), ("d2", str(d2))],
        "operation": "deltoid_area",
    }


def _build_orthodiagonal(rng, level):
    d1 = 2 * _int_dim(rng, level, (2, 6), (3, 9), (4, 12))
    d2 = 2 * _int_dim(rng, level, (1, 5), (2, 8), (3, 11))
    if d1 == d2:
        d1 += 2
    p = Fraction(d1 * d2, 2)
    return {
        "question": (f"Četverougao ima normalne (okomite) dijagonale "
                     f"$d_1 = {d1}$ cm i $d_2 = {d2}$ cm. Kolika je njegova "
                     "površina?"),
        "answer": p, "unit": "cm²",
        "formula": "P = \\frac{d_1 \\cdot d_2}{2}",
        "substitution": f"P = \\frac{{{d1} \\cdot {d2}}}{{2}}",
        "chain": f"P = \\frac{{{d1 * d2}}}{{2}} = {core.fraction_display(p)}",
        "distractors": [Fraction(d1 * d2), Fraction(d1 + d2), p * 2],
        "signature": [("d1", str(d1)), ("d2", str(d2))],
        "operation": "orthodiagonal_area",
    }


def _k_quad_perimeter(rng, level):
    sides = [_int_dim(rng, level, (2, 9), (4, 14), (6, 22)) for _ in range(4)]
    o = sum(sides)
    a, b, c, d = sides
    return {
        "question": (f"Četverougao ima stranice $a = {a}$ cm, $b = {b}$ cm, "
                     f"$c = {c}$ cm i $d = {d}$ cm. Koliki je obim?"),
        "answer": Fraction(o), "unit": "cm",
        "formula": "O = a+b+c+d",
        "substitution": f"O = {a} + {b} + {c} + {d}",
        "chain": f"O = {a} + {b} + {c} + {d} = {o}",
        "distractors": [Fraction(o - d), Fraction(o) * 2, Fraction(o) + 2],
        "signature": [("sides", "+".join(map(str, sides)))],
        "operation": "quad_perimeter",
    }


# --- KRUG (π simbolički) ---------------------------------------------------

def _k_circle_circumference(rng, level):
    r = _int_dim(rng, level, (2, 9), (3, 12), (5, 20))
    if level >= 2 and rng.random() < 0.5:
        big_r = 2 * r
        return {
            "question": (f"Prečnik kruga je $R = {big_r}$ cm. Koliki je obim "
                         "kruga? (Rezultat izrazi preko $\\pi$.)"),
            "answer": PiValue(big_r), "unit": "cm",
            "formula": "O = \\pi R", "substitution": f"O = \\pi \\cdot {big_r}",
            "chain": f"O = {big_r}\\pi",
            "distractors": [PiValue(r), PiValue(4 * r), PiValue(big_r + 2)],
            "signature": [("R", str(big_r))],
            "operation": "circle_circumference_from_R",
        }
    return {
        "question": (f"Poluprečnik kruga je $r = {r}$ cm. Koliki je obim "
                     "kruga? (Rezultat izrazi preko $\\pi$.)"),
        "answer": PiValue(2 * r), "unit": "cm",
        "formula": "O = 2\\pi r", "substitution": f"O = 2\\pi \\cdot {r}",
        "chain": f"O = {2 * r}\\pi",
        "distractors": [PiValue(r), PiValue(r * r), PiValue(4 * r)],
        "signature": [("r", str(r))], "operation": "circle_circumference",
    }


def _k_circle_area(rng, level):
    r = _int_dim(rng, level, (2, 9), (3, 12), (4, 20))
    return {
        "question": (f"Poluprečnik kruga je $r = {r}$ cm. Kolika je površina "
                     "kruga? (Rezultat izrazi preko $\\pi$.)"),
        "answer": PiValue(r * r), "unit": "cm²",
        "formula": "P = \\pi r^2", "substitution": f"P = \\pi \\cdot {r}^2",
        "chain": f"P = {r * r}\\pi",
        "distractors": [PiValue(2 * r), PiValue(4 * r * r), PiValue(r)],
        "signature": [("r", str(r))], "operation": "circle_area",
    }


def _k_arc_length(rng, level):
    r = _int_dim(rng, level, (2, 9), (3, 12), (4, 18))
    alpha = rng.choice((30, 45, 60, 90, 120, 180) if level < 3
                       else (30, 45, 60, 90, 120, 135, 150, 240, 270))
    length = PiValue(Fraction(r * alpha, 180))
    return {
        "question": (f"Kružni luk pripada kružnici poluprečnika $r = {r}$ cm i "
                     f"centralnom uglu od ${alpha}^\\circ$. Kolika je dužina "
                     "luka? (Preko $\\pi$.)"),
        "answer": length, "unit": "cm",
        "formula": "l = \\frac{\\pi r \\alpha}{180^\\circ}",
        "substitution": f"l = \\frac{{\\pi \\cdot {r} \\cdot {alpha}}}{{180}}",
        "chain": f"l = {length.display()}",
        "distractors": [PiValue(Fraction(r * alpha, 360)),
                        PiValue(Fraction(r * r * alpha, 360)),
                        PiValue(2 * r)],
        "signature": [("r", str(r)), ("alpha", str(alpha))],
        "operation": "arc_length",
    }


def _k_sector_area(rng, level):
    r = _int_dim(rng, level, (2, 8), (3, 12), (4, 18))
    alpha = rng.choice((30, 45, 60, 90, 120, 180) if level < 3
                       else (30, 45, 60, 90, 135, 240, 270))
    area = PiValue(Fraction(r * r * alpha, 360))
    return {
        "question": (f"Kružni isječak pripada krugu poluprečnika $r = {r}$ cm i "
                     f"centralnom uglu od ${alpha}^\\circ$. Kolika je površina "
                     "isječka? (Preko $\\pi$.)"),
        "answer": area, "unit": "cm²",
        "formula": "P = \\frac{\\pi r^2 \\alpha}{360^\\circ}",
        "substitution": f"P = \\frac{{\\pi \\cdot {r}^2 \\cdot {alpha}}}{{360}}",
        "chain": f"P = {area.display()}",
        "distractors": [PiValue(Fraction(r * alpha, 180)),
                        PiValue(Fraction(r * r * alpha, 180)),
                        PiValue(r * r)],
        "signature": [("r", str(r)), ("alpha", str(alpha))],
        "operation": "sector_area",
    }


def _k_annulus_area(rng, level):
    r2 = _int_dim(rng, level, (1, 5), (2, 8), (3, 12))
    r1 = r2 + _int_dim(rng, level, (1, 4), (2, 6), (2, 8))
    area = PiValue(r1 * r1 - r2 * r2)
    return {
        "question": (f"Kružni prsten određen je poluprečnicima $r_1 = {r1}$ cm "
                     f"i $r_2 = {r2}$ cm. Kolika je površina prstena? "
                     "(Preko $\\pi$.)"),
        "answer": area, "unit": "cm²",
        "formula": "P = \\pi(r_1^2 - r_2^2)",
        "substitution": f"P = \\pi({r1}^2 - {r2}^2)",
        "chain": f"P = \\pi({r1 * r1} - {r2 * r2}) = {area.display()}",
        "distractors": [PiValue((r1 - r2) ** 2), PiValue(r1 * r1 + r2 * r2),
                        PiValue(2 * (r1 - r2))],
        "signature": [("r1", str(r1)), ("r2", str(r2))],
        "operation": "annulus_area",
    }


# --- MNOGOUGAO -------------------------------------------------------------

def _k_polygon_interior_sum(rng, level):
    n = rng.randint(4, 7) if level == 1 else rng.randint(5, 12)
    total = (n - 2) * 180
    if level >= 2 and rng.random() < 0.5:
        return {
            "question": (f"Zbir unutrašnjih uglova mnogougla iznosi "
                         f"${total}^\\circ$. Koliko stranica ima mnogougao?"),
            "answer": Fraction(n), "unit": "",
            "formula": "S_n = (n-2) \\cdot 180^\\circ",
            "substitution": f"{total} = (n-2) \\cdot 180",
            "chain": f"n = {total} : 180 + 2 = {total // 180} + 2 = {n}",
            "distractors": [Fraction(n - 2), Fraction(n + 1), Fraction(n - 1)],
            "signature": [("S", str(total))],
            "operation": "polygon_sides_from_sum",
        }
    return {
        "question": (f"Koliki je zbir unutrašnjih uglova mnogougla sa "
                     f"$n = {n}$ stranica?"),
        "answer": Fraction(total), "unit": "°",
        "formula": "S_n = (n-2) \\cdot 180^\\circ",
        "substitution": f"S_n = ({n}-2) \\cdot 180",
        "chain": f"S_n = {n - 2} \\cdot 180 = {total}",
        "distractors": [Fraction(n * 180), Fraction(360), Fraction(total - 180)],
        "signature": [("n", str(n))], "operation": "polygon_interior_sum",
    }


def _k_polygon_diagonals(rng, level):
    n = rng.randint(4, 7) if level == 1 else rng.randint(5, 12)
    d = n * (n - 3) // 2
    return {
        "question": f"Koliko dijagonala ima mnogougao sa $n = {n}$ tjemena?",
        "answer": Fraction(d), "unit": "",
        "formula": "D_n = \\frac{n(n-3)}{2}",
        "substitution": f"D_n = \\frac{{{n} \\cdot ({n}-3)}}{{2}}",
        "chain": f"D_n = \\frac{{{n} \\cdot {n - 3}}}{{2}} = {d}",
        "distractors": [Fraction(n * (n - 3)), Fraction(n - 3), Fraction(d + n)],
        "signature": [("n", str(n))], "operation": "polygon_diagonals",
    }


def _k_regular_polygon_angle(rng, level):
    n = rng.choice((3, 4, 5, 6) if level == 1 else (5, 6, 8, 9, 10, 12))
    angle = Fraction((n - 2) * 180, n)
    return {
        "question": (f"Koliki je unutrašnji ugao pravilnog mnogougla sa "
                     f"$n = {n}$ stranica?"),
        "answer": angle, "unit": "°",
        "formula": "\\alpha = \\frac{(n-2) \\cdot 180^\\circ}{n}",
        "substitution": f"\\alpha = \\frac{{({n}-2) \\cdot 180}}{{{n}}}",
        "chain": (f"\\alpha = \\frac{{{(n - 2) * 180}}}{{{n}}} = "
                  f"{core.fraction_display(angle)}"),
        "distractors": [Fraction(360, n), Fraction(180), angle / 2],
        "signature": [("n", str(n))], "operation": "regular_polygon_angle",
    }


def _k_regular_polygon_perimeter(rng, level):
    n = rng.choice((3, 4, 5, 6) if level == 1 else (5, 6, 8, 10, 12))
    a = _int_dim(rng, level)
    return {
        "question": (f"Pravilan mnogougao ima $n = {n}$ stranica dužine "
                     f"$a = {a}$ cm. Koliki je obim mnogougla?"),
        "answer": Fraction(n * a), "unit": "cm",
        "formula": "O = n \\cdot a", "substitution": f"O = {n} \\cdot {a}",
        "chain": f"O = {n} \\cdot {a} = {n * a}",
        "distractors": [Fraction(n + a), Fraction(n * a) * 2, Fraction(a * a)],
        "signature": [("n", str(n)), ("a", str(a))],
        "operation": "regular_polygon_perimeter",
    }


# ---------------------------------------------------------------------------
# PITAGORA
# ---------------------------------------------------------------------------

_TRIPLES = ((3, 4, 5), (6, 8, 10), (5, 12, 13), (9, 12, 15), (8, 15, 17),
            (12, 16, 20), (7, 24, 25), (20, 21, 29), (10, 24, 26), (9, 40, 41))


def _triple(rng, level):
    pool = _TRIPLES[:4] if level == 1 else (_TRIPLES[2:] if level == 3
                                            else _TRIPLES[:7])
    return rng.choice(pool)


def _k_hypotenuse(rng, level):
    if level == 3 and rng.random() < 0.5:
        a = rng.randint(2, 7)
        b = rng.randint(2, 7)
        c = RadicalValue.sqrt_of(a * a + b * b)
        if c.is_rational:
            b += 1
            c = RadicalValue.sqrt_of(a * a + b * b)
        return {
            "question": (f"Katete pravouglog trougla su $a = {a}$ cm i "
                         f"$b = {b}$ cm. Kolika je hipotenuza $c$?"),
            "answer": c, "unit": "cm",
            "formula": "c^2 = a^2 + b^2",
            "substitution": f"c^2 = {a}^2 + {b}^2 = {a * a + b * b}",
            "chain": f"c = \\sqrt{{{a * a + b * b}}} = {c.display()}",
            "distractors": [RadicalValue.of(a + b), RadicalValue.sqrt_of(a * a + b * b + 1),
                            RadicalValue.of(Fraction(a * a + b * b))],
            "signature": [("a", str(a)), ("b", str(b))],
            "operation": "hypotenuse",
        }
    a, b, c = _triple(rng, level)
    return {
        "question": (f"Katete pravouglog trougla su $a = {a}$ cm i "
                     f"$b = {b}$ cm. Kolika je hipotenuza $c$?"),
        "answer": Fraction(c), "unit": "cm",
        "formula": "c^2 = a^2 + b^2",
        "substitution": f"c^2 = {a}^2 + {b}^2 = {a * a + b * b}",
        "chain": f"c = \\sqrt{{{a * a + b * b}}} = {c}",
        "distractors": [Fraction(a + b), Fraction(c) + 1, Fraction(c) - 1,
                        Fraction(a * a + b * b)],
        "signature": [("a", str(a)), ("b", str(b))],
        "operation": "hypotenuse",
    }


def _k_leg(rng, level):
    a, b, c = _triple(rng, level)
    return {
        "question": (f"Hipotenuza pravouglog trougla je $c = {c}$ cm, a jedna "
                     f"kateta $a = {a}$ cm. Kolika je druga kateta $b$?"),
        "answer": Fraction(b), "unit": "cm",
        "formula": "b^2 = c^2 - a^2",
        "substitution": f"b^2 = {c}^2 - {a}^2 = {c * c - a * a}",
        "chain": f"b = \\sqrt{{{c * c - a * a}}} = {b}",
        "distractors": [Fraction(c - a), Fraction(c + a), Fraction(b) + 1],
        "signature": [("c", str(c)), ("a", str(a))],
        "operation": "leg",
    }


def _k_verify_triple(rng, level):
    a, b, c = _triple(rng, level)
    wrong = []
    for _ in range(200):
        x = rng.randint(2, 15)
        y = rng.randint(x, 20)
        z = rng.randint(y + 1, y + 6)
        if x * x + y * y != z * z and (x, y, z) not in wrong:
            wrong.append((x, y, z))
        if len(wrong) == 3:
            break
    if len(wrong) < 3:
        raise DeterministicGenerationError("nedovoljno netrojki")
    correct_text = f"${a}$ cm, ${b}$ cm i ${c}$ cm"
    options = [correct_text] + [f"${x}$ cm, ${y}$ cm i ${z}$ cm"
                                for x, y, z in wrong]
    return {
        "question": ("Koje od ponuđenih dužina mogu biti stranice PRAVOUGLOG "
                     "trougla?"),
        "answer": (a, b, c), "unit": "",
        "formula": "c^2 = a^2 + b^2",
        "substitution": f"{a}^2 + {b}^2 = {a * a} + {b * b} = {a * a + b * b}",
        "chain": f"{a}^2 + {b}^2 = {a * a + b * b} = {c}^2",
        "distractors": [], "option_texts": tuple(options),
        "signature": [("triple", f"{a}+{b}+{c}")],
        "operation": "verify_triple",
    }


def _k_square_diagonal(rng, level):
    a = _int_dim(rng, level)
    d = RadicalValue.of(a, 2)
    return {
        "question": f"Kvadrat ima stranicu $a = {a}$ cm. Kolika je dijagonala kvadrata?",
        "answer": d, "unit": "cm",
        "formula": "d = a\\sqrt{2}",
        "substitution": f"d = {a}\\sqrt{{2}}",
        "chain": f"d = {d.display()}",
        "distractors": [RadicalValue.of(2 * a), RadicalValue.of(a, 3),
                        RadicalValue.of(Fraction(a, 2), 2)],
        "signature": [("a", str(a))], "operation": "square_diagonal",
    }


def _k_rectangle_diagonal(rng, level):
    a, b, d = _triple(rng, level)
    return {
        "question": (f"Pravougaonik ima stranice $a = {a}$ cm i $b = {b}$ cm. "
                     "Kolika je dijagonala pravougaonika?"),
        "answer": Fraction(d), "unit": "cm",
        "formula": "d = \\sqrt{a^2+b^2}",
        "substitution": f"d = \\sqrt{{{a}^2 + {b}^2}} = \\sqrt{{{a * a + b * b}}}",
        "chain": f"d = \\sqrt{{{a * a + b * b}}} = {d}",
        "distractors": [Fraction(a + b), Fraction(d) + 1, Fraction(d) - 1],
        "signature": [("a", str(a)), ("b", str(b))],
        "operation": "rectangle_diagonal",
    }


def _k_isosceles_height(rng, level):
    half, height, leg = _triple(rng, level)
    a = 2 * half
    return {
        "question": (f"Jednakokraki trougao ima osnovicu $a = {a}$ cm i krak "
                     f"$b = {leg}$ cm. Kolika je visina na osnovicu?"),
        "answer": Fraction(height), "unit": "cm",
        "formula": "h_a = \\sqrt{b^2 - \\left(\\frac{a}{2}\\right)^2}",
        "substitution": (f"h_a = \\sqrt{{{leg}^2 - {half}^2}} = "
                         f"\\sqrt{{{leg * leg - half * half}}}"),
        "chain": f"h_a = \\sqrt{{{leg * leg - half * half}}} = {height}",
        "distractors": [Fraction(leg - half), Fraction(height) + 1,
                        Fraction(leg)],
        "signature": [("a", str(a)), ("b", str(leg))],
        "operation": "isosceles_height",
    }


def _k_equilateral_height(rng, level):
    a = 2 * _int_dim(rng, level, (1, 5), (2, 7), (3, 10))
    h = RadicalValue.of(Fraction(a, 2), 3)
    return {
        "question": (f"Jednakostranični trougao ima stranicu $a = {a}$ cm. "
                     "Kolika je visina trougla?"),
        "answer": h, "unit": "cm",
        "formula": "h = \\frac{a\\sqrt{3}}{2}",
        "substitution": f"h = \\frac{{{a}\\sqrt{{3}}}}{{2}}",
        "chain": f"h = {h.display()}",
        "distractors": [RadicalValue.of(a, 3), RadicalValue.of(Fraction(a, 2), 2),
                        RadicalValue.of(Fraction(a, 4), 3)],
        "signature": [("a", str(a))], "operation": "equilateral_height",
    }


def _k_equilateral_area(rng, level):
    a = 2 * _int_dim(rng, level, (1, 5), (2, 7), (3, 10))
    p = RadicalValue.of(Fraction(a * a, 4), 3)
    return {
        "question": (f"Jednakostranični trougao ima stranicu $a = {a}$ cm. "
                     "Kolika je površina trougla?"),
        "answer": p, "unit": "cm²",
        "formula": "P = \\frac{a^2\\sqrt{3}}{4}",
        "substitution": f"P = \\frac{{{a}^2\\sqrt{{3}}}}{{4}} = \\frac{{{a * a}\\sqrt{{3}}}}{{4}}",
        "chain": f"P = {p.display()}",
        "distractors": [RadicalValue.of(Fraction(a * a, 2), 3),
                        RadicalValue.of(a * a, 3),
                        RadicalValue.of(Fraction(a, 2), 3)],
        "signature": [("a", str(a))], "operation": "equilateral_area",
    }


def _k_rhombus_side(rng, level):
    h1, h2, side = _triple(rng, level)
    d1, d2 = 2 * h1, 2 * h2
    return {
        "question": (f"Romb ima dijagonale $d_1 = {d1}$ cm i $d_2 = {d2}$ cm. "
                     "Kolika je stranica romba?"),
        "answer": Fraction(side), "unit": "cm",
        "formula": "a^2 = \\left(\\frac{d_1}{2}\\right)^2 + \\left(\\frac{d_2}{2}\\right)^2",
        "substitution": f"a^2 = {h1}^2 + {h2}^2 = {h1 * h1 + h2 * h2}",
        "chain": f"a = \\sqrt{{{h1 * h1 + h2 * h2}}} = {side}",
        "distractors": [Fraction(h1 + h2), Fraction(side) + 1, Fraction(d1)],
        "signature": [("d1", str(d1)), ("d2", str(d2))],
        "operation": "rhombus_side",
    }


def _k_isosceles_trapezoid_height(rng, level):
    half, h, leg = _triple(rng, level)
    c = _int_dim(rng, level, (2, 8), (3, 12), (4, 16))
    a = c + 2 * half
    return {
        "question": (f"Jednakokraki trapez ima osnovice $a = {a}$ cm i "
                     f"$c = {c}$ cm i krak $b = {leg}$ cm. Kolika je visina "
                     "trapeza?"),
        "answer": Fraction(h), "unit": "cm",
        "formula": "h = \\sqrt{b^2 - \\left(\\frac{a-c}{2}\\right)^2}",
        "substitution": (f"h = \\sqrt{{{leg}^2 - {half}^2}} = "
                         f"\\sqrt{{{leg * leg - half * half}}}"),
        "chain": f"h = \\sqrt{{{leg * leg - half * half}}} = {h}",
        "distractors": [Fraction(leg - half), Fraction(h) + 1, Fraction(leg)],
        "signature": [("a", str(a)), ("c", str(c)), ("b", str(leg))],
        "operation": "isosceles_trapezoid_height",
    }


def _k_right_trapezoid_leg(rng, level):
    half, h, leg = _triple(rng, level)
    c = _int_dim(rng, level, (2, 8), (3, 12), (4, 16))
    a = c + half
    return {
        "question": (f"Pravougli trapez ima osnovice $a = {a}$ cm i $c = {c}$ "
                     f"cm i visinu $h = {h}$ cm. Koliki je kosi krak trapeza?"),
        "answer": Fraction(leg), "unit": "cm",
        "formula": "b^2 = h^2 + (a-c)^2",
        "substitution": f"b^2 = {h}^2 + {half}^2 = {h * h + half * half}",
        "chain": f"b = \\sqrt{{{h * h + half * half}}} = {leg}",
        "distractors": [Fraction(h + half), Fraction(leg) + 1, Fraction(h)],
        "signature": [("a", str(a)), ("c", str(c)), ("h", str(h))],
        "operation": "right_trapezoid_leg",
    }


def _k_chord_distance(rng, level):
    half, dist, r = _triple(rng, level)
    t = 2 * half
    return {
        "question": (f"Tetiva dužine $t = {t}$ cm pripada kružnici "
                     f"poluprečnika $r = {r}$ cm. Kolika je udaljenost tetive "
                     "od centra kružnice?"),
        "answer": Fraction(dist), "unit": "cm",
        "formula": "r^2 = x^2 + \\left(\\frac{t}{2}\\right)^2",
        "substitution": f"x^2 = {r}^2 - {half}^2 = {r * r - half * half}",
        "chain": f"x = \\sqrt{{{r * r - half * half}}} = {dist}",
        "distractors": [Fraction(r - half), Fraction(dist) + 1, Fraction(half)],
        "signature": [("t", str(t)), ("r", str(r))],
        "operation": "chord_distance",
    }


# ---------------------------------------------------------------------------
# TIJELA
# ---------------------------------------------------------------------------

def _k_cube_surface(rng, level):
    a = _int_dim(rng, level)
    return {
        "question": f"Ivica kocke je $a = {a}$ cm. Kolika je površina kocke?",
        "answer": Fraction(6 * a * a), "unit": "cm²",
        "formula": "P = 6a^2", "substitution": f"P = 6 \\cdot {a}^2",
        "chain": f"P = 6 \\cdot {a * a} = {6 * a * a}",
        "distractors": [Fraction(a * a * a), Fraction(4 * a * a), Fraction(12 * a)],
        "signature": [("a", str(a))], "operation": "cube_surface",
    }


def _k_cube_volume(rng, level):
    a = _int_dim(rng, level)
    return {
        "question": f"Ivica kocke je $a = {a}$ cm. Kolika je zapremina kocke?",
        "answer": Fraction(a ** 3), "unit": "cm³",
        "formula": "V = a^3", "substitution": f"V = {a}^3",
        "chain": f"V = {a} \\cdot {a} \\cdot {a} = {a ** 3}",
        "distractors": [Fraction(6 * a * a), Fraction(a * a), Fraction(3 * a)],
        "signature": [("a", str(a))], "operation": "cube_volume",
    }


def _k_cube_space_diagonal(rng, level):
    a = _int_dim(rng, level)
    diag = RadicalValue.of(a, 3)
    return {
        "question": f"Ivica kocke je $a = {a}$ cm. Kolika je prostorna dijagonala kocke?",
        "answer": diag, "unit": "cm",
        "formula": "D = a\\sqrt{3}", "substitution": f"D = {a}\\sqrt{{3}}",
        "chain": f"D = {diag.display()}",
        "distractors": [RadicalValue.of(a, 2), RadicalValue.of(3 * a),
                        RadicalValue.of(2 * a, 3)],
        "signature": [("a", str(a))], "operation": "cube_space_diagonal",
    }


def _k_cuboid_surface(rng, level):
    a, b = _rect_dims(rng, level)
    c = _int_dim(rng, level, (2, 6), (2, 9), (3, 12))
    p = 2 * (a * b + a * c + b * c)
    return {
        "question": (f"Kvadar ima ivice $a = {a}$ cm, $b = {b}$ cm i "
                     f"$c = {c}$ cm. Kolika je površina kvadra?"),
        "answer": Fraction(p), "unit": "cm²",
        "formula": "P = 2(ab + ac + bc)",
        "substitution": f"P = 2({a} \\cdot {b} + {a} \\cdot {c} + {b} \\cdot {c})",
        "chain": f"P = 2({a * b} + {a * c} + {b * c}) = {p}",
        "distractors": [Fraction(a * b * c), Fraction(p) // 2, Fraction(p) * 2],
        "signature": [("a", str(a)), ("b", str(b)), ("c", str(c))],
        "operation": "cuboid_surface",
    }


def _k_cuboid_volume(rng, level):
    a, b = _rect_dims(rng, level)
    c = _int_dim(rng, level, (2, 6), (2, 9), (3, 12))
    return {
        "question": (f"Kvadar ima ivice $a = {a}$ cm, $b = {b}$ cm i "
                     f"$c = {c}$ cm. Kolika je zapremina kvadra?"),
        "answer": Fraction(a * b * c), "unit": "cm³",
        "formula": "V = abc", "substitution": f"V = {a} \\cdot {b} \\cdot {c}",
        "chain": f"V = {a} \\cdot {b} \\cdot {c} = {a * b * c}",
        "distractors": [Fraction(2 * (a * b + a * c + b * c)),
                        Fraction(a + b + c), Fraction(a * b * c) * 2],
        "signature": [("a", str(a)), ("b", str(b)), ("c", str(c))],
        "operation": "cuboid_volume",
    }


def _k_prism4_lateral(rng, level):
    a = _int_dim(rng, level, (2, 8), (3, 12), (4, 15))
    h = _int_dim(rng, level, (3, 9), (4, 14), (5, 20))
    m = 4 * a * h
    return {
        "question": (f"Pravilna četverostrana prizma ima ivicu baze $a = {a}$ "
                     f"cm i visinu $H = {h}$ cm. Kolika je površina omotača?"),
        "answer": Fraction(m), "unit": "cm²",
        "formula": "M = O_B \\cdot H = 4a \\cdot H",
        "substitution": f"M = 4 \\cdot {a} \\cdot {h}",
        "chain": f"M = {4 * a} \\cdot {h} = {m}",
        "distractors": [Fraction(a * a * h), Fraction(2 * a * h), Fraction(m) * 2],
        "signature": [("a", str(a)), ("H", str(h))],
        "operation": "prism4_lateral",
    }


def _k_prism4_surface(rng, level):
    a = _int_dim(rng, level, (2, 8), (3, 12), (4, 15))
    h = _int_dim(rng, level, (3, 9), (4, 14), (5, 20))
    b = a * a
    m = 4 * a * h
    p = 2 * b + m
    return {
        "question": (f"Pravilna četverostrana prizma ima ivicu baze $a = {a}$ "
                     f"cm i visinu $H = {h}$ cm. Kolika je ukupna površina "
                     "prizme?"),
        "answer": Fraction(p), "unit": "cm²",
        "formula": "P = 2B + M = 2a^2 + 4aH",
        "substitution": f"P = 2 \\cdot {a}^2 + 4 \\cdot {a} \\cdot {h}",
        "chain": f"P = {2 * b} + {m} = {p}",
        "distractors": [Fraction(b + m), Fraction(a * a * h), Fraction(m)],
        "signature": [("a", str(a)), ("H", str(h))],
        "operation": "prism4_surface",
    }


def _k_prism4_volume(rng, level):
    a = _int_dim(rng, level, (2, 8), (3, 12), (4, 15))
    h = _int_dim(rng, level, (3, 9), (4, 14), (5, 20))
    return {
        "question": (f"Pravilna četverostrana prizma ima ivicu baze $a = {a}$ "
                     f"cm i visinu $H = {h}$ cm. Kolika je zapremina prizme?"),
        "answer": Fraction(a * a * h), "unit": "cm³",
        "formula": "V = B \\cdot H = a^2 \\cdot H",
        "substitution": f"V = {a}^2 \\cdot {h}",
        "chain": f"V = {a * a} \\cdot {h} = {a * a * h}",
        "distractors": [Fraction(4 * a * h), Fraction(a * h), Fraction(a * a * h) * 2],
        "signature": [("a", str(a)), ("H", str(h))],
        "operation": "prism4_volume",
    }


def _k_prism4_space_diagonal(rng, level):
    a, h, d = _triple(rng, level)
    return {
        "question": (f"Pravilna četverostrana prizma ima dijagonalu baze "
                     f"$d = {a}$ cm i visinu $H = {h}$ cm. Kolika je "
                     "prostorna dijagonala prizme?"),
        "answer": Fraction(d), "unit": "cm",
        "formula": "D = \\sqrt{d^2 + H^2}",
        "substitution": f"D = \\sqrt{{{a}^2 + {h}^2}} = \\sqrt{{{a * a + h * h}}}",
        "chain": f"D = \\sqrt{{{a * a + h * h}}} = {d}",
        "distractors": [Fraction(a + h), Fraction(d) + 1, Fraction(d) - 1],
        "signature": [("d", str(a)), ("H", str(h))],
        "operation": "prism4_space_diagonal",
    }


def _k_prism3_volume(rng, level):
    a = 2 * _int_dim(rng, level, (1, 4), (2, 6), (2, 8))
    h = _int_dim(rng, level, (3, 9), (4, 12), (5, 18))
    volume = RadicalValue.of(Fraction(a * a * h, 4), 3)
    return {
        "question": (f"Pravilna trostrana prizma ima ivicu baze $a = {a}$ cm i "
                     f"visinu $H = {h}$ cm. Kolika je zapremina prizme? "
                     "(Preko korijena.)"),
        "answer": volume, "unit": "cm³",
        "formula": "V = B \\cdot H = \\frac{a^2\\sqrt{3}}{4} \\cdot H",
        "substitution": f"V = \\frac{{{a}^2\\sqrt{{3}}}}{{4}} \\cdot {h}",
        "chain": f"V = \\frac{{{a * a}\\sqrt{{3}}}}{{4}} \\cdot {h} = {volume.display()}",
        "distractors": [RadicalValue.of(Fraction(a * a * h, 2), 3),
                        RadicalValue.of(a * a * h, 3),
                        RadicalValue.of(Fraction(a * h, 4), 3)],
        "signature": [("a", str(a)), ("H", str(h))],
        "operation": "prism3_volume",
    }


def _k_prism3_lateral(rng, level):
    a = _int_dim(rng, level, (2, 8), (3, 12), (4, 15))
    h = _int_dim(rng, level, (3, 9), (4, 14), (5, 20))
    m = 3 * a * h
    return {
        "question": (f"Pravilna trostrana prizma ima ivicu baze $a = {a}$ cm i "
                     f"visinu $H = {h}$ cm. Kolika je površina omotača?"),
        "answer": Fraction(m), "unit": "cm²",
        "formula": "M = O_B \\cdot H = 3a \\cdot H",
        "substitution": f"M = 3 \\cdot {a} \\cdot {h}",
        "chain": f"M = {3 * a} \\cdot {h} = {m}",
        "distractors": [Fraction(4 * a * h), Fraction(a * h), Fraction(m) * 2],
        "signature": [("a", str(a)), ("H", str(h))],
        "operation": "prism3_lateral",
    }


def _k_pyramid4_apothem(rng, level):
    half, h, apothem = _triple(rng, level)
    a = 2 * half
    return {
        "question": (f"Pravilna četverostrana piramida ima ivicu baze "
                     f"$a = {a}$ cm i visinu $H = {h}$ cm. Kolika je apotema "
                     "$h_a$ piramide?"),
        "answer": Fraction(apothem), "unit": "cm",
        "formula": "h_a = \\sqrt{H^2 + \\left(\\frac{a}{2}\\right)^2}",
        "substitution": (f"h_a = \\sqrt{{{h}^2 + {half}^2}} = "
                         f"\\sqrt{{{h * h + half * half}}}"),
        "chain": f"h_a = \\sqrt{{{h * h + half * half}}} = {apothem}",
        "distractors": [Fraction(h + half), Fraction(apothem) + 1, Fraction(h)],
        "signature": [("a", str(a)), ("H", str(h))],
        "operation": "pyramid4_apothem",
    }


def _k_pyramid4_lateral(rng, level):
    a = 2 * _int_dim(rng, level, (1, 5), (2, 7), (2, 9))
    apothem = _int_dim(rng, level, (2, 9), (3, 12), (4, 16))
    m = 4 * Fraction(a * apothem, 2)
    return {
        "question": (f"Pravilna četverostrana piramida ima ivicu baze "
                     f"$a = {a}$ cm i apotemu $h_a = {apothem}$ cm. Kolika je "
                     "površina omotača?"),
        "answer": m, "unit": "cm²",
        "formula": "M = 4 \\cdot \\frac{a \\cdot h_a}{2}",
        "substitution": f"M = 4 \\cdot \\frac{{{a} \\cdot {apothem}}}{{2}}",
        "chain": f"M = 2 \\cdot {a} \\cdot {apothem} = {core.fraction_display(m)}",
        "distractors": [Fraction(a * apothem), Fraction(4 * a * apothem),
                        Fraction(a * a)],
        "signature": [("a", str(a)), ("h_a", str(apothem))],
        "operation": "pyramid4_lateral",
    }


def _k_pyramid4_surface(rng, level):
    a = 2 * _int_dim(rng, level, (1, 5), (2, 7), (2, 9))
    apothem = _int_dim(rng, level, (2, 9), (3, 12), (4, 16))
    b = a * a
    m = 2 * a * apothem
    return {
        "question": (f"Pravilna četverostrana piramida ima ivicu baze "
                     f"$a = {a}$ cm i apotemu $h_a = {apothem}$ cm. Kolika je "
                     "ukupna površina piramide?"),
        "answer": Fraction(b + m), "unit": "cm²",
        "formula": "P = B + M = a^2 + 4 \\cdot \\frac{a \\cdot h_a}{2}",
        "substitution": f"P = {a}^2 + 2 \\cdot {a} \\cdot {apothem}",
        "chain": f"P = {b} + {m} = {b + m}",
        "distractors": [Fraction(m), Fraction(b + m) * 2, Fraction(b)],
        "signature": [("a", str(a)), ("h_a", str(apothem))],
        "operation": "pyramid4_surface",
    }


def _k_pyramid4_volume(rng, level):
    a = _int_dim(rng, level, (2, 6), (3, 9), (3, 12))
    h = rng.choice((3, 6, 9, 12) if level < 3 else (6, 9, 12, 15))
    volume = Fraction(a * a * h, 3)
    return {
        "question": (f"Pravilna četverostrana piramida ima ivicu baze "
                     f"$a = {a}$ cm i visinu $H = {h}$ cm. Kolika je "
                     "zapremina piramide?"),
        "answer": volume, "unit": "cm³",
        "formula": "V = \\frac{B \\cdot H}{3} = \\frac{a^2 \\cdot H}{3}",
        "substitution": f"V = \\frac{{{a}^2 \\cdot {h}}}{{3}}",
        "chain": f"V = \\frac{{{a * a} \\cdot {h}}}{{3}} = {core.fraction_display(volume)}",
        "distractors": [Fraction(a * a * h), volume * 3, volume * 2],
        "signature": [("a", str(a)), ("H", str(h))],
        "operation": "pyramid4_volume",
    }


def _k_cylinder_surface(rng, level):
    r = _int_dim(rng, level, (1, 6), (2, 9), (3, 12))
    h = _int_dim(rng, level, (2, 9), (3, 12), (4, 16))
    p = PiValue(2 * r * (r + h))
    return {
        "question": (f"Valjak ima poluprečnik baze $r = {r}$ cm i visinu "
                     f"$H = {h}$ cm. Kolika je površina valjka? (Preko $\\pi$.)"),
        "answer": p, "unit": "cm²",
        "formula": "P = 2B + M = 2\\pi r^2 + 2\\pi r H = 2\\pi r(r + H)",
        "substitution": f"P = 2\\pi \\cdot {r}({r} + {h})",
        "chain": f"P = 2\\pi \\cdot {r} \\cdot {r + h} = {p.display()}",
        "distractors": [PiValue(2 * r * h), PiValue(r * r * h),
                        PiValue(r * (r + h))],
        "signature": [("r", str(r)), ("H", str(h))],
        "operation": "cylinder_surface",
    }


def _k_cylinder_volume(rng, level):
    r = _int_dim(rng, level, (1, 6), (2, 9), (3, 12))
    h = _int_dim(rng, level, (2, 9), (3, 12), (4, 16))
    return {
        "question": (f"Valjak ima poluprečnik baze $r = {r}$ cm i visinu "
                     f"$H = {h}$ cm. Kolika je zapremina valjka? (Preko $\\pi$.)"),
        "answer": PiValue(r * r * h), "unit": "cm³",
        "formula": "V = B \\cdot H = \\pi r^2 H",
        "substitution": f"V = \\pi \\cdot {r}^2 \\cdot {h}",
        "chain": f"V = {r * r * h}\\pi",
        "distractors": [PiValue(2 * r * h), PiValue(r * h), PiValue(r * r * h * 2)],
        "signature": [("r", str(r)), ("H", str(h))],
        "operation": "cylinder_volume",
    }


def _k_cylinder_axial_section(rng, level):
    r = _int_dim(rng, level, (1, 6), (2, 9), (3, 12))
    h = _int_dim(rng, level, (2, 9), (3, 12), (4, 16))
    return {
        "question": (f"Valjak ima poluprečnik baze $r = {r}$ cm i visinu "
                     f"$H = {h}$ cm. Kolika je površina osnog presjeka valjka?"),
        "answer": Fraction(2 * r * h), "unit": "cm²",
        "formula": "P_{presjeka} = 2r \\cdot H",
        "substitution": f"P = 2 \\cdot {r} \\cdot {h}",
        "chain": f"P = {2 * r} \\cdot {h} = {2 * r * h}",
        "distractors": [Fraction(r * h), Fraction(4 * r * h), Fraction(r * r * h)],
        "signature": [("r", str(r)), ("H", str(h))],
        "operation": "cylinder_axial_section",
    }


def _k_cone_slant(rng, level):
    r, h, s = _triple(rng, level)
    return {
        "question": (f"Kupa ima poluprečnik baze $r = {r}$ cm i visinu "
                     f"$H = {h}$ cm. Kolika je izvodnica $s$ kupe?"),
        "answer": Fraction(s), "unit": "cm",
        "formula": "s = \\sqrt{r^2 + H^2}",
        "substitution": f"s = \\sqrt{{{r}^2 + {h}^2}} = \\sqrt{{{r * r + h * h}}}",
        "chain": f"s = \\sqrt{{{r * r + h * h}}} = {s}",
        "distractors": [Fraction(r + h), Fraction(s) + 1, Fraction(s) - 1],
        "signature": [("r", str(r)), ("H", str(h))],
        "operation": "cone_slant",
    }


def _k_cone_surface(rng, level):
    r, h, s = _triple(rng, level)
    p = PiValue(r * (r + s))
    return {
        "question": (f"Kupa ima poluprečnik baze $r = {r}$ cm i izvodnicu "
                     f"$s = {s}$ cm. Kolika je površina kupe? (Preko $\\pi$.)"),
        "answer": p, "unit": "cm²",
        "formula": "P = B + M = \\pi r^2 + \\pi r s = \\pi r(r + s)",
        "substitution": f"P = \\pi \\cdot {r}({r} + {s})",
        "chain": f"P = \\pi \\cdot {r} \\cdot {r + s} = {p.display()}",
        "distractors": [PiValue(r * s), PiValue(r * r), PiValue(2 * r * (r + s))],
        "signature": [("r", str(r)), ("s", str(s))],
        "operation": "cone_surface",
    }


def _k_cone_volume(rng, level):
    r = _int_dim(rng, level, (1, 6), (2, 9), (2, 12))
    h = rng.choice((3, 6, 9, 12) if level < 3 else (6, 9, 12, 15))
    volume = PiValue(Fraction(r * r * h, 3))
    return {
        "question": (f"Kupa ima poluprečnik baze $r = {r}$ cm i visinu "
                     f"$H = {h}$ cm. Kolika je zapremina kupe? (Preko $\\pi$.)"),
        "answer": volume, "unit": "cm³",
        "formula": "V = \\frac{B \\cdot H}{3} = \\frac{\\pi r^2 H}{3}",
        "substitution": f"V = \\frac{{\\pi \\cdot {r}^2 \\cdot {h}}}{{3}}",
        "chain": f"V = {volume.display()}",
        "distractors": [PiValue(r * r * h), PiValue(Fraction(r * h, 3)),
                        PiValue(Fraction(2 * r * r * h, 3))],
        "signature": [("r", str(r)), ("H", str(h))],
        "operation": "cone_volume",
    }


def _k_cone_axial_section(rng, level):
    r = _int_dim(rng, level, (1, 6), (2, 9), (3, 12))
    h = _int_dim(rng, level, (2, 9), (3, 12), (4, 16))
    return {
        "question": (f"Kupa ima poluprečnik baze $r = {r}$ cm i visinu "
                     f"$H = {h}$ cm. Kolika je površina osnog presjeka kupe?"),
        "answer": Fraction(r * h), "unit": "cm²",
        "formula": "P_{presjeka} = \\frac{2r \\cdot H}{2} = r \\cdot H",
        "substitution": f"P = \\frac{{2 \\cdot {r} \\cdot {h}}}{{2}}",
        "chain": f"P = {r} \\cdot {h} = {r * h}",
        "distractors": [Fraction(2 * r * h), Fraction(r * r * h), Fraction(r + h)],
        "signature": [("r", str(r)), ("H", str(h))],
        "operation": "cone_axial_section",
    }


def _k_sphere_surface(rng, level):
    r = _int_dim(rng, level, (1, 6), (2, 9), (3, 12))
    return {
        "question": (f"Poluprečnik sfere je $r = {r}$ cm. Kolika je površina "
                     "sfere? (Preko $\\pi$.)"),
        "answer": PiValue(4 * r * r), "unit": "cm²",
        "formula": "P = 4\\pi r^2", "substitution": f"P = 4\\pi \\cdot {r}^2",
        "chain": f"P = {4 * r * r}\\pi",
        "distractors": [PiValue(r * r), PiValue(2 * r * r),
                        PiValue(Fraction(4 * r ** 3, 3))],
        "signature": [("r", str(r))], "operation": "sphere_surface",
    }


def _k_ball_volume(rng, level):
    r = rng.choice((3, 6, 9) if level < 3 else (3, 6, 9, 12))
    volume = PiValue(Fraction(4 * r ** 3, 3))
    return {
        "question": (f"Poluprečnik lopte je $r = {r}$ cm. Kolika je zapremina "
                     "lopte? (Preko $\\pi$.)"),
        "answer": volume, "unit": "cm³",
        "formula": "V = \\frac{4\\pi r^3}{3}",
        "substitution": f"V = \\frac{{4\\pi \\cdot {r}^3}}{{3}}",
        "chain": f"V = \\frac{{4 \\cdot {r ** 3}}}{{3}}\\pi = {volume.display()}",
        "distractors": [PiValue(4 * r * r), PiValue(r ** 3),
                        PiValue(Fraction(2 * r ** 3, 3))],
        "signature": [("r", str(r))], "operation": "ball_volume",
    }


def _k_prism_pyramid_ratio(rng, level):
    a = _int_dim(rng, level, (2, 6), (3, 9), (3, 12))
    h = rng.choice((3, 6, 9, 12))
    v_prism = a * a * h
    v_pyramid = Fraction(v_prism, 3)
    return {
        "question": (f"Prizma i piramida imaju JEDNAKE baze (kvadrat stranice "
                     f"$a = {a}$ cm) i jednake visine $H = {h}$ cm. Zapremina "
                     f"prizme je $V = {v_prism}$ cm³. Kolika je zapremina "
                     "piramide?"),
        "answer": v_pyramid, "unit": "cm³",
        "formula": "V_{piramide} = \\frac{V_{prizme}}{3}",
        "substitution": f"V = \\frac{{{v_prism}}}{{3}}",
        "chain": f"V = {v_prism} : 3 = {core.fraction_display(v_pyramid)}",
        "distractors": [Fraction(v_prism), Fraction(v_prism) * 3,
                        Fraction(v_prism, 2)],
        "signature": [("a", str(a)), ("H", str(h))],
        "operation": "prism_pyramid_ratio",
    }


def _k_cylinder_cone_ratio(rng, level):
    r = _int_dim(rng, level, (2, 6), (2, 9), (3, 12))
    h = rng.choice((3, 6, 9, 12))
    v_cyl = r * r * h
    return {
        "question": (f"Valjak i kupa imaju jednake baze (poluprečnik $r = {r}$ "
                     f"cm) i jednake visine $H = {h}$ cm. Zapremina valjka je "
                     f"$V = {v_cyl}\\pi$ cm³. Kolika je zapremina kupe? "
                     "(Preko $\\pi$.)"),
        "answer": PiValue(Fraction(v_cyl, 3)), "unit": "cm³",
        "formula": "V_{kupe} = \\frac{V_{valjka}}{3}",
        "substitution": f"V = \\frac{{{v_cyl}\\pi}}{{3}}",
        "chain": f"V = {PiValue(Fraction(v_cyl, 3)).display()}",
        "distractors": [PiValue(v_cyl), PiValue(v_cyl * 3), PiValue(Fraction(v_cyl, 2))],
        "signature": [("r", str(r)), ("H", str(h))],
        "operation": "cylinder_cone_ratio",
    }


def _k_density_mass(rng, level):
    volume = rng.choice((10, 20, 50, 100, 200) if level < 3
                        else (40, 250, 400, 500))
    density = rng.choice((Fraction(2), Fraction(5), Fraction(8),
                          Fraction(27, 10), Fraction(79, 10)))
    mass = density * volume
    density_display = (str(density.numerator) if density.denominator == 1
                       else core.decimal_display(density))
    mass_display = (str(mass.numerator) if mass.denominator == 1
                    else core.decimal_display(mass))
    return {
        "question": (f"Tijelo ima zapreminu $V = {volume}$ cm³, a gustina "
                     f"materijala je $\\rho = {density_display}$ g/cm³. "
                     "Kolika je masa tijela?"),
        "answer": mass, "unit": "g",
        "formula": "m = \\rho \\cdot V",
        "substitution": f"m = {density_display} \\cdot {volume}",
        "chain": f"m = {density_display} \\cdot {volume} = {mass_display}",
        "distractors": [Fraction(volume) / density if density != 0 else mass * 2,
                        mass * 10, mass / 10, Fraction(volume)],
        "signature": [("V", str(volume)), ("rho", str(density))],
        "operation": "density_mass",
    }


def _k_polyhedron_elements(rng, level):
    n = rng.choice((3, 4) if level == 1 else (3, 4, 5, 6, 8))
    prism = rng.random() < 0.5
    which = rng.choice(("tjemena", "ivica", "strana"))
    if prism:
        counts = {"tjemena": 2 * n, "ivica": 3 * n, "strana": n + 2}
        body = f"{n}-tostrana prizma"
        formula = "prizma: 2n tjemena, 3n ivica, n+2 strane"
    else:
        counts = {"tjemena": n + 1, "ivica": 2 * n, "strana": n + 1}
        body = f"{n}-tostrana piramida"
        formula = "piramida: n+1 tjemena, 2n ivica, n+1 strana"
    answer = counts[which]
    others = sorted(set(counts.values()) - {answer})
    distractors = [Fraction(v) for v in others]
    return {
        "question": f"Koliko {which} ima {body}?",
        "answer": Fraction(answer), "unit": "",
        "formula": formula,
        "substitution": f"n = {n}",
        "chain": f"{answer}",
        "distractors": distractors + [Fraction(answer + 1), Fraction(answer - 1),
                                      Fraction(answer + 2)],
        "signature": [("n", str(n)), ("body", "prizma" if prism else "piramida"),
                      ("which", which)],
        "operation": "polyhedron_elements",
        "rule": "Za pravilan poliedar broj elemenata zavisi samo od broja stranica baze",
    }


# ---------------------------------------------------------------------------
# REGISTAR VRSTA I ULAZNA TAČKA
# ---------------------------------------------------------------------------

_KINDS = {
    # 2D
    "square_perimeter": _k_square_perimeter,
    "square_area": _k_square_area,
    "rectangle_perimeter": _k_rectangle_perimeter,
    "rectangle_area": _k_rectangle_area,
    "triangle_perimeter": _k_triangle_perimeter,
    "triangle_area": _k_triangle_area,
    "parallelogram_area": _k_parallelogram_area,
    "parallelogram_perimeter": _k_parallelogram_perimeter,
    "trapezoid_area": _k_trapezoid_area,
    "trapezoid_midline": _k_trapezoid_midline,
    "triangle_midline": _k_triangle_midline,
    "rhombus_area_diagonals": _k_rhombus_area,
    "rhombus_perimeter": _k_rhombus_perimeter,
    "deltoid_area": _k_deltoid_area,
    "orthodiagonal_area": _build_orthodiagonal,
    "quad_perimeter": _k_quad_perimeter,
    "circle_circumference": _k_circle_circumference,
    "circle_area": _k_circle_area,
    "arc_length": _k_arc_length,
    "sector_area": _k_sector_area,
    "annulus_area": _k_annulus_area,
    "polygon_interior_sum": _k_polygon_interior_sum,
    "polygon_diagonals": _k_polygon_diagonals,
    "regular_polygon_angle": _k_regular_polygon_angle,
    "regular_polygon_perimeter": _k_regular_polygon_perimeter,
    # Pitagora
    "hypotenuse": _k_hypotenuse,
    "leg": _k_leg,
    "verify_triple": _k_verify_triple,
    "square_diagonal": _k_square_diagonal,
    "rectangle_diagonal": _k_rectangle_diagonal,
    "isosceles_height": _k_isosceles_height,
    "equilateral_height": _k_equilateral_height,
    "equilateral_area": _k_equilateral_area,
    "rhombus_side": _k_rhombus_side,
    "isosceles_trapezoid_height": _k_isosceles_trapezoid_height,
    "right_trapezoid_leg": _k_right_trapezoid_leg,
    "chord_distance": _k_chord_distance,
    # tijela
    "cube_surface": _k_cube_surface,
    "cube_volume": _k_cube_volume,
    "cube_space_diagonal": _k_cube_space_diagonal,
    "cuboid_surface": _k_cuboid_surface,
    "cuboid_volume": _k_cuboid_volume,
    "prism4_lateral": _k_prism4_lateral,
    "prism4_surface": _k_prism4_surface,
    "prism4_volume": _k_prism4_volume,
    "prism4_space_diagonal": _k_prism4_space_diagonal,
    "prism3_volume": _k_prism3_volume,
    "prism3_lateral": _k_prism3_lateral,
    "pyramid4_apothem": _k_pyramid4_apothem,
    "pyramid4_lateral": _k_pyramid4_lateral,
    "pyramid4_surface": _k_pyramid4_surface,
    "pyramid4_volume": _k_pyramid4_volume,
    "cylinder_surface": _k_cylinder_surface,
    "cylinder_volume": _k_cylinder_volume,
    "cylinder_axial_section": _k_cylinder_axial_section,
    "cone_slant": _k_cone_slant,
    "cone_surface": _k_cone_surface,
    "cone_volume": _k_cone_volume,
    "cone_axial_section": _k_cone_axial_section,
    "sphere_surface": _k_sphere_surface,
    "ball_volume": _k_ball_volume,
    "prism_pyramid_ratio": _k_prism_pyramid_ratio,
    "cylinder_cone_ratio": _k_cylinder_cone_ratio,
    "density_mass": _k_density_mass,
    "polyhedron_elements": _k_polyhedron_elements,
}

# Porodica se izvodi iz VRSTE zadatka — lekcija nosi samo listu vrsta.
_PYTHAGORAS_KINDS = frozenset({
    "hypotenuse", "leg", "verify_triple", "square_diagonal",
    "rectangle_diagonal", "isosceles_height", "equilateral_height",
    "equilateral_area", "rhombus_side", "isosceles_trapezoid_height",
    "right_trapezoid_leg", "chord_distance"})
_SOLID_KINDS = frozenset({
    "cube_surface", "cube_volume", "cube_space_diagonal", "cuboid_surface",
    "cuboid_volume", "prism4_lateral", "prism4_surface", "prism4_volume",
    "prism4_space_diagonal", "prism3_volume", "prism3_lateral",
    "pyramid4_apothem", "pyramid4_lateral", "pyramid4_surface",
    "pyramid4_volume", "cylinder_surface", "cylinder_volume",
    "cylinder_axial_section", "cone_slant", "cone_surface", "cone_volume",
    "cone_axial_section", "sphere_surface", "ball_volume",
    "prism_pyramid_ratio", "cylinder_cone_ratio", "density_mass",
    "polyhedron_elements"})


def _family_for_kind(kind):
    if kind in _PYTHAGORAS_KINDS:
        return "pythagoras_direct"
    if kind in _SOLID_KINDS:
        return "solid_geometry_direct"
    return "geometry_formula_2d"


def supports(parameters) -> bool:
    parameters = parameters or {}
    kinds = parameters.get("kinds") or ()
    return bool(kinds) and all(kind in _KINDS for kind in kinds)


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    kinds = tuple(parameters["kinds"])
    for _ in range(60):
        try:
            kind = rng.choice(kinds)
            family_id = _family_for_kind(kind)
            spec = _KINDS[kind](rng, level)
            if "option_texts" in spec:
                # Vrsta s tekstualnim opcijama (npr. provjera trojke).
                answer = spec["answer"]
                if spec["option_texts"][0] in f"Formula: ${spec['formula']}$.":
                    continue
                return core.build_package(
                    lesson_id=lesson_id, lesson_title=lesson_title,
                    family_id=family_id, operation=spec["operation"],
                    level=level, question=spec["question"],
                    answer_value=answer,
                    answer_display=spec["option_texts"][0],
                    distractor_values=(),
                    hints=(f"Formula: ${spec['formula']}$.",
                           f"Provjeri: ${spec['substitution']}$.",
                           f"Dakle: ${spec['chain']}$ — tačno jedna trojka "
                           "zadovoljava jednakost."),
                    solution=(f"Provjerimo Pitagorinu teoremu: "
                              f"${spec['chain']}$ — jednakost važi samo za "
                              "označene dužine."),
                    signature_parameters=list(spec["signature"])
                    + [("kind", spec["operation"])],
                    required_conditions=[spec["operation"]],
                    relevant_objects=["geometry"],
                    generator_version=GENERATOR_VERSION,
                    option_texts=spec["option_texts"], wrap="",
                    evidence=spec.get("evidence") or _evidence_formula(level))
            return _package_from_spec(spec, family_id, lesson_id,
                                      lesson_title, level)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")
