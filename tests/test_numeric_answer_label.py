# -*- coding: utf-8 -*-
"""Numerički odgovor s oznakom jedinice mora ostati PROVJERLJIV.

ŽIVI NALAZ P1 (post-release mjerenje dostupnosti, Kontrolni, 6. razred,
čitanje podataka iz tabele):

    „Tabela: Amna $18$, Boris $25$, Dženan $16$, Ema $21$.
     Koliko je boca Boris sakupio više od Dženana?"     25 − 16 = 9

     *a) $7$ boca      ← OZNAČENO, POGREŠNO
      b) $9$ boca      ← TAČNO
      c) $41$ boca
      d) $<tajlandsko pismo>$ boca

    expected_answer : $7$ boca   (slaže se s POGREŠNOM opcijom)
    rješenje        : „…Razlika je $25-16=9$ boca."   ← izvodi TAČAN broj
    ishod           : OBJAVLJENO

IZMJERENI UZROK — čuvar je bio SLIJEP, ne pogrešan:

    _option_numeric_value("$7$ boca")  -> unsupported
    _unique_value("$7$ boca")          -> None
    _solution_contradicts_marked_value -> False     (ćuti)

    KONTROLA bez oznake:
    _unique_value("$7$")               -> 7.0
    _solution_contradicts_marked_value -> True      ← POSTOJEĆI čuvar PALI

Jedinice UNUTAR matematike (`\\text{cm}`) već uklanja
`mathcheck._strip_units_and_spacing`; ovo je isti posao IZVAN spana.

PRAVILO JE STRUKTURNO, ne „skini zadnju riječ": jedan kompletan `$…$` span (ili
goli broj) na početku, razmak, pa oznaka od najviše dvije čiste riječi bez
cifara/LaTeX-a i bez ijedne riječi koja mijenja značenje.

KORPUS (9.463 skupa opcija): 1.948 opcija postaje čitljivo kroz 52 oznake — sve
stvarne jedinice i brojive imenice. Odbijeno ostaje sve što mijenja smisao, a
korpus to stvarno sadrži: „je veći", „cm od obje stranice", „km prema istoku",
„i 3 ostatka", „i $7$", parovi koordinata, sistemi jednačina.
"""
import json

import pytest

from matbot import kontrolni, mcq_integrity
from matbot.schema import KontrolniQuestionOutput
from matbot.tutor import lesson_context

BS = chr(92)
THAI = chr(0x0E40) + chr(0x0E28) + chr(0x0E29)

# --- TAČAN ŽIVI PAKET -------------------------------------------------------
LESSON, GRADE, SLOTNO = "6-13-006", 6, 3
STEM = ("Tabela prikazuje broj sakupljenih plastičnih boca: Amna $18$, "
        "Boris $25$, Dženan $16$, Ema $21$. Koliko je boca Boris sakupio "
        "više od Dženana?")
OPTIONS = ["$7$ boca", "$9$ boca", "$41$ boca", "$ " + THAI + "$ boca"]
CLEAN_OPTIONS = ["$7$ boca", "$9$ boca", "$41$ boca", "$12$ boca"]
SOLUTION = ("Iz tabele očitamo da je Boris sakupio $25$ boca, a Dženan $16$ "
            "boca. Razlika je $25-16=9$ boca.")
WRONG, RIGHT = 0, 1


def publish(options, marked, solution=SOLUTION, stem=STEM,
            lesson=LESSON, grade=GRADE):
    slot = {"slot": SLOTNO, "lesson_id": lesson, "lesson_title": "L",
            "difficulty": "medium"}
    parsed = KontrolniQuestionOutput(
        slot=SLOTNO, lesson_id=lesson, text=stem, options=list(options),
        correct_option_index=marked, expected_answer=options[marked],
        solution=solution, difficulty="medium")
    ctx = lesson_context.build(grade, lesson)
    clean, code = kontrolni.validate_generated_question(parsed, slot, ctx, set())
    return ("ACCEPT" if clean is not None else "REJECT"), code


# ---------------------------------------------------------------------------
# 1) TAČAN ŽIVI DEFEKT
# ---------------------------------------------------------------------------

def test_live_p1_package_is_rejected():
    verdict, code = publish(OPTIONS, WRONG)
    assert verdict == "REJECT"
    assert code, "mora postojati kod odbijanja"


