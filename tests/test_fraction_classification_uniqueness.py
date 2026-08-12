"""P0 — klasifikacijski MCQ razlomaka smije imati TAČNO JEDNU tačnu opciju.

ŽIVI NALAZ (QA nad objavljenim Practice paketom, lekcija 6-04-003): objavljeno
je „Koji od ponuđenih razlomaka je PRAVI?“ s opcijama $\\frac{1}{9}$,
$\\frac{24}{8}$, $\\frac{2}{8}$, $\\frac{9}{8}$. DVIJE opcije su bile pravi
razlomci, a server je priznavao samo jednu — matematički tačan odgovor učenika
($\\frac{1}{9}$) ocijenjen je kao NETAČAN.

Uzrok je bio u konstrukciji: distraktori su birani po ULOZI („po jedan primjer
svake vrste + još jedan pravi razlomak“), a ne po predikatu tražene vrste.
Isti defekt je pogađao i „Koji je NEPRAVI?“, jer je PRIVIDNI razlomak po
kurikularnoj definiciji PODSKUP nepravih.

Ovi testovi ne kodiraju brojeve iz screenshota: svaku opciju NEZAVISNO
klasifikuju predikatom koji porodica posjeduje
(`fractionconcepts.FRACTION_TYPE_PREDICATES`) i traže sumu tačno 1.
"""
import random
import re
from fractions import Fraction

import pytest

from matbot.deterministic import fractionconcepts as fc
from matbot.deterministic.core import DeterministicGenerationError
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM

LESSON_ID = "6-04-003"
LESSON_TITLE = "Pravi, nepravi i prividni razlomci; mješoviti brojevi"
TYPES_PARAMETERS = {"concepts": ["fraction_types"]}
FRAC_RE = re.compile(r"\\frac\{(\d+)\}\{(\d+)\}")
WORD_TO_KIND = {"PRAVI": "proper", "NEPRAVI": "improper", "PRIVIDNI": "apparent"}


# ---------------------------------------------------------------------------
# KURIKULARNI PREDIKATI — dokaz da su definicije one koje lekcija podučava
# ---------------------------------------------------------------------------

def test_curriculum_predicates_match_taught_definitions():
    # pravi: brojnik < nazivnik
    assert fc.is_proper_fraction(1, 9) and fc.is_proper_fraction(2, 8)
    assert not fc.is_proper_fraction(9, 8) and not fc.is_proper_fraction(8, 8)
    # nepravi: brojnik >= nazivnik (uključuje jednakost)
    assert fc.is_improper_fraction(9, 8) and fc.is_improper_fraction(8, 8)
    assert not fc.is_improper_fraction(1, 9)
    # prividni: vrijednost je cio broj — i JESTE podskup nepravih
    assert fc.is_apparent_fraction(24, 8) and fc.is_improper_fraction(24, 8)
    assert not fc.is_apparent_fraction(9, 8)


def options_of(package):
    """(brojnik, nazivnik) svake opcije — opcija koja nije jedan razlomak pada."""
    pairs = []
    for text in package.option_texts:
        found = FRAC_RE.findall(text)
        assert len(found) == 1, f"opcija nije jedan razlomak: {text}"
        pairs.append((int(found[0][0]), int(found[0][1])))
    return pairs


def generate_types_packages(levels=(1, 2, 3), seeds=range(120)):
    """Paketi PRAVE produkcijske porodice; klasifikacijske varijante odvojeno."""
    for seed in seeds:
        for level in levels:
            rng = random.Random((seed << 4) | level)
            try:
                package = fc.generate_package(
                    LESSON_ID, LESSON_TITLE, TYPES_PARAMETERS, level, rng=rng)
            except DeterministicGenerationError:
                continue
            yield package


def classification_packages(**kwargs):
    for package in generate_types_packages(**kwargs):
        kind = dict(package.signature_parameters).get("kind")
        if kind in fc.FRACTION_TYPE_PREDICATES:
            yield kind, package


# ---------------------------------------------------------------------------
# 1) REGRESIJA SCREENSHOT KLASE — „Koji je PRAVI?“
# ---------------------------------------------------------------------------

