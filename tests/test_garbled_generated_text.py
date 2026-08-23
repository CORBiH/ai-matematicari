# -*- coding: utf-8 -*-
"""Iskvaren generisani tekst ne smije stići do učenika (dva uska pravila).

ŽIVI NALAZ (verifikacija Kontrolnog poslije izdanja, 3 slučaja u 85 pitanja).
U sva tri ključ i ocjena su bili ISPRAVNI — kvar je bio u onome što učenik vidi:

  D1a  6. razred, decimalni brojevi
       „Zapiši razlomak $\\frac{7}{20}$ u obliku decimalnog broja."
       opcije: 0,35 (tačna) / 0,25 / 0,7 / **„0, intez"**

  D1b  7. razred, racionalni brojevi
       „Zapiši razlomak $\\frac{17}{20}$ u obliku decimalnog broja."
       opcije: $0,805$ / **$0,<KANNADA>?85$** / $0,85$ (tačna) / $1,17$

  D1c  6. razred, razlomci
       rješenje: „…najvećim zajedničkim djelio<ĆIRILICA>, brojem $14$…"

Nijedan postojeći validator nije reagovao: nema kontrolnih znakova, LaTeX je
uredan, opcije nisu duplikati ni ekvivalentne, orakli nisu primjenjivi.

DVA ODVOJENA PRAVILA, jer su to dva različita kvara:

  A  `mcq_integrity.numeric_option_shape_failure` — u DOKAZANO numeričkoj
     porodici opcija koja nosi DOKAZ KVARA (slomljen decimalni broj ili strano
     pismo) obara paket. Distraktor NE mora biti tačan, samo sintaksno moguć.

  B  `mathsafe.find_script_corruption` — riječ koja miješa latinicu s drugim
     pismom je kvar, ne jezik. Dijeljeno pravilo (isti kvar je izmjeren i u
     Practice i u Explain artefaktima), primjenjuje se SAMO izvan `$…$`.

KORPUSNA MJERA (vidi izvještaj): 306 dokazano numeričkih porodica opcija i
27.590 proznih stringova; odbijena su tačno dva demonstrirana slučaja i
označeno 26 stvarnih kvarova — nula lažnih pozitiva.
"""
import json

import pytest

from matbot import kontrolni, mathsafe, mcq_integrity
from matbot.schema import KontrolniQuestionOutput
from matbot.tutor import lesson_context
from matbot.tutor.package_preflight import safe_visible_text

BS = chr(92)
KANNADA = chr(0x0CB5) + chr(0x0CC6)
CYR_CEM = chr(0x0446) + chr(0x0435) + chr(0x043C)

# --- TAČNI ŽIVI ZAPISI ------------------------------------------------------
D1A_STEM = "Zapiši razlomak $" + BS + "frac{7}{20}$ u obliku decimalnog broja."
D1A_OPTIONS = ["0,35", "0,25", "0,7", "0, intez"]
D1A_CLEAN = ["0,35", "0,25", "0,7", "0,53"]
D1A_MARKED = 0

D1B_STEM = "Zapiši razlomak $" + BS + "frac{17}{20}$ u obliku decimalnog broja."
D1B_OPTIONS = ["$0,805$", "$0," + KANNADA + "?85$", "$0,85$", "$1,17$"]
D1B_CLEAN = ["$0,805$", "$0,58$", "$0,85$", "$1,17$"]
D1B_MARKED = 2

D1C_SOLUTION = ("Brojnik i nazivnik podijelimo njihovim najvećim zajedničkim "
                "djelio" + CYR_CEM + ", brojem $14$. Dobijamo $" + BS +
                "frac{42}{56}=" + BS + "frac{3}{4}$.")
D1C_CLEAN_SOLUTION = D1C_SOLUTION.replace("djelio" + CYR_CEM, "djeliocem")


def publish(stem, options, marked, solution="Postupak je opisan.",
            grade=6, lesson="6-05-002"):
    """Pusti paket kroz STVARNI publikacijski validator Kontrolnog."""
    slot = {"slot": 1, "lesson_id": lesson, "lesson_title": "L",
            "difficulty": "medium"}
    parsed = KontrolniQuestionOutput(
        slot=1, lesson_id=lesson, text=stem, options=list(options),
        correct_option_index=marked, expected_answer=options[marked],
        solution=solution, difficulty="medium")
    context = lesson_context.build(grade, lesson)
    return kontrolni.validate_generated_question(parsed, slot, context, set())


# ---------------------------------------------------------------------------
# 1) TAČNI ŽIVI ZAPISI — moraju pasti, čisti blizanci moraju proći
# ---------------------------------------------------------------------------

def test_d1a_malformed_numeric_distractor_is_rejected():
    clean, code = publish(D1A_STEM, D1A_OPTIONS, D1A_MARKED)
    assert clean is None
    assert code == "malformed_numeric_option"


def test_d1a_clean_twin_publishes():
    clean, code = publish(D1A_STEM, D1A_CLEAN, D1A_MARKED)
    assert clean is not None, code


