r"""Recenzent mora znati KAKO da popravi nečitljiv uslov djeljivosti.

ŽIVI CILJANI TALAS F4E (commit 34a2fe8, 48 poziva, scenariji E01 i E12):

    E01  nacrt      : divisibility_condition_ambiguous
         recenzent  : correct
         konačno    : divisibility_condition_ambiguous  (unchanged=True)
         objavljeno : ne → sigurna poruka

    E12  nacrt      : difficulty_evidence_outside_target
         recenzent  : correct  (težina popravljena)
         konačno    : divisibility_condition_ambiguous  (unchanged=False)
         objavljeno : ne → sigurna poruka

`unchanged=True` u E01 je dokaz: recenzent je vidio nalaz, vratio `correct` i
proizveo paket s POTPUNO ISTIM nalazom — dakle nije znao šta da promijeni.

UZROK: `format_for_reviewer` nabraja lijek za duple opcije, dokaz težine,
nepotpun tekst i nesiguran zapis — ali NIJEDAN za kodove uskog matematičkog
orakla. Recenzent dobije goli kod `divisibility_condition_ambiguous` bez ijedne
riječi o tome šta je nečitljivo ni kako se to piše ispravno. Faza 4E je taj kod
učinila znatno dostupnijim (uslov mora biti DOKAZANO potpun), pa je nedostatak
uputstva postao stalan izvor sigurnih poruka.

ORAKL SE NE SLABI. Prag ostaje identičan — mijenja se samo to što recenzent
sada dobije izvodljivo uputstvo i može popraviti paket u ISTOM (drugom i
posljednjem) pozivu, kako je `correct` i zamišljen.
"""
import pytest

from matbot import mcq_integrity
from matbot.tutor import package_preflight
from tests.conftest import make_task_payload

AMBIGUOUS = "Koji od sljedećih brojeva je djeljiv sa 6 i istovremeno sa 25?"
ZERO_CORRECT = "Koji od sljedećih brojeva je djeljiv i sa 6 i sa 25?"
BAD_OPTIONS = ("8", "6", "7", "9")


def _issues(text, options=BAD_OPTIONS, marked=1):
    return package_preflight.collect_package_issues(
        make_task_payload(text=text, options=options, correct_option_index=marked,
                          expected=options[marked]))


def _block(text, **kwargs):
    return package_preflight.format_for_reviewer(_issues(text, **kwargs))


# ---------------------------------------------------------------------------
# 1. NALAZ MORA REĆI ŠTA JE SERVER STVARNO PROČITAO
# ---------------------------------------------------------------------------

def test_ambiguous_condition_issue_reports_the_divisors_the_server_could_read():
    issues = _issues(AMBIGUOUS)
    described = package_preflight.describe_issues(issues)
    assert "divisibility_condition_ambiguous" in described
    # Server je pročitao samo „6“ — recenzent mora vidjeti gdje je stao.
    assert "6" in described, described


def test_issue_detail_never_carries_the_task_text_or_options():
    """Dijagnostika ostaje bez sadržaja (CLAUDE.md, pravilo 7)."""
    described = package_preflight.describe_issues(_issues(AMBIGUOUS))
    assert "djeljiv" not in described.lower()
    assert "sljede" not in described.lower()
    for option in BAD_OPTIONS:
        assert f"'{option}'" not in described


# ---------------------------------------------------------------------------
# 2. RECENZENT MORA DOBITI IZVODLJIVO UPUTSTVO
# ---------------------------------------------------------------------------

def test_reviewer_block_explains_how_to_repair_an_unreadable_condition():
    block = _block(AMBIGUOUS)
    assert "divisibility_condition_ambiguous" in block
    lowered = block.lower()
    # Mora reći ŠTA da uradi (prepiši pitanje) i KOJI oblik je čitljiv.
    assert "rewrite" in lowered
    assert "djeljiv sa" in lowered, "uputstvo mora pokazati ispravan oblik uslova"


def test_reviewer_block_forbids_the_shapes_that_make_a_condition_unreadable():
    lowered = _block(AMBIGUOUS).lower()
    for forbidden in ("ili", "negat"):
        assert forbidden in lowered, forbidden


def test_reviewer_block_explains_zero_and_multiple_correct_options():
    zero = _block(ZERO_CORRECT).lower()
    assert "no_correct_option" in zero
    assert "exactly one" in zero

    multiple = _block(ZERO_CORRECT, options=("150", "300", "75", "90"), marked=0).lower()
    assert "multiple_correct_options" in multiple


def test_reviewer_block_explains_a_wrong_marked_option():
    block = _block(ZERO_CORRECT, options=("150", "60", "75", "90"), marked=2).lower()
    assert "marked_option_math_mismatch" in block
    assert "expected_answer" in block


# ---------------------------------------------------------------------------
# 3. GRANICE — UPUTSTVO SE POJAVLJUJE SAMO KAD JE RELEVANTNO
# ---------------------------------------------------------------------------

def test_a_clean_package_gets_no_findings_and_no_block():
    issues = _issues(ZERO_CORRECT, options=("150", "60", "75", "90"), marked=0)
    assert issues == ()
    assert package_preflight.format_for_reviewer(issues) == ""


def test_only_the_detected_finding_is_listed_as_a_server_fact():
    """Uputstvo je JEDAN statičan pravilnik (zatečeni oblik ovog bloka), pa se
    lijekovi za sve kodove uvijek navode. Ono što se NE smije pomiješati je
    lista NALAZA: tamo stoji samo ono što je server stvarno dokazao."""
    block = package_preflight.format_for_reviewer(
        package_preflight.collect_package_issues(
            make_task_payload(text="Izračunaj $2+2$.",
                              options=("4", "4", "5", "6"),
                              correct_option_index=0, expected="4")))
    findings = [line for line in block.splitlines() if line.startswith("- ")]
    assert any("duplicate_option_text" in line for line in findings)
    assert not any("divisibility" in line for line in findings), findings


def test_the_oracle_threshold_is_unchanged_by_the_instruction():
    """Uputstvo je tekst prompta — matematički prag ostaje identičan."""
    assert not mcq_integrity.evaluate_divisibility_mcq(AMBIGUOUS, BAD_OPTIONS).valid
    assert not mcq_integrity.evaluate_divisibility_mcq(ZERO_CORRECT, BAD_OPTIONS).valid
    good = mcq_integrity.evaluate_divisibility_mcq(ZERO_CORRECT, ("150", "60", "75", "90"))
    assert good.valid and good.correct_value == 150
