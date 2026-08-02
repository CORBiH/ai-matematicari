"""D35-2: kad odgovor IZRIČITO deklariše vrijednost π, svaki kasniji izraz s π
mora biti dosljedan TOJ vrijednosti.

Regresija je vezana za tačan tekst iz poziva 19 kampanje od 35: deklarisano
„π≈3,14“, a zatim napisano „$6\\pi\\approx18,85$“ (18,84 je tačno uz 3,14)."""
import pytest

from matbot.mathcheck import declared_pi_values, find_numeric_inconsistencies

CALL19_INCONSISTENT = "Decimalna aproksimacija s π\\approx3,14 daje $6\\pi\\approx18,85$"
CALL19_CONSISTENT = "Decimalna aproksimacija s π\\approx3,14 daje $6\\pi\\approx18,84$"


def test_no_declaration_accepts_full_pi_rounding():
    assert find_numeric_inconsistencies("Obim je $6\\pi\\approx18,85$") == []


def test_declared_314_accepts_consistent_product():
    assert find_numeric_inconsistencies(CALL19_CONSISTENT) == []


def test_declared_314_rejects_inconsistent_product():
    issues = find_numeric_inconsistencies(CALL19_INCONSISTENT)
    assert issues
    assert issues[0].startswith("numeric_equality_mismatch")


def test_declared_31416_is_used_consistently():
    assert find_numeric_inconsistencies("$π\\approx3,1416$, pa je $6\\pi\\approx18,85$") == []


def test_exact_symbolic_answer_is_not_rejected():
    assert find_numeric_inconsistencies("Obim je $6\\pi\\,\\text{cm}$.") == []
    assert find_numeric_inconsistencies("π\\approx3,14. Obim je $6\\pi\\,\\text{cm}$.") == []


def test_decimal_comma_and_point_both_work():
    assert find_numeric_inconsistencies("$\\pi\\approx3.14$ pa $6\\pi\\approx18.85$")
    assert find_numeric_inconsistencies("$\\pi\\approx3,14$ pa $6\\pi\\approx18,85$")


def test_declared_value_applies_to_explicit_multiplication():
    assert find_numeric_inconsistencies("π\\approx3,14; $6\\cdot3,14=18,84$") == []
    assert find_numeric_inconsistencies("π\\approx3,14; $6\\cdot3,14=18,85$")


@pytest.mark.parametrize("text,expected", [
    ("π\\approx3,14: $-6\\pi\\approx-18,84$", []),
    ("π\\approx3,14: $-2\\pi\\approx-6,28$", []),
])
def test_negative_coefficients_pass_when_consistent(text, expected):
    assert find_numeric_inconsistencies(text) == expected


def test_negative_coefficient_rejected_when_inconsistent():
    assert find_numeric_inconsistencies("π\\approx3,14: $-6\\pi\\approx-18,85$")


def test_a_product_with_pi_is_not_read_as_a_declaration():
    # „$2\pi\approx6,28$“ je izračun, ne izjava o vrijednosti π.
    assert declared_pi_values("$2\\pi\\approx6,28$") == ()
    assert find_numeric_inconsistencies("$2\\pi\\approx6,28$ i $9\\pi\\approx28,26$") == []


def test_implausible_declared_value_is_ignored_not_guessed():
    assert declared_pi_values("π\\approx4,50") == ()


def test_declaration_is_found_in_prose_outside_math():
    assert declared_pi_values("Računamo s π\\approx3,14 u ovom razredu.") == (3.14,)


def test_declaration_is_found_inside_math_too():
    assert declared_pi_values("$\\pi\\approx3,14$") == (3.14,)


def test_no_eval_is_introduced_by_the_pi_extension():
    """Provjera nad AST-om, ne nad tekstom: nijedan poziv ugrađenih
    eval/exec/compile ne smije postojati u modulu."""
    import ast
    import inspect

    from matbot import mathcheck

    tree = ast.parse(inspect.getsource(mathcheck))
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"eval", "exec", "compile", "__import__"}