def test_proper_fraction_mcq_has_exactly_one_proper_option():
    seen = 0
    for kind, package in classification_packages():
        if kind != "proper":
            continue
        seen += 1
        assert "je PRAVI?" in package.question
        pairs = options_of(package)
        assert sum(fc.is_proper_fraction(*pair) for pair in pairs) == 1, (
            package.question, package.option_texts)
    assert seen >= 20, "premalo uzoraka varijante 'pravi'"


def test_screenshot_option_set_is_no_longer_producible():
    """Konkretan objavljeni skup opcija ima DVIJE tačne — ne smije nastati."""
    screenshot = {(1, 9), (24, 8), (2, 8), (9, 8)}
    assert sum(fc.is_proper_fraction(*pair) for pair in screenshot) == 2
    for _kind, package in classification_packages(seeds=range(400)):
        assert set(options_of(package)) != screenshot


# ---------------------------------------------------------------------------
# 2) SVOJSTVO PORODICE — sve klasifikacijske varijante, svi nivoi, mnogo sjemena
# ---------------------------------------------------------------------------

def test_every_classification_variant_marks_exactly_one_true_option():
    counts = {}
    for kind, package in classification_packages(seeds=range(200)):
        predicate = fc.FRACTION_TYPE_PREDICATES[kind]
        pairs = options_of(package)
        flags = [predicate(*pair) for pair in pairs]
        assert sum(flags) == 1, (kind, package.question, package.option_texts)
        # označena opcija zadovoljava predikat, nijedna druga ne zadovoljava
        assert flags[package.correct_index] is True
        assert not any(flag for index, flag in enumerate(flags)
                       if index != package.correct_index)
        counts[kind] = counts.get(kind, 0) + 1
    assert set(counts) == {"proper", "improper", "apparent"}
    assert min(counts.values()) >= 20, counts


def test_apparent_fraction_never_offered_as_distractor_for_improper():
    """Prividni JE nepravi — kao distraktor bi bio druga tačna opcija."""
    seen = 0
    for kind, package in classification_packages(seeds=range(200)):
        if kind != "improper":
            continue
        seen += 1
        pairs = options_of(package)
        marked = pairs[package.correct_index]
        for index, pair in enumerate(pairs):
            if index == package.correct_index:
                continue
            assert not fc.is_apparent_fraction(*pair), (package.option_texts,)
            assert not fc.is_improper_fraction(*pair), (package.option_texts,)
        assert fc.is_improper_fraction(*marked)
    assert seen >= 20


def test_options_are_distinct_values_not_only_distinct_texts():
    """Dvije opcije iste vrijednosti objava odbija (option_equivalence)."""
    for _kind, package in classification_packages(seeds=range(150)):
        values = [Fraction(n, d) for n, d in options_of(package)]
        assert len(set(values)) == 4, package.option_texts


# ---------------------------------------------------------------------------
# 3) MJEŠOVITI BROJ — vrijednosna varijanta iste porodice
# ---------------------------------------------------------------------------

_MIXED_RE = re.compile(r"^\$(\d+)\\frac\{(\d+)\}\{(\d+)\}\$$")


def test_mixed_number_variant_has_exactly_one_equal_option():
    seen = 0
    for package in generate_types_packages(levels=(3,), seeds=range(200)):
        if dict(package.signature_parameters).get("kind") != "mixed":
            continue
        seen += 1
        target_pairs = FRAC_RE.findall(package.question)
        assert len(target_pairs) == 1
        target = Fraction(int(target_pairs[0][0]), int(target_pairs[0][1]))
        equal = []
        for index, text in enumerate(package.option_texts):
            matched = _MIXED_RE.match(text)
            assert matched, text
            whole, numerator, denominator = (int(g) for g in matched.groups())
            if whole + Fraction(numerator, denominator) == target:
                equal.append(index)
        assert equal == [package.correct_index], (package.question,
                                                  package.option_texts)
    assert seen >= 20


# ---------------------------------------------------------------------------
# 4) STVARNI PUT ODGOVORA — objava, miješanje, ocjena klika (nula poziva)
# ---------------------------------------------------------------------------

