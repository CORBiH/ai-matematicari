"""Finding B: deterministička provjera numeričke dosljednosti (matbot/mathcheck.py).

Živi nalaz (Explain, „Pravilni mnogougao“): ispravna formula, pogrešan lanac —
$P=\\frac{3\\cdot16\\sqrt{3}}{2}=48\\sqrt{3}\\approx83,14$ umjesto $24\\sqrt{3}\\approx41,57$.
"""
import json

import pytest

from matbot import config
from matbot.mathcheck import (
    find_numeric_inconsistencies, is_numerically_consistent, math_segments,
)
from matbot.explain import run_explain_turn
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.quick import run_quick_turn
from matbot.session_store import SessionStore
from tests.conftest import (queue_two_call, FakeLLM, make_explain_output, make_options, make_output,
                             make_quick_output, make_task, make_task_for_family)


def rejects(text):
    return bool(find_numeric_inconsistencies(text))


# ---------------------------------------------------------------------------
# Exact live failure / correction
# ---------------------------------------------------------------------------

LIVE_WRONG = "$P=\\frac{3\\cdot16\\sqrt{3}}{2}=48\\sqrt{3}\\approx83,14\\,\\text{cm}^2$"
LIVE_RIGHT = "$P=\\frac{3\\cdot16\\sqrt{3}}{2}=24\\sqrt{3}\\approx41,57\\,\\text{cm}^2$"


def test_exact_live_wrong_equality_chain_is_rejected():
    issues = find_numeric_inconsistencies(LIVE_WRONG)
    assert issues
    assert "numeric_equality_mismatch" in issues[0]


def test_exact_live_corrected_chain_is_accepted():
    assert not find_numeric_inconsistencies(LIVE_RIGHT)


# ---------------------------------------------------------------------------
# 1-11. Required numeric cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$3\\cdot16/2=24$",
    "$\\sqrt{100}=10$",
    "$\\frac{75}{3}=25$",
    "$4\\pi\\cdot9=36\\pi$",
    "$24\\sqrt3\\approx41,57$",
    "$24\\sqrt3\\approx41,6$",
])
def test_valid_numeric_chains_are_accepted(text):
    assert not rejects(text), text


@pytest.mark.parametrize("text", [
    "$3\\cdot16/2=48$",
    "$\\sqrt{100}=20$",
    "$\\frac{75}{3}=15$",
    "$4\\pi\\cdot9=18\\pi$",
    "$24\\sqrt3\\approx83,14$",
])
def test_invalid_numeric_chains_are_rejected(text):
    assert rejects(text), text


# ---------------------------------------------------------------------------
# 12-13. Decimal comma and units
# ---------------------------------------------------------------------------

def test_decimal_comma_is_handled():
    assert not rejects("$2,5\\cdot4=10$")
    assert rejects("$2,5\\cdot4=12$")


def test_units_are_ignored_for_computation():
    assert not rejects("$P=\\frac{10\\cdot 6}{2}=30\\,\\text{cm}^2$")
    assert not rejects("$V=\\frac{25\\cdot9}{3}=75\\,\\text{cm}^3$")
    assert rejects("$V=\\frac{25\\cdot9}{3}=70\\,\\text{cm}^3$")


def test_units_remain_byte_identical_in_output():
    """Checker NIKAD ne mijenja tekst — samo prijavljuje."""
    text = "$P=30\\,\\text{cm}^2$"
    find_numeric_inconsistencies(text)
    assert text == "$P=30\\,\\text{cm}^2$"


# ---------------------------------------------------------------------------
# 14-16. Safe skipping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$c^2=a^2+b^2$",
    "$P=\\frac{3a^2\\sqrt3}{2}$",
    "$x+7=15$",
    "$P=2\\pi r(r+H)$",
    "$M=O_BH$",
    "$V=\\frac{BH}{3}$",
    "$(x,y)=(3,2)$",
    "$\\log(100)=2$",
    "$35^\\circ+55^\\circ=90^\\circ$",
])
def test_symbolic_or_unsupported_expressions_are_skipped(text):
    assert not rejects(text), text


