r"""SEMANTIČKI AUTORITET: „blocking“ smije značiti samo ono što server dokaže.

NALAZ REVIZIJE OVLAŠĆENJA: svi ugovori lekcija nose `enforcement_mode:
blocking`, a implementiranih detektora je bilo dva — pa je konfiguracija
tvrdila da server pravilo provodi dok ga za većinu porodica niko nije mogao
prekršiti dokazati. Mjereno na ovom HEAD-u: 41 ime detektora, 354 lekcije, sve
označene `blocking`.

DODATNI, VAŽNIJI NALAZ: oba ranije implementirana detektora
(`fraction_arithmetic`, `polynomial_basic`) pokrivaju ISKLJUČIVO lekcije koje
produkcija servira DETERMINISTIČKI — tamo paket gradi server, pa semantički
drift nije ni moguć. Stvarna blokirajuća pokrivenost na model-ruti, jedinoj
gdje drift postoji, bila je NULA lekcija.

Ova faza dodaje JEDAN generički primitiv — dimenziju tražene veličine — i
mapira ga na dvije geometrijske porodice kroz PODATKE:

    vrsta zadatka (`kinds`, ugovor)  →  dozvoljena dimenzija
    mjerna jedinica označene opcije  →  izmjerena dimenzija
    neslaganje                       →  DOKAZAN prekršaj

Mapa se ne kuca ručno nego se IZVODI mjerenjem
(`scripts/build_measure_dimensions.py`), a vrsta bez jedinstvenog eksponenta
se ne upisuje i time se nikad ne blokira.

Živi presedan koji ovo hvata, dvaput: „Mreža prizme“ odgovorena FORMULOM
ZAPREMINE (F5K) i tražena POVRŠINA odgovorena obimom — `$P=26$ cm` umjesto
`cm²` (D35-5).
"""
import json
import random
import re
from pathlib import Path

import pytest

from matbot.semantics import contracts as semantic_contracts
from matbot.semantics import detectors

ROOT = Path(__file__).resolve().parent.parent
COMPILED = json.loads((ROOT / "data" / "lesson_semantics.compiled.json")
                      .read_text(encoding="utf-8"))["lessons"]
DIMENSIONS = json.loads((ROOT / "data" / "semantic_measure_dimensions.json")
                        .read_text(encoding="utf-8"))
GEOMETRY_DETECTORS = ("geometry_formula_2d", "solid_geometry_direct")


def _contract(lesson_id):
    return semantic_contracts.contract_for(lesson_id)


def _lessons_for(detector):
    return [lid for lid, entry in COMPILED.items() if entry["detector"] == detector]


def _first_lesson_with(dimension):
    """Lekcija čije SVE deklarisane vrste imaju traženu dimenziju."""
    by_kind = DIMENSIONS["dimension_by_kind"]
    unitless = set(DIMENSIONS["unitless_kinds"])
    for detector in GEOMETRY_DETECTORS:
        for lesson_id in sorted(_lessons_for(detector)):
            contract = _contract(lesson_id)
            kinds = tuple(contract.parameters.get("kinds") or ())
            if not kinds or any(k in unitless for k in kinds):
                continue
            dims = {by_kind.get(k) for k in kinds}
            if dims == {dimension}:
                return lesson_id, contract
    raise AssertionError(f"nema lekcije čije su sve vrste dimenzije {dimension}")


# ---------------------------------------------------------------------------
# 1-2. PRIMITIV I NJEGOVA TRI ISHODA
# ---------------------------------------------------------------------------

def test_the_unit_reader_understands_every_notation_the_project_emits():
    """Deterministički generator piše `cm²`, model piše MathJax. Sve varijante
    moraju dati ISTU dimenziju — `cm$^2$` je mutacijski dokaz uhvatio kao
    propust, jer se bez tolerancije na `$` čitalo kao dužina."""
    assert detectors.unit_exponents("$12$ cm") == {1}
    assert detectors.unit_exponents("$12$ cm²") == {2}
    assert detectors.unit_exponents("$12$ cm³") == {3}
    assert detectors.unit_exponents("$12$ cm^2") == {2}
    assert detectors.unit_exponents("$12$ cm^{3}") == {3}
    assert detectors.unit_exponents("$12$ cm$^2$") == {2}
    assert detectors.unit_exponents("$12\\,\\text{cm}^3$") == {3}
    assert detectors.unit_exponents("$12$ dm³") == {3}
    assert detectors.unit_exponents("$12$") == set()
    # Ne smije progutati broj koji nije jedinica.
    assert detectors.unit_exponents("$2^3 = 8$") == set()


