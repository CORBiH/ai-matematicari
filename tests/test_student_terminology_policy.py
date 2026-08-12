"""Studentska terminološka politika (živi QA nalaz, direktor škole).

KURIKULARNI DOKAZ (reference/curriculum/semantics/MATBOT_Faza1_KS_RS_NPP):

  • NAZIVNIK/IMENILAC — oba su KS termina. Sheet `Terminologija` vodi ih kao
    EKSPLICITNI ALIAS („imenilac (nazivnik)“, KS_2018 str. 8), a KS_2018-0046
    nabraja „brojilac (brojnik), imenilac (nazivnik)“. Kanonski termin
    projekta je „nazivnik“ → dvojni oblik SAMO pri uvođenju pojma.

  • FAKTOR/ČINILAC — „faktor“ se u KS_2018 pojavljuje 56 puta u punom tekstu i
    13 puta u ishodima; „činilac“ NIJEDNOM (dolazi samo iz RS_2014, 8 pojava).
    Zato: „faktor“ jedini, bez dvojnog oblika — dvojni par ovdje nema
    kurikularno pokriće.

  • DJELILAC/DJELITELJ — „djelilac“ ima 42 pojave u KS_2018 i 9 u RS_2014;
    „djelitelj“ NIJEDNU ni u jednom izvoru. Zato: „djelilac“, a „djelitelj“ se
    NE uvodi.

Granica koja se ne smije izgubiti: „faktor“ je uloga u MNOŽENJU i u
rastavljanju na proste faktore, „djelilac“ u DIJELJENJU. To su različiti
pojmovi i nikad se ne zamjenjuju jedan drugim.
"""
import json
import random
import re
from pathlib import Path

import pytest

from matbot import deterministic as det
from matbot import practice_policy, rules, terminology
from matbot.tutor import package_preflight

ROOT = Path(__file__).resolve().parent.parent

IMENILAC = re.compile(r"\bimenil\w*|\bimenioc\w*|\bimenitelj\w*", re.I)
CINILAC = re.compile(r"\b[čc]inilac\w*|\b[čc]inioc\w*", re.I)
DJELITELJ = re.compile(r"\bdjelitelj\w*", re.I)
ALIAS_PAIR = re.compile(r"nazivnik\s*\(\s*imenil\w*\s*\)", re.I)


# ---------------------------------------------------------------------------
# 1) NORMALIZATOR — padeži, i to bez izmišljenog NLP-a
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,expected", [
    ("Nepoznati činilac je pet.", "Nepoznati faktor je pet."),
    ("Podijeli poznatim činiocem.", "Podijeli poznatim faktorom."),
    ("Rastavi na činioce.", "Rastavi na faktore."),
    ("Dva činioca su jednaka.", "Dva faktora su jednaka."),
    ("Činioci su 2 i 3.", "Faktori su 2 i 3."),
    ("Zbir činilaca je sedam.", "Zbir faktora je sedam."),
    ("Poznatim činiocima podijeli.", "Poznatim faktorima podijeli."),
    ("Vrijednost u činiocu.", "Vrijednost u faktoru."),
])
def test_cinilac_is_normalized_in_every_case(source, expected):
    assert terminology.normalize_terminology(source) == expected


@pytest.mark.parametrize("source,expected", [
    ("Zajednički imenilac je 12.", "Zajednički nazivnik je 12."),
    ("Razlomci jednakih imenilaca.", "Razlomci jednakih nazivnika."),
    ("Broj u imeniocu.", "Broj u nazivniku."),
    ("Pomnoži imeniocem.", "Pomnoži nazivnikom."),
])
def test_imenilac_is_normalized_in_every_case(source, expected):
    assert terminology.normalize_terminology(source) == expected


def test_normalization_never_touches_math_segments():
    text = r"Faktor $a \cdot b$ i nazivnik $\frac{1}{2}$ ostaju isti."
    assert terminology.normalize_terminology(text) == text


def test_neither_pair_is_a_forbidden_term_for_the_repo_scan():
    """Kurikularni podaci legitimno sadrže oba oblika — repo-sken ne smije pasti."""
    assert not terminology.contains_forbidden_term("imenilac")
    assert not terminology.contains_forbidden_term("činilac")
    # kontrola: stvarno zabranjen termin i dalje jeste zabranjen
    assert terminology.contains_forbidden_term("čimbenik")


