# -*- coding: utf-8 -*-
"""Grupisane hiljade u LaTeX zapisu: `10\\,000` je JEDAN broj (matbot/mathcheck.py).

ŽIVI NALAZ (verifikacija Kontrolnog, 9. razred, lekcija 9-08-011): objavljeno je
OCJENJIVANO pitanje „2,4 m² u cm²“ sa označenim NETAČNIM odgovorom
`2\\,400\\,\\text{cm}^2`, dok je tačna opcija `24\\,000\\,\\text{cm}^2` stajala
među ponuđenima. Rješenje je sadržavalo doslovno netačnu jednakost:

    $2,4\\cdot10\\,000\\,\\text{cm}^2=2\\,400\\,\\text{cm}^2$

Uzrok: `\\,` se pretvarao u RAZMAK, pa je `10\\,000` postajalo „10 000“ — dva
broja razdvojena razmakom, dakle neparsabilan izraz. `evaluate_candidates` je
dizao `_Unsupported`, a `check_segment` je par PRESKAKAO. Mjereno na roditelju:
`$2,4\\cdot10000=2400$` se hvata, `$2,4\\cdot10\\,000=2\\,400$` ne.

Posljedica je bila da NIJEDAN broj pisan s razdvojenim hiljadama nikad nije
prošao provjeru numeričke dosljednosti — a to je kućni stil ovog projekta
(deterministički generatori pišu `35\\,000`).
"""
import pytest

from matbot import mathcheck

TS = "\\,"          # tanki razmak
CDOT = "\\cdot"


def rejected(text):
    return bool(mathcheck.find_numeric_inconsistencies(text))


# ---------------------------------------------------------------------------
# PARSIRANJE: grupisan broj mora dobiti SVOJU vrijednost
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr,value", [
    ("10" + TS + "000", 10000),
    ("24" + TS + "000", 24000),
    ("240" + TS + "000", 240000),
    ("1" + TS + "000" + TS + "000", 1000000),
    ("1" + TS + "234" + TS + "567", 1234567),
    ("12" + TS + "500+7" + TS + "500", 20000),
    ("2,4" + CDOT + "10" + TS + "000", 24000),
])
def test_grouped_thousands_evaluate_to_their_value(expr, value):
    assert mathcheck.evaluate_candidates(expr)[0] == pytest.approx(value)


# ---------------------------------------------------------------------------
# TAČNE JEDNAKOSTI — moraju proći
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment", [
    "$2,4" + CDOT + "10" + TS + "000=24" + TS + "000$",
    "$3,5" + CDOT + "1" + TS + "000=3" + TS + "500$",
    "$12" + TS + "500+7" + TS + "500=20" + TS + "000$",
    "$100" + TS + "000:10=10" + TS + "000$",
    "$50" + TS + "000-25" + TS + "000=25" + TS + "000$",
    "$1" + TS + "234" + TS + "567+1=1" + TS + "234" + TS + "568$",
])
def test_correct_grouped_equalities_are_accepted(segment):
    assert not rejected(segment)


# ---------------------------------------------------------------------------
# NETAČNE JEDNAKOSTI — moraju pasti (ovo je bio slijepi ugao)
# ---------------------------------------------------------------------------

def test_the_live_kontrolni_equality_is_now_rejected():
    """Tačna jednakost iz objavljenog ocjenjivanog pitanja."""
    assert rejected("$2,4" + CDOT + "10" + TS + "000=2" + TS + "400$")


@pytest.mark.parametrize("segment", [
    "$3,5" + CDOT + "1" + TS + "000=350$",
    "$12" + TS + "500+7" + TS + "500=2" + TS + "000$",
    "$100" + TS + "000:10=1" + TS + "000$",
    "$50" + TS + "000-25" + TS + "000=2" + TS + "500$",
])
def test_false_grouped_equalities_are_rejected(segment):
    assert rejected(segment)


def test_units_do_not_hide_the_false_equality():
    """Oblik iz živog nalaza nosi jedinice na obje strane."""
    assert rejected("$2,4" + CDOT + "10" + TS + "000" + TS + "\\text{cm}^2"
                    "=2" + TS + "400" + TS + "\\text{cm}^2$")


# ---------------------------------------------------------------------------
# DECIMALNI ZAREZ SE NE SMIJE PROMIJENITI — pravilo gleda ISKLJUČIVO `\,`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment,bad", [
    ("$2,4+0,6=3$", False), ("$2,4+0,6=30$", True),
    ("$0,75" + CDOT + "4=3$", False), ("$0,75" + CDOT + "4=30$", True),
    ("$12,50+0,50=13$", False),
    ("$0,004" + CDOT + "1000=4$", False), ("$0,004" + CDOT + "1000=40$", True),
])
def test_decimal_comma_semantics_unchanged(segment, bad):
    assert rejected(segment) is bad


def test_decimal_number_is_never_collapsed():
    """`2,4` ostaje 2,4 — nikad 24."""
    assert mathcheck.evaluate_candidates("2,4")[0] == pytest.approx(2.4)