def test_d1b_foreign_script_distractor_is_rejected():
    clean, code = publish(D1B_STEM, D1B_OPTIONS, D1B_MARKED, grade=7,
                          lesson="7-03-009")
    assert clean is None
    assert code == "malformed_numeric_option"


def test_d1b_clean_twin_publishes():
    clean, code = publish(D1B_STEM, D1B_CLEAN, D1B_MARKED, grade=7,
                          lesson="7-03-009")
    assert clean is not None, code


def test_d1c_mixed_script_solution_is_rejected():
    """Rješenje ide kroz POSTOJEĆI `_safe_field` → `safe_visible_text`."""
    clean, code = publish("Skrati razlomak $" + BS + "frac{42}{56}$.",
                          ["$" + BS + "frac{3}{4}$", "$" + BS + "frac{4}{5}$",
                           "$" + BS + "frac{2}{3}$", "$" + BS + "frac{7}{8}$"],
                          0, solution=D1C_SOLUTION, lesson="6-04-006")
    assert clean is None
    assert code == "unsafe_or_long_solution"


def test_d1c_clean_twin_publishes():
    clean, code = publish("Skrati razlomak $" + BS + "frac{42}{56}$.",
                          ["$" + BS + "frac{3}{4}$", "$" + BS + "frac{4}{5}$",
                           "$" + BS + "frac{2}{3}$", "$" + BS + "frac{7}{8}$"],
                          0, solution=D1C_CLEAN_SOLUTION, lesson="6-04-006")
    assert clean is not None, code


# ---------------------------------------------------------------------------
# 2) PRAVILO B — riječ, ne pismo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word", [
    "djelio" + CYR_CEM,                       # živi nalaz
    "prav" + chr(0x0435),                     # `pravе`
    "brojnik" + chr(0x0430),                  # `brojnikа`
    "uređ" + chr(0x0435) + chr(0x043D) + chr(0x0438) + chr(0x0445),
])
def test_mixed_script_word_is_corruption(word):
    assert "mixed_script_word" in mathsafe.find_script_corruption("Tekst " + word + " dalje.")


def test_whole_foreign_word_in_latin_prose_is_corruption():
    assert "foreign_script_word" in mathsafe.find_script_corruption(
        "Odaberite faktorizaciju koja " + chr(0x0458) + chr(0x0435) + " tačna.")


@pytest.mark.parametrize("prose", [
    "Trougao ima tačan ćošak, žuta šara, đačka klupa.",
    "Između koja dva uzastopna prirodna broja se nalazi korijen?",
    "Uglomjer mjeri ugao, a zbir uglova je 180 stepeni.",
    "Broj π i obim kruga",                    # grčko slovo je legitimno
    "Dobro — evo rješenja, korak po korak.",  # em crta i interpunkcija
    "Površina je 144 cm², a obim 48 cm.",
    "Čačkalica, ćevap, žirafa, šargarepa, đevrek.",
])
def test_legitimate_bosnian_prose_is_clean(prose):
    assert mathsafe.find_script_corruption(prose) == []


def test_greek_letters_are_not_corruption():
    for text in ("Ugao α je oštar.", "Zbir α + β = 90 stepeni.", "Broj π ≈ 3,14"):
        assert mathsafe.find_script_corruption(text) == []


def test_rule_b_does_not_apply_inside_math():
    """Unutar `$…$` vlada MATHJAX_COMMAND_ALLOWLIST, ne ovo pravilo."""
    issues = mathsafe.find_unsafe_math_issues("$" + BS + "alpha+" + BS + "beta=90^" + BS + "circ$")
    assert "mixed_script_word" not in issues
    assert "foreign_script_word" not in issues


# ---------------------------------------------------------------------------
# 3) PRAVILO A — dokaz kvara, ne „neizračunljivost"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("options,marked", [
    # IZMJERENI LAŽNI POZITIVI prve verzije pravila — moraju proći.
    (["$" + BS + "sqrt{2}$", "$0,75$", "$" + BS + "sqrt{5}$", "$" + BS + "pi$"], 1),
    (["$" + BS + "sqrt{2}$", "$-" + BS + "sqrt{3}$", "$" + BS + "frac{3}{4}$",
      "$" + BS + "pi$"], 2),
    (["$-" + BS + "frac{3}{2}$", "$-" + BS + "frac{2}{3}$", "$" + BS + "frac{3}{2}$",
      "$" + BS + "frac{-6}{x}$"], 0),
    (["2", "0", "1", "beskonačno mnogo"], 1),
    (["$4$", "$5$", "$7$", "nijedan od ponuđenih"], 0),
])
def test_legitimate_non_numeric_options_are_not_flagged(options, marked):
    assert mcq_integrity.numeric_option_shape_failure(options, marked) == ""


