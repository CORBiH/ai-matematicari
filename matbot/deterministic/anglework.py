"""Deterministički generator porodice brojevnih odnosa među uglovima.

Jedna semantička porodica (`angle_relationships_direct`), vrste zadataka kao
podaci ugovora — sve su ČISTO BROJEVNI odnosi (nikakav crtež nije potreban):
klasifikacija ugla po mjeri, centralni–periferijski, uporedni/unakrsni,
komplementni/suplementni, uglovi uz transverzalu (imenovanim odnosom),
paralelni/normalni kraci (s izričitim oštar/tup), računanje sa stepenima,
minutama i sekundama, treći ugao trougla, vanjski ugao, jednakokraki i
pravougli trougao, četvrti ugao četverougla, nejednakost trougla i odnos
stranica i uglova.

AUTORITET: cjelobrojna/racionalna aritmetika stepeni (i minuta/sekundi u
bazi 60). Zapis: `$47^\\circ$`, `$35^\\circ 20'$` — mathcheck ovakve segmente
preskače (nisu čista aritmetika), pa tačnost dokazuju testovi svojstava.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.tutor.schema import DifficultyEvidence

FAMILY_IDS = ("angle_relationships_direct",)
GENERATOR_VERSION = "detang-1"


def _deg(value):
    return f"{value}^\\circ"


def _dms(minutes_total):
    degrees, minutes = divmod(minutes_total, 60)
    if minutes:
        return f"{degrees}^\\circ {minutes}'"
    return f"{degrees}^\\circ"


def _evidence_direct(level):
    if level == 1:
        return DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=2,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    return core.evidence_for_level(level)


# ---------------------------------------------------------------------------
# VRSTE
# ---------------------------------------------------------------------------

def _k_classify_by_measure(rng, level):
    kinds = [("oštar ugao", lambda: rng.randint(5, 85)),
             ("tup ugao", lambda: rng.randint(95, 175)),
             ("prav ugao", lambda: 90),
             ("opružen ugao", lambda: 180)]
    if level >= 2:
        kinds.append(("pun ugao", lambda: 360))
    name, make = rng.choice(kinds)
    alpha = make()
    labels = ["oštar ugao", "prav ugao", "tup ugao", "opružen ugao", "pun ugao"]
    others = [label for label in labels if label != name]
    rng.shuffle(others)
    return {
        "question": f"Kako se zove ugao od $\\alpha = {_deg(alpha)}$?",
        "option_texts": (name, *others[:3]),
        "rule": ("Uglovi po mjeri: oštar je manji od $90^\\circ$, prav je "
                 "tačno $90^\\circ$, tup je između $90^\\circ$ i $180^\\circ$, "
                 "opružen je $180^\\circ$, a pun $360^\\circ$"),
        "work": f"{_deg(alpha)}",
        "signature": [("alpha", str(alpha)), ("name", name)],
        "operation": "classify_angle",
    }


def _k_central_peripheral(rng, level):
    psi = rng.randint(10, 80) if level == 1 else rng.randint(12, 88)
    phi = 2 * psi
    if rng.random() < 0.5:
        return {
            "question": (f"Periferijski ugao nad tetivom je $\\beta = {_deg(psi)}$. "
                         "Koliki je centralni ugao nad ISTOM tetivom?"),
            "answer": Fraction(phi), "unit": "°",
            "rule": "Centralni ugao je DVOSTRUKO veći od periferijskog nad istim lukom",
            "work": f"\\alpha = 2 \\cdot {psi} = {phi}",
            "distractors": [Fraction(psi), Fraction(psi // 2) if psi % 2 == 0
                            else Fraction(psi + 10), Fraction(180 - psi)],
            "signature": [("psi", str(psi))], "operation": "central_from_peripheral",
        }
    return {
        "question": (f"Centralni ugao nad lukom je $\\alpha = {_deg(phi)}$. "
                     "Koliki je periferijski ugao nad ISTIM lukom?"),
        "answer": Fraction(psi), "unit": "°",
        "rule": "Periferijski ugao je POLOVINA centralnog ugla nad istim lukom",
        "work": f"\\beta = {phi} : 2 = {psi}",
        "distractors": [Fraction(phi), Fraction(2 * phi), Fraction(180 - phi)
                        if phi < 180 else Fraction(phi - 90)],
        "signature": [("phi", str(phi))], "operation": "peripheral_from_central",
    }


def _k_central_fraction(rng, level):
    part = rng.choice(((2, "polovinu"), (4, "četvrtinu"), (3, "trećinu"),
                       (6, "šestinu"))[:2 if level == 1 else 4])
    n, word = part
    alpha = Fraction(360, n)
    return {
        "question": (f"Centralni ugao odsijeca {word} kružnice. Koliki je taj "
                     "centralni ugao?"),
        "answer": alpha, "unit": "°",
        "rule": "Puni ugao oko centra kružnice ima $360^\\circ$",
        "rule_neutral": ("Centralni ugao je odgovarajući dio punog ugla oko "
                         "centra kružnice"),
        "work": f"\\alpha = 360 : {n} = {core.fraction_display(alpha)}",
        "distractors": [Fraction(180, n), Fraction(360), Fraction(90)],
        "signature": [("n", str(n))], "operation": "central_fraction",
    }


def _k_compare_dms(rng, level):
    values = set()
    while len(values) < 4:
        values.add(rng.randint(20 * 60, 160 * 60)
                   if level > 1 else 60 * rng.randint(20, 160))
    ordered = sorted(values)
    wants_max = rng.random() < 0.5
    correct = ordered[-1] if wants_max else ordered[0]
    displays = {value: f"${_dms(value)}$" for value in ordered}
    option_values = [correct] + [v for v in ordered if v != correct]
    return {
        "question": ("Koji je od ponuđenih uglova najveći?" if wants_max
                     else "Koji je od ponuđenih uglova najmanji?"),
        "option_texts": tuple(displays[value] for value in option_values),
        "rule": ("Uglovi se porede prvo po stepenima, a pri jednakim "
                 "stepenima po minutama"),
        "work": " < ".join(_dms(value) for value in ordered),
        "signature": [("values", "+".join(map(str, ordered)))],
        "operation": "compare_angles", "comparison": True,
    }


def _k_dms_add_sub(rng, level):
    a = rng.randint(15, 90) * 60 + (rng.randint(1, 59) if level > 1 else
                                    rng.choice((0, 10, 20, 30, 40, 50)))
    b = rng.randint(5, 60) * 60 + (rng.randint(1, 59) if level > 1 else
                                   rng.choice((0, 10, 20, 30, 40, 50)))
    add = rng.random() < 0.5
    if not add and b >= a:
        a, b = b, a
    total = a + b if add else a - b
    if total <= 0:
        raise DeterministicGenerationError("negativan ugao")
    symbol = "+" if add else "-"
    return {
        "question": (f"Izračunaj: ${_dms(a)} {symbol} {_dms(b)}$"),
        "answer": Fraction(total), "unit": "",
        "display_override": _dms(total),
        "rule": ("Stepeni se računaju sa stepenima, minute s minutama; "
                 "$60'$ čini $1^\\circ$"),
        "work": f"{_dms(a)} {symbol} {_dms(b)} = {_dms(total)}",
        "distractors": [Fraction(total + 60), Fraction(total - 60)
                        if total > 60 else Fraction(total + 120),
                        Fraction(total + 10), Fraction(total - 10)],
        "distractor_display": _dms,
        "signature": [("a", str(a)), ("b", str(b)), ("op", symbol)],
        "operation": "dms_add_sub",
    }


def _k_angle_times_n(rng, level):
    n = rng.randint(2, 4) if level == 1 else rng.randint(2, 6)
    base = rng.randint(8, 40) * 60 + (0 if level == 1 else
                                      rng.choice((0, 15, 20, 30, 45)))
    multiply = rng.random() < 0.5
    if multiply:
        total = base * n
        if total >= 360 * 60:
            raise DeterministicGenerationError("prevelik ugao")
        question = f"Izračunaj: ${n} \\cdot ({_dms(base)})$"
        work = f"{n} \\cdot {_dms(base)} = {_dms(total)}"
    else:
        total = base
        base = base * n
        question = f"Izračunaj: $({_dms(base)}) : {n}$"
        work = f"{_dms(base)} : {n} = {_dms(total)}"
    return {
        "question": question,
        "answer": Fraction(total), "unit": "",
        "display_override": _dms(total),
        "rule": ("Ugao se množi ili dijeli prirodnim brojem tako da se "
                 "posebno računaju stepeni i minute (uz prenos od $60'$)"),
        "work": work,
        "distractors": [Fraction(total + 60), Fraction(total - 60)
                        if total > 60 else Fraction(total + 120),
                        Fraction(total + 30)],
        "distractor_display": _dms,
        "signature": [("base", str(base)), ("n", str(n)),
                      ("op", "mul" if multiply else "div")],
        "operation": "angle_times_n",
    }


def _k_adjacent_vertical(rng, level):
    alpha = rng.randint(20, 160)
    vertical = rng.random() < 0.5
    if vertical:
        return {
            "question": (f"Jedan od unakrsnih uglova je $\\alpha = {_deg(alpha)}$. "
                         "Koliki je njemu unakrsni ugao?"),
            "answer": Fraction(alpha), "unit": "°",
            "rule": "Unakrsni uglovi su JEDNAKI",
            "work": f"\\beta = \\alpha = {alpha}",
            "distractors": [Fraction(180 - alpha), Fraction(90 - alpha)
                            if alpha < 90 else Fraction(alpha - 90),
                            Fraction(360 - alpha)],
            "signature": [("alpha", str(alpha)), ("kind", "vertical")],
            "operation": "vertical_angle",
        }
    return {
        "question": (f"Jedan od uporednih uglova je $\\alpha = {_deg(alpha)}$. "
                     "Koliki je njemu uporedni ugao?"),
        "answer": Fraction(180 - alpha), "unit": "°",
        "rule": "Uporedni uglovi zajedno čine opružen ugao od $180^\\circ$",
        "work": f"\\beta = 180 - {alpha} = {180 - alpha}",
        "distractors": [Fraction(alpha), Fraction(90 - alpha)
                        if alpha < 90 else Fraction(alpha - 90),
                        Fraction(360 - alpha)],
        "signature": [("alpha", str(alpha)), ("kind", "adjacent")],
        "operation": "supplementary_adjacent",
    }


def _k_comp_supp(rng, level):
    comp = rng.random() < 0.5
    if comp:
        alpha = rng.randint(5, 85)
        return {
            "question": (f"Ugao iznosi $\\alpha = {_deg(alpha)}$. Koliki je "
                         "njemu KOMPLEMENTAN ugao?"),
            "answer": Fraction(90 - alpha), "unit": "°",
            "rule": "Komplementni uglovi zajedno čine $90^\\circ$",
            "work": f"\\beta = 90 - {alpha} = {90 - alpha}",
            "distractors": [Fraction(180 - alpha), Fraction(alpha),
                            Fraction(90 + alpha)],
            "signature": [("alpha", str(alpha)), ("kind", "comp")],
            "operation": "complementary",
        }
    alpha = rng.randint(5, 175)
    return {
        "question": (f"Ugao iznosi $\\alpha = {_deg(alpha)}$. Koliki je njemu "
                     "SUPLEMENTAN ugao?"),
        "answer": Fraction(180 - alpha), "unit": "°",
        "rule": "Suplementni uglovi zajedno čine $180^\\circ$",
        "work": f"\\beta = 180 - {alpha} = {180 - alpha}",
        "distractors": [Fraction(90 - alpha) if alpha < 90
                        else Fraction(alpha - 90), Fraction(alpha),
                        Fraction(360 - alpha)],
        "signature": [("alpha", str(alpha)), ("kind", "supp")],
        "operation": "supplementary",
    }


def _k_transversal(rng, level):
    alpha = rng.randint(30, 150)
    relation = rng.choice((("saglasni (odgovarajući)", alpha),
                           ("naizmjenični", alpha),
                           ("suprotni (sa iste strane transverzale)",
                            180 - alpha)))
    name, value = relation
    return {
        "question": (f"Transverzala siječe dvije PARALELNE prave i s jednom "
                     f"od njih zaklapa ugao $\\alpha = {_deg(alpha)}$. Koliki "
                     f"je njemu {name} ugao?"),
        "answer": Fraction(value), "unit": "°",
        "rule": ("Uz transverzalu paralelnih pravih: saglasni i naizmjenični "
                 "uglovi su jednaki, a suprotni su suplementni"),
        "work": (f"\\beta = {alpha}" if value == alpha
                 else f"\\beta = 180 - {alpha} = {value}"),
        "distractors": [Fraction(180 - value), Fraction(90 - alpha)
                        if alpha < 90 else Fraction(alpha - 20),
                        Fraction(360 - value)],
        "signature": [("alpha", str(alpha)), ("rel", name[:9])],
        "operation": "transversal_angle",
    }


def _k_parallel_normal_arms(rng, level):
    alpha = rng.randint(25, 85)
    same = rng.random() < 0.5
    arms = rng.choice(("paralelne", "normalne"))
    if same:
        return {
            "question": (f"Dva ugla imaju {arms} krake i OBA su oštra. Prvi "
                         f"ugao je $\\alpha = {_deg(alpha)}$. Koliki je drugi?"),
            "answer": Fraction(alpha), "unit": "°",
            "rule": (f"Uglovi s {arms[:-1]}im kracima su jednaki kad su oba "
                     "oštra (ili oba tupa)"),
            "work": f"\\beta = \\alpha = {alpha}",
            "distractors": [Fraction(180 - alpha), Fraction(90 - alpha),
                            Fraction(90 + alpha)],
            "signature": [("alpha", str(alpha)), ("arms", arms), ("same", "1")],
            "operation": "parallel_normal_arms",
        }
    return {
        "question": (f"Dva ugla imaju {arms} krake; jedan je oštar, a drugi "
                     f"tup. Oštri ugao je $\\alpha = {_deg(alpha)}$. Koliki "
                     "je tupi ugao?"),
        "answer": Fraction(180 - alpha), "unit": "°",
        "rule": (f"Uglovi s {arms[:-1]}im kracima su suplementni kad je jedan "
                 "oštar, a drugi tup"),
        "work": f"\\beta = 180 - {alpha} = {180 - alpha}",
        "distractors": [Fraction(alpha), Fraction(90 + alpha),
                        Fraction(90 - alpha)],
        "signature": [("alpha", str(alpha)), ("arms", arms), ("same", "0")],
        "operation": "parallel_normal_arms",
    }


def _k_triangle_third_angle(rng, level):
    if level == 3:
        # Tri stvarna koraka: izračunaj beta iz odnosa, pa treći ugao.
        alpha = rng.randint(25, 70)
        delta = rng.randint(10, 40)
        beta = alpha + delta
        gamma = 180 - alpha - beta
        if gamma < 10:
            raise DeterministicGenerationError("premalen treći ugao")
        return {
            "question": (f"Ugao trougla je $\\alpha = {_deg(alpha)}$, a ugao "
                         f"$\\beta$ je za ${_deg(delta)}$ veći od ugla "
                         "$\\alpha$. Koliki je treći ugao $\\gamma$?"),
            "answer": Fraction(gamma), "unit": "°",
            "rule": "Zbir unutrašnjih uglova trougla je $180^\\circ$",
            # JEDAN lanac po segmentu: dvije jednačine u istom $...$ mathcheck
            # spaja u lažan par (98 = 180-61-98) — zato jedan egzaktan lanac.
            "work": (f"\\gamma = 180 - {alpha} - ({alpha} + {delta}) "
                     f"= 180 - {alpha} - {beta} = {gamma}"),
            "distractors": [Fraction(beta), Fraction(180 - alpha - delta),
                            Fraction(gamma + 10), Fraction(gamma - 10)],
            "signature": [("alpha", str(alpha)), ("delta", str(delta))],
            "operation": "triangle_third_angle", "evidence_level3": True,
        }
    alpha = rng.randint(25, 95)
    beta = rng.randint(20, 170 - alpha)
    gamma = 180 - alpha - beta
    return {
        "question": (f"Dva ugla trougla su $\\alpha = {_deg(alpha)}$ i "
                     f"$\\beta = {_deg(beta)}$. Koliki je treći ugao "
                     "$\\gamma$?"),
        "answer": Fraction(gamma), "unit": "°",
        "rule": "Zbir unutrašnjih uglova trougla je $180^\\circ$",
        "work": f"\\gamma = 180 - {alpha} - {beta} = {gamma}",
        "distractors": [Fraction(alpha + beta), Fraction(360 - alpha - beta),
                        Fraction(gamma + 10), Fraction(gamma - 10)],
        "signature": [("alpha", str(alpha)), ("beta", str(beta))],
        "operation": "triangle_third_angle", "evidence_level2": True,
    }


def _k_exterior_angle(rng, level):
    alpha = rng.randint(25, 90)
    beta = rng.randint(20, 150 - alpha)
    exterior = alpha + beta
    return {
        "question": (f"Unutrašnji uglovi trougla uz jednu stranicu su "
                     f"$\\alpha = {_deg(alpha)}$ i $\\beta = {_deg(beta)}$. "
                     "Koliki je vanjski ugao kod trećeg tjemena?"),
        "answer": Fraction(exterior), "unit": "°",
        "rule": ("Vanjski ugao trougla jednak je ZBIRU dva unutrašnja "
                 "nesusjedna ugla"),
        "work": f"\\gamma_1 = {alpha} + {beta} = {exterior}",
        "distractors": [Fraction(180 - alpha - beta), Fraction(alpha),
                        Fraction(180 - exterior) if exterior < 180
                        else Fraction(exterior - 90)],
        "signature": [("alpha", str(alpha)), ("beta", str(beta))],
        "operation": "exterior_angle",
    }


def _k_exterior_from_interior(rng, level):
    alpha = rng.randint(25, 155)
    return {
        "question": (f"Unutrašnji ugao trougla je $\\alpha = {_deg(alpha)}$. "
                     "Koliki je vanjski ugao uz taj isti ugao? (Zbir svih "
                     "vanjskih uglova trougla je $360^\\circ$.)"),
        "answer": Fraction(180 - alpha), "unit": "°",
        "rule": "Unutrašnji i njemu susjedni vanjski ugao su uporedni",
        "work": f"\\alpha_1 = 180 - {alpha} = {180 - alpha}",
        "distractors": [Fraction(alpha), Fraction(360 - alpha),
                        Fraction(90 - alpha) if alpha < 90
                        else Fraction(alpha - 90)],
        "signature": [("alpha", str(alpha))],
        "operation": "exterior_from_interior",
    }


def _k_classify_triangle_sides(rng, level):
    kind = rng.choice(("jednakostraničan", "jednakokraki", "raznostraničan"))
    if kind == "jednakostraničan":
        a = rng.randint(3, 15)
        sides = (a, a, a)
    elif kind == "jednakokraki":
        a = rng.randint(3, 12)
        b = rng.randint(3, 12)
        while b == a:
            b = rng.randint(3, 12)
        if b >= 2 * a:
            b = 2 * a - 1
        sides = (a, a, b)
    else:
        while True:
            trio = sorted(rng.sample(range(3, 16), 3))
            if trio[0] + trio[1] > trio[2]:
                sides = tuple(trio)
                break
    labels = ["jednakostraničan", "jednakokraki", "raznostraničan"]
    others = [label for label in labels if label != kind]
    rng.shuffle(others)
    a, b, c = sides
    return {
        "question": (f"Trougao ima stranice $a = {a}$ cm, $b = {b}$ cm i "
                     f"$c = {c}$ cm. Kakav je taj trougao prema stranicama?"),
        "option_texts": (kind, *others, "pravougli")[:4],
        "rule": ("Prema stranicama trougao je jednakostraničan (sve tri "
                 "jednake), jednakokraki (tačno dvije jednake) ili "
                 "raznostraničan (sve različite)"),
        "rule_neutral": ("Prebroj koliko je stranica trougla međusobno "
                         "jednakih dužina"),
        "work": f"a = {a},\\; b = {b},\\; c = {c}",
        "signature": [("sides", f"{a}+{b}+{c}")],
        "operation": "classify_triangle_sides",
    }


def _k_classify_triangle_angles(rng, level):
    kind = rng.choice(("oštrougli", "pravougli", "tupougli"))
    if kind == "pravougli":
        alpha = 90
        beta = rng.randint(20, 70)
    elif kind == "tupougli":
        alpha = rng.randint(95, 140)
        beta = rng.randint(15, 175 - alpha)
    else:
        alpha = rng.randint(55, 85)
        beta = rng.randint(50, min(85, 175 - alpha))
        if 180 - alpha - beta >= 90:
            alpha = 85
            beta = 60
    gamma = 180 - alpha - beta
    labels = ["oštrougli", "pravougli", "tupougli"]
    others = [label for label in labels if label != kind]
    return {
        "question": (f"Trougao ima uglove $\\alpha = {_deg(alpha)}$, "
                     f"$\\beta = {_deg(beta)}$ i $\\gamma = {_deg(gamma)}$. "
                     "Kakav je taj trougao prema uglovima?"),
        "option_texts": (kind, *others, "jednakostraničan")[:4],
        "rule": ("Prema uglovima trougao je oštrougli (svi uglovi oštri), "
                 "pravougli (jedan prav) ili tupougli (jedan tup)"),
        "rule_neutral": "Uporedi najveći ugao trougla s pravim uglom",
        "work": f"\\alpha = {alpha},\\; \\beta = {beta},\\; \\gamma = {gamma}",
        "signature": [("angles", f"{alpha}+{beta}+{gamma}")],
        "operation": "classify_triangle_angles",
    }


def _k_side_angle_order(rng, level):
    while True:
        alpha = rng.randint(30, 100)
        beta = rng.randint(25, 165 - alpha)
        gamma = 180 - alpha - beta
        if len({alpha, beta, gamma}) == 3 and gamma > 5:
            break
    angles = {"a": alpha, "b": beta, "c": gamma}
    largest = max(angles, key=angles.get)
    others = [name for name in ("a", "b", "c") if name != largest]
    return {
        "question": (f"U trouglu je $\\alpha = {_deg(alpha)}$ (naspram "
                     f"stranice $a$), $\\beta = {_deg(beta)}$ (naspram $b$) i "
                     f"$\\gamma = {_deg(gamma)}$ (naspram $c$). Koja je "
                     "stranica NAJDUŽA?"),
        "option_texts": (f"stranica ${largest}$", f"stranica ${others[0]}$",
                         f"stranica ${others[1]}$",
                         "sve stranice su jednake"),
        "rule": "Naspram većeg ugla trougla leži duža stranica",
        "work": f"\\alpha = {alpha},\\; \\beta = {beta},\\; \\gamma = {gamma}",
        "signature": [("angles", f"{alpha}+{beta}+{gamma}")],
        "operation": "side_angle_order", "comparison": True,
    }


def _k_triangle_inequality(rng, level):
    while True:
        a = rng.randint(3, 12)
        b = rng.randint(a, 15)
        c = rng.randint(max(b - a + 1, 2) + b - b, a + b - 1)
        c = rng.randint(abs(a - b) + 1, a + b - 1)
        if c >= 2:
            break
    wrong = []
    for _ in range(300):
        x = rng.randint(2, 10)
        y = rng.randint(x, 14)
        z = y + x + rng.randint(0, 4)
        if z >= x + y and (x, y, z) not in wrong:
            wrong.append((x, y, z))
        if len(wrong) == 3:
            break
    if len(wrong) < 3:
        raise DeterministicGenerationError("nedovoljno loših trojki")
    correct = f"${a}$ cm, ${b}$ cm i ${c}$ cm"
    options = [correct] + [f"${x}$ cm, ${y}$ cm i ${z}$ cm" for x, y, z in wrong]
    return {
        "question": ("Od kojih se od ponuđenih dužina MOŽE sastaviti "
                     "trougao?"),
        "option_texts": tuple(options),
        "rule": ("Nejednakost trougla: zbir svake dvije stranice mora biti "
                 "VEĆI od treće stranice"),
        "work": f"{a} + {b} > {c},\\; {a} + {c} > {b},\\; {b} + {c} > {a}",
        "signature": [("triple", f"{a}+{b}+{c}")],
        "operation": "triangle_inequality",
    }


def _k_isosceles_angles(rng, level):
    if level == 3:
        # Ugao pri vrhu je za d veći od ugla na osnovici: 2x + (x+d) = 180.
        delta = rng.choice((15, 30, 45, 60))
        base = (180 - delta) // 3
        if 3 * base + delta != 180:
            raise DeterministicGenerationError("nedjeljiv slučaj")
        top = base + delta
        return {
            "question": (f"U jednakokrakom trouglu ugao pri vrhu je za "
                         f"${_deg(delta)}$ veći od ugla na osnovici. Koliki "
                         "je ugao na osnovici?"),
            "answer": Fraction(base), "unit": "°",
            "rule": ("Uglovi na osnovici su jednaki, a zbir svih uglova "
                     "trougla je $180^\\circ$; iz $2\\alpha + (\\alpha + "
                     f"{delta}) = 180$ slijedi jedan egzaktan lanac"),
            "work": f"\\alpha = (180 - {delta}) : 3 = {180 - delta} : 3 = {base}",
            "distractors": [Fraction(top), Fraction((180 - delta) // 2)
                            if (180 - delta) % 2 == 0 else Fraction(base + 5),
                            Fraction(base + 10), Fraction(base - 10)],
            "signature": [("delta", str(delta))],
            "operation": "isosceles_relation_angle", "evidence_level3": True,
        }
    top = rng.randint(20, 140)
    if top % 2:
        top += 1
    base = (180 - top) // 2
    if rng.random() < 0.5:
        return {
            "question": (f"Ugao pri vrhu jednakokrakog trougla je "
                         f"$\\gamma = {_deg(top)}$. Koliki je ugao na "
                         "osnovici?"),
            "answer": Fraction(base), "unit": "°",
            "rule": ("Uglovi na osnovici jednakokrakog trougla su jednaki, a "
                     "zbir svih uglova je $180^\\circ$"),
            "work": f"\\alpha = (180 - {top}) : 2 = {base}",
            "distractors": [Fraction(180 - top), Fraction(top),
                            Fraction(90 - top // 2) if top < 180
                            else Fraction(base + 5)],
            "signature": [("top", str(top))],
            "operation": "isosceles_base_angle", "evidence_level2": True,
        }
    return {
        "question": (f"Ugao na osnovici jednakokrakog trougla je "
                     f"$\\alpha = {_deg(base)}$. Koliki je ugao pri vrhu?"),
        "answer": Fraction(top), "unit": "°",
        "rule": ("Uglovi na osnovici jednakokrakog trougla su jednaki, a "
                 "zbir svih uglova je $180^\\circ$"),
        "work": f"\\gamma = 180 - 2 \\cdot {base} = {top}",
        "distractors": [Fraction(180 - base), Fraction(base),
                        Fraction(90 - base) if base < 90
                        else Fraction(base - 10)],
        "signature": [("base", str(base))],
        "operation": "isosceles_top_angle", "evidence_level2": True,
    }


def _k_right_triangle_acute(rng, level):
    alpha = rng.randint(15, 75)
    return {
        "question": (f"Jedan oštri ugao pravouglog trougla je "
                     f"$\\alpha = {_deg(alpha)}$. Koliki je drugi oštri ugao?"),
        "answer": Fraction(90 - alpha), "unit": "°",
        "rule": ("Oštri uglovi pravouglog trougla zajedno čine $90^\\circ$"),
        "work": f"\\beta = 90 - {alpha} = {90 - alpha}",
        "distractors": [Fraction(180 - alpha), Fraction(alpha),
                        Fraction(90 + alpha)],
        "signature": [("alpha", str(alpha))],
        "operation": "right_triangle_acute",
    }


def _k_quad_fourth_angle(rng, level):
    alpha = rng.randint(50, 130)
    beta = rng.randint(50, 130)
    gamma = rng.randint(40, min(140, 350 - alpha - beta))
    delta = 360 - alpha - beta - gamma
    if not 5 <= delta <= 175:
        raise DeterministicGenerationError("nerealan četvrti ugao")
    return {
        "question": (f"Tri ugla četverougla su $\\alpha = {_deg(alpha)}$, "
                     f"$\\beta = {_deg(beta)}$ i $\\gamma = {_deg(gamma)}$. "
                     "Koliki je četvrti ugao $\\delta$?"),
        "answer": Fraction(delta), "unit": "°",
        "rule": "Zbir unutrašnjih uglova četverougla je $360^\\circ$",
        "rule_neutral": ("Zbir unutrašnjih uglova četverougla jednak je "
                         "zbiru uglova dva trougla"),
        "work": f"\\delta = 360 - {alpha} - {beta} - {gamma} = {delta}",
        "distractors": [Fraction(180 - delta) if delta < 180
                        else Fraction(delta - 90),
                        Fraction(alpha + beta + gamma), Fraction(delta + 10)],
        "signature": [("angles", f"{alpha}+{beta}+{gamma}")],
        "operation": "quad_fourth_angle", "evidence_level3": True,
    }


def _k_quad_exterior(rng, level):
    alpha = rng.randint(50, 160)
    return {
        "question": (f"Unutrašnji ugao četverougla je $\\alpha = {_deg(alpha)}$. "
                     "Koliki je vanjski ugao uz taj isti ugao? (Zbir vanjskih "
                     "uglova konveksnog četverougla je $360^\\circ$.)"),
        "answer": Fraction(180 - alpha), "unit": "°",
        "rule": "Unutrašnji i njemu susjedni vanjski ugao su uporedni",
        "work": f"\\alpha_1 = 180 - {alpha} = {180 - alpha}",
        "distractors": [Fraction(360 - alpha), Fraction(alpha),
                        Fraction(90 - alpha) if alpha < 90
                        else Fraction(alpha - 45)],
        "signature": [("alpha", str(alpha))],
        "operation": "quad_exterior",
    }


_KINDS = {
    "classify_angle": _k_classify_by_measure,
    "central_peripheral": _k_central_peripheral,
    "central_fraction": _k_central_fraction,
    "compare_angles": _k_compare_dms,
    "dms_add_sub": _k_dms_add_sub,
    "angle_times_n": _k_angle_times_n,
    "adjacent_vertical": _k_adjacent_vertical,
    "comp_supp": _k_comp_supp,
    "transversal": _k_transversal,
    "parallel_normal_arms": _k_parallel_normal_arms,
    "triangle_third_angle": _k_triangle_third_angle,
    "exterior_angle": _k_exterior_angle,
    "exterior_from_interior": _k_exterior_from_interior,
    "classify_triangle_sides": _k_classify_triangle_sides,
    "classify_triangle_angles": _k_classify_triangle_angles,
    "side_angle_order": _k_side_angle_order,
    "triangle_inequality": _k_triangle_inequality,
    "isosceles_angles": _k_isosceles_angles,
    "right_triangle_acute": _k_right_triangle_acute,
    "quad_fourth_angle": _k_quad_fourth_angle,
    "quad_exterior": _k_quad_exterior,
}


def supports(parameters) -> bool:
    parameters = parameters or {}
    kinds = parameters.get("kinds") or ()
    return bool(kinds) and all(kind in _KINDS for kind in kinds)


def _spec_evidence(spec, level):
    if spec.get("evidence_level3") and level == 3:
        return core.evidence_for_level(3)
    if spec.get("evidence_level2") and level >= 2:
        return core.evidence_for_level(2)
    if spec.get("comparison"):
        return core.evidence_for_level(level, comparison=True)
    return _evidence_direct(level)


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    kinds = tuple(parameters["kinds"])
    for _ in range(60):
        try:
            kind = rng.choice(kinds)
            spec = _KINDS[kind](rng, level)
        except DeterministicGenerationError:
            continue
        rule = spec["rule"]
        work = spec["work"]
        hints = (f"{rule}.",
                 f"Primijeni pravilo na zadate vrijednosti: ${work.split('=')[0].strip()}$.",
                 f"Postupak: ${work}$ — još samo pročitaj rezultat.")
        solution = f"{rule}. Računamo: ${work}$."

        def _leak_free_hints(display):
            # Prva uputa ne smije sadržavati prikaz odgovora (curenje kroz
            # konstantu pravila, npr. "60°" unutar "360°", ili kroz nabrajanje
            # svih klasa). Strukturno curenje rješava neutralno pravilo iz
            # speca; numerička kolizija se rješava novim pokušajem.
            if display not in hints[0]:
                return hints
            neutral = spec.get("rule_neutral")
            if neutral is None or display in neutral:
                return None
            return (f"{neutral}.",) + hints[1:]

        if "option_texts" in spec:
            option_texts = spec["option_texts"]
            if len(set(option_texts)) != 4:
                continue
            safe_hints = _leak_free_hints(option_texts[0])
            if safe_hints is None:
                continue
            hints = safe_hints
            return core.build_package(
                lesson_id=lesson_id, lesson_title=lesson_title,
                family_id="angle_relationships_direct",
                operation=spec["operation"], level=level,
                question=spec["question"], answer_value=option_texts[0],
                answer_display=option_texts[0], distractor_values=(),
                hints=hints, solution=solution,
                signature_parameters=list(spec["signature"])
                + [("kind", spec["operation"])],
                required_conditions=[spec["operation"]],
                relevant_objects=["angle"],
                generator_version=GENERATOR_VERSION,
                option_texts=option_texts, wrap="",
                evidence=_spec_evidence(spec, level))
        answer = spec["answer"]
        unit = spec.get("unit", "")
        display_of = spec.get("distractor_display")
        if display_of is not None:
            renderer = lambda value, _d=display_of: _d(int(value))
        else:
            renderer = core.fraction_display
        answer_display = spec.get("display_override") or (
            core.fraction_display(answer))
        degree = "^\\circ" if unit else ""
        safe_hints = _leak_free_hints(answer_display + degree)
        if safe_hints is None:
            continue
        hints = safe_hints
        option_values = [answer]
        option_texts = ["$" + answer_display + degree + "$"]
        for candidate in spec["distractors"]:
            candidate = Fraction(candidate)
            if candidate <= 0 or candidate in option_values:
                continue
            text = "$" + renderer(candidate) + degree + "$"
            if text in option_texts:
                continue
            option_values.append(candidate)
            option_texts.append(text)
            if len(option_texts) == 4:
                break
        while len(option_texts) < 4:
            candidate = answer + Fraction(len(option_texts) * 7)
            if candidate in option_values:
                candidate += 3
            option_values.append(candidate)
            option_texts.append("$" + renderer(candidate) + degree + "$")
        return core.build_package(
            lesson_id=lesson_id, lesson_title=lesson_title,
            family_id="angle_relationships_direct",
            operation=spec["operation"], level=level,
            question=spec["question"], answer_value=answer,
            answer_display=answer_display + degree,
            distractor_values=(), hints=hints, solution=solution,
            signature_parameters=list(spec["signature"])
            + [("kind", spec["operation"])],
            required_conditions=[spec["operation"]],
            relevant_objects=["angle"], generator_version=GENERATOR_VERSION,
            option_texts=tuple(option_texts), wrap="",
            evidence=_spec_evidence(spec, level))
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")