def test_skipping_is_not_proof_of_correctness():
    """Dokumentuje politiku: preskočen izraz vraća praznu listu, isto kao
    ispravan — checker je čuvar dosljednosti, ne dokazivač."""
    assert find_numeric_inconsistencies("$c^2=a^2+b^2$") == []
    assert find_numeric_inconsistencies("$2+2=4$") == []


# ---------------------------------------------------------------------------
# 17. Division by zero / invalid root
# ---------------------------------------------------------------------------

def test_division_by_zero_is_rejected():
    assert rejects("$\\frac{5}{0}=1$")


def test_invalid_square_root_is_rejected():
    assert rejects("$\\sqrt{-4}=2$")


# ---------------------------------------------------------------------------
# 18. No arbitrary code execution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "$__import__('os').system('echo hi')=1$",
    "$(1).__class__=2$",
    "$open('x')=1$",
    "$[1,2][0]=1$",
    "$lambda:1=1$",
    "$9**9**9=1$",
])
def test_no_arbitrary_code_execution(payload):
    """Sve mora biti ili sigurno preskočeno ili odbijeno — nikad izvršeno."""
    find_numeric_inconsistencies(payload)  # ne smije baciti niti išta izvršiti


def test_evaluator_rejects_non_whitelisted_ast_nodes():
    from matbot.mathcheck import evaluate_candidates, _Unsupported
    # Podrška za školsko dijeljenje ':' ne smije pretvoriti Python dict
    # sintaksu "{1:2}" u aritmetiku "(1/2)". Samostalne vitičaste zagrade s
    # dvotačkom ostaju nepodržane; obično matematičko grupisanje koristi ().
    for expr in ["__import__", "open(1)", "[1,2]", "(1,2)", "{1:2}", "a.b"]:
        with pytest.raises(Exception):
            evaluate_candidates(expr)


def test_colon_inside_recognized_latex_argument_still_works():
    """Uska code-syntax zabrana ne smije blokirati pravi LaTeX argument."""
    from matbot.mathcheck import evaluate_candidates

    assert evaluate_candidates(r"\frac{60:15}{2}") == [2.0]


# ---------------------------------------------------------------------------
# School π convention (live-verified: model computes with 3,14)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$B=\\pi\\cdot3^2=9\\pi\\approx28,26\\,\\text{cm}^2$",
    "$M=\\pi\\cdot3\\cdot5=15\\pi\\approx47,10\\,\\text{cm}^2$",
    "$P=\\pi\\cdot3(3+5)=24\\pi\\approx75,36\\,\\text{cm}^2$",
    "$P=4\\pi\\cdot6^2=452,16\\,\\text{cm}^2$",
])
def test_school_pi_314_approximations_are_accepted(text):
    assert not rejects(text), text


# ---------------------------------------------------------------------------
# Mixed numbers (regression: 1\frac{3}{20} is 1+3/20, not 1*3/20)
# ---------------------------------------------------------------------------

def test_mixed_number_is_addition_not_multiplication():
    assert not rejects("$\\frac{23}{20}=1\\frac{3}{20}$")
    assert not rejects("$2\\frac{1}{3}=\\frac{7}{3}$")
    assert rejects("$\\frac{23}{20}=1\\frac{5}{20}$")


def test_sqrt_after_digit_is_still_multiplication():
    assert not rejects("$24\\sqrt{3}\\approx41,57$")


# ---------------------------------------------------------------------------
# Chains and segments
# ---------------------------------------------------------------------------

def test_long_valid_chain_is_accepted():
    assert not rejects("$s=\\sqrt{3^2+4^2}=\\sqrt{9+16}=\\sqrt{25}=5\\,\\text{cm}$")


def test_rational_exact_mismatch_is_rejected():
    assert rejects("$7/2=3$")


