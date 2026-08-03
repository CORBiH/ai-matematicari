"""Serverski GENERATOR matematičkog kostura zadatka — server računa, model piše.

ZAŠTO POSTOJI (Live96 kampanja, pozivi 503-598): u prethodnoj fazi je model
izmišljao matematiku i morao je PONOVO opisati u strukturisanom dokazu, a server
je iz dokaza rekonstruisao istinu. 11 od 48 pilot poziva je odbijeno — nijedno
zbog pogrešne matematike, sva zbog reprezentacije (pogrešna vrsta dokaza,
rupa u dokazu koji je ne dozvoljava, sukob između učenikove molbe i dodijeljenog
oblika). U dva poziva (536, 548) je serverov VLASTITI prompt nudio vrstu dokaza
koju serverov VLASTITI validator odbija.

Sada smjer ide obrnuto: server IZ UGOVORA konstruiše operande, operaciju,
tačan odgovor, distraktore i označeni indeks — pa su tačnost, vjernost lekciji
i označeni odgovor tačni PO KONSTRUKCIJI. Model dobija gotov zadatak i piše
samo okolnu bosansku prozu (reply/hint/feedback), koja se posebno provjerava.

Determinizam: sve odluke idu kroz predani `rng` (random.Random) — test sa
fiksnim seedom uvijek dobije isti kostur. Samoprovjera na kraju koristi ISTE
deterministe kao stara faza (constraints/difficulty/verifiers) — konstrukcija
koja ih ne zadovolji baca GenerationError i pada zatvoreno, bez modela i bez
mutacije stanja.

Nijedna funkcija ovdje ne zna nijednu lekciju: ista K1 sposobnost generiše i
sabiranje jednakih imenilaca i množenje razlomaka — razlika je isključivo u
vrijednostima ugovora (allowed_operations, denominator_relation, ...).
"""
from dataclasses import dataclass
from fractions import Fraction
from math import gcd

from matbot.contracts import constraints, difficulty, evidence as ev, verifiers

# Koliko puta generator smije pokušati prije nego što odustane (fail closed).
# Konstrukcija po pravilu uspije iz prvog pokušaja; petlja postoji zbog
# izbjegavanja nedavno viđenih tekstova i zbog filtera "rezultat već skraćen".
MAX_ATTEMPTS = 60

# Arhetipi za koje postoji STVARNI serverski generator. Registry pri učitavanju
# traži da svaki efektivni arhetip UKLJUČENOG ugovora bude ovdje — širenje
# podataka bez generičkog generatora je greška starta/CI-ja, nikad iznenađenje
# pred učenikom. (K2 find_missing_value i K4 identify_error dolaze kasnije.)
IMPLEMENTED_ARCHETYPES = frozenset({"direct_computation", "identify_equivalent"})


class GenerationError(ValueError):
    """Kostur se nije mogao konstruisati unutar granica ugovora. Poruka je
    INTERNA (log); učenik vidi postojeću sigurnu poruku, bez AI poziva."""


@dataclass(frozen=True)
class TaskSkeleton:
    """Potpun, verifikovan matematički kostur jednog zadatka.

    SAV vidljivi matematički sadržaj (tekst pitanja, sve četiri opcije,
    očekivani odgovor) je ovdje i SAMO ovdje — model ga ne smije ni pisati ni
    prepričavati. `correct_index` je PRE-shuffle; miješanje i ID-jeve opcija
    radi postojeći serverski kod (practice._shuffle_options)."""

    archetype_id: str
    question_text: str
    option_texts: tuple
    correct_index: int
    expected_answer: str
    difficulty_label: str          # "easy" | "standard" | "hard" (za sesiju)
    truth: Fraction
    primary_nodes: tuple           # čvorovi za samoprovjeru ograničenja
    option_nodes: tuple            # Node po opciji (predshuffle)
    reference: object = None       # Node — samo identify_equivalent
    target_levels: dict = None     # ciljni nivoi težine (interno/log)