@pytest.mark.parametrize("options,marked", [
    (["0,35", "0,25", "0,7", "0,53"], 0),                       # decimale
    (["$" + BS + "frac{3}{4}$", "$" + BS + "frac{4}{5}$",
      "$" + BS + "frac{2}{3}$", "$" + BS + "frac{7}{8}$"], 0),  # razlomci
    (["$-2$", "$2$", "$-4$", "$4$"], 0),                        # negativni
    (["$144" + BS + "," + BS + "text{cm}^2$", "$576" + BS + "," + BS + "text{cm}^2$",
      "$48" + BS + "," + BS + "text{cm}^2$", "$192" + BS + "," + BS + "text{cm}^2$"], 0),
    (["$35" + BS + ",000$", "$3" + BS + ",500$", "$350$", "$350" + BS + ",000$"], 0),
])
def test_valid_numeric_option_families_publish(options, marked):
    assert mcq_integrity.numeric_option_shape_failure(options, marked) == ""


@pytest.mark.parametrize("options,marked", [
    (["Između $8$ i $9$", "Između $6$ i $7$", "Između $7$ i $8$",
      "Između $9$ i $10$"], 0),
    (["5 knjiga", "4 knjige", "3 knjige", "7 knjiga"], 3),
    (["$x^4$", "$x^{10}$", "$x^0$", "$x^3$"], 0),
    (["$" + BS + "mathbb{N}$", "$" + BS + "mathbb{Z}$", "$" + BS + "mathbb{Q}$",
      "$" + BS + "mathbb{R}$"], 0),
    (["Broj je racionalan", "Broj je iracionalan", "Broj je cijeli",
      "Broj je prirodan"], 1),
])
def test_non_numeric_families_abstain(options, marked):
    assert mcq_integrity.numeric_option_shape_failure(options, marked) == ""


def test_unreadable_marked_option_abstains():
    """Ako ni označena opcija nije broj, porodica nije dokazana."""
    assert mcq_integrity.numeric_option_shape_failure(
        ["0,35", "0,25", "0,7", "0, intez"], 3) == ""


# ---------------------------------------------------------------------------
# 4) POPRAVNA STAZA I NEPROPUSTANJE INTERNIH KODOVA
# ---------------------------------------------------------------------------
from tests.test_kontrolni import EchoKontrolniLLM, start_payload  # noqa: E402


class _GarbledLLM(EchoKontrolniLLM):
    """Slot 1 dobije iskvaren distraktor; `heal` bira da li popravni poziv sanira."""

    def __init__(self, heal):
        super().__init__()
        self.heal = heal

    def kontrolni_turn(self, instructions, input_text, timeout_s=None):
        result = super().kontrolni_turn(instructions, input_text, timeout_s)
        good = self.heal and self.batch_calls >= 2
        for question in result.output.questions:
            if question.slot != 1:
                continue
            question.text = D1A_STEM
            question.options = list(D1A_CLEAN if good else D1A_OPTIONS)
            question.correct_option_index = D1A_MARKED
            question.expected_answer = question.options[D1A_MARKED]
            question.solution = ("Proširimo razlomak na nazivnik $100$: "
                                 "$\frac{7}{20}=\frac{35}{100}$, pa je zapis $0,35$.")
        return result


def test_bad_draft_is_repaired_and_publishes():
    store = kontrolni.KontrolniStore()
    llm = _GarbledLLM(heal=True)
    _status, resp = kontrolni.run_start(store, llm, start_payload())
    assert resp.get("status") == "ready", resp
    assert llm.batch_calls == 2


def test_still_garbled_after_repair_fails_closed():
    store = kontrolni.KontrolniStore()
    llm = _GarbledLLM(heal=False)
    _status, resp = kontrolni.run_start(store, llm, start_payload())
    assert resp.get("status") != "ready"
    assert llm.batch_calls == 2, "bez trećeg poziva"
    assert not (store.get("kontrolni-sess") or {}).get("questions")


def test_no_internal_code_reaches_the_client():
    store = kontrolni.KontrolniStore()
    _status, resp = kontrolni.run_start(store, _GarbledLLM(heal=False),
                                        start_payload())
    body = json.dumps(resp, ensure_ascii=False)
    for code in ("malformed_numeric_option", "mixed_script_word",
                 "foreign_script_word", "unsafe_or_long_solution"):
        assert code not in body


# ---------------------------------------------------------------------------
# 5) DIJELJENI SLOJ — isti kvar pada i van Kontrolnog
# ---------------------------------------------------------------------------

def test_shared_safe_text_rejects_mixed_script_prose():
    """Isti kvar je izmjeren u Practice i Explain artefaktima, pa pravilo živi
    u dijeljenom sloju, a ne kao Kontrolni zakrpa."""
    text, safe = safe_visible_text("Provjerimo faktorizaciju brojnik" + chr(0x0430) + ".")
    assert safe is False and text == ""


def test_shared_safe_text_keeps_valid_bosnian():
    text, safe = safe_visible_text("Provjerimo faktorizaciju brojnika.")
    assert safe is True and "brojnika" in text