def test_rounded_equality_with_irrational_is_tolerated():
    assert not rejects("$\\sqrt{2}=1,41$")


def test_math_segments_extraction():
    assert math_segments("Tekst $1+1=2$ i $3+3=6$.") == ["1+1=2", "3+3=6"]
    assert math_segments("Bez matematike.") == []


def test_is_numerically_consistent_helper():
    assert is_numerically_consistent("$2+2=4$")
    assert not is_numerically_consistent("$2+2=5$")


# ---------------------------------------------------------------------------
# 19-22. Integration through the three modes
# ---------------------------------------------------------------------------

def _explain_turn(grade=8, topic="8-08-005"):
    return {"grade": grade, "selected_topic": topic, "selected_oblast": "",
            "student_message": "Objasni mi.", "interaction_phase": "",
            "conversation_history": [], "last_tutor_message": ""}


def _quick_turn(grade=8, topic="8-08-005"):
    return {"grade": grade, "selected_topic": topic, "selected_oblast": "",
            "student_message": "Izračunaj.", "interaction_phase": "",
            "conversation_history": []}


def test_explain_exact_live_wrong_response_returns_safe_message():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply=f"Primjer: {LIVE_WRONG}"))
    r = run_explain_turn(fake, _explain_turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert fake.call_count == 1  # bez popravnog poziva


def test_explain_corrected_response_is_accepted_unchanged():
    fake = FakeLLM()
    reply = f"Primjer: {LIVE_RIGHT}"
    fake.queue(make_explain_output(reply=reply))
    r = run_explain_turn(fake, _explain_turn())
    assert r["answer"] == reply
    assert fake.call_count == 1


def test_quick_inconsistent_equality_is_rejected():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$3\\cdot16/2=48$"))
    r = run_quick_turn(fake, _quick_turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1


def test_quick_consistent_equality_is_accepted():
    fake = FakeLLM()
    reply = "$3\\cdot16/2=24$"
    fake.queue(make_quick_output(reply=reply))
    r = run_quick_turn(fake, _quick_turn())
    assert r["answer"] == reply


def _practice_payload(msg="Daj zadatak.", **kw):
    base = {"session_id": "sess-mathcheck", "grade": 6, "selected_topic": "6-04-007",
            "selected_oblast": "", "student_message": msg, "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "", "selected_option_id": "", "client_turn_id": ""}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Faza A2 (docs/CURRENT_STATE.md C-4): bosansko školsko dijeljenje "a:b" —
# ranije nije bilo podržano ("60:15=5" je bio tiho preskočen, ne provjeren).
# ---------------------------------------------------------------------------

def test_case11_simple_colon_division_correct_passes():
    assert find_numeric_inconsistencies("$60:15=4$") == []


def test_case12_simple_colon_division_wrong_rejects():
    issues = find_numeric_inconsistencies("$60:15=5$")
    assert issues
    assert "60:15" in issues[0]


def test_case13_another_colon_division_correct_passes():
    assert find_numeric_inconsistencies("$72:9=8$") == []


def test_case13b_another_colon_division_wrong_rejects():
    assert find_numeric_inconsistencies("$72:9=7$")


def test_case14_decimal_comma_colon_division_correct_passes():
    assert find_numeric_inconsistencies("$3,5:0,5=7$") == []


def test_case15_decimal_comma_colon_division_wrong_rejects():
    assert find_numeric_inconsistencies("$3,5:0,5=8$")


def test_case16_prose_time_outside_math_is_ignored():
    # mathcheck SAMO ikad gleda sadržaj unutar $...$/$$...$$ (vidi
    # math_segments) — "12:30" u običnoj prozi nikad i ne ulazi u provjeru.
    assert find_numeric_inconsistencies("Sastanak je zakazan za 12:30 popodne.") == []


def test_case17_colon_in_non_math_prose_is_ignored():
    assert find_numeric_inconsistencies("Napomena: ovo je važno, provjeri dvaput.") == []


def test_case18_no_unrestricted_eval_colon_division_by_zero_is_math_error_not_crash():
    # dijeljenje nulom kroz ':' mora proći isti siguran put kao '/' — _MathError,
    # NIKAD eval() i NIKAD nekontrolisan izuzetak koji izlazi iz check_segment.
    issues = find_numeric_inconsistencies("$60:0=0$")
    assert issues
    assert "dijeljenje nulom" in issues[0]


def test_colon_division_with_parentheses():
    assert find_numeric_inconsistencies("$(24+6):5=6$") == []
    assert find_numeric_inconsistencies("$(24+6):5=5$")


def test_colon_ratio_without_equality_is_not_falsely_rejected():
    # bez relacije (=/≈) nema šta da se uporedi — čist odnos ostaje neprovjeren,
    # ne pogrešno odbijen.
    assert find_numeric_inconsistencies("$3:4$ je razmjer.") == []


def test_colon_equivalent_ratios_with_equality_checked_consistently():
    assert find_numeric_inconsistencies("$3:4=6:8$") == []
    assert find_numeric_inconsistencies("$3:4=6:9$")


# ---------------------------------------------------------------------------
# ANOTACIJA „broj: zbir njegovih cifara“ (živi gate b7025e4, lekcija 6-03-004
# „Pravila djeljivosti…“). Dvotačka je tu OZNAKA broja, ne dijeljenje: tačan
# odgovor `$12:\;1+2=3$` je bio odbijen jer je lijeva strana računata kao
# 12/1+2 = 14.
# ---------------------------------------------------------------------------

def test_live_digit_sum_annotation_is_not_read_as_division():
    """Tačan živi string koji je pao na gateu mora proći."""
    assert find_numeric_inconsistencies("$12:\\;1+2=3$") == []


@pytest.mark.parametrize("text", [
    "$12:\\;1+2=3$",
    "$135:\\;1+3+5=9$",
    "$405:\\;4+0+5=9$",
    "$10:\\;1+0=1$",            # cifra 0 među sabircima
    "$999:\\;9+9+9=27$",        # ponovljene cifre
    "$12: 1+2=3$",              # bez LaTeX razmaka
    "$12:1+2=3$",               # bez ijednog razmaka
    "$12:\\,1+2=3$",            # druga LaTeX komanda za razmak
    "$12:\\quad 1+2=3$",
])
def test_valid_digit_sum_annotation_passes(text):
    assert find_numeric_inconsistencies(text) == []


@pytest.mark.parametrize("text", [
    "$12:\\;1+2=4$",            # zbir cifara je 3, ne 4
    "$135:\\;1+3+5=10$",        # zbir cifara je 9, ne 10
    "$405:\\;4+0+5=10$",
])
def test_wrong_digit_sum_annotation_is_still_rejected(text):
    assert find_numeric_inconsistencies(text)


def test_sum_that_is_not_the_prefix_digits_stays_division():
    """`1+3` NISU cifre broja 12 → nema anotacije, dvotačka ostaje dijeljenje.

    Bez ovoga bi „obriši sve prije dvotačke“ proglasilo `12:1+3=4` tačnim samo
    zato što je 1+3=4."""
    issues = find_numeric_inconsistencies("$12:\\;1+3=4$")
    assert issues
    assert "(15)" in issues[0]   # 12/1+3 — stvarno pročitano kao dijeljenje


def test_same_digits_in_wrong_order_stay_division():
    """Redoslijed cifara je dio dokaza: `2+1` nije dekompozicija broja 12."""
    assert find_numeric_inconsistencies("$12:\\;2+1=3$")


def test_single_digit_prefix_stays_division():
    """`$5:5=1$` je ispravno dijeljenje — prag od dvije cifre ga čuva.

    Da je prag jedna cifra, cifre `[5]` bi se poklopile sa sabircima `[5]`,
    izraz bi postao zbir 5 i tačno dijeljenje bi bilo lažno odbijeno."""
    assert find_numeric_inconsistencies("$5:5=1$") == []
    assert find_numeric_inconsistencies("$5:5=2$")


@pytest.mark.parametrize("text,expected_ok", [
    ("$12:3=4$", True), ("$12:3=5$", False),
    ("$12 : 3 = 4$", True), ("$12 : 3 = 5$", False),
    ("$20:5=4$", True), ("$20:5=5$", False),
    ("$60:15=4$", True), ("$60:15=5$", False),
    ("$3,5:0,5=7$", True), ("$3,5:0,5=8$", False),
    ("$(24+6):5=6$", True), ("$(24+6):5=5$", False),
])
def test_genuine_colon_division_is_unaffected(text, expected_ok):
    assert (find_numeric_inconsistencies(text) == []) is expected_ok


def test_annotation_inside_surrounding_prose():
    text = ("Broj $12$ nije djeljiv sa $9$ jer je zbir cifara "
            "$12:\\;1+2=3$, a $3$ nije djeljivo sa $9$.")
    assert find_numeric_inconsistencies(text) == []


def test_multiple_annotated_checks_in_one_solution_all_pass():
    text = ("Provjeri zbirove cifara: $135:\\;1+3+5=9$, zatim $405:\\;4+0+5=9$ "
            "i na kraju $12:\\;1+2=3$.")
    assert find_numeric_inconsistencies(text) == []


def test_one_wrong_equality_among_several_valid_annotations_is_caught():
    text = ("Provjeri: $135:\\;1+3+5=9$, pa $405:\\;4+0+5=8$, pa $12:\\;1+2=3$.")
    issues = find_numeric_inconsistencies(text)
    assert len(issues) == 1
    assert "405" in issues[0]


@pytest.mark.parametrize("text", [
    "$12:\\\\;1+2=3$",     # ZAOSTALA dvostruka kosa crta prije komande
    "$12\\\\quad+3=15$",
    "$12:\\ty 1+2=3$",     # nepoznata kontrolna riječ
])
def test_unknown_or_doubled_backslash_still_skips_as_before(text):
    """Prepoznavanje anotacije NE SMIJE nikom drugom promijeniti put.

    Sonda za razmake radi samo unutar `_strip_digit_sum_annotation`; izraz koji
    nije anotacija stiže u `_latex_to_python` doslovno kakav je i dosad stizao,
    pa zaostala dvostruka kosa crta i nepoznata komanda ostaju „nepodržano“ i
    tiho se preskaču (a u produkciji ih `mathsafe` odbije prije ove provjere).
    Ranija verzija ove popravke je razmake skidala PRIJE poziva i time je
    `\\\\quad` postajao običan razmak — tihi gubitak zatečenog ponašanja."""
    assert find_numeric_inconsistencies(text) == []


# ---------------------------------------------------------------------------
# NAMJERNO LAŽNA JEDNAKOST — dokaz kontradikcije (živi release gate 5ac723e,
# scenario grade9, lekcija 9-05-010 „Sistem bez rješenja“)
# ---------------------------------------------------------------------------
# Rješenje sistema bez rješenja MORA prikazati lažnu jednakost ($3=5$, $0=2$)
# da bi dokazalo kontradikciju. Validator je takav segment tretirao kao
# aritmetičku grešku, recenzent nije imao ispravku koja zadržava smisao
# lekcije, i CIJELA lekcija je postala neobjavljiva — fail closed na svakom
# pokušaju. Lažna jednakost se priznaje SAMO kad je ISTA ili anaforična
# rečenica izričito proglašava netačnom.

@pytest.mark.parametrize("text", [
    # marker u istoj rečenici, POSLIJE segmenta
    "Iz obje jednačine izraz $x+y$ bi morao biti i $3$ i $5$, pa bi slijedilo "
    "$3=5$, što nije tačno. Sistem nema rješenja.",
    "Oduzimanjem jednačina dobijamo $0=2$, što je nemoguće, pa sistem nema rješenja.",
    "Dobili bismo $3=5$, a to ne važi ni za jedan par brojeva.",
    # marker u SLJEDEĆOJ rečenici koja počinje anaforom („To je…“)
    "Kada oduzmemo prvu jednačinu od druge: $0 = 2$. To je kontradikcija, "
    "pa sistem nema rješenja.",
    # marker u istoj rečenici, PRIJE segmenta
    "Slijedi netačna jednakost $3=5$, pa sistem nema rješenja.",
    "Dobili bismo nemoguću jednakost $0=2$, pa sistem nema rješenja.",
    # display oblik
    "Oduzimanjem dobijamo $$0=2$$, što je nemoguće, pa sistem nema rješenja.",
])
def test_declared_false_contradiction_is_accepted(text):
    assert not rejects(text), text


@pytest.mark.parametrize("text", [
    # bez ijednog markera lažnosti — ostaje odbijeno (konzervativno)
    "Dobili bismo $3=5$.",
    # marker u DRUGOJ rečenici BEZ anafore (stil ocjene odgovora) — pogrešan
    # lanac modela mora ostati odbijen, „Netačno.“ se odnosi na učenika
    "Netačno. Pravilan postupak: $17-9=7$.",
    "Pravilan postupak: $17-9=7$. Tvoj odgovor je netačan.",
    # aproksimativni lanac se NIKAD ne proglašava „namjerno lažnim“
    "$24\\sqrt3\\approx83,14$, što nije tačno.",
    # potvrdna riječ bez negacije nije marker
    "Provjera: $2+3=6$, dakle tačno.",
])
def test_wrong_chains_near_unrelated_negation_stay_rejected(text):
    assert rejects(text), text


def test_declared_false_suppression_never_hides_other_segments():
    """Lažna jednakost s markerom NE amnestira drugi, stvarno pogrešan segment."""
    text = ("Dobijamo $3=5$, što nije tačno, pa sistem nema rješenja. "
            "Provjera zbira: $2+3=6$.")
    issues = find_numeric_inconsistencies(text)
    assert len(issues) == 1
    assert "2+3" in issues[0]


def test_annotation_does_not_disturb_ordinary_arithmetic():
    """Ostale operacije moraju ostati tačno onakve kakve su bile."""
    for ok in ("$2+3=5$", "$7-4=3$", "$6\\cdot7=42$", "$\\frac{3}{4}=0,75$",
               "$2^3=8$", "$1,5+2,5=4$", "$-3+(-4)=-7$", "$\\sqrt{16}=4$"):
        assert find_numeric_inconsistencies(ok) == [], ok
    # `$\frac{3}{4}=0,9$`, ne `0,8`: tolerancija se izvodi iz preciznosti
    # decimalnog literala, pa je 0,8 legitimno zaokruženje broja 0,75 na jednu
    # decimalu (zatečeno ponašanje `_tolerance`, nedirnuto ovom izmjenom).
    for bad in ("$2+3=6$", "$7-4=4$", "$6\\cdot7=41$", "$\\frac{3}{4}=0,9$",
                "$2^3=9$", "$1,5+2,5=5$", "$-3+(-4)=-1$", "$\\sqrt{16}=5$"):
        assert find_numeric_inconsistencies(bad), bad


# ---------------------------------------------------------------------------
# JEDINICE U LANCU JEDNAKOSTI (živi F5D talas, scenario D23)
# ---------------------------------------------------------------------------
# Model je na slobodno pitanje o pretvaranju jedinica odgovorio matematički
# TAČNOM jednakošću "$1\,\text{cm} = 10\,\text{mm}$", a mathcheck je jedinice
# uklonio i uporedio gole brojeve 1 != 10 — ispravan odgovor je pao zatvoreno.
# Pravilo: kad OBJE strane nose jedinice a skupovi se razlikuju, jednakost je
# iskaz PRETVARANJA čiju istinitost modul ne može numerički dokazati →
# preskače se (preskočeno nije dokaz ispravnosti). Jedinica samo uz REZULTAT
# je anotacija i provjerava se kao i dosad.

def test_unit_conversion_equality_is_skipped_not_rejected():
    from matbot.mathcheck import find_numeric_inconsistencies
    assert find_numeric_inconsistencies(
        "Jedan centimetar ima deset milimetara: "
        r"$1\,\text{cm} = 10\,\text{mm}$.") == []
    assert find_numeric_inconsistencies(
        r"$1\,\text{m}^2 = 10000\,\text{cm}^2$") == []
    assert find_numeric_inconsistencies(
        r"$1\,\mathrm{m} = 100\,\mathrm{cm}$") == []
    # Ista jedinica, različit eksponent = različita jedinica → pretvaranje.
    assert find_numeric_inconsistencies(
        r"$1\,\text{m}^2 = 100\,\text{m}$") == []


def test_same_unit_and_annotation_equalities_are_still_checked():
    from matbot.mathcheck import find_numeric_inconsistencies
    assert find_numeric_inconsistencies(
        r"$2\,\text{cm} + 3\,\text{cm} = 5\,\text{cm}$") == []
    assert find_numeric_inconsistencies(
        r"$2\,\text{cm} + 3\,\text{cm} = 6\,\text{cm}$") != []
    # Bez jedinica: gola lažna jednakost i dalje pada.
    assert find_numeric_inconsistencies("$1 = 10$") != []
    # Jedinica SAMO uz rezultat je anotacija — pogrešan račun i dalje pada
    # (osnivački slučaj ovog modula).
    assert find_numeric_inconsistencies(
        r"$V=\frac{25\cdot9}{3}=70\,\text{cm}^3$") != []
    assert find_numeric_inconsistencies(
        r"$V=\frac{25\cdot9}{3}=75\,\text{cm}^3$") == []


# ---------------------------------------------------------------------------
# ŠKOLSKI ZAPIS DIJELJENJA S OSTATKOM (živi release gate 2a2a204, harder_level2)
# ---------------------------------------------------------------------------
# Tutor i Recenzent su za tekstualni zadatak napisali pedagoški ISPRAVNO
# "$23 : 5 = 4$, a ostatak je $3$", a modul je pročitao golu lažnu jednakost
# 23/5 = 4 i oborio oba poziva. Zapis se priznaje SAMO kad je POTPUNO DOKAZAN:
# dijeljenje nije egzaktno, količnik je tačno cjelobrojni količnik, a ostatak
# je naveden uz segment i tačno iznosi a - b·q. Sve ostalo i dalje pada.

def test_verified_remainder_division_is_accepted():
    from matbot.mathcheck import find_numeric_inconsistencies
    assert find_numeric_inconsistencies(
        "Podijelimo olovke: $23 : 5 = 4$, a ostatak je $3$.") == []
    assert find_numeric_inconsistencies(
        "Dakle $23 : 5 = 4$ (ostatak $3$), pa treba pet kutija.") == []
    assert find_numeric_inconsistencies(
        "Količnik: $23 : 5 = 4$. Ostatak je $3$ olovke.") == []


def test_unproven_or_false_remainder_division_still_fails():
    from matbot.mathcheck import find_numeric_inconsistencies
    # Gola lažna jednakost bez ostatka u blizini.
    assert find_numeric_inconsistencies("$23 : 5 = 4$") != []
    # Pogrešan ostatak.
    assert find_numeric_inconsistencies(
        "$23 : 5 = 4$, a ostatak je $4$.") != []
    # Pogrešan količnik (nije cjelobrojni količnik).
    assert find_numeric_inconsistencies(
        "$23 : 5 = 5$, a ostatak je $3$.") != []
    # Egzaktno dijeljenje s "ostatkom" je i dalje greška.
    assert find_numeric_inconsistencies(
        "$20 : 5 = 3$, a ostatak je $5$.") != []
    # Približenje se nikad ne amnestira ovim mehanizmom.
    assert find_numeric_inconsistencies(
        r"$23 : 5 \approx 4$, a ostatak je $3$.") != []


def test_remainder_stated_sentences_later_and_verb_forms_are_verified():
    """ŽIVI F5E TALAS: model ostatak navodi i dalje od samog dijeljenja, i
    raznim glagolskim oblicima — egzaktan a - b·q je zaštita, ne blizina."""
    from matbot.mathcheck import find_numeric_inconsistencies
    assert find_numeric_inconsistencies(
        "Podijelimo: $30 : 4 = 7$. Dakle, Ivana će napuniti $7$ kutija. "
        "Nakon toga ostaju joj $2$ čokoladice.") == []
    assert find_numeric_inconsistencies(
        "Računamo $22 : 6 = 3$ pune kutije. Na kraju će ostati $4$ olovke.") == []
    assert find_numeric_inconsistencies(
        "Kad podijelimo, $23 : 5 = 4$ i preostane $3$.") == []
    # Pogrešan broj uz riječ ostatka i dalje ne amnestira.
    assert find_numeric_inconsistencies(
        "Podijelimo: $30 : 4 = 7$. Nakon toga ostaju joj $3$ čokoladice.") != []


def test_remainder_division_with_inline_annotation_is_verified():
    """ŽIVI F5E RERUN (E02): količnik s anotacijom — oba poziva su bila
    matematički TAČNA (47:6=7 ost. 5; 53:7=7 ost. 4) i oba su padala."""
    from matbot.mathcheck import find_numeric_inconsistencies
    assert find_numeric_inconsistencies(
        r"Podijelimo: $47 : 6 = 7\text{ (punih paketa)}$. "
        r"Nakon punjenja ostaje $5$ čokoladica.") == []
    assert find_numeric_inconsistencies(
        r"$53 : 7 = 7\,\text{kutija}$, a preostaju $4$ olovke.") == []
    # Anotacija ne amnestira pogrešan ostatak.
    assert find_numeric_inconsistencies(
        r"$47 : 6 = 7\text{ (punih paketa)}$. Ostaje $6$ čokoladica.") != []


# ---------------------------------------------------------------------------
# UVRŠTAVANJE PRIBLIŽNOG KORIJENA KAO FAKTORA (živi release gate 949e608,
# rotirajuća lekcija 8. razreda — zapremina piramide)
# ---------------------------------------------------------------------------

def test_rounded_root_as_factor_is_accepted_scaled_by_the_cofactor():
    from matbot.mathcheck import find_numeric_inconsistencies
    # Tutorov zapis i recenzentova preciznija ispravka — oba školski tačna.
    assert find_numeric_inconsistencies(r"$27\sqrt{3} = 27\cdot1,732$") == []
    assert find_numeric_inconsistencies(r"$27\sqrt{3} = 27\cdot1,73205$") == []
    assert find_numeric_inconsistencies(r"$27\sqrt{3} \approx 46,77$") == []


def test_founding_irrational_failures_still_fail():
    from matbot.mathcheck import find_numeric_inconsistencies
    # Osnivački slučaj modula: pogrešan koeficijent uz korijen i dalje pada.
    assert find_numeric_inconsistencies(r"$24\sqrt{3} \approx 83,14$") != []
    # Čista decimalna aritmetika se NE skalira — greška ostaje greška.
    assert find_numeric_inconsistencies("$0,3 + 0,25 = 0,54$") != []
    # Deklarisana vrijednost π i dalje obavezuje (D35-2).
    assert find_numeric_inconsistencies(
        r"Uzmi $\pi \approx 3,14$. Tada je $6\pi \approx 18,85$.") != []
    # Grubo pogrešno uvrštavanje korijena pada i sa skaliranjem.
    assert find_numeric_inconsistencies(r"$27\sqrt{3} = 27\cdot1,8$") != []
