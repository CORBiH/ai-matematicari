# -*- coding: utf-8 -*-
"""U+007F DELETE nikad ne smije stići do učenika (dijeljena math-safety zaštita).

ŽIVI NALAZ (kontrolni, verifikacija poslije izdanja, 7. razred — uglovi u
trouglu): objavljeno pitanje je glasilo

    „U trouglu su unutrašnji uglovi $<U+007F>50^\\circ$, $<U+007F>60^\\circ$ i
     $<U+007F>70^\\circ$. Kolika je mjera najvećeg ugla?“

Tri DELETE znaka u tekstu koji je učenik STVARNO vidio. Postojeća zaštita je
gledala `ord(ch) < 0x20`, a DEL ima `0x7F`, pa je prošao netaknut kroz
`sanitize_and_validate_math_text` (`is_safe=True`) i kroz `safe_visible_text`.

Uzvodni kvar je ista D2 porodica kao U+0000/U+0005/U+0007/U+000C (model umjesto
`\\\\` pošalje važeći JSON escape kontrolnog znaka); razlika je samo što DEL
leži IZA stare granice.

DOKTRINA — NEMA POPRAVKE: `$<DEL>50^\\circ$` se NE prepravlja u `$50^\\circ$`.
Ne zna se šta je znak pojeo, pa paket pada zatvoreno. Zato DEL namjerno NIJE
dodan u listu za uklanjanje u `_repair_control_chars`.

OPSEG (Part 3): dodaje se TAČNO U+007F. C1 blok (U+0080–U+009F) se NE dira —
za njega nema izmjerenog nalaza, a `_test_c1_policy_is_recorded_not_changed`
pribija tu odluku da se ne bi „usput“ proširila.
"""
import json

import pytest

from matbot import mathsafe
from matbot.tutor.package_preflight import safe_visible_text

DEL = chr(0x7F)
BS = chr(92)
CIRC = BS + "circ"

# TACAN objavljeni oblik iz žive kontrolni verifikacije
LIVE_STEM = (
    "U trouglu su unutrašnji uglovi $" + DEL + "50^" + CIRC + "$, $"
    + DEL + "60^" + CIRC + "$ i $" + DEL + "70^" + CIRC
    + "$. Kolika je mjera najvećeg ugla?"
)
CLEAN_STEM = LIVE_STEM.replace(DEL, "")


# ---------------------------------------------------------------------------
# 1) TAČAN ŽIVI SLUČAJ — kroz DIJELJENU objavnu stazu, ne samo helper
# ---------------------------------------------------------------------------

def test_live_kontrolni_stem_with_delete_is_rejected():
    """Paket koji je STVARNO objavljen mora sada pasti zatvoreno."""
    text, safe = safe_visible_text(LIVE_STEM)
    assert safe is False
    assert text == ""          # ništa objavljivo ne izlazi iz zaštite


def test_identical_clean_stem_still_publishes():
    """Isti zadatak bez DEL-a mora proći nepromijenjen — nema lažnog bloka."""
    text, safe = safe_visible_text(CLEAN_STEM)
    assert safe is True
    assert "50^" + CIRC in text
    assert DEL not in text


def test_delete_is_reported_not_repaired():
    """Nikad `$50^\\circ$` kao „popravak“: znak preživi, ali paket padne."""
    cleaned, issues = mathsafe.sanitize_and_validate_math_text_with_issues(LIVE_STEM)
    assert issues, "DEL mora biti prijavljen"
    assert all(code == "control_character_in_math" for code in issues)
    assert DEL in cleaned, "sanitizator NE smije tiho ukloniti DEL"


@pytest.mark.parametrize("raw,expected_code", [
    ("$" + DEL + "50^" + CIRC + "$", "control_character_in_math"),
    ("tekst " + DEL + " tekst", "control_character"),
    (DEL, "control_character"),
])
def test_delete_unsafe_in_every_region(raw, expected_code):
    assert expected_code in mathsafe.find_unsafe_math_issues(raw)


# ---------------------------------------------------------------------------
# 2) POZNATI C0 KONTROLNI ZNAKOVI — ponašanje se NE mijenja
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code_point", [0x00, 0x05, 0x07, 0x0C, 0x1F])
def test_known_c0_controls_stay_unsafe(code_point):
    raw = "tekst " + chr(code_point) + " tekst"
    assert "control_character" in mathsafe.find_unsafe_math_issues(raw)
    assert safe_visible_text(raw) == ("", False)


@pytest.mark.parametrize("code_point", [0x00, 0x05, 0x07, 0x0C, 0x1F])
def test_known_c0_controls_inside_math_unchanged(code_point):
    """C0 unutar matematike uklanja POSTOJEĆI `_repair_control_chars`.

    Ovaj test NE odobrava to ponašanje — pribija ga kao ZATEČENO, da se vidi
    ako ga neko slučajno promijeni. DEL se namjerno ponaša drukčije."""
    raw = "$" + chr(code_point) + "50^" + CIRC + "$"
    assert "control_character_in_math" in mathsafe.find_unsafe_math_issues(raw)
    cleaned, safe = mathsafe.sanitize_and_validate_math_text(raw)
    assert safe is True and chr(code_point) not in cleaned