def test_wrong_key_with_clean_options_hits_the_existing_guard():
    """Bez pokvarene opcije pada TAČNO na postojećem čuvaru pogrešnog ključa."""
    verdict, code = publish(CLEAN_OPTIONS, WRONG)
    assert verdict == "REJECT"
    assert code == "solution_marked_value_divergence"


def test_corrected_twin_publishes():
    verdict, code = publish(CLEAN_OPTIONS, RIGHT)
    assert verdict == "ACCEPT", code


def test_solution_contradiction_is_now_visible():
    assert kontrolni._solution_numeric_values(SOLUTION) == [25.0, 16.0, 9.0, 9.0]
    assert kontrolni._solution_contradicts_marked_value("$7$ boca", SOLUTION) is True
    assert kontrolni._solution_contradicts_marked_value("$9$ boca", SOLUTION) is False


def test_second_live_wrong_key_found_by_replay():
    """Drugi STVARNI pogrešan ključ iz iste kampanje (t17): rješenje izvodi
    $60$, označeno je $50$."""
    stem = ("Učenik je pročitao knjigu od $120$ stranica. Prvog dana pročitao je "
            "$" + BS + "frac{1}{3}$ knjige, a drugog dana još $20$ stranica. "
            "Koliko mu je stranica ostalo da pročita?")
    options = ["$40$ stranica", "$50$ stranica", "$60$ stranica", "$80$ stranica"]
    solution = ("Neka je broj preostalih stranica $x$. Učenik je prvog dana "
                "pročitao $120:3=40$ stranica, pa važi $40+20+x=120$. Zato je "
                "$x=60$, a ponuđeni tačan rezultat je $60$ stranica.")
    verdict, code = publish(options, 1, solution=solution, stem=stem,
                            lesson="6-13-006")
    assert verdict == "REJECT"
    assert code == "solution_marked_value_divergence"
    verdict, code = publish(options, 2, solution=solution, stem=stem,
                            lesson="6-13-006")
    assert verdict == "ACCEPT", code


# ---------------------------------------------------------------------------
# 2) OBLIK: šta se skida, a šta NIKAKO
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("option,core", [
    ("$7$ boca", "$7$"),
    ("$9$ učenika", "$9$"),
    ("$15$ knjiga", "$15$"),
    ("$12$ cm", "$12$"),
    ("$3,5$ kg", "$3,5$"),
    ("$25$ KM", "$25$"),
    ("$0,75$ litara", "$0,75$"),
    ("$-4$ boda", "$-4$"),
    ("$" + BS + "frac{3}{4}$ litre", "$" + BS + "frac{3}{4}$"),
    ("$144$ cm²", "$144$"),
    ("7 boca", "7"),
])
def test_label_is_stripped_from_the_numeric_core(option, core):
    assert mcq_integrity.strip_answer_label(option) == core


@pytest.mark.parametrize("option", [
    "$7$ ili $9$ boca",            # dva broja — nije jedan odgovor
    "$7$ više",                    # komparativ mijenja značenje
    "$7$ manje",
    "$7$ ili više",
    "$7$ do $9$",
    "$7$ približno",
    "$2$ cm od obje stranice",     # korpus: kvalifikator
    "$5$ km prema istoku",         # korpus: smjer
    "$3$ i 0 ostatka",             # korpus: složen odgovor
    "$120$ KM je veći rezultat.",  # korpus: tvrdnja
    "$(3,2)$ i $(1,1)$",           # korpus: par koordinata
    "$7$",                         # nema oznake — ostaje isto
    "beskonačno mnogo",
])
def test_ambiguous_or_meaning_bearing_suffix_is_left_alone(option):
    assert mcq_integrity.strip_answer_label(option) == option


def test_variable_core_stays_non_numeric():
    """`$x$ boca` smije izgubiti oznaku, ali NE postaje broj."""
    assert mcq_integrity.strip_answer_label("$x$ boca") == "$x$"
    assert mcq_integrity._option_numeric_value("$x$ boca")[0] != "value"


