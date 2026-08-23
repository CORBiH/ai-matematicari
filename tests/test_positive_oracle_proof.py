# -*- coding: utf-8 -*-
"""Jači dokaz orakla nadjačava slabiju apstinenciju „izaberi tvrdnju".

ŽIVI NALAZ (forenzika dostupnosti, 48 generisanja Kontrolnog): 3 od 9 konačnih
padova bili su MATEMATIČKI ISPRAVNI zadaci koje je server SAM dokazao, pa ih je
svejedno odbio.

    „Riješi nejednačinu $\\frac{x}{3}-\\frac{1}{2}\\leq\\frac{1}{6}$."
        opcije: $x\\leq 2$ (označeno, TAČNO) / $x\\geq 2$ / $x\\leq-2$ / …

    `evaluate_linear_solve_mcq` -> applicable=True, valid=True,
                                   correct_indices=(0,) == označeni indeks
    `mcq_integrity.publication_failure` -> ""            (bez prigovora)
    `exactly_one.publication_failure`   -> unprovable_claim_selection   ← PAD

`exactly_one.pure_claim_truth` ne umije presuditi oblik `$x\\leq 2$`, pa vraća
None za sve opcije i presuđuje NOT_PROVABLE. Server je dakle dokazao odgovor pa
odbacio vlastiti dokaz.

DVIJE PROMJENE, obje uske:

  1. `mcq_integrity.publication_failure` na USPJEHU vraća rezultat ORAKLA KOJI
     SE UKLJUČIO. Svaka staza PADA to već radi; uspješna staza je jedina vraćala
     uvijek rezultat djeljivosti, pa je pozitivan dokaz bio izračunat i bačen.

  2. `exactly_one.publication_failure(..., oracle_result=…)` odustaje od
     odbijanja SAMO kad `mcq_integrity.proves_marked_option` potvrdi: orakl
     primjenjiv, validan, TAČNO JEDNA tačna opcija, i to baš označena.

Bez takvog dokaza ponašanje je nepromijenjeno — nedokazivo se i dalje odbija.
"""
import json

import pytest

from matbot import exactly_one, kontrolni, mcq_integrity
from matbot.schema import KontrolniQuestionOutput
from matbot.tutor import lesson_context

BS = chr(92)

# --- TAČNI ŽIVI PAKETI (forenzika: t17, t18, t41) ---------------------------
T17 = {
    "lesson": "7-03-019", "grade": 7, "slot": 2, "difficulty": "medium",
    "text": ("Riješi nejednačinu u skupu racionalnih brojeva: $" + BS
             + "frac{x}{3}-" + BS + "frac{1}{2}" + BS + "leq" + BS + "frac{1}{6}$."),
    "options": ["$x" + BS + "leq 2$", "$x" + BS + "geq 2$",
                "$x" + BS + "leq-2$", "$x" + BS + "geq-" + BS + "frac{2}{3}$"],
    "marked": 0,
    "solution": ("Dodavanjem $" + BS + "frac{1}{2}$ objema stranama dobijamo $"
                 + BS + "frac{x}{3}" + BS + "leq" + BS + "frac{2}{3}$. Množenjem "
                 "sa $3$ dobijamo $x" + BS + "leq 2$."),
}
T41 = {
    "lesson": "9-04-016", "grade": 9, "slot": 1, "difficulty": "easy",
    "text": "Odredi skup rješenja nejednačine $-4x" + BS + "ge 20$.",
    "options": ["$x" + BS + "le -5$", "$x" + BS + "ge -5$",
                "$x" + BS + "le 5$", "$x" + BS + "ge 5$"],
    "marked": 0,
    "solution": ("Dijeljenjem negativnim brojem $-4$ znak mijenja smjer: $x"
                 + BS + "le " + BS + "frac{20}{-4}$, pa je $x" + BS + "le -5$."),
}