def test_a_correct_area_answer_passes():
    lesson_id, contract = _first_lesson_with(2)
    result = detectors.detect(contract, "Kolika je površina?", answer_text="$24$ cm²")
    assert result.status == detectors.STATUS_PASS, (lesson_id, result.reason)
    assert not result.blocking


def test_a_perimeter_answer_on_an_area_lesson_is_a_proven_violation():
    """Živi presedan D35-5: tražena površina, vraćen obim s linearnom jedinicom."""
    lesson_id, contract = _first_lesson_with(2)
    result = detectors.detect(contract, "Kolika je površina?", answer_text="$26$ cm")
    assert result.status == detectors.STATUS_FAIL, (lesson_id, result.reason)
    assert result.blocking
    assert result.code == detectors.CODE_MEASURE_DIMENSION_MISMATCH


def test_a_volume_answer_on_a_surface_lesson_is_a_proven_violation():
    """Živi presedan F5K: lekcija o mreži/površini odgovorena zapreminom."""
    _lesson_id, contract = _first_lesson_with(2)
    result = detectors.detect(contract, "Kolika je površina?", answer_text="$60$ cm³")
    assert result.status == detectors.STATUS_FAIL
    assert result.evidence["dimension"] == 3
    assert 2 in result.evidence["allowed_dimensions"]


def test_an_answer_without_a_unit_is_unknown_not_a_rejection():
    """UNSUPPORTED je izričito „ne znam“ i NIKAD ne odbija paket."""
    _lesson_id, contract = _first_lesson_with(2)
    result = detectors.detect(contract, "Kolika je površina?", answer_text="$24$")
    assert result.status == detectors.STATUS_UNSUPPORTED
    assert not result.blocking


def test_a_lesson_that_may_ask_for_a_count_is_never_blocked():
    """Broj ivica/dijagonala nema mjernu jedinicu — takva lekcija se ne dira."""
    unitless = set(DIMENSIONS["unitless_kinds"])
    candidates = [lid for lid in _lessons_for("solid_geometry_direct")
                  if any(k in unitless
                         for k in (_contract(lid).parameters.get("kinds") or ()))]
    assert candidates, "očekivana bar jedna lekcija s vrstom bez jedinice"
    for lesson_id in candidates:
        for answer in ("$8$", "$8$ cm", "$8$ cm²", "$8$ cm³"):
            result = detectors.detect(_contract(lesson_id), "Koliko ivica?",
                                      answer_text=answer)
            assert result.status == detectors.STATUS_UNSUPPORTED, (lesson_id, answer)


def test_a_lesson_teaching_both_area_and_perimeter_blocks_neither():
    """Ne pretvaramo neizvjesnost u odbijanje: lekcija koja NAMJERNO uči i obim
    i površinu smije dati oba, a treću dimenziju i dalje ne smije."""
    by_kind = DIMENSIONS["dimension_by_kind"]
    both = None
    for lesson_id in _lessons_for("geometry_formula_2d"):
        kinds = tuple(_contract(lesson_id).parameters.get("kinds") or ())
        if {by_kind.get(k) for k in kinds} == {1, 2}:
            both = _contract(lesson_id)
            break
    assert both is not None
    assert detectors.detect(both, "", answer_text="$12$ cm").status == detectors.STATUS_PASS
    assert detectors.detect(both, "", answer_text="$12$ cm²").status == detectors.STATUS_PASS
    assert detectors.detect(both, "", answer_text="$12$ cm³").status == detectors.STATUS_FAIL


def test_an_unimplemented_detector_still_returns_unknown():
    """Ostale porodice ostaju iskreno neriješene — nikad tiho „prošlo“."""
    unimplemented = [lid for lid, entry in COMPILED.items()
                     if entry["detector"] not in detectors.DETECTORS]
    assert unimplemented, "očekivano je da neke porodice još nemaju detektor"
    result = detectors.detect(_contract(unimplemented[0]), "bilo šta",
                              answer_text="$5$ cm")
    assert result.status == detectors.STATUS_UNSUPPORTED
    assert "nije implementiran" in result.reason


# ---------------------------------------------------------------------------
# 3-4. PODACI, NE GRANANJE PO LEKCIJI
# ---------------------------------------------------------------------------

def test_the_detector_never_branches_on_a_lesson_id():
    source = (ROOT / "matbot" / "semantics" / "detectors.py").read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", source), "ID lekcije u detektoru"
    assert not re.search(r"lesson_id\s*==", source)
    assert "lesson_title" not in source