# ---------------------------------------------------------------------------
# MALFORMISANO GRUPISANJE — ne postaje „čarobno validno"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr", [
    "1" + TS + "00",            # grupa od 2 cifre
    "10" + TS + "00",
    "1" + TS + "0000",          # grupa od 4 cifre
    "1" + TS + "234" + TS + "56",
    "12" + TS + "3456",
])
def test_malformed_grouping_stays_unparsed(expr):
    """Nedokazivo se ne spaja — ostaje neparsabilno i preskače se, kao i ranije."""
    with pytest.raises(Exception):
        mathcheck.evaluate_candidates(expr)


# ---------------------------------------------------------------------------
# TIPOGRAFSKI RAZMAK NIJE GRUPISANJE CIFARA
# ---------------------------------------------------------------------------

def test_thin_space_around_operators_is_still_spacing():
    assert not rejected("$2,4" + TS + CDOT + TS + "10" + TS + "000=24" + TS + "000$")
    assert rejected("$2,4" + TS + CDOT + TS + "10" + TS + "000=2" + TS + "400$")


def test_thin_space_before_units_is_still_spacing():
    assert not rejected("$24" + TS + "000" + TS + "\\mathrm{cm}^2"
                        "=24" + TS + "000" + TS + "\\mathrm{cm}^2$")


def test_unit_conversion_claims_are_still_skipped():
    """Živi nalaz D23: različite jedinice na obje strane → modul ne dokazuje."""
    assert not rejected("$1" + TS + "\\text{m}=100" + TS + "\\text{cm}$")
    assert not rejected("$1" + TS + "\\text{dm}^3=1" + TS + "000" + TS + "\\text{cm}^3$")


# ---------------------------------------------------------------------------
# UGRAĐIVANJE: nema modela, nema promjene granice poziva
# ---------------------------------------------------------------------------

def test_fix_adds_no_model_call():
    assert not hasattr(mathcheck, "llm")
    assert rejected("$2,4" + CDOT + "10" + TS + "000=2" + TS + "400$")


def test_plain_ungrouped_forms_behave_exactly_as_before():
    assert rejected("$2,4" + CDOT + "10000=2400$")
    assert not rejected("$2,4" + CDOT + "10000=24000$")
    assert not rejected("$3/4=0,75$")
    assert rejected("$2+2=5$")
    assert not rejected("$2+2=4$")


# ---------------------------------------------------------------------------
# KONTROLNI: TAČAN PAKET IZ ŽIVOG NALAZA MORA PASTI PRIJE OBJAVE
# ---------------------------------------------------------------------------

def _kontrolni_package(solution, marked_index):
    import types
    return types.SimpleNamespace(
        slot=1, lesson_id="9-08-011", difficulty="easy",
        text=("Kolika je površina izražena u kvadratnim centimetrima "
              "ako je data površina $2,4" + TS + "\\text{m}^2$?"),
        options=["$24" + TS + "000" + TS + "\\text{cm}^2$",
                 "$2" + TS + "400" + TS + "\\text{cm}^2$",
                 "$240" + TS + "\\text{cm}^2$",
                 "$24" + TS + "\\text{cm}^2$"],
        correct_option_index=marked_index,
        expected_answer=("$24" + TS + "000" + TS + "\\text{cm}^2$" if marked_index == 0
                         else "$2" + TS + "400" + TS + "\\text{cm}^2$"),
        solution=solution)


_SOL_BAD = ("Pošto je $1" + TS + "\\text{m}=100" + TS + "\\text{cm}$, za površinu se "
            "faktor kvadrira: $1" + TS + "\\text{m}^2=100^2" + TS + "\\text{cm}^2"
            "=10" + TS + "000" + TS + "\\text{cm}^2$. Zato je $2,4" + TS + "\\text{m}^2"
            "=2,4\\cdot10" + TS + "000" + TS + "\\text{cm}^2=2" + TS + "400" + TS
            + "\\text{cm}^2$.")
_SOL_GOOD = _SOL_BAD.replace("=2" + TS + "400" + TS + "\\text{cm}^2$",
                             "=24" + TS + "000" + TS + "\\text{cm}^2$")


def _slot_and_context():
    from matbot import kontrolni
    slot = {"slot": 1, "lesson_id": "9-08-011",
            "lesson_title": "Pretvaranje mjernih jedinica za dužinu, površinu i zapreminu",
            "difficulty": "easy"}
    return slot, kontrolni._slot_contexts(9, [slot])[1]


def test_live_kontrolni_defect_package_is_now_rejected():
    """Pitanje koje je stiglo do učenika s POGREŠNIM ključem više ne prolazi."""
    from matbot import kontrolni
    slot, ctx = _slot_and_context()
    clean, code = kontrolni.validate_generated_question(
        _kontrolni_package(_SOL_BAD, 1), slot, ctx, ())
    assert clean is None
    assert code == "numeric_inconsistency_solution"


def test_corrected_kontrolni_package_still_publishes():
    """Ispravka ne smije oboriti ISPRAVAN paket iste forme."""
    from matbot import kontrolni
    slot, ctx = _slot_and_context()
    clean, code = kontrolni.validate_generated_question(
        _kontrolni_package(_SOL_GOOD, 0), slot, ctx, ())
    assert clean is not None, code
