r"""Nagovještaj mora govoriti METODU KOJU ZADATAK TRAŽI, ne metodu porodice.

RUČNI NALAZ IZ POTPUNE 0-POZIVNE REVIZIJE: porodica `linear_system_direct` je
za dva različita OBLIKA zadatka servirala isti nagovještaj.

  • `verify_pair` pita „Koji od ponuđenih uređenih parova JESTE rješenje
    sistema?“ — a nagovještaj je glasio „Sistem se rješava supstitucijom ili
    metodom suprotnih koeficijenata…“. Učeniku se nalagalo da RIJEŠI sistem,
    iako zadatak traži samo da PROVJERI ponuđene parove. Mjereno: 3 lekcije
    (9-05-003, 9-05-004, 9-05-015) emituju isključivo taj oblik, pa je svaki
    njihov nagovještaj govorio pogrešan postupak.
  • `system_geometry` pita „U kakvom su međusobnom položaju te prave?“ — a
    dobijao je tekst o BROJU rješenja, koji prave ne spominje.

Uzrok je strukturni: `_k_verify_pair` gradi paket pozivom `_k_solve` i mijenja
samo pitanje i `operation`, pa je NASLIJEDIO tuđi nagovještaj. Popravka zato
nije novi tekst nego nova vlast: `hints[0]` je ČISTA FUNKCIJA server-vlasničkog
oblika zadatka (`_RULE_BY_OPERATION`), pa nasljeđivanje tuđe metode više nije
moguće po konstrukciji.

Produkcija servira TAČNO JEDAN nagovještaj; ovi testovi to ne mijenjaju, samo
dokazuju da je taj jedan tačan za svoj oblik. Nula modelskih poziva.
"""
import json
import random
import re
from fractions import Fraction
from pathlib import Path

import pytest

from matbot import mcq_integrity, option_equivalence, solution_consistency
from matbot.deterministic import systems
from matbot.tutor import pipeline

ROOT = Path(__file__).resolve().parent.parent

# Tekst koji je PRIJE popravke stizao na `verify_pair` — trajna zaštita od
# povratka, jer je nastao nasljeđivanjem a ne odlukom.
_SOLVE_RULE = systems._RULE_BY_OPERATION["solve_system"]

# Riječi koje znače RJEŠAVANJE cijelog sistema.
_SOLVE_METHOD_RE = re.compile(
    r"(supstitucij|metod(?:om|a) suprotnih koeficijenata|izrazi jednu nepoznatu"
    r"|izjedna[čc]i koeficijente|rje[šs]ava)", re.IGNORECASE)


def _lessons():
    payload = json.loads((ROOT / "data" / "lesson_semantic_assignments.json")
                         .read_text(encoding="utf-8"))["assignments"]
    return [row for row in payload if row.get("family_id") == "linear_system_direct"]


def _packages(kinds, levels=(1, 2, 3), seeds=25, lesson_id="9-05-003"):
    parameters = {"kinds": list(kinds)}
    out = []
    for level in levels:
        for seed in range(seeds):
            rng = random.Random(f"{lesson_id}|{level}|{seed}")
            out.append(systems.generate_package(lesson_id, "", parameters, level,
                                                rng=rng))
    return out


# ---------------------------------------------------------------------------
# 1-2. SVAKI OBLIK DOBIJA SVOJU METODU
# ---------------------------------------------------------------------------

def test_verify_pair_gets_a_substitution_hint_not_a_solve_hint():
    for package in _packages(["verify_pair"]):
        assert package.operation == "verify_pair"
        hint = package.hints[0]
        assert "uvrsti" in hint.lower(), hint
        assert re.search(r"obje\s+jedna[čc]", hint, re.IGNORECASE), hint
        assert not _SOLVE_METHOD_RE.search(hint), hint
        assert hint != f"{_SOLVE_RULE}.", "vraćen je naslijeđeni nagovještaj"


def test_solve_system_keeps_its_own_solving_method():
    for package in _packages(["solve"], lesson_id="9-05-007"):
        assert package.operation == "solve_system"
        assert _SOLVE_METHOD_RE.search(package.hints[0]), package.hints[0]


def test_the_verification_ladder_never_tells_the_student_to_solve():
    """Ljestvica 1→2→3 je rollback put; i ona mora govoriti traženu metodu."""
    for package in _packages(["verify_pair"]):
        for step in package.hints:
            assert not _SOLVE_METHOD_RE.search(step), step


# ---------------------------------------------------------------------------
# 3. OBLICI NE SMIJU DIJELITI JEDAN GENERIČKI NAGOVJEŠTAJ
# ---------------------------------------------------------------------------

def test_no_two_task_shapes_share_one_hint():
    hint_by_shape = {}
    for row in _lessons():
        for package in _packages(row["parameters"]["kinds"], seeds=12,
                                 lesson_id=row["lesson_id"]):
            hint_by_shape.setdefault(package.operation, set()).add(package.hints[0])
    assert len(hint_by_shape) >= 6, sorted(hint_by_shape)
    for shape, hints in hint_by_shape.items():
        assert len(hints) == 1, (shape, hints)
    seen = {}
    for shape, hints in hint_by_shape.items():
        hint = next(iter(hints))
        assert hint not in seen, (shape, seen.get(hint))
        seen[hint] = shape