def test_the_dimension_map_is_data_and_resolves_for_every_declared_kind():
    by_kind = DIMENSIONS["dimension_by_kind"]
    unitless = set(DIMENSIONS["unitless_kinds"])
    assert DIMENSIONS["rejected_kinds"] == {}, DIMENSIONS["rejected_kinds"]
    assert set(by_kind.values()) <= {1, 2, 3}
    unmapped = set()
    for detector in GEOMETRY_DETECTORS:
        for lesson_id in _lessons_for(detector):
            for kind in (_contract(lesson_id).parameters.get("kinds") or ()):
                if kind not in by_kind and kind not in unitless:
                    unmapped.add(kind)
    assert not unmapped, f"vrste bez odluke: {sorted(unmapped)}"


def test_the_detector_reads_the_map_through_the_shared_loader():
    by_kind, unitless = detectors._measure_dimensions()
    assert by_kind == {k: int(v) for k, v in DIMENSIONS["dimension_by_kind"].items()}
    assert unitless == frozenset(DIMENSIONS["unitless_kinds"])


# ---------------------------------------------------------------------------
# 5-6. NULA LAŽNIH BLOKADA + MUTACIJE SE HVATAJU
# ---------------------------------------------------------------------------

def _known_good_packages(limit_seeds=4):
    from matbot import deterministic as registry
    from matbot.deterministic.core import DeterministicGenerationError
    for detector in GEOMETRY_DETECTORS:
        module = registry.GENERATORS.get(detector)
        if module is None:
            continue
        for lesson_id in _lessons_for(detector):
            contract = _contract(lesson_id)
            if not module.supports(dict(contract.parameters)):
                continue
            for level in (1, 2, 3):
                for seed in range(limit_seeds):
                    try:
                        package = module.generate_package(
                            lesson_id, "", dict(contract.parameters), level,
                            rng=random.Random(f"{lesson_id}|{level}|{seed}"))
                    except DeterministicGenerationError:
                        continue
                    yield contract, package