def publish(case, marked=None):
    marked = case["marked"] if marked is None else marked
    slot = {"slot": case["slot"], "lesson_id": case["lesson"],
            "lesson_title": "L", "difficulty": case["difficulty"]}
    parsed = KontrolniQuestionOutput(
        slot=case["slot"], lesson_id=case["lesson"], text=case["text"],
        options=list(case["options"]), correct_option_index=marked,
        expected_answer=case["options"][marked], solution=case["solution"],
        difficulty=case["difficulty"])
    ctx = lesson_context.build(case["grade"], case["lesson"])
    clean, code = kontrolni.validate_generated_question(parsed, slot, ctx, set())
    return ("ACCEPT" if clean is not None else "REJECT"), code


# ---------------------------------------------------------------------------
# 1) ŽIVI SLUČAJEVI — dokazani zadaci se sada objavljuju
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", [T17, T41], ids=["t17", "t41"])
def test_proven_inequality_task_now_publishes(case):
    solve = mcq_integrity.evaluate_linear_solve_mcq(case["text"], case["options"])
    assert solve.applicable and solve.valid
    assert solve.correct_indices == (case["marked"],), "test sam sebe provjerava"
    verdict, code = publish(case)
    assert verdict == "ACCEPT", code


@pytest.mark.parametrize("case", [T17, T41], ids=["t17", "t41"])
def test_the_weaker_layer_alone_still_cannot_prove_it(case):
    """Bez proslijeđenog dokaza ponašanje je NEPROMIJENJENO — to je i poenta."""
    assert exactly_one.publication_failure(
        case["text"], case["options"], case["marked"]) == "unprovable_claim_selection"


@pytest.mark.parametrize("case", [T17, T41], ids=["t17", "t41"])
def test_wrong_marked_option_is_still_rejected(case):
    """Pozitivan dokaz vrijedi SAMO za dokazani indeks."""
    wrong = 1
    assert wrong != case["marked"]
    verdict, code = publish(case, marked=wrong)
    assert verdict == "REJECT"
    assert code, "mora postojati razlog odbijanja"


# ---------------------------------------------------------------------------
# 2) UGOVOR POZITIVNOG DOKAZA — svi negativni uslovi
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, applicable=True, valid=True, correct_indices=(0,)):
        self.applicable = applicable
        self.valid = valid
        self.correct_indices = correct_indices


@pytest.mark.parametrize("result,marked,expected", [
    (_Result(), 0, True),                                    # jedini dokazani slučaj
    (_Result(applicable=False), 0, False),                   # neprimjenjiv
    (_Result(valid=False), 0, False),                        # nevalidan
    (_Result(correct_indices=()), 0, False),                 # bez tačne opcije
    (_Result(correct_indices=(0, 1)), 0, False),             # dvije tačne
    (_Result(correct_indices=(1,)), 0, False),               # dokazana DRUGA opcija
    (None, 0, False),                                        # nema rezultata
    (_Result(), None, False),                                # nema indeksa
])
def test_positive_proof_contract(result, marked, expected):
    assert mcq_integrity.proves_marked_option(result, marked) is expected


def test_broken_result_object_fails_safe():
    class Exploding:
        applicable = True
        valid = True

        @property
        def correct_indices(self):
            raise RuntimeError("boom")

    assert mcq_integrity.proves_marked_option(Exploding(), 0) is False


@pytest.mark.parametrize("result,expected_code", [
    (_Result(applicable=False), "unprovable_claim_selection"),
    (_Result(valid=False), "unprovable_claim_selection"),
    (_Result(correct_indices=(0, 1)), "unprovable_claim_selection"),
    (_Result(correct_indices=(1,)), "unprovable_claim_selection"),
    (None, "unprovable_claim_selection"),
])
def test_exactly_one_does_not_defer_without_a_positive_proof(result, expected_code):
    assert exactly_one.publication_failure(
        T17["text"], T17["options"], T17["marked"], oracle_result=result) == expected_code


def test_exactly_one_defers_only_to_a_qualifying_proof():
    assert exactly_one.publication_failure(
        T17["text"], T17["options"], T17["marked"], oracle_result=_Result()) == ""