# ---------------------------------------------------------------------------
# 2) SANKCIONISANI DVOJNI NAZIV se NE smije sažeti u „nazivnik (nazivnik)“
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Nazivnik (imenilac) kazuje na koliko je dijelova cjelina podijeljena.",
    "nazivnik (imenilac) je donji broj",
    "NAZIVNIK (IMENILAC) je donji broj",
    "Nazivnik ( imenilac ) sa razmacima.",
])
def test_sanctioned_alias_pair_survives_normalization(text):
    assert terminology.normalize_terminology(text) == text


def test_alias_pair_protection_is_narrow():
    """Zaštita važi SAMO za par; samostalni „imenilac“ se i dalje normalizuje."""
    assert terminology.normalize_terminology(
        "Nazivnik je 5, a imenilac je isti pojam.") == \
        "Nazivnik je 5, a nazivnik je isti pojam."


# ---------------------------------------------------------------------------
# 3) IZVORI: politika i deterministički šabloni
# ---------------------------------------------------------------------------

def test_unknown_factor_role_uses_faktor_not_cinilac():
    name, relation = practice_policy.UNKNOWN_ROLE_RELATIONS["unknown_factor"]
    assert name == "nepoznati faktor"
    assert not CINILAC.search(relation)
    assert not CINILAC.search(
        practice_policy.UNKNOWN_ROLE_EXPLANATIONS["unknown_factor"])


def test_divisor_roles_still_use_djelilac_not_faktor():
    """Granica: dijeljenje ostaje djelilac/djeljenik, nikad faktor."""
    for role in ("unknown_dividend", "unknown_divisor"):
        name, relation = practice_policy.UNKNOWN_ROLE_RELATIONS[role]
        blob = f"{name} {relation} {practice_policy.UNKNOWN_ROLE_EXPLANATIONS[role]}"
        assert "djel" in blob
        assert not re.search(r"\bfaktor", blob, re.I)


def test_no_cinilac_remains_in_any_deterministic_template():
    engine_dir = ROOT / "matbot" / "deterministic"
    offenders = []
    for path in sorted(engine_dir.glob("*.py")):
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if CINILAC.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# 4) MODEL RUTA — Tutor i Recenzent dobijaju ISTU terminologiju
# ---------------------------------------------------------------------------

def test_language_rules_carry_the_three_policies():
    block = rules.language_rules() if hasattr(rules, "language_rules") \
        else rules._LANGUAGE_RULES
    assert "nazivnik (imenilac)" in block          # dvojni oblik pri uvođenju
    assert "činilac" in block and "nikad" in block.lower()   # zabrana činioca
    assert "djelilac" in block                     # djeljivost ostaje djelilac
    assert "faktor" in block


def test_tutor_and_reviewer_receive_the_same_terminology_block():
    from matbot.tutor import prompts as tutor_prompts
    from matbot.tutor import lesson_context
    for lesson_id, grade in (("6-04-002", 6), ("6-07-004", 6), ("6-03-001", 6)):
        context = lesson_context.build(grade, lesson_id)
        tutor = tutor_prompts.build_tutor_instructions(context)
        reviewer = tutor_prompts.build_reviewer_instructions(context)
        for needle in ("nazivnik (imenilac)", "djelilac", "faktor"):
            assert needle in tutor, (lesson_id, needle, "tutor")
            assert needle in reviewer, (lesson_id, needle, "reviewer")


# ---------------------------------------------------------------------------
# 5) STUDENTSKI VIDLJIV KORPUS
# ---------------------------------------------------------------------------

def _published(text):
    cleaned, safe = package_preflight.safe_visible_text(text or "",
                                                        allow_wrap=True)
    return cleaned if safe else (text or "")


def _visible(package):
    yield package.question
    yield from package.option_texts
    yield from package.hints
    yield package.solution
    yield package.display_answer


def _generate(lesson_id, title, entry, level, seed, grade):
    module = det.GENERATORS[entry["family_id"]]
    policy = practice_policy.resolve(grade=grade, lesson_id=lesson_id)
    rng = random.Random(seed)
    try:
        return module.generate_package(lesson_id, title, entry["parameters"],
                                       level, rng=rng, policy=policy)
    except TypeError:
        return module.generate_package(lesson_id, title, entry["parameters"],
                                       level, rng=random.Random(seed))