def test_the_hint_is_a_pure_function_of_the_server_owned_shape():
    """Nema pogađanja iz teksta zadatka i nema grananja po lekciji."""
    for row in _lessons():
        for package in _packages(row["parameters"]["kinds"], seeds=8,
                                 lesson_id=row["lesson_id"]):
            expected = systems._RULE_BY_OPERATION[package.operation]
            assert package.hints[0] == f"{expected}.", package.operation


def test_an_undeclared_shape_fails_closed_instead_of_borrowing_a_method():
    """Radije nema zadatka nego zadatak koji uči pogrešan postupak."""
    original = dict(systems._RULE_BY_OPERATION)
    systems._RULE_BY_OPERATION.pop("verify_pair")
    try:
        with pytest.raises(Exception) as excinfo:
            _packages(["verify_pair"], levels=(1,), seeds=1)
        assert "verify_pair" in str(excinfo.value) or "paket nije nastao" in str(excinfo.value)
    finally:
        systems._RULE_BY_OPERATION.clear()
        systems._RULE_BY_OPERATION.update(original)


# ---------------------------------------------------------------------------
# 4-5. NAGOVJEŠTAJ NE ODAJE PRESUDU NI OPCIJU
# ---------------------------------------------------------------------------

def test_the_hint_never_reveals_the_verdict_or_the_marked_option():
    verdict = re.compile(r"(ta[čc]an odgovor|odgovor je|opcija|jeste rje[šs]enje"
                         r"|nije rje[šs]enje)", re.IGNORECASE)
    for row in _lessons():
        for package in _packages(row["parameters"]["kinds"], seeds=10,
                                 lesson_id=row["lesson_id"]):
            hint = package.hints[0]
            assert not verdict.search(hint), hint
            assert package.display_answer not in hint
            for option in package.option_texts:
                assert option.strip("$") not in hint, (option, hint)
            # Nagovještaj `verify_pair`-a je čista uputa o postupku: ne smije
            # nositi nijedan broj iz zadatka, jer bi tako radio i dio posla.
            if package.operation == "verify_pair":
                assert not re.search(r"\d", hint), hint


# ---------------------------------------------------------------------------
# 6-8. JEDAN NAGOVJEŠTAJ OSTAJE JEDAN NAGOVJEŠTAJ
# ---------------------------------------------------------------------------

class _NoModel:
    """Svaki modelski poziv je greška."""

    def __getattr__(self, name):
        def explode(*args, **kwargs):
            raise AssertionError(f"deterministička lekcija je pozvala model: {name}")
        return explode


