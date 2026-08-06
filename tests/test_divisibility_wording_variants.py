"""Fazа 4G, Workstream D — jezičke varijante složenog uslova djeljivosti.

ŽIVI KONTEKST: produkcijski nalaz (MCQ „djeljiv i sa 6 i sa 25“ bez ijedne
tačne opcije) je već zatvoren granicom potpunosti uslova; živi gate 93ad85c je
zatvorio „istovremeno“ NA POČETKU liste. Vezničke varijante UNUTAR liste
(„i istovremeno sa 25“, „sa brojem 25“, „, ali i sa 25“, „te sa 25“) su i
dalje padale kao `divisibility_condition_ambiguous` iako su za čovjeka
jednoznačno konjunktivne — svaki takav turn je gubio oba poziva.

Skup priznatih veznika ostaje ZATVOREN: svaka riječ je imenovana u regexu,
disjunkcija („ili“) i negacija („nije djeljiv“) i dalje deterministički padaju,
a nepročitan broj u istoj rečenici i dalje znači nedokazan uslov.
"""
from matbot import mcq_integrity

# Tačna opcija mora biti djeljiva i sa 6 i sa 25 — dakle sa NZS(6,25)=150.
# Vrijednost je TESTNI podatak; produkcijski kod uslov provjerava opštim
# `value % divisor == 0` preko svih pročitanih djelilaca, bez ikakvog NZS-a.
OPTIONS_6_25 = ("150", "60", "75", "90")


def evaluate(question, options=OPTIONS_6_25):
    return mcq_integrity.evaluate_divisibility_mcq(question, options)


# ---------------------------------------------------------------------------
# 1) KONJUNKTIVNE VARIJANTE — uslov je potpun i dokaziv
# ---------------------------------------------------------------------------

def test_both_conjuncts_with_repeated_sa():
    result = evaluate("Koji od ponuđenih brojeva je djeljiv i sa 6 i sa 25?")
    assert result.applicable and result.valid
    assert result.divisors == (6, 25)
    assert result.correct_value == 150


def test_istovremeno_inside_the_list():
    result = evaluate("Koji broj je djeljiv sa 6 i istovremeno sa 25?")
    assert result.applicable and result.valid
    assert result.divisors == (6, 25)
    assert result.correct_value == 150


def test_sa_brojem_form():
    result = evaluate("Koji broj je djeljiv sa 6 i sa brojem 25?")
    assert result.applicable and result.valid
    assert result.divisors == (6, 25)
    assert result.correct_value == 150


def test_ali_i_conjunction():
    result = evaluate("Koji broj je djeljiv sa 6, ali i sa 25?")
    assert result.applicable and result.valid
    assert result.divisors == (6, 25)
    assert result.correct_value == 150


def test_te_conjunction():
    result = evaluate("Koji broj je djeljiv sa 6 te sa 25?")
    assert result.applicable and result.valid
    assert result.divisors == (6, 25)
    assert result.correct_value == 150


def test_bare_second_divisor():
    result = evaluate("Koji broj je djeljiv sa 6 i 25?")
    assert result.applicable and result.valid
    assert result.divisors == (6, 25)
    assert result.correct_value == 150


def test_compact_three_divisor_list_still_works():
    result = mcq_integrity.evaluate_divisibility_mcq(
        "Koji broj je djeljiv sa 2, 3 i 5?", ("120", "122", "123", "125"))
    assert result.valid and result.divisors == (2, 3, 5)
    assert result.correct_value == 120


def test_istovremeno_at_list_start_still_works():
    result = evaluate("Koji broj je djeljiv istovremeno sa 25 i sa 6?")
    assert result.applicable and result.valid
    assert result.divisors == (25, 6)
    assert result.correct_value == 150


# ---------------------------------------------------------------------------
# 2) GRANICE — negacija, disjunkcija i nepročitan broj i dalje padaju
# ---------------------------------------------------------------------------

def test_negated_condition_is_never_converted_to_positive():
    result = evaluate("Koji broj je djeljiv sa 10, ali nije djeljiv sa 25?",
                      ("30", "50", "75", "100"))
    assert result.applicable and not result.valid
    assert result.reason_code == "divisibility_condition_ambiguous"


def test_disjunction_still_fails_closed():
    result = evaluate("Koji broj je djeljiv sa 6 ili sa 25?")
    assert result.applicable and not result.valid
    assert result.reason_code == "divisibility_condition_ambiguous"


def test_unread_trailing_number_still_fails_closed():
    result = evaluate("Koji broj je djeljiv sa 6 i sa polovinom broja 50?")
    assert result.applicable and not result.valid
    assert result.reason_code == "divisibility_condition_ambiguous"


def test_unknown_connective_still_fails_closed():
    # „kao i“ NIJE u zatvorenom skupu — uslov ostaje nedokazan.
    result = evaluate("Koji broj je djeljiv sa 6 kao i sa 25?")
    assert result.applicable and not result.valid
    assert result.reason_code == "divisibility_condition_ambiguous"


# ---------------------------------------------------------------------------
# 3) INTEGRITET PAKETA NAD NOVIM VARIJANTAMA
# ---------------------------------------------------------------------------

def test_no_correct_option_for_compound_condition_is_caught():
    # TAČAN živi oblik produkcijskog nalaza: nijedna opcija nije djeljiva sa oba.
    result = evaluate("Koji od sljedećih brojeva je djeljiv i sa 6 i sa 25?",
                      ("8", "6", "7", "9"))
    assert result.applicable and not result.valid
    assert result.reason_code == "no_correct_option"


def test_multiple_correct_options_for_compound_condition_is_caught():
    result = evaluate("Koji broj je djeljiv sa 6 i istovremeno sa 25?",
                      ("150", "300", "75", "90"))
    assert result.reason_code == "multiple_correct_options"


def test_publication_failure_for_wrong_mark_on_new_variant():
    failure, _ = mcq_integrity.publication_failure(
        "Koji broj je djeljiv sa 6 te sa 25?", OPTIONS_6_25, 1, "150")
    assert failure == "marked_option_math_mismatch"


def test_fingerprints_distinguish_different_compound_tasks():
    first = mcq_integrity.evaluate_divisibility_mcq(
        "Koji broj je djeljiv i sa 6 i sa 25?", OPTIONS_6_25)
    second = mcq_integrity.evaluate_divisibility_mcq(
        "Koji broj je djeljiv i sa 6 i sa 25?", ("300", "60", "75", "90"))
    fp1 = mcq_integrity.mathematical_fingerprint(first, "direct_computation")
    fp2 = mcq_integrity.mathematical_fingerprint(second, "direct_computation")
    assert fp1 and fp2 and fp1 != fp2
    same = mcq_integrity.evaluate_divisibility_mcq(
        "Koji od brojeva je djeljiv i sa 6 i sa 25?", OPTIONS_6_25)
    assert mcq_integrity.mathematical_fingerprint(same, "direct_computation") == fp1