def _contracted_lessons():
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))["lessons"]
    topics = json.loads((ROOT / "data" / "topics.json").read_text(
        encoding="utf-8"))
    meta = {l["id"]: (int(g), l["title"])
            for g, gd in topics["grades"].items() for l in gd["lessons"]}
    for lesson_id, entry in sorted(compiled.items()):
        module = det.GENERATORS.get(entry["family_id"])
        if module is None or not module.supports(entry["parameters"]):
            continue
        grade, title = meta.get(lesson_id, (0, lesson_id))
        yield lesson_id, title, entry, grade


def test_no_unexpected_alternate_term_reaches_the_student():
    """Jedini dozvoljeni „imenilac“ je sankcionisani par pri uvođenju pojma."""
    unexpected, parenthetical = [], 0
    for lesson_id, title, entry, grade in _contracted_lessons():
        for seed in range(3):
            for level in (1, 2, 3):
                package = _generate(lesson_id, title, entry, level, seed, grade)
                for raw in _visible(package):
                    text = _published(raw)
                    if CINILAC.search(text) or DJELITELJ.search(text):
                        unexpected.append((lesson_id, text))
                    for _hit in IMENILAC.finditer(text):
                        if ALIAS_PAIR.search(text):
                            parenthetical += 1
                        else:
                            unexpected.append((lesson_id, text))
    assert not unexpected, unexpected[:5]
    assert parenthetical > 0, "sankcionisani par se nigdje ne pojavljuje"


def test_the_alias_pair_appears_only_where_the_term_is_introduced():
    """Dvojni oblik NIJE u svakom zadatku — samo u definicijskom nagovještaju."""
    lessons_with_pair = set()
    for lesson_id, title, entry, grade in _contracted_lessons():
        for seed in range(3):
            for level in (1, 2, 3):
                package = _generate(lesson_id, title, entry, level, seed, grade)
                if any(ALIAS_PAIR.search(_published(t)) for t in _visible(package)):
                    lessons_with_pair.add(lesson_id)
    assert lessons_with_pair == {"6-04-002"}, lessons_with_pair


# ---------------------------------------------------------------------------
# 6) SEMANTIČKE GRANICE — kontrolni testovi protiv globalne zamjene
# ---------------------------------------------------------------------------

def test_factorization_lessons_still_say_faktor():
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))["lessons"]
    entry = compiled["6-03-007"]          # Rastavljanje na proste faktore
    seen = 0
    for seed in range(20):
        package = _generate("6-03-007", "Rastavljanje složenih brojeva na "
                            "proste faktore", entry, 1, seed, 6)
        if any("faktor" in _published(t).lower() for t in _visible(package)):
            seen += 1
    assert seen >= 10, seen


def test_divisibility_lessons_say_djelilac_and_never_faktor_for_the_divisor():
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))["lessons"]
    entry = compiled["6-03-001"]          # Djelilac i sadržilac prirodnog broja
    divisor_words = 0
    for seed in range(20):
        for level in (1, 2, 3):
            package = _generate("6-03-001", "Djelilac/faktor i sadržilac/"
                                "višekratnik prirodnog broja", entry, level,
                                seed, 6)
            blob = " ".join(_published(t) for t in _visible(package)).lower()
            if "djelilac" in blob or "djelioc" in blob:
                divisor_words += 1
            # nikad „faktor“ kao ime onoga čime se dijeli
            assert not re.search(r"faktor\w*\s+broja", blob), blob
    assert divisor_words >= 10, divisor_words


def test_numerator_terminology_is_untouched():
    assert terminology.normalize_terminology("Brojnik je gornji broj.") == \
        "Brojnik je gornji broj."
    assert terminology.normalize_terminology("Brojilac je gornji broj.") == \
        "Brojilac je gornji broj."


def test_curriculum_lesson_titles_are_not_rewritten():
    """Naslovi su kurikularni autoritet i ulaze u lesson_fidelity — ne diraju se."""
    topics = json.loads((ROOT / "data" / "topics.json").read_text(
        encoding="utf-8"))
    titles = {l["id"]: l["title"]
              for gd in topics["grades"].values() for l in gd["lessons"]}
    assert titles["6-03-001"] == \
        "Djelilac/faktor i sadržilac/višekratnik prirodnog broja"
    assert titles["6-04-001"] == \
        "Pojam razlomka, brojnik/brojilac i nazivnik/imenilac"
    assert titles["6-04-009"] == \
        "Sabiranje i oduzimanje razlomaka jednakih imenilaca"