def _turn(session_id, lesson_id, message, request="", intent=""):
    return {"session_id": session_id, "grade": 9, "selected_topic": lesson_id,
            "selected_oblast": "", "student_message": message, "intent": intent,
            "difficulty_request": request, "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


@pytest.mark.parametrize("lesson_id", ["9-05-003", "9-05-004", "9-05-015"])
def test_one_hint_stays_one_hint_and_costs_no_call(lesson_id, monkeypatch):
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    store, llm = SessionStore(), _NoModel()
    session_id = f"hint-{lesson_id}"
    run_practice_turn(store, llm, _turn(session_id, lesson_id, "Daj mi zadatak."))

    hints = [run_practice_turn(store, llm,
                               _turn(session_id, lesson_id, "Ne znam",
                                     intent="hint_request"))["answer"]
             for _ in range(3)]
    assert hints[0] == hints[1] == hints[2], "ponovljeni klik mora vratiti ISTI tekst"
    assert "uvrsti" in hints[0].lower()
    assert not _SOLVE_METHOD_RE.search(hints[0]), hints[0]

    solution = run_practice_turn(store, llm,
                                 _turn(session_id, lesson_id, "Uradi ga ti",
                                       intent="solution_request"))
    assert solution.get("answer")
    assert solution["answer"] != hints[0], "puno rješenje je zasebna radnja"


@pytest.mark.parametrize("message,difficulty_request", [
    ("Daj mi novi zadatak.", ""),
    ("Daj mi teži zadatak.", "harder"),
    ("Daj mi lakši zadatak.", "easier"),
])
def test_a_new_task_clears_the_stored_hint(message, difficulty_request, monkeypatch):
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    store, llm = SessionStore(), _NoModel()
    session_id = f"reset-{difficulty_request or 'new'}"
    run_practice_turn(store, llm, _turn(session_id, "9-05-003", "Daj mi zadatak."))
    run_practice_turn(store, llm, _turn(session_id, "9-05-003", "Ne znam",
                                        intent="hint_request"))
    assert pipeline.stored_hint_for_active_task(store.peek(session_id) or {})
    run_practice_turn(store, llm,
                      _turn(session_id, "9-05-003", message, difficulty_request))
    assert not pipeline.stored_hint_for_active_task(store.peek(session_id) or {})


# ---------------------------------------------------------------------------
# 9. PUNO RJEŠENJE ZA `verify_pair` STVARNO PROVJERAVA PAR
# ---------------------------------------------------------------------------

_NUM = r"(?:-?\\frac\{\d+\}\{\d+\}|-?\d+)"
_PAIR_RE = re.compile(rf"\(\s*({_NUM})\s*,\s*({_NUM})\s*\)")
_EQ_RE = re.compile(
    rf"({_NUM}?[xy]|-[xy]|[xy])\s*([+-])\s*({_NUM}?[xy]|[xy])\s*=\s*({_NUM})")


def _value(text):
    match = re.fullmatch(r"(-?)\\frac\{(\d+)\}\{(\d+)\}", text.strip())
    if match:
        sign = -1 if match.group(1) else 1
        return sign * Fraction(int(match.group(2)), int(match.group(3)))
    return Fraction(int(text.strip()))


def _coefficient(token):
    token = token.strip()
    if token in ("x", "y"):
        return Fraction(1)
    if token in ("-x", "-y"):
        return Fraction(-1)
    return _value(token[:-1])


def test_the_full_solution_verifies_the_pair_in_both_equations():
    for package in _packages(["verify_pair"], seeds=15):
        solution = package.solution
        assert "obje jednakosti" in solution.lower(), solution
        assert solution.count("\\cdot") >= 4, solution
        pair = _PAIR_RE.search(package.display_answer)
        assert pair, package.display_answer
        px, py = _value(pair.group(1)), _value(pair.group(2))
        equations = []
        for match in _EQ_RE.finditer(package.question):
            a = _coefficient(match.group(1))
            b = _coefficient(match.group(3))
            if match.group(2) == "-":
                b = -b
            equations.append((a, b, _value(match.group(4))))
        assert len(equations) == 2, package.question
        for a, b, c in equations:
            assert a * px + b * py == c, (package.question, package.display_answer)
        marked = package.option_texts[package.correct_index]
        assert _PAIR_RE.search(marked).groups() == pair.groups()
        diverged, _why = solution_consistency.divergence(package.display_answer,
                                                         solution)
        assert not diverged, solution


def test_exactly_one_option_solves_the_system_for_every_pair_shape():
    for kinds, lesson_id in ((["verify_pair"], "9-05-003"), (["solve"], "9-05-007")):
        for package in _packages(kinds, seeds=15, lesson_id=lesson_id):
            equations = []
            for match in _EQ_RE.finditer(package.question):
                a = _coefficient(match.group(1))
                b = _coefficient(match.group(3))
                if match.group(2) == "-":
                    b = -b
                equations.append((a, b, _value(match.group(4))))
            assert len(equations) == 2
            satisfying = []
            for option in package.option_texts:
                found = _PAIR_RE.search(option)
                assert found, option
                px, py = _value(found.group(1)), _value(found.group(2))
                if all(a * px + b * py == c for a, b, c in equations):
                    satisfying.append(option)
            assert satisfying == [package.option_texts[package.correct_index]], (
                package.question, satisfying)
            assert not option_equivalence.find_equivalent_option_pairs(
                list(package.option_texts))
            oracle = mcq_integrity.evaluate_linear_solve_mcq(
                package.question, list(package.option_texts))
            if oracle.applicable:
                assert oracle.valid
                assert oracle.correct_indices == (package.correct_index,)


# ---------------------------------------------------------------------------
# 10. NEMA GRANANJA PO LEKCIJI
# ---------------------------------------------------------------------------

def test_the_generator_never_branches_on_a_lesson_id():
    """Oblik zadatka je server-vlasnička činjenica; lekcija na njega ne utiče.

    Da ijedan ID lekcije uopšte postoji u motorima — i u kodu i u komentaru —
    zabranjuje već `test_deterministic_capability_engines.py`. Ovdje se
    dokazuje jača, strukturna tvrdnja: `lesson_id` samo PROLAZI kroz generator
    i ne učestvuje ni u jednoj odluci."""
    import ast

    path = ROOT / "matbot" / "deterministic" / "systems.py"
    source = path.read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", source), "ID lekcije u generatoru"

    # `lesson_id` smije samo PROĆI kroz generator do `build_package`; ne smije
    # ući ni u jedno poređenje, uslov ili pretragu po ključu.
    tree = ast.parse(source)

    def _mentions_lesson_id(node):
        return any(isinstance(child, ast.Name) and child.id == "lesson_id"
                   for child in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Compare, ast.Subscript)):
            assert not _mentions_lesson_id(node), ast.dump(node)[:200]
        if isinstance(node, (ast.If, ast.IfExp)):
            assert not _mentions_lesson_id(node.test), ast.dump(node.test)[:200]


def test_every_reachable_shape_declares_its_own_method():
    reachable = set()
    for row in _lessons():
        for package in _packages(row["parameters"]["kinds"], seeds=8,
                                 lesson_id=row["lesson_id"]):
            reachable.add(package.operation)
    assert reachable == set(systems._RULE_BY_OPERATION), (
        reachable ^ set(systems._RULE_BY_OPERATION))