@pytest.mark.parametrize("option,value", [
    ("$7$ boca", 7.0), ("$3,5$ kg", 3.5), ("$-4$ boda", -4.0),
    ("$" + BS + "frac{3}{4}$ litre", 0.75), ("$25$ KM", 25.0),
])
def test_numeric_value_now_readable(option, value):
    status, got, _expr = mcq_integrity._option_numeric_value(option)
    assert status == "value"
    assert abs(got - value) < 1e-9


# ---------------------------------------------------------------------------
# 3) NUMERIČKA PORODICA — nema lažnih pozitiva
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("options,marked", [
    (["$" + BS + "sqrt{2}$", "$0,75$", "$" + BS + "sqrt{5}$", "$" + BS + "pi$"], 1),
    (["$-" + BS + "frac{3}{2}$", "$-" + BS + "frac{2}{3}$",
      "$" + BS + "frac{3}{2}$", "$" + BS + "frac{-6}{x}$"], 0),
    (["2", "0", "1", "beskonačno mnogo"], 1),
    (["$4$", "$5$", "$7$", "nijedan od ponuđenih"], 0),
    (["Broj je racionalan", "Broj je iracionalan", "Broj je cijeli",
      "Broj je prirodan"], 1),
    (["$" + BS + "mathbb{N}$", "$" + BS + "mathbb{Z}$",
      "$" + BS + "mathbb{Q}$", "$" + BS + "mathbb{R}$"], 0),
    (["$x^4$", "$x^{10}$", "$x^0$", "$x^3$"], 0),
    (["Između $8$ i $9$", "Između $6$ i $7$", "Između $7$ i $8$",
      "Između $9$ i $10$"], 0),
])
def test_non_numeric_families_still_abstain(options, marked):
    assert mcq_integrity.numeric_option_shape_failure(options, marked) == ""


def test_unit_options_become_a_provable_numeric_family():
    """Prirodna posljedica: s citljivim jezgrima porodica je dokaziva, pa se
    iskvarena opcija u njoj sada vidi (tajlandsko pismo iz zivog nalaza)."""
    assert mcq_integrity.numeric_option_shape_failure(OPTIONS, WRONG) == \
        "malformed_numeric_option"
    assert mcq_integrity.numeric_option_shape_failure(CLEAN_OPTIONS, RIGHT) == ""


# ---------------------------------------------------------------------------
# 4) POPRAVNA STAZA I NEPROPUSTANJE KODOVA
# ---------------------------------------------------------------------------
from tests.test_kontrolni import EchoKontrolniLLM, start_payload  # noqa: E402


class _WrongKeyLLM(EchoKontrolniLLM):
    """Slot 1 nosi pogrešan ključ s oznakom jedinice; `heal` sanira popravkom."""

    def __init__(self, heal):
        super().__init__()
        self.heal = heal

    def kontrolni_turn(self, instructions, input_text, timeout_s=None):
        result = super().kontrolni_turn(instructions, input_text, timeout_s)
        good = self.heal and self.batch_calls >= 2
        for question in result.output.questions:
            if question.slot != 1:
                continue
            question.text = STEM
            question.options = list(CLEAN_OPTIONS)
            question.correct_option_index = RIGHT if good else WRONG
            question.expected_answer = question.options[question.correct_option_index]
            question.solution = SOLUTION
        return result


def test_bad_draft_is_repaired_and_publishes():
    store = kontrolni.KontrolniStore()
    llm = _WrongKeyLLM(heal=True)
    _status, resp = kontrolni.run_start(store, llm, start_payload())
    assert resp.get("status") == "ready", resp
    assert llm.batch_calls == 2


def test_still_wrong_after_repair_fails_closed():
    store = kontrolni.KontrolniStore()
    llm = _WrongKeyLLM(heal=False)
    _status, resp = kontrolni.run_start(store, llm, start_payload())
    assert resp.get("status") != "ready"
    assert llm.batch_calls == 2, "bez trećeg poziva"
    assert not (store.get("kontrolni-sess") or {}).get("questions")


def test_no_internal_code_reaches_the_client():
    store = kontrolni.KontrolniStore()
    _status, resp = kontrolni.run_start(store, _WrongKeyLLM(heal=False),
                                        start_payload())
    body = json.dumps(resp, ensure_ascii=False)
    for code in ("solution_marked_value_divergence", "malformed_numeric_option",
                 "strip_answer_label", "correct_option_id"):
        assert code not in body