# ---------------------------------------------------------------------------
# 3) GRANICA — susjedi ne smiju biti povučeni sa sobom
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code_point,unsafe", [
    (0x1F, True),    # zadnji C0 — bio i ostaje nebezbjedan
    (0x20, False),   # obični razmak
    (0x7E, False),   # tilda `~` — susjed DEL-a, mora ostati ispravna
    (0x7F, True),    # DELETE — jedina promjena ovog izdanja
    (0x80, False),   # C1 — NAMJERNO netaknut, nema dokaza
])
def test_boundary_code_points(code_point, unsafe):
    raw = "tekst " + chr(code_point) + " tekst"
    issues = mathsafe.find_unsafe_math_issues(raw)
    assert bool(issues) is unsafe, (hex(code_point), issues)


def test_c1_policy_is_recorded_not_changed():
    """Opseg je usko vezan za dokaz: SAMO U+007F je dodan."""
    assert mathsafe._DISALLOWED_CONTROL_CODEPOINTS == frozenset({0x7F})
    assert mathsafe._is_disallowed_control(chr(0x7F)) is True
    assert mathsafe._is_disallowed_control(chr(0x80)) is False
    assert mathsafe._is_disallowed_control(chr(0x7E)) is False
    assert mathsafe._is_disallowed_control(chr(0x00)) is True


# ---------------------------------------------------------------------------
# 4) KANONSKA MATEMATIKA I PROZA — nula lažnih blokova
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "$" + BS + "frac{2}{3}$",
    "$" + BS + "sqrt{2}$",
    "$" + BS + "alpha+" + BS + "beta=90^" + CIRC + "$",
    "$3,98" + BS + "approx4$",
    "Trougao ima tačan ćošak, žuta šara, đačka klupa.",
    "Uglomjer mjeri ugao od $45^" + CIRC + "$.",
    CLEAN_STEM,
])
def test_canonical_content_is_untouched(raw):
    assert mathsafe.find_unsafe_math_issues(raw) == []
    _text, safe = safe_visible_text(raw)
    assert safe is True


# ---------------------------------------------------------------------------
# 5) KRAJ-DO-KRAJA: STVARNA objavna staza Kontrolnog, ne samo helper
# ---------------------------------------------------------------------------
# Model koji U SVAKOM pozivu vrati DEL u jednom slotu. Bitno je da kvar
# preživi i uslovni popravni poziv — inače bi test mjerio popravku, ne odbijanje.

from tests.test_kontrolni import EchoKontrolniLLM, start_payload  # noqa: E402
from matbot import kontrolni as _kontrolni                        # noqa: E402


class _PersistentDeleteLLM(EchoKontrolniLLM):
    """Ubaci U+007F u tekst slota 1 pri SVAKOM batch pozivu."""

    def kontrolni_turn(self, instructions, input_text, timeout_s=None):
        result = super().kontrolni_turn(instructions, input_text, timeout_s)
        for question in result.output.questions:
            if question.slot == 1:
                question.text = question.text.replace("U razredu", "U" + DEL + " razredu")
        return result


def test_kontrolni_package_with_delete_fails_closed_end_to_end():
    store = _kontrolni.KontrolniStore()
    llm = _PersistentDeleteLLM()
    status, resp = _kontrolni.run_start(store, llm, start_payload())

    assert resp.get("status") != "ready", "paket s DEL-om se NE smije objaviti"
    assert llm.batch_calls <= 2, "granica od dva poziva ostaje"
    # Ništa iz pokvarenog paketa ne izlazi ka učeniku.
    assert DEL not in json.dumps(resp, ensure_ascii=False)
    assert not (store.get("kontrolni-sess") or {}).get("questions")


def test_identical_kontrolni_package_without_delete_publishes():
    """Kontrola: isti model bez DEL-a objavljuje uredan test od 5 pitanja."""
    store = _kontrolni.KontrolniStore()
    llm = EchoKontrolniLLM()
    _status, resp = _kontrolni.run_start(store, llm, start_payload())

    assert resp.get("status") == "ready"
    assert len(resp["questions"]) == 5
    assert DEL not in json.dumps(resp, ensure_ascii=False)


def test_no_internal_issue_code_reaches_the_client():
    """CLAUDE.md pravilo 7: kod defekta ide u log, nikad u payload."""
    store = _kontrolni.KontrolniStore()
    status, resp = _kontrolni.run_start(store, _PersistentDeleteLLM(), start_payload())
    body = json.dumps(resp, ensure_ascii=False)
    for code in ("control_character", "control_character_in_math",
                 "unsafe_question_notation", "unsafe_option_notation"):
        assert code not in body