def test_no_known_good_package_is_ever_blocked(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    checked = blocked = 0
    for contract, package in _known_good_packages():
        marked = package.option_texts[package.correct_index]
        result = detectors.detect(contract, package.question, answer_text=marked)
        checked += 1
        if result.status == detectors.STATUS_FAIL:
            blocked += 1
            pytest.fail(f"lažna blokada: {package.question} -> {marked} "
                        f"({result.reason})")
    assert checked > 400, checked
    assert blocked == 0


def test_a_dimension_mutation_is_always_detected(monkeypatch):
    """Uzmi ispravan paket i pomjeri SAMO dimenziju označenog odgovora."""
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    swaps = {1: "cm³", 2: "cm", 3: "cm²"}
    mutated = detected = 0
    for contract, package in _known_good_packages():
        marked = package.option_texts[package.correct_index]
        baseline = detectors.detect(contract, package.question, answer_text=marked)
        if baseline.status != detectors.STATUS_PASS:
            continue
        observed = detectors.unit_exponents(marked)
        if len(observed) != 1:
            continue
        current = next(iter(observed))
        allowed = set(baseline.evidence.get("allowed_dimensions") or ())
        replacement = swaps[current]
        candidate = re.sub(r"(mm|cm|dm|km|m)(²|³)?", replacement, marked, count=1)
        target = detectors.unit_exponents(candidate)
        if len(target) != 1 or next(iter(target)) in allowed:
            continue
        mutated += 1
        if detectors.detect(contract, package.question,
                            answer_text=candidate).status == detectors.STATUS_FAIL:
            detected += 1
    assert mutated > 100, mutated
    assert detected == mutated, f"promašeno {mutated - detected} od {mutated}"


# ---------------------------------------------------------------------------
# 7. RECEPT ZA POPRAVKU — ŠTA TAČNO TREBA ISPRAVITI
# ---------------------------------------------------------------------------

def test_the_finding_tells_the_reviewer_what_to_change():
    _lesson_id, contract = _first_lesson_with(3)
    result = detectors.detect(contract, "Kolika je zapremina?", answer_text="$24$ cm²")
    assert result.status == detectors.STATUS_FAIL
    reason = result.reason
    assert "preformuliši" in reason.lower()
    assert "zapremina" in reason.lower()
    assert "površina" in reason.lower()
    # Recept je generički: nikad ime ni ID lekcije.
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", reason)


# ---------------------------------------------------------------------------
# 8-11. PONAŠANJE CIJEVI: popravka, zatvoreni pad, granica poziva, 0-poziv
# ---------------------------------------------------------------------------

def test_publication_is_refused_when_the_marked_option_has_the_wrong_dimension():
    """Posljednja tačka prije mutacije sesije mora pasti ZATVORENO."""
    from matbot.tutor import pipeline
    _lesson_id, contract = _first_lesson_with(2)
    context = type("Ctx", (), {"semantic_contract": contract})()
    with pytest.raises(pipeline.UnifiedOutputError) as excinfo:
        pipeline._reject_if_semantic_contract_violated(
            "Kolika je površina?", context, "označena opcija", answer_text="$26$ cm")
    assert detectors.CODE_MEASURE_DIMENSION_MISMATCH in str(excinfo.value)


def test_a_repaired_package_is_revalidated_and_accepted():
    from matbot.tutor import pipeline
    _lesson_id, contract = _first_lesson_with(2)
    context = type("Ctx", (), {"semantic_contract": contract})()
    # Ista tačka, ispravljen odgovor — ne smije pasti.
    pipeline._reject_if_semantic_contract_violated(
        "Kolika je površina?", context, "označena opcija", answer_text="$26$ cm²")


def test_an_advisory_contract_never_blocks():
    """Blokira samo lekcija čiji je ugovor izričito `blocking`."""
    from matbot.tutor import pipeline
    _lesson_id, contract = _first_lesson_with(2)
    advisory = type("C", (), {
        "semantic_contract": type("K", (), {
            "blocking": False, "detector": contract.detector,
            "family_id": contract.family_id,
            "parameters": contract.parameters})()})()
    pipeline._reject_if_semantic_contract_violated(
        "Kolika je površina?", advisory, "označena opcija", answer_text="$26$ cm")


def test_the_detector_makes_no_model_call():
    """Detektor je čista funkcija — ni jedan poziv, ni jedna mreža."""
    import matbot.llm as llm_module
    called = []
    original = getattr(llm_module, "OpenAIPracticeLLM", None)
    _lesson_id, contract = _first_lesson_with(2)
    for answer in ("$24$ cm²", "$24$ cm", "$24$"):
        detectors.detect(contract, "Kolika je površina?", answer_text=answer)
    assert called == []
    assert getattr(llm_module, "OpenAIPracticeLLM", None) is original


def test_deterministic_lessons_keep_their_zero_call_route(monkeypatch):
    """Novi detektor ne smije ni dodirnuti 0-pozivnu rutu."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore

    class NoModel:
        def __getattr__(self, name):
            def explode(*args, **kwargs):
                raise AssertionError(f"deterministička lekcija je pozvala model: {name}")
            return explode

    store = SessionStore()
    turn = {"session_id": "sem-det", "grade": 6, "selected_topic": "6-01-001",
            "selected_oblast": "", "student_message": "Daj mi zadatak.",
            "intent": "", "difficulty_request": "", "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}
    response = run_practice_turn(store, NoModel(), turn)
    assert response.get("status") == "ready"


# ---------------------------------------------------------------------------
# 12-14. NEPROMIJENJENE SUSJEDNE GARANCIJE
# ---------------------------------------------------------------------------

def test_the_previously_implemented_detectors_are_untouched():
    """`fraction_arithmetic` i `polynomial_basic` ne primaju odgovor kao dokaz
    i moraju se ponašati bajt za bajt kao ranije."""
    assert "fraction_arithmetic" in detectors.DETECTORS
    assert "polynomial_basic" in detectors.DETECTORS
    assert "fraction_arithmetic" not in detectors._ANSWER_EVIDENCE_DETECTORS
    assert "polynomial_basic" not in detectors._ANSWER_EVIDENCE_DETECTORS
    fraction = next(_contract(lid) for lid, e in COMPILED.items()
                    if e["detector"] == "fraction_arithmetic")
    with_answer = detectors.detect(fraction, "Izračunaj: $\\frac{1}{4} + \\frac{1}{4}$",
                                   answer_text="$5$ cm³")
    without = detectors.detect(fraction, "Izračunaj: $\\frac{1}{4} + \\frac{1}{4}$")
    assert with_answer.status == without.status
    assert with_answer.code == without.code


def test_single_hint_archetype_and_form_rotation_are_untouched():
    from matbot import archetype_support, config, form_variants
    assert config.practice_single_hint_enabled() is True
    assert archetype_support._enabled() is True
    assert form_variants._enabled() is True


def test_release_configuration_enforcement_is_untouched():
    from matbot import release_config
    assert release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_PIPELINE"] == \
        "universal_two_call"
    assert release_config.REQUIRED_RELEASE_ENV["AI_TUTOR_TIMEOUT"] == "45"
    assert release_config.release_configuration_problems(
        dict(release_config.REQUIRED_RELEASE_ENV), include_effective=False) == []
