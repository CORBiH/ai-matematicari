# -*- coding: utf-8 -*-
"""Označena opcija mora se poklapati s DOKAZANIM intervalom korijena.

ŽIVI NALAZ (verifikacija Kontrolnog poslije izdanja, 8. razred, oblast realnih
brojeva, lekcija o procjeni korijena):

    „Između koja dva uzastopna prirodna broja se nalazi $\\sqrt{70}$?"
    Opcije: „Između $8$ i $9$" / „Između $6$ i $7$" / „Između $7$ i $8$" /
            „Između $9$ i $10$"
    Označeno: „Između $7$ i $8$"   ← POGREŠNO
    Rješenje: „…broj $70$ se nalazi između $8^2$ i $9^2$. Zato se
               $\\sqrt{70}$ nalazi između $8$ i $9$."   ← TAČNO

Paket je sam sebi protivrječio i svejedno objavljen → pogrešan ključ → pogrešna
ocjena učenika.

ZAŠTO GA NIŠTA NIJE UHVATILO: `expected_answer` je bio JEDNAK označenoj opciji
(oba pogrešna zajedno), svaki pojedinačni račun je aritmetički tačan, a
`_solution_contradicts_marked_value` traži označenu opciju s TAČNO JEDNOM
vrijednošću — „Između $7$ i $8$" ih ima dvije, pa ćuti.

RJEŠENJE JE STRUKTURNO, ne po lekciji: server prepozna oblik zadatka i sam
izračuna interval egzaktnom cjelobrojnom aritmetikom (`math.isqrt`).
"""
import pytest

from matbot import kontrolni, root_interval_mcq
from matbot.schema import KontrolniQuestionOutput
from matbot.tutor import lesson_context

BS = chr(92)


def sqrt(n):
    return "$" + BS + "sqrt{" + str(n) + "}$"


def stem(n):
    return "Između koja dva uzastopna prirodna broja se nalazi " + sqrt(n) + "?"


def pair(a, b):
    return "Između $%d$ i $%d$" % (a, b)


# --- TAČAN ŽIVI PAKET -------------------------------------------------------
LIVE_STEM = stem(70)
LIVE_OPTIONS = [pair(8, 9), pair(6, 7), pair(7, 8), pair(9, 10)]
LIVE_SOLUTION = ("Pošto je $7^2=49$ i $8^2=64$, a $9^2=81$, broj $70$ se nalazi "
                 "između $8^2$ i $9^2$. Zato se " + sqrt(70) + " nalazi između "
                 "$8$ i $9$.")
WRONG_INDEX, RIGHT_INDEX = 2, 0

GRADE, LESSON = 8, "8-01-007"
SLOT = {"slot": 1, "lesson_id": LESSON,
        "lesson_title": "Savršeni kvadrati i procjena", "difficulty": "medium"}


def publish(text, options, marked, solution=LIVE_SOLUTION, expected=None):
    """Pusti paket kroz STVARNI publikacijski validator Kontrolnog."""
    parsed = KontrolniQuestionOutput(
        slot=SLOT["slot"], lesson_id=LESSON, text=text, options=list(options),
        correct_option_index=marked,
        expected_answer=expected if expected is not None else options[marked],
        solution=solution, difficulty=SLOT["difficulty"])
    context = lesson_context.build(GRADE, LESSON)
    return kontrolni.validate_generated_question(parsed, SLOT, context, set())


# ---------------------------------------------------------------------------
# 1) TAČAN ŽIVI DEFEKT
# ---------------------------------------------------------------------------

def test_live_defect_is_rejected():
    clean, code = publish(LIVE_STEM, LIVE_OPTIONS, WRONG_INDEX)
    assert clean is None
    assert code == "root_interval_marked_interval_mismatch"


def test_corrected_live_package_publishes():
    clean, code = publish(LIVE_STEM, LIVE_OPTIONS, RIGHT_INDEX)
    assert clean is not None, code
    assert clean["correct_index"] == RIGHT_INDEX


# ---------------------------------------------------------------------------
# 2) VIŠE RADIKANADA — pravilo je matematičko, ne primjer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n,low", [
    (2, 1), (10, 3), (17, 4), (20, 4), (35, 5), (39, 6),
    (50, 7), (70, 8), (99, 9), (120, 10),
])
def test_correct_marked_interval_is_accepted(n, low):
    options = [pair(low, low + 1), pair(low + 2, low + 3),
               pair(low - 1, low), pair(low + 3, low + 4)]
    clean, code = publish(stem(n), options, 0, solution="Procjena kvadriranjem.")
    assert clean is not None, code


@pytest.mark.parametrize("n,wrong_low", [
    (35, 4), (70, 7), (99, 8), (120, 9), (20, 3), (50, 6),
])
def test_wrong_marked_interval_is_rejected(n, wrong_low):
    from math import isqrt
    true_low = isqrt(n)
    assert wrong_low != true_low                       # test sam sebe provjerava
    options = [pair(true_low, true_low + 1), pair(wrong_low, wrong_low + 1),
               pair(true_low + 2, true_low + 3), pair(true_low + 3, true_low + 4)]
    clean, code = publish(stem(n), options, 1, solution="Procjena kvadriranjem.")
    assert clean is None
    assert code == "root_interval_marked_interval_mismatch"