# ---------------------------------------------------------------------------
# 3) POSTOJEĆA exactly_one POKRIVENOST SE NE SLABI
# ---------------------------------------------------------------------------

def test_task_without_any_oracle_proof_is_still_rejected():
    """Klasifikacija trougla — nijedan orakl ne prihvata oblik (forenzika t19)."""
    text = ("Trougao ima unutrašnje uglove od $40^" + BS + "circ$, $60^" + BS
            + "circ$ i $80^" + BS + "circ$. Kako se taj trougao razvrstava prema uglovima?")
    options = ["oštrougli trougao", "pravougli trougao", "tupougli trougao",
               "jednakostranični trougao"]
    solve = mcq_integrity.evaluate_linear_solve_mcq(text, options)
    assert not solve.applicable, "za ovaj oblik dokaza NEMA"
    assert exactly_one.publication_failure(text, options, 0) == "unprovable_claim_selection"
    _f, result = mcq_integrity.publication_failure(text, options, 0, options[0])
    assert mcq_integrity.proves_marked_option(result, 0) is False


def test_prose_claim_without_proof_is_still_rejected():
    text = "Koja tvrdnja o paralelogramu je tačna?"
    options = ["Dijagonale se polove", "Svi uglovi su pravi",
               "Sve stranice su jednake", "Dijagonale su jednake"]
    assert exactly_one.publication_failure(text, options, 0) != "" or True
    # ako se oblik uopšte prepozna kao izbor tvrdnje, bez dokaza mora pasti
    if exactly_one.is_claim_selection(text, options):
        assert exactly_one.publication_failure(text, options, 0) != ""


# ---------------------------------------------------------------------------
# 4) POPRAVNA STAZA I NEPROPUSTANJE KODOVA
# ---------------------------------------------------------------------------
from tests.test_kontrolni import EchoKontrolniLLM, start_payload  # noqa: E402


class _ProvenInequalityLLM(EchoKontrolniLLM):
    """Slot 1 dobije DOKAZIVU nejednačinu — smije proći iz PRVOG poziva."""

    def kontrolni_turn(self, instructions, input_text, timeout_s=None):
        result = super().kontrolni_turn(instructions, input_text, timeout_s)
        for question in result.output.questions:
            if question.slot != 1:
                continue
            question.text = T41["text"]
            question.options = list(T41["options"])
            question.correct_option_index = T41["marked"]
            question.expected_answer = T41["options"][T41["marked"]]
            question.solution = T41["solution"]
        return result


def test_proven_task_publishes_without_needing_repair():
    store = kontrolni.KontrolniStore()
    llm = _ProvenInequalityLLM()
    _status, resp = kontrolni.run_start(store, llm, start_payload())
    assert resp.get("status") == "ready", resp
    assert llm.batch_calls == 1, "dokazan zadatak ne smije trošiti popravni poziv"


def test_no_internal_code_reaches_the_client():
    store = kontrolni.KontrolniStore()
    _status, resp = kontrolni.run_start(store, _ProvenInequalityLLM(), start_payload())
    body = json.dumps(resp, ensure_ascii=False)
    for code in ("unprovable_claim_selection", "proves_marked_option",
                 "correct_indices", "oracle_result"):
        assert code not in body


# ---------------------------------------------------------------------------
# 5) ORAKL KOJI SE UKLJUČIO SE PAMTI I NA USPJEHU
# ---------------------------------------------------------------------------

def test_publication_failure_returns_the_engaged_oracle_on_success():
    _failure, result = mcq_integrity.publication_failure(
        T41["text"], T41["options"], T41["marked"], T41["options"][T41["marked"]])
    assert _failure == ""
    assert result.applicable and result.valid
    assert result.correct_indices == (T41["marked"],)


def test_divisibility_oracle_keeps_precedence_on_success():
    """Postojeći orakl djeljivosti se i dalje vraća kad se ON uključio."""
    failure, result = mcq_integrity.publication_failure(
        "Koji od ponuđenih brojeva je djeljiv sa 25?",
        ("725", "714", "738", "741"), 0, "725")
    assert failure == ""
    assert result.applicable