# ---------------------------------------------------------------------------
# RENDER — server piše MathJax; nijedan drugi sloj ne smije mijenjati brojeve.
# ---------------------------------------------------------------------------

_OPERATOR_TOKENS = {"add": "+", "subtract": "-", "multiply": r"\cdot", "divide": ":"}


def render_pair(num, den):
    """MathJax zapis NEREDUKOVANOG para — struktura zapisa je dio zadatka
    (distraktor „$\\frac{8}{16}$“ mora ostati 8/16, ne 1/2)."""
    if den == 1:
        return str(num)
    return r"\frac{%d}{%d}" % (num, den)


def render_node(node):
    if node.is_literal:
        return render_pair(node.num, node.den)
    left, right = node.args
    token = _OPERATOR_TOKENS[node.op]
    return f"{render_node(left)} {token} {render_node(right)}"


def render_value(value):
    """MathJax zapis REDUKOVANE vrijednosti (tačan odgovor)."""
    return render_pair(value.numerator, value.denominator)


def _math(text):
    return "$" + text + "$"


# ---------------------------------------------------------------------------
# GRANICE VELIČINE OPERANADA — inverz difficulty.magnitude_level, da izmjerena
# težina objavljenog zadatka bude TAČNO ciljni nivo (ne samo "unutar granica").
# ---------------------------------------------------------------------------

def _magnitude_band(level, cap):
    if level <= 1:
        low, high = 1, 12
    elif level == 2:
        low, high = 13, 50
    else:
        low, high = 51, cap
    high = min(high, cap)
    if low > high:
        # Ugovor s malim integer_range ne može doseći viši nivo — ostani u
        # najvišem dostižnom pojasu umjesto da izađeš iz opsega.
        return 1, cap
    return low, high


def _denominator_pool(low, high, rng):
    pool = [d for d in range(max(2, low), high + 1)]
    if not pool:
        pool = [2]
    rng.shuffle(pool)
    return pool


# ---------------------------------------------------------------------------
# K1 — direct_computation nad razlomcima/cijelim brojevima
# ---------------------------------------------------------------------------

def _pick_operands(contract, levels, rng, operation):
    """Operandi po ugovoru: odnos imenilaca, znak, opseg, ciljna veličina."""
    bounds = contract.constraint("integer_range") or [1, 60]
    cap = bounds[1]
    low, high = _magnitude_band(levels.get("operand_magnitude", 1), cap)
    relation = contract.constraint("denominator_relation", "any")
    term_count = levels.get("term_count", 2)
    if operation == "divide":
        # Lanac a : b : c je zapisno dvosmislen za 6. razred — dijeljenje
        # uvijek ostaje binarno, bez obzira na ciljni broj članova.
        term_count = 2
    term_count = max(2, min(3, term_count))

    denominators = _denominator_pool(low, high, rng)
    pairs = []
    if relation == "equal":
        den = denominators[0]
        numerators = list(range(1, den))
        if len(numerators) < term_count:
            return None
        rng.shuffle(numerators)
        pairs = [(numerators[i], den) for i in range(term_count)]
    elif relation == "different":
        if len(denominators) < term_count:
            return None
        dens = denominators[:term_count]
        for den in dens:
            pairs.append((rng.randint(1, den - 1) if den > 1 else 1, den))
    else:
        # Bez zahtjeva na imenioce (množenje/dijeljenje): pravi razlomci s
        # imeniocem >= 2, da zadatak nedvosmisleno vježba razlomke.
        if len(denominators) < term_count:
            return None
        dens = [denominators[i % len(denominators)] for i in range(term_count)]
        for den in dens:
            pairs.append((rng.randint(1, den - 1) if den > 1 else 1, den))

    # Bar jedan broj u ciljanom pojasu veličine → izmjeren nivo == ciljni.
    magnitudes = [abs(v) for n, d in pairs for v in (n, d)]
    if not any(low <= m <= high for m in magnitudes):
        return None
    if any(m > cap for m in magnitudes):
        return None
    return pairs