# ---------------------------------------------------------------------------
# 3) INTEGRITET OPCIJA
# ---------------------------------------------------------------------------

def test_correct_interval_absent_is_rejected():
    """Nijedna opcija ne nosi tačan interval → paket pada."""
    options = [pair(6, 7), pair(7, 8), pair(9, 10), pair(10, 11)]   # nema 8 i 9
    clean, code = publish(stem(70), options, 0, solution="Procjena.")
    assert clean is None
    assert code == "root_interval_correct_interval_absent"


def test_duplicated_correct_interval_is_rejected():
    """Dvije opcije tvrde ISTI tačan interval → dva tačna odgovora."""
    options = [pair(8, 9), pair(6, 7), "$8$ i $9$", pair(9, 10)]
    clean, code = publish(stem(70), options, 0, solution="Procjena.")
    assert clean is None
    # Postojeći `equivalent_options`/`duplicate_options` smiju uhvatiti prvi;
    # bitno je da paket NE prođe i da razlog bude jedan od ta tri.
    assert code in ("root_interval_correct_interval_duplicated",
                    "duplicate_options", "equivalent_options"), code


# ---------------------------------------------------------------------------
# 4) SVI OBLICI OPCIJA IZ STVARNOG KORPUSA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("option,expected", [
    ("Između $8$ i $9$", (8, 9)),        # živi nalaz
    ("između 6 i 7", (6, 7)),            # gola proza
    ("$49$ i $50$", (49, 50)),           # samo par
    ("$7<" + BS + "sqrt{50}<8$", (7, 8)),  # lanac nejednakosti
    ("Izmedju $4$ i $5$", (4, 5)),       # bez dijakritike
])
def test_corpus_option_forms_parse(option, expected):
    assert root_interval_mcq._option_interval(option) == expected


@pytest.mark.parametrize("option", [
    "Broj je iracionalan",
    "$" + BS + "sqrt{70}$",
    "nijedan od ponuđenih",
    "$8,37$",
])
def test_unreadable_options_are_not_guessed(option):
    assert root_interval_mcq._option_interval(option) is None


def test_unreadable_marked_option_stays_silent():
    """Ako se OZNAČENA opcija ne može pročitati, orakl ne tvrdi ništa."""
    options = [pair(8, 9), pair(6, 7), "nijedan od ponuđenih", pair(9, 10)]
    assert root_interval_mcq.publication_failure(stem(70), options, 2) == ""


# ---------------------------------------------------------------------------
# 5) POTPUN KVADRAT — zatečena politika, pribijena da se ne izmišlja
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 4, 9, 16, 25, 36, 49, 64, 81, 100])
def test_perfect_square_radicand_is_not_applicable(n):
    """„Između koja dva uzastopna broja" za $\\sqrt{64}$ nema dogovoreno
    značenje, a generator takav radikand u ovoj porodici nikad nije emitovao.
    Semantika se NE izmišlja: orakl ćuti, sude ga postojeći validatori."""
    result = root_interval_mcq.evaluate_root_interval_mcq(
        stem(n), [pair(1, 2), pair(2, 3), pair(3, 4), pair(4, 5)])
    assert result.applicable is False
    assert root_interval_mcq.publication_failure(
        stem(n), [pair(1, 2), pair(2, 3), pair(3, 4), pair(4, 5)], 0) == ""


# ---------------------------------------------------------------------------
# 6) NULA LAŽNIH POZITIVA NA NESRODNOM TEKSTU
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    # obična „između" proza bez korijena
    "Koji broj se nalazi između $3$ i $4$?",
    "Između koja dva uzastopna cijela broja se nalazi $-2,5$?",
    # korijen bez pitanja o uzastopnim brojevima
    "Koliko je " + sqrt(81) + "?",
    "Uprosti izraz " + sqrt(18) + ".",
    # „uzastopn" bez korijena
    "Zbir tri uzastopna prirodna broja je $42$. Koji su to brojevi?",
    # više korijena — oblik nije jednoznačan
    "Između koja dva uzastopna broja je zbir " + sqrt(2) + " i " + sqrt(3) + "?",
    # geometrija
    "Ugao između dijagonala kvadrata iznosi $90^" + BS + "circ$.",
])
def test_unrelated_questions_are_not_applicable(text):
    options = ["$1$ i $2$", "$2$ i $3$", "$3$ i $4$", "$4$ i $5$"]
    assert root_interval_mcq.evaluate_root_interval_mcq(text, options).applicable is False
    assert root_interval_mcq.publication_failure(text, options, 0) == ""


# ---------------------------------------------------------------------------
# 7) PART 12 — dosljednost rješenja: šta jeste, a šta NIJE pokriveno
# ---------------------------------------------------------------------------

def test_case_a_wrong_key_against_true_solution_is_rejected():
    """CASE A (živi nalaz): rješenje tačno, ključ pogrešan → REJECT."""
    clean, code = publish(LIVE_STEM, LIVE_OPTIONS, WRONG_INDEX, LIVE_SOLUTION)
    assert clean is None and code == "root_interval_marked_interval_mismatch"


