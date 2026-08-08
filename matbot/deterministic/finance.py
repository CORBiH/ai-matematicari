"""Deterministička finansijska aritmetika (Batch #4, Prioritet 5).

Jedna semantička porodica: ``financial_arithmetic_direct``.

  • ``currency_conversion``  — preračun po kursu NAVEDENOM u zadatku;
  • ``simple_interest``      — prosta kamata K = G·p·t/100 (štednja);
  • ``credit_repayment``     — glavnica + prosta kamata (ukupan povrat);
  • ``budget_balance``       — prihodi minus rashodi ličnog budžeta;
  • ``percent_change_price`` — sniženje/poskupljenje za navedeni procenat.

MATEMATIČKI AUTORITET: egzaktni razlomci (`Fraction`); svaki vidljivi iznos
ima KONAČAN decimalni zapis (core.decimal_display, nikad binarni float).
NIJEDAN kurs, kamatna stopa ni cijena ne dolazi iz vanjskog svijeta — svaka
stopa je navedena u samom zadatku, pa je odgovor dokaziv iz vidljivog teksta.
Složena kamata, promjenjive stope i stvarni bankarski proizvodi NISU u obimu.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("financial_arithmetic_direct",)
GENERATOR_VERSION = "detfinance-1"

_SUPPORTED_CONCEPTS = frozenset({
    "currency_conversion", "simple_interest", "credit_repayment",
    "budget_balance", "percent_change_price",
})


def supports(parameters) -> bool:
    parameters = parameters or {}
    concepts = set(parameters.get("concepts") or ())
    return bool(concepts) and concepts <= _SUPPORTED_CONCEPTS


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    builders = {
        "currency_conversion": _conversion_package,
        "simple_interest": _interest_package,
        "credit_repayment": _credit_package,
        "budget_balance": _budget_package,
        "percent_change_price": _percent_change_package,
    }
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            return builders[concept](rng, level, lesson_id, lesson_title, concept)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _money(value: Fraction) -> str:
    if not core.is_terminating_decimal(value):
        raise DeterministicGenerationError("iznos nema konačan zapis")
    return core.decimal_display(value)


def _money_package(lesson_id, lesson_title, concept, level, question, answer,
                   distractors, hints, solution, signature, unit="KM"):
    def display(value):
        return f"{_money(value)}"

    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="financial_arithmetic_direct", operation=concept,
        level=level, question=question, answer_value=answer,
        answer_display=display(answer), distractor_values=distractors,
        hints=hints, solution=solution, signature_parameters=signature,
        required_conditions=[concept], relevant_objects=["novac"],
        generator_version=GENERATOR_VERSION, display_of=display)


# ---------------------------------------------------------------------------
# PRERAČUNAVANJE VALUTA — kurs UVIJEK u zadatku
# ---------------------------------------------------------------------------

_RATES = (
    ("euro", "EUR", Fraction(195, 100)),
    ("dolar", "USD", Fraction(180, 100)),
    ("funta", "GBP", Fraction(225, 100)),
)


def _conversion_package(rng, level, lesson_id, lesson_title, concept):
    currency_name, currency, rate = rng.choice(_RATES)
    to_km = rng.random() < 0.5 or level == 1
    rate_text = _money(rate)
    if to_km:
        amount = Fraction(rng.randint(2, 40 if level == 1 else 200))
        answer = amount * rate
        question = (f"Kurs je: $1$ {currency} $= {rate_text}$ KM. Koliko KM "
                    f"vrijedi ${_money(amount)}$ {currency}?")
        hints = (
            "Iznos u stranoj valuti množi se navedenim kursom.",
            f"Izračunaj ${_money(amount)} \\cdot {rate_text}$.",
            "Rezultat je iznos u konvertibilnim markama.",
        )
        solution = (f"${_money(amount)} \\cdot {rate_text} = "
                    f"{_money(answer)}$, dakle ${_money(answer)}$ KM.")
        distractors = [amount / rate, answer + rate, answer - rate,
                       amount + rate]
    else:
        units = Fraction(rng.randint(2, 40 if level < 3 else 200))
        amount = units * rate
        answer = units
        question = (f"Kurs je: $1$ {currency} $= {rate_text}$ KM. Koliko "
                    f"{currency_name}a dobiješ za ${_money(amount)}$ KM?")
        hints = (
            "Iznos u KM dijeli se navedenim kursom.",
            f"Izračunaj ${_money(amount)} : {rate_text}$.",
            "Rezultat je iznos u stranoj valuti.",
        )
        solution = (f"${_money(amount)} : {rate_text} = {_money(answer)}$, "
                    f"dakle ${_money(answer)}$ {currency}.")
        distractors = [amount * rate, answer + 1, answer - 1, amount - rate]
    distractors = [d for d in distractors
                   if d > 0 and core.is_terminating_decimal(d)]
    return _money_package(lesson_id, lesson_title, concept, level, question,
                          answer, distractors, hints, solution,
                          [("rate", str(rate)), ("currency", currency),
                           ("direction", "to_km" if to_km else "from_km")])


# ---------------------------------------------------------------------------
# PROSTA KAMATA
# ---------------------------------------------------------------------------

def _interest_terms(rng, level):
    principal = Fraction(rng.choice((200, 400, 500, 800, 1000, 1200, 1500)
                                    if level < 3 else
                                    (600, 900, 1600, 2400, 3000)))
    percent = Fraction(rng.choice((2, 3, 4, 5) if level == 1 else
                                  (2, 3, 4, 5, 6, 8)))
    years = Fraction(rng.randint(1, 1 if level == 1 else 3))
    return principal, percent, years


def _interest_package(rng, level, lesson_id, lesson_title, concept):
    principal, percent, years = _interest_terms(rng, level)
    interest = principal * percent * years / 100
    year_word = "godinu" if years == 1 else "godine"
    question = (f"Na štednju je uloženo ${_money(principal)}$ KM uz prostu "
                f"godišnju kamatnu stopu od ${percent}$ %. Kolika je kamata "
                f"poslije ${years}$ {year_word}?")
    # Prvi hint bez ijedne cifre: iznos kamate (npr. „10“ ili „100“ KM) ne
    # smije se pojaviti ni kao podniz formule (živi 100-seed fuzz nalaz).
    hints = (
        "Prosta kamata: pomnoži glavnicu, godišnju stopu i broj godina, pa "
        "proizvod podijeli sa sto.",
        f"Uvrsti: $K = {_money(principal)} \\cdot {percent} \\cdot "
        f"{years} : 100$.",
        "Prvo pomnoži sve tri vrijednosti, na kraju podijeli sa sto.",
    )
    solution = (f"$K = {_money(principal)} \\cdot {percent} \\cdot {years} "
                f": 100 = {_money(interest)}$, pa kamata iznosi "
                f"${_money(interest)}$ KM.")
    distractors = [interest * 10, interest / 10 if interest % 10 == 0
                   else interest + 10, principal + interest,
                   interest + Fraction(5), interest * 2]
    distractors = [d for d in distractors
                   if d > 0 and d != interest and core.is_terminating_decimal(d)]
    return _money_package(lesson_id, lesson_title, concept, level, question,
                          interest, distractors, hints, solution,
                          [("principal", str(principal)),
                           ("percent", str(percent)), ("years", str(years))])


def _credit_package(rng, level, lesson_id, lesson_title, concept):
    principal, percent, years = _interest_terms(rng, level)
    interest = principal * percent * years / 100
    total = principal + interest
    year_word = "godinu" if years == 1 else "godine"
    question = (f"Podignut je kredit od ${_money(principal)}$ KM uz prostu "
                f"godišnju kamatnu stopu od ${percent}$ % na ${years}$ "
                f"{year_word}. Koliko se UKUPNO vraća banci (glavnica plus "
                "kamata)?")
    # Prvi hint bez cifara — vidi bilješku u _interest_package.
    hints = (
        "Prvo izračunaj kamatu: pomnoži glavnicu, stopu i broj godina, pa "
        "podijeli sa sto.",
        f"Kamata je ${_money(interest)}$ KM.",
        "Ukupan povrat je glavnica plus kamata.",
    )
    solution = (f"Kamata: $K = {_money(principal)} \\cdot {percent} \\cdot "
                f"{years} : 100 = {_money(interest)}$ KM. Ukupno se vraća "
                f"${_money(principal)} + {_money(interest)} = "
                f"{_money(total)}$ KM.")
    distractors = [interest, principal, total + interest,
                   principal - interest, total + Fraction(10)]
    distractors = [d for d in distractors
                   if d > 0 and d != total and core.is_terminating_decimal(d)]
    return _money_package(lesson_id, lesson_title, concept, level, question,
                          total, distractors, hints, solution,
                          [("principal", str(principal)),
                           ("percent", str(percent)), ("years", str(years))])


# ---------------------------------------------------------------------------
# LIČNI BUDŽET
# ---------------------------------------------------------------------------

def _budget_package(rng, level, lesson_id, lesson_title, concept):
    income = Fraction(rng.choice((50, 60, 80, 100, 120)))
    items = 2 if level == 1 else 3
    names = rng.sample(("užinu", "kartu za kino", "poklon", "knjigu",
                        "sladoled"), items)
    amounts = []
    for _ in range(items):
        whole = rng.randint(3, 20)
        cents = rng.choice((0, 50)) if level < 3 else rng.choice((0, 25, 50, 75))
        amounts.append(Fraction(whole * 100 + cents, 100))
    spent = sum(amounts, Fraction(0))
    remainder = income - spent
    if remainder <= 0:
        raise DeterministicGenerationError("budžet u minusu")
    spent_prose = ", ".join(
        f"${_money(amount)}$ KM za {name}"
        for name, amount in zip(names, amounts))
    question = (f"Džeparac iznosi ${_money(income)}$ KM. Potrošeno je: "
                f"{spent_prose}. Koliko novca OSTAJE u budžetu?")
    chain = " + ".join(_money(amount) for amount in amounts)
    hints = (
        "Ostatak budžeta je prihod minus zbir svih rashoda.",
        f"Saberi rashode: ${chain}$.",
        f"Zbir rashoda oduzmi od ${_money(income)}$ KM.",
    )
    solution = (f"Rashodi: ${chain} = {_money(spent)}$ KM. Ostaje "
                f"${_money(income)} - {_money(spent)} = "
                f"{_money(remainder)}$ KM.")
    distractors = [spent, remainder + Fraction(1), remainder - Fraction(1, 2),
                   income]
    distractors = [d for d in distractors
                   if d > 0 and d != remainder
                   and core.is_terminating_decimal(d)]
    return _money_package(lesson_id, lesson_title, concept, level, question,
                          remainder, distractors, hints, solution,
                          [("income", str(income)),
                           ("spent", str(spent))])


# ---------------------------------------------------------------------------
# SNIŽENJE / POSKUPLJENJE
# ---------------------------------------------------------------------------

def _percent_change_package(rng, level, lesson_id, lesson_title, concept):
    price = Fraction(rng.choice((40, 60, 80, 120, 200) if level == 1 else
                                (30, 50, 90, 150, 240, 360)))
    percent = Fraction(rng.choice((10, 20, 25, 50) if level < 3 else
                                  (5, 10, 15, 20, 25, 30, 40)))
    discount = rng.random() < 0.6
    change = price * percent / 100
    final = price - change if discount else price + change
    word = "SNIŽENA" if discount else "POVEĆANA"
    question = (f"Cijena artikla od ${_money(price)}$ KM {word.lower()} je "
                f"za ${percent}$ %. Kolika je nova cijena?")
    # Prvi hint bez cifara — nova cijena ne smije biti podniz (npr. „20“
    # u „20 %“); vidi bilješku u _interest_package.
    hints = (
        "Prvo izračunaj navedeni procenat od stare cijene.",
        f"Promjena: ${_money(price)} \\cdot {percent} : 100 = "
        f"{_money(change)}$ KM.",
        ("Promjenu oduzmi od stare cijene." if discount
         else "Promjenu dodaj na staru cijenu."),
    )
    sign = "-" if discount else "+"
    solution = (f"Promjena iznosi ${_money(change)}$ KM, pa je nova cijena "
                f"${_money(price)} {sign} {_money(change)} = "
                f"{_money(final)}$ KM.")
    distractors = [change, price,
                   price + change if discount else price - change,
                   final + Fraction(5)]
    distractors = [d for d in distractors
                   if d > 0 and d != final and core.is_terminating_decimal(d)]
    return _money_package(lesson_id, lesson_title, concept, level, question,
                          final, distractors, hints, solution,
                          [("price", str(price)), ("percent", str(percent)),
                           ("direction", "down" if discount else "up")])