def _turn(session_id, message, **changes):
    payload = {
        "session_id": session_id, "grade": 6, "selected_topic": LESSON_ID,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _asked_kind(question):
    for word, kind in WORD_TO_KIND.items():
        if f"je {word}?" in question:
            return kind
    return None


def test_published_task_and_every_click_agree_with_the_predicate(universal):
    """Nijedna matematički tačna opcija ne smije biti odbijena — PRAVI PUT."""
    checked, accepted, rejected = 0, 0, 0
    for index in range(24):
        session_id = f"frac-uniq-{index}"
        store, fake = SessionStore(), FakeLLM()
        response = run_practice_turn(store, fake, _turn(
            session_id, "Daj mi jedan zadatak za vježbu iz ove teme."))
        assert response["status"] == "ready" and fake.call_count == 0
        session = store.peek(session_id)
        kind = _asked_kind(session["current_task"])
        if kind is None:
            continue
        checked += 1
        predicate = fc.FRACTION_TYPE_PREDICATES[kind]
        truth = {}
        for option in session["current_options"]:
            found = FRAC_RE.findall(option["text"])
            assert len(found) == 1, option["text"]
            truth[option["id"]] = predicate(int(found[0][0]), int(found[0][1]))
        # tačno jedna objavljena opcija je tačna, i to baš označena
        assert sum(truth.values()) == 1, session["current_task"]
        assert truth[session["correct_option_id"]] is True

        for option_id, is_true in truth.items():
            click_store, click_fake = SessionStore(), FakeLLM()
            click_id = f"{session_id}-{option_id}"
            assert run_practice_turn(click_store, click_fake, _turn(
                click_id, "Daj mi jedan zadatak za vježbu iz ove teme.")
            )["status"] == "ready"
            click_session = click_store.peek(click_id)
            click_kind = _asked_kind(click_session["current_task"])
            if click_kind is None:
                continue
            click_predicate = fc.FRACTION_TYPE_PREDICATES[click_kind]
            text = next(o["text"] for o in click_session["current_options"]
                        if o["id"] == option_id)
            found = FRAC_RE.findall(text)
            math_true = (len(found) == 1
                         and click_predicate(int(found[0][0]), int(found[0][1])))
            verdict = run_practice_turn(click_store, click_fake, _turn(
                click_id, "[odgovor]", interaction_type="choice_answer",
                selected_option_id=option_id, client_turn_id="c1"))
            is_marked = option_id == click_session["correct_option_id"]
            if verdict["answer_verdict"] == "correct":
                accepted += 1
                assert is_marked and math_true
            else:
                rejected += 1
                # KLJUČNA TVRDNJA: odbijena opcija NIKAD ne zadovoljava predikat
                assert not math_true, (click_session["current_task"], text)
    assert checked >= 10 and accepted >= 1 and rejected >= 1


def test_shuffle_moves_only_position_not_the_semantic_answer(universal):
    """Miješanje mijenja poziciju; označeni odgovor ostaje isti TEKST."""
    original = fc.generate_package
    captured = {}

    def recording(*args, **kwargs):
        package = original(*args, **kwargs)
        captured["package"] = package
        return package

    fc.generate_package = recording
    try:
        positions = set()
        for index in range(40):
            session_id = f"frac-shuffle-{index}"
            store, fake = SessionStore(), FakeLLM()
            assert run_practice_turn(store, fake, _turn(
                session_id, "Daj mi jedan zadatak za vježbu iz ove teme.")
            )["status"] == "ready"
            session = store.peek(session_id)
            package = captured["package"]
            published = session["current_options"]
            # isti SKUP tekstova prije i poslije miješanja
            assert sorted(o["text"] for o in published) == \
                sorted(package.option_texts)
            marked = next(o["text"] for o in published
                          if o["id"] == session["correct_option_id"])
            assert marked == package.option_texts[package.correct_index]
            positions.add(session["correct_option_id"])
        assert len(positions) > 1, "miješanje ne mijenja poziciju"
    finally:
        fc.generate_package = original