def test_case_b_false_solution_with_correct_key_is_currently_accepted():
    """CASE B: ključ TAČAN, a rješenje matematički netačno → trenutno PROLAZI.

    Ovo NIJE odobravanje ponašanja — pribija se ZATEČENA politika. Ocjena
    učenika je ispravna (ključ je tačan), ali obrazloženje poslije predaje nije.
    Zatvaranje tog oblika traži čitanje PROZNE tvrdnje iz rješenja, što je
    izričito izvan opsega ovog popravka (nema fuzzy NLP parsera). Prijavljeno
    kao odvojen, još nedemonstriran nalaz."""
    false_solution = ("Pošto je $7^2=49$ i $8^2=64$, broj $70$ se nalazi između "
                      "$7^2$ i $8^2$. Zato se " + sqrt(70) + " nalazi između $7$ i $8$.")
    clean, _code = publish(LIVE_STEM, LIVE_OPTIONS, RIGHT_INDEX, false_solution)
    assert clean is not None


# ---------------------------------------------------------------------------
# 8) ZABRANA IZUZETAKA PO LEKCIJI / RAZREDU / RADIKANDU
# ---------------------------------------------------------------------------

def test_rule_contains_no_lesson_grade_or_radicand_special_cases():
    """Pravilo mora biti MATEMATIČKO. Gleda se IZVRŠNI kod preko AST-a —
    docstring smije (i treba) citirati živi nalaz, kod ne smije."""
    import ast
    import pathlib
    import re

    tree = ast.parse(pathlib.Path(root_interval_mcq.__file__).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    numbers, strings, names = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) and node.value not in docstrings:
                strings.append(node.value)
            elif isinstance(node.value, int) and not isinstance(node.value, bool):
                numbers.append(node.value)
        elif isinstance(node, ast.Name):
            names.append(node.id)

    assert not any(re.search(r"\d-\d\d-\d\d\d", s) for s in strings), "ID lekcije u kodu"
    assert not any("grade" in s for s in strings), "razred u kodu"
    assert not any("grade" in n for n in names), "razred u kodu"
    # Radikand iz živog nalaza (70) i njegov tačan interval (8, 9) ne smiju
    # postojati kao konstante — pravilo ih izvodi iz `isqrt`.
    for forbidden in (70, 8, 9):
        assert forbidden not in numbers, "konstanta iz živog nalaza u kodu"


# ---------------------------------------------------------------------------
# 9) POPRAVNA STAZA — novi kod se uklapa u POSTOJEĆU arhitekturu (bez retry petlje)
# ---------------------------------------------------------------------------
import json                                                        # noqa: E402

from tests.test_kontrolni import EchoKontrolniLLM, start_payload    # noqa: E402


class _RootIntervalLLM(EchoKontrolniLLM):
    """Slot 1 dobije zadatak porodice korijena; `heal` bira da li drugi
    (popravni) batch poziv vraća TAČAN ključ."""

    def __init__(self, heal):
        super().__init__()
        self.heal = heal

    def kontrolni_turn(self, instructions, input_text, timeout_s=None):
        result = super().kontrolni_turn(instructions, input_text, timeout_s)
        good = self.heal and self.batch_calls >= 2
        for question in result.output.questions:
            if question.slot != 1:
                continue
            question.text = stem(70)
            question.options = list(LIVE_OPTIONS)
            question.correct_option_index = RIGHT_INDEX if good else WRONG_INDEX
            question.expected_answer = LIVE_OPTIONS[question.correct_option_index]
            question.solution = LIVE_SOLUTION
        return result


def test_bad_draft_is_repaired_and_publishes():
    """Loš prvi nacrt → odbijen → POSTOJEĆI popravni poziv → objava. Maks. 2."""
    store = kontrolni.KontrolniStore()
    llm = _RootIntervalLLM(heal=True)
    _status, resp = kontrolni.run_start(store, llm, start_payload())
    assert resp.get("status") == "ready", resp
    assert llm.batch_calls == 2, "tačno jedan uslovni popravni poziv"
    assert len(resp["questions"]) == 5


def test_still_bad_after_repair_fails_closed():
    """Popravak i dalje nosi pogrešan ključ → cio test pada zatvoreno."""
    store = kontrolni.KontrolniStore()
    llm = _RootIntervalLLM(heal=False)
    _status, resp = kontrolni.run_start(store, llm, start_payload())
    assert resp.get("status") != "ready"
    assert llm.batch_calls == 2, "bez trećeg poziva"
    assert not (store.get("kontrolni-sess") or {}).get("questions")


def test_internal_code_never_reaches_the_client():
    """CLAUDE.md pravilo 7: kod ide u log, nikad u payload."""
    store = kontrolni.KontrolniStore()
    _status, resp = kontrolni.run_start(store, _RootIntervalLLM(heal=False),
                                        start_payload())
    body = json.dumps(resp, ensure_ascii=False)
    for code in ("root_interval", "marked_interval_mismatch",
                 "correct_interval_absent", "correct_interval_duplicated"):
        assert code not in body