def _fold(operation, pairs):
    node = ev.Node(num=pairs[0][0], den=pairs[0][1])
    for num, den in pairs[1:]:
        node = ev.Node(op=operation, args=(node, ev.Node(num=num, den=den)))
    return node


def _raw_result(operation, pairs):
    """(brojnik, imenilac) NEREDUKOVANOG rezultata — filter „rezultat je već
    skraćen“ time izbjegava tihо uvlačenje druge vještine (skraćivanja) u
    lekciju koja je ne uči."""
    num, den = pairs[0]
    for n, d in pairs[1:]:
        if operation == "add":
            common = den * d // gcd(den, d)
            num, den = num * (common // den) + n * (common // d), common
        elif operation == "subtract":
            common = den * d // gcd(den, d)
            num, den = num * (common // den) - n * (common // d), common
        elif operation == "multiply":
            num, den = num * n, den * d
        else:
            num, den = num * d, den * n
    return num, den


def _direct_computation_distractors(operation, pairs, truth, rng):
    """Distraktori se prave UNAPRIJED primjenom stvarnih grešaka učenika —
    isti katalog grešaka koji verifiers.py umije strukturno prepoznati."""
    candidates = []
    (n1, d1), (n2, d2) = pairs[0], pairs[1]
    if operation == "add":
        candidates.append((n1 + n2, d1 + d2))          # sabrao i imenioce
        candidates.append((n1 + n2 + 1, d1))           # pogrešan brojnik
        candidates.append((abs(n1 - n2), d1) if n1 != n2 else (n1 * n2, d1))
        if d1 != d2:
            candidates.append((n1 + n2, max(d1, d2)))  # zaboravio proširiti
    elif operation == "subtract":
        candidates.append((n1 + n2, d1))               # pogrešna operacija
        candidates.append((abs(n1 - n2) + 1, d1))      # pogrešan brojnik
        candidates.append((n1 - n2, d1 + d2) if d1 + d2 > 0 else (n1, d1 + 1))
        if d1 != d2:
            candidates.append((abs(n1 - n2), max(d1, d2)))
    elif operation == "multiply":
        candidates.append((n1 * d2, d1 * n2))          # pomnožio unakrsno
        candidates.append((n1 + n2, d1 + d2))          # sabrao umjesto množenja
        candidates.append((n1 * n2, d1 + d2))          # imenioce sabrao
    else:  # divide
        candidates.append((n1 * n2, d1 * d2))          # bez recipročne vrijednosti
        candidates.append((d1 * n2, n1 * d2))          # okrenuo pogrešan razlomak
        candidates.append((n1 + n2, d1 + d2))          # sabrao umjesto dijeljenja
    # Rezervni kandidati kad se gornji poklope po vrijednosti.
    candidates.append((truth.numerator + 1, truth.denominator))
    candidates.append((truth.numerator + truth.denominator, truth.denominator))
    candidates.append((truth.numerator, truth.denominator + 1))
    return candidates


def _select_distractors(candidates, truth, forbidden_values=(), count=3):
    """Prve `count` kandidate s međusobno RAZLIČITIM vrijednostima, različitim
    od istine i od svake zabranjene vrijednosti. Nedovoljno kandidata = None."""
    chosen, seen = [], {truth} | set(forbidden_values)
    for num, den in candidates:
        if den <= 0 or num <= 0:
            continue
        value = Fraction(num, den)
        if value in seen:
            continue
        seen.add(value)
        chosen.append((num, den))
        if len(chosen) == count:
            return chosen
    return None


def _generate_direct_computation(contract, levels, rng):
    operations = list(contract.allowed_operations) or ["add"]
    operation = operations[rng.randrange(len(operations))]

    pairs = _pick_operands(contract, levels, rng, operation)
    if pairs is None:
        return None
    if operation == "subtract":
        # Q+ (sign_policy non_negative): rezultat ne smije biti negativan.
        pairs.sort(key=lambda p: Fraction(p[0], p[1]), reverse=True)

    raw_num, raw_den = _raw_result(operation, pairs)
    if raw_num <= 0 or raw_den <= 0:
        return None
    if gcd(raw_num, raw_den) != 1:
        # Rezultat koji se mora skraćivati tiho uvodi DRUGU vještinu
        # (skraćivanje) — takav pokušaj se odbacuje i bira se drugi.
        return None
    truth = Fraction(raw_num, raw_den)

    distractors = _select_distractors(
        _direct_computation_distractors(operation, pairs, truth, rng), truth
    )
    if distractors is None:
        return None

    expression = _fold(operation, pairs)
    option_pairs = [(truth.numerator, truth.denominator)] + distractors
    option_nodes = tuple(ev.Node(num=n, den=d) for n, d in option_pairs)
    option_texts = tuple(_math(render_pair(n, d)) for n, d in option_pairs)

    return TaskSkeleton(
        archetype_id="direct_computation",
        question_text="Izračunaj: " + _math(render_node(expression)) + ".",
        option_texts=option_texts,
        correct_index=0,
        expected_answer=_math(render_value(truth)),
        difficulty_label="",
        truth=truth,
        primary_nodes=(expression,),
        option_nodes=option_nodes,
        target_levels=dict(levels),
    )


# ---------------------------------------------------------------------------
# K3 — identify_equivalent (proširivanje/skraćivanje kroz scaling_direction)
# ---------------------------------------------------------------------------

def _generate_identify_equivalent(contract, levels, rng):
    bounds = contract.constraint("integer_range") or [1, 60]
    cap = bounds[1]
    low, high = _magnitude_band(levels.get("operand_magnitude", 1), cap)
    direction = contract.constraint("scaling_direction", "expand")

    # Nesvodiva osnova n0/d0 i faktor k >= 2 unutar opsega ugovora. Osnova
    # smije rasti do cap//2 da bi i „teži“ zahtjev (veći ciljni pojas) imao
    # dostižnu referencu, a k=2 uvijek ostao unutar opsega.
    base_pool = [(n, d) for d in range(2, min(high, cap // 2) + 1)
                 for n in range(1, d) if gcd(n, d) == 1]
    if not base_pool:
        return None
    rng.shuffle(base_pool)
    for n0, d0 in base_pool:
        max_k = cap // d0
        factors = [k for k in range(2, max_k + 1) if d0 * k <= cap and n0 * k <= cap]
        if not factors:
            continue
        rng.shuffle(factors)
        k = factors[0]

        if direction == "reduce":
            reference_pair = (n0 * k, d0 * k)   # zadan je proširen zapis
            answer_pair = (n0, d0)              # tačan zapis: nesvodiv
        else:
            reference_pair = (n0, d0)           # zadana je osnova
            answer_pair = (n0 * k, d0 * k)      # tačan zapis: proširen

        # Bar jedan vidljiv broj (u referenci — težina se mjeri nad njom)
        # mora biti u ciljanom pojasu veličine.
        magnitudes = [abs(v) for v in reference_pair]
        if not any(low <= m <= high for m in magnitudes):
            continue

        value = Fraction(n0, d0)
        j = k + 1 if (d0 * (k + 1) <= cap and k + 1 != k) else max(2, k - 1)
        candidates = [
            (n0 * k, d0 * j),        # brojnik i imenilac različito skalirani
            (n0 * j, d0 * k),        # obrnuto različito skalirani
            (n0 * k + 1, d0 * k),    # brojnik promašen za 1
            (n0, d0 * k),            # skalirao samo imenilac
            (n0 * k, d0),            # skalirao samo brojnik
        ]
        distractors = _select_distractors(candidates, value)
        if distractors is None:
            continue

        reference = ev.Node(num=reference_pair[0], den=reference_pair[1])
        option_pairs = [answer_pair] + distractors
        option_nodes = tuple(ev.Node(num=n, den=d) for n, d in option_pairs)
        option_texts = tuple(_math(render_pair(n, d)) for n, d in option_pairs)

        question = (
            "Koji od ponuđenih zapisa ima ISTU vrijednost kao razlomak "
            + _math(render_pair(*reference_pair)) + "?"
        )
        return TaskSkeleton(
            archetype_id="identify_equivalent",
            question_text=question,
            option_texts=option_texts,
            correct_index=0,
            expected_answer=_math(render_pair(*answer_pair)),
            difficulty_label="",
            truth=value,
            primary_nodes=(reference,),
            option_nodes=option_nodes,
            reference=reference,
            target_levels=dict(levels),
        )
    return None


_GENERATORS = {
    "direct_computation": _generate_direct_computation,
    "identify_equivalent": _generate_identify_equivalent,
}
assert frozenset(_GENERATORS) == IMPLEMENTED_ARCHETYPES


# ---------------------------------------------------------------------------
# SAMOPROVJERA — isti deterministi kao ranija faza; „nije se moglo dokazati“
# nikad ne znači „prošlo je“.
# ---------------------------------------------------------------------------

def self_verify(contract, skeleton):
    """Vrati (ok, code). Provjerava kostur ISTIM kodom kojim je stara faza
    provjeravala modelov dokaz — konstrukcija ne dobija povlasticu povjerenja."""
    facts = ev.facts_for(skeleton.primary_nodes)

    constraint_result = constraints.check_evidence(contract, facts)
    if not constraint_result.valid:
        return False, constraint_result.code

    difficulty_result = difficulty.check_within_bounds(contract, facts)
    if not difficulty_result.valid:
        return False, difficulty_result.code

    reference = skeleton.reference
    verified = verifiers.verify_exact_rational(
        skeleton.truth, skeleton.option_nodes, skeleton.correct_index,
        require_distinct_from=reference,
    )
    if not verified.ok:
        return False, verified.code

    if reference is not None:
        answer_node = skeleton.option_nodes[skeleton.correct_index]
        relation = constraints.check_answer_relation(contract, reference, answer_node)
        if not relation.valid:
            return False, relation.code

    if len(set(skeleton.option_texts)) != len(skeleton.option_texts):
        return False, "duplicate_option_text"
    return True, "ok"


def generate(contract, archetype_id, difficulty_request="", rng=None,
             avoid_texts=()):
    """Konstruiši i verifikuj kostur zadatka. Baca GenerationError — nikad ne
    pogađa i nikad ne vraća djelimičan/neprovjeren kostur."""
    if rng is None:
        import random as _random
        rng = _random.Random()
    build = _GENERATORS.get(archetype_id)
    if build is None:
        raise GenerationError(f"arhetip '{archetype_id}' nema generator")

    levels = difficulty.target_levels(contract, difficulty_request)
    request = (difficulty_request or "").strip().lower()
    label = {"harder": "hard", "easier": "easy"}.get(request, "standard")
    avoid = {(text or "").strip() for text in avoid_texts}

    last_code = "generation_exhausted"
    for _ in range(MAX_ATTEMPTS):
        skeleton = build(contract, levels, rng)
        if skeleton is None:
            continue
        if skeleton.question_text.strip() in avoid:
            last_code = "recent_repeat_avoided"
            continue
        ok, code = self_verify(contract, skeleton)
        if not ok:
            # Konstrukcija koja padne na vlastitoj provjeri se NE objavljuje i
            # NE popravlja pogađanjem — pokušaj se odbacuje.
            last_code = code
            continue
        return TaskSkeleton(
            archetype_id=skeleton.archetype_id,
            question_text=skeleton.question_text,
            option_texts=skeleton.option_texts,
            correct_index=skeleton.correct_index,
            expected_answer=skeleton.expected_answer,
            difficulty_label=label,
            truth=skeleton.truth,
            primary_nodes=skeleton.primary_nodes,
            option_nodes=skeleton.option_nodes,
            reference=skeleton.reference,
            target_levels=skeleton.target_levels,
        )
    raise GenerationError(last_code)
