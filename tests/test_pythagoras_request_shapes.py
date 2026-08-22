# -*- coding: utf-8 -*-
"""Prepoznavač kanonskog oblika zahtjeva `pythagoras_direct` + semantička kapija.

ŽIVI NALAZ L5 (izdanje 6e785ad): „Pravougli trougao ima katete 3 cm i 4 cm.
Kolika je hipotenuza?" — 6. razred je dobio „hipotenuza je 5 cm". Zahtjev ne
imenuje ni Pitagoru ni korijen, pa ga eksplicitni detektor ne vidi; semantička
porodica lekcije ga NE MOŽE riješiti jer prati izabranu lekciju, ne zahtjev.

Ovaj fajl mjeri DOMET prepoznavača. Tvrdnja o tačnosti vrijedi TAČNO nad ovim
korpusom i ni riječ šire — oblik koji se ne prepozna nije zabranjen nego
nedokazan, pa turn ide modelu (fail-open).
"""
import json

import pytest

from matbot import capability_requests, practice_policy
from matbot.explain import run_explain_turn
from matbot.practice import SAFE_ERROR_MESSAGE
from matbot.semantics import request_shapes

EXPLAIN_CONTRACT = {"status", "answer", "answer_verdict", "last_tutor_task",
                    "next_state", "session_mode", "effective_topic"}


class CountingLLM:
    def __init__(self, reply="Neutralan odgovor."):
        self.calls = 0
        self.reply = reply

    def explain_turn(self, instructions, input_text):
        self.calls += 1

        class _Out:
            reply = self.reply

        class _Res:
            output = _Out()
            latency_ms = 1
            usage = {}

        return _Res()


def _turn(grade, topic, message):
    return {"session_id": "t", "grade": grade, "selected_topic": topic,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": "", "interaction_phase": "",
            "last_tutor_task": "", "last_tutor_message": "",
            "conversation_history": []}


# ---------------------------------------------------------------------------
# KORPUS — pozitivni oblici
# ---------------------------------------------------------------------------

POSITIVE = [
    # hipotenuza iz dvije katete — razne formulacije
    ("Pravougli trougao ima katete $3$ cm i $4$ cm. Kolika je hipotenuza?", "hypotenuse"),
    ("Katete su 3 i 4. Kolika je hipotenuza?", "hypotenuse"),
    ("Kolika je hipotenuza pravouglog trougla s katetama 6 cm i 8 cm?", "hypotenuse"),
    ("Izracunaj hipotenuzu ako su katete 6 cm i 8 cm.", "hypotenuse"),
    ("Izračunaj hipotenuzu ako su katete $5$ cm i $12$ cm.", "hypotenuse"),
    ("Odredi hipotenuzu trougla čije su katete 9 i 12.", "hypotenuse"),
    ("Katete pravouglog trougla su 1,5 cm i 2 cm. Koliko iznosi hipotenuza?", "hypotenuse"),
    ("Kolika je duljina hipotenuze ako su katete 3 i 4?", "hypotenuse"),
    ("U pravouglom trouglu katete su 7 cm i 24 cm, kolika je hipotenuza", "hypotenuse"),
    # nepoznata kateta
    ("Hipotenuza je 13 cm, jedna kateta 5 cm. Kolika je druga kateta?", "leg"),
    ("Izracunaj drugu katetu ako je hipotenuza 10 cm, a kateta 6 cm.", "leg"),
    ("Kolika je kateta ako je hipotenuza 25 i druga kateta 7?", "leg"),
    # dijagonala pravougaonika
    ("Pravougaonik ima stranice $6$ cm i $8$ cm. Kolika je dijagonala?", "rectangle_diagonal"),
    ("Kolika je dijagonala pravougaonika sa stranicama 6 i 8?", "rectangle_diagonal"),
    ("Odredi dijagonalu pravougaonika stranica 9 cm i 12 cm.", "rectangle_diagonal"),
    ("Pravokutnik ima stranice 5 i 12 cm, kolika je dijagonala", "rectangle_diagonal"),
    # dijagonala kvadrata
    ("Kvadrat ima stranicu $5$ cm. Kolika je dijagonala?", "square_diagonal"),
    ("Izracunaj dijagonalu kvadrata stranice 8 cm.", "square_diagonal"),
    # --- prosireno u pregledu: mjerni kvalifikator ispred prave velicine ---
    ("Kolika je duzina hipotenuze ako su katete 3 i 4?", "hypotenuse"),
    ("Kolika je dužina hipotenuze ako su katete 3 i 4?", "hypotenuse"),
    ("Kolika je veličina hipotenuze ako su katete 6 i 8?", "hypotenuse"),
    ("Kolika je dužina dijagonale pravougaonika 6 i 8?", "rectangle_diagonal"),
    ("Kolika je dužina dijagonale kvadrata stranice 5?", "square_diagonal"),
    # --- interpunkcija, skracenice, redoslijed ---
    ("Pravougaonik je 6 cm x 8 cm. Kolika mu je dijagonala?", "rectangle_diagonal"),
    ("katete 3 i 4 kolika je hipotenuza", "hypotenuse"),
    ("Izračunaj hipotenuzu, katete su 5 i 12.", "hypotenuse"),
    ("Odredi drugu katetu; hipotenuza 13, kateta 5.", "leg"),
    ("Kvadrat stranice 7, kolika je dijagonala?", "square_diagonal"),
    ("Kolika je dijagonala kvadrata čija je površina 16?", "square_diagonal"),
    ("Nađi hipotenuzu za katete 8 i 15.", "hypotenuse"),
]

# ---------------------------------------------------------------------------
# KORPUS — tvrdi negativi
# ---------------------------------------------------------------------------

HARD_NEGATIVE = [
    # pojmovna / definicijska pitanja
    "Šta je hipotenuza?",
    "Sta je hipotenuza?",
    "Koje stranice su katete?",
    "Koja stranica je hipotenuza?",
    "Šta je pravougli trougao?",
    "Šta je kateta?",
    "Kada se uči Pitagorina teorema?",
    "U kojem razredu se uči Pitagorina teorema?",
    "Objasni mi Pitagorinu teoremu.",
    "Pitagorina teorema se uči kasnije.",
    "Zašto se hipotenuza tako zove?",
    # traži se DRUGA veličina, iako su katete date — sve su u gradivu
    "Kolika je površina trougla čije su katete 3 i 4?",
    "Koliki je obim trougla sa stranicama 3, 4 i 5?",
    "Kolika je površina pravougaonika 6 i 8?",
    "Koliki je obim pravougaonika stranica 6 cm i 8 cm?",
    "Kolika je površina kvadrata stranice 5?",
    "Koliki je obim kvadrata stranice 5 cm?",
    "Kolika je visina trougla ako je površina 24 i osnovica 6?",
    # uglovi
    "Kolika je mjera ugla ako je drugi ugao 30 stepeni?",
    "Koliki je zbir uglova u trouglu?",
    # potpuno nevezano
    "Koliko je $\\frac{1}{2}+\\frac{1}{3}$?",
    "Riješi $x + 2 = 5$.",
    "Koliko je $12 \\cdot 4$?",
    "Objasni mi sabiranje razlomaka.",
    # spominje figuru, ali ne traži nikakvu veličinu
    "Nacrtaj pravougli trougao.",
    "Pravougaonik ima četiri prava ugla.",
    # --- prosireno u pregledu: rijec jedinice NIJE figura ---
    "Površina je 20 kvadratnih centimetara. Kolika je dijagonala?",
    "Pravougaonik ima površinu 20 kvadratnih cm. Kolika je dijagonala?",
    # --- Pitagorin rjecnik + DRUGA trazena velicina ---
    "Kolika je površina pravouglog trougla čije su katete 3 i 4?",
    "Koliki je obim pravouglog trougla sa katetama 3 i 4 i hipotenuzom 5?",
    "Kolika je visina na hipotenuzu ako je površina 24?",
    "Koliki je ugao između katete i hipotenuze?",
    "Kolika je dužina stranice kvadrata površine 16?",
    # --- dijagonale bez zahtjeva za duzinom ---
    "Da li pravougaonik ima dijagonale?",
    "Nacrtaj dijagonalu pravougaonika.",
    "Koja dijagonala je duža?",
    "Koliko dijagonala ima pravougaonik?",
    # --- pojmovna pitanja, dodatne formulacije ---
    "Koja stranica pravouglog trougla se zove hipotenuza?",
    "Objasni šta su katete.",
    "Šta je Pitagorina teorema?",
    "Zbog čega je hipotenuza najduža stranica?",
    # --- tekst nalik generisanom odgovoru / MCQ opciji ---
    "Povučem visinu iz pravog ugla na hipotenuzu.",
    "Izmjerim samo kraću katetu.",
    "Dijagonale su jednake.",
    "Izbrišem dijagonalu AC i nacrtam proizvoljan krug.",
    # --- malformirano, ali nije zahtjev za racunom ---
    "hipotenuza kateta dijagonala",
    "??? katete ???",
    "",
]


@pytest.mark.parametrize("message,shape", POSITIVE)
def test_positive_corpus_resolves_the_canonical_shape(message, shape):
    assert request_shapes.recognise_pythagoras_direct(message) == shape


@pytest.mark.parametrize("message", HARD_NEGATIVE)
def test_hard_negative_corpus_never_claims_a_shape(message):
    assert request_shapes.recognise_pythagoras_direct(message) == ""


def test_corpus_has_zero_measured_false_positives_and_negatives():
    """Mjera domet: brojevi se drže u testu, da regresija bude vidljiva."""
    false_negative = [m for m, shape in POSITIVE
                      if request_shapes.recognise_pythagoras_direct(m) != shape]
    false_positive = [m for m in HARD_NEGATIVE
                      if request_shapes.recognise_pythagoras_direct(m)]
    assert false_negative == [], false_negative
    assert false_positive == [], false_positive
    assert len(POSITIVE) == 30 and len(HARD_NEGATIVE) == 48


# ---------------------------------------------------------------------------
# DEKLARACIJA U PODACIMA
# ---------------------------------------------------------------------------

def test_every_recognised_shape_is_declared_in_canonical_data():
    declared = request_shapes._shape_requirements()["pythagoras_direct"]
    for _, shape in POSITIVE:
        assert shape in declared, shape
        assert declared[shape] == ("pythagoras_operation",)


def test_declared_shapes_are_a_subset_of_the_family_kinds():
    payload = json.loads(request_shapes.FAMILIES_PATH.read_text(encoding="utf-8"))
    family = payload["families"]["pythagoras_direct"]
    kinds = set(family["parameter_schema"]["kinds"]["values"])
    assert set(family["shape_required_operations"]) <= kinds


def test_declared_operations_use_the_shared_capability_vocabulary():
    for family, shapes in request_shapes._shape_requirements().items():
        for shape, operations in shapes.items():
            for operation in operations:
                assert operation in capability_requests.KNOWN_OPERATIONS, \
                    (family, shape, operation)


def test_unknown_operation_never_blocks():
    """Fail-open: nedokazano nikad ne znaci zabranjeno."""
    assert capability_requests.operation_allowed(
        "nepostojeca_operacija", practice_policy.resolve(grade=6)) is True


# ---------------------------------------------------------------------------
# INTEGRACIJA KROZ STVARNI EXPLAIN PUT
# ---------------------------------------------------------------------------

L5 = "Pravougli trougao ima katete $3$ cm i $4$ cm. Kolika je hipotenuza?"
DIAGONAL = "Pravougaonik ima stranice $6$ cm i $8$ cm. Kolika je dijagonala?"
UNKNOWN_LEG = "Hipotenuza je 13 cm, jedna kateta 5 cm. Kolika je druga kateta?"


@pytest.mark.parametrize("grade,lesson", ((6, "6-09-003"), (7, "7-04-019")))
@pytest.mark.parametrize("message", (L5, DIAGONAL, UNKNOWN_LEG))
def test_operational_request_is_blocked_before_the_model(grade, lesson, message):
    llm = CountingLLM(reply="Hipotenuza je $5\\,\\text{cm}$.")
    result = run_explain_turn(llm, _turn(grade, lesson, message))
    assert llm.calls == 0, "model je pozvan za zabranjenu operaciju"
    assert result["status"] == "ready"
    assert result["answer"] != SAFE_ERROR_MESSAGE
    assert set(result) == EXPLAIN_CONTRACT


@pytest.mark.parametrize("grade,lesson", ((6, "6-09-003"), (7, "7-04-019")))
def test_blocked_answer_never_reveals_the_result(grade, lesson):
    llm = CountingLLM()
    answer = run_explain_turn(llm, _turn(grade, lesson, L5))["answer"]
    assert "5 cm" not in answer
    assert "5\\,\\text{cm}" not in answer
    assert "Pitagorina teorema" in answer


def test_rectangle_diagonal_answer_never_reveals_ten():
    llm = CountingLLM()
    answer = run_explain_turn(llm, _turn(6, "6-09-003", DIAGONAL))["answer"]
    assert llm.calls == 0
    assert "10" not in answer


@pytest.mark.parametrize("message", (L5, DIAGONAL, UNKNOWN_LEG))
def test_grade_eight_is_never_intercepted(message):
    llm = CountingLLM(reply="Rezultat je $5\\,\\text{cm}$.")
    result = run_explain_turn(llm, _turn(8, "8-04-001", message))
    assert llm.calls == 1
    assert result["answer"] == "Rezultat je $5\\,\\text{cm}$."


@pytest.mark.parametrize("grade,lesson", ((6, "6-09-003"), (7, "7-04-019")))
@pytest.mark.parametrize("message", (
    "Šta je hipotenuza?", "Šta je pravougli trougao?",
    "Kada se uči Pitagorina teorema?", "Koje stranice su katete?",
))
def test_conceptual_questions_still_reach_the_model(grade, lesson, message):
    llm = CountingLLM()
    run_explain_turn(llm, _turn(grade, lesson, message))
    assert llm.calls == 1, message


@pytest.mark.parametrize("grade,lesson,message", (
    (6, "6-09-003", "Kolika je površina trougla čije su katete 3 i 4?"),
    (6, "6-09-003", "Koliki je obim pravougaonika stranica 6 cm i 8 cm?"),
    (7, "7-04-019", "Kolika je površina pravougaonika 6 i 8?"),
))
def test_in_grade_ordinary_geometry_is_never_intercepted(grade, lesson, message):
    llm = CountingLLM()
    run_explain_turn(llm, _turn(grade, lesson, message))
    assert llm.calls == 1, message


# ---------------------------------------------------------------------------
# POSTOJEĆE SPOSOBNOSTI OSTAJU NETAKNUTE
# ---------------------------------------------------------------------------

def test_existing_radical_explicit_gate_is_untouched():
    llm = CountingLLM()
    result = run_explain_turn(
        llm, _turn(6, "6-08-004", "Koliki je kvadratni korijen broja 49?"))
    assert llm.calls == 0
    assert "8. razredu" in result["answer"]


def test_grade_seven_radical_recognition_still_reaches_the_model():
    llm = CountingLLM(reply="Broj $\\sqrt{2}$ nije racionalan.")
    result = run_explain_turn(
        llm, _turn(7, "7-03-001", "Da li je $\\sqrt{2}$ racionalan broj?"))
    assert llm.calls == 1
    assert "\\sqrt{2}" in result["answer"]


def test_grade_seven_direct_root_computation_stays_zero_call():
    llm = CountingLLM()
    run_explain_turn(llm, _turn(7, "7-05-019", "Pojednostavi $\\sqrt{20}$."))
    assert llm.calls == 0


def test_grade_eight_root_operation_stays_model_backed():
    llm = CountingLLM(reply="$\\sqrt{169}=13$.")
    result = run_explain_turn(llm, _turn(8, "8-01-008", "Izračunaj $\\sqrt{169}$."))
    assert llm.calls == 1
    assert result["answer"] == "$\\sqrt{169}=13$."


def test_grade_six_equation_method_unaffected():
    llm = CountingLLM(reply="Nepoznati sabirak je $x=\\frac{3}{7}$.")
    result = run_explain_turn(
        llm, _turn(6, "6-07-002", "Riješi: $x + \\frac{2}{7} = \\frac{5}{7}$"))
    assert llm.calls == 1
    assert result["status"] == "ready"


# ---------------------------------------------------------------------------
# ARHITEKTURA
# ---------------------------------------------------------------------------

def test_resolver_is_request_scoped_not_lesson_scoped():
    """Ista poruka, ista odluka — bez obzira koju je lekciju učenik kliknuo."""
    for lesson in ("6-09-003", "6-08-004", "6-07-002", "6-13-004"):
        llm = CountingLLM()
        run_explain_turn(llm, _turn(6, lesson, L5))
        assert llm.calls == 0, lesson


def test_no_grade_literals_or_lesson_ids_in_the_semantic_layer():
    import re
    source = open(request_shapes.__file__, encoding="utf-8").read()
    assert not re.search(r"grade\s*[<>=]=?\s*\d", source)
    assert not re.search(r"[\"']\d-\d\d-\d\d\d[\"']", source)
    for banned in ("task_type", "answer_type"):
        assert banned not in source


# ---------------------------------------------------------------------------
# KOMPAJLER — nevaljana deklaracija MORA oboriti build, nikad se tiho ignorisati
# ---------------------------------------------------------------------------

def _load_compiler():
    import importlib.util
    from pathlib import Path
    root = Path(request_shapes.FAMILIES_PATH).parent.parent
    spec = importlib.util.spec_from_file_location(
        "build_lesson_semantics", root / "scripts" / "build_lesson_semantics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _minimal_family(**overrides):
    family = {
        "family_version": 1,
        "family_name": "test",
        "detector": "test",
        "core_skill": "test",
        "parameter_schema": {
            "kinds": {"kind": "enum_set", "values": ["hypotenuse", "leg"],
                      "required": True},
        },
        "enforced_parameters": ["kinds"],
        "advisory_parameters": [],
        "prompt_template": {"header": "h", "closing": "c"},
    }
    family.update(overrides)
    return {"probni": family}


def test_compiler_rejects_an_unknown_request_shape():
    compiler = _load_compiler()
    families = _minimal_family(
        shape_required_operations={"nepostojeci_oblik": ["pythagoras_operation"]})
    with pytest.raises(compiler.SemanticSchemaError, match="nepoznat oblik"):
        compiler._validate_shape_requirements(families)


def test_compiler_rejects_an_unknown_operation():
    compiler = _load_compiler()
    families = _minimal_family(
        shape_required_operations={"hypotenuse": ["teleportacija"]})
    with pytest.raises(compiler.SemanticSchemaError, match="nepoznatu operaciju"):
        compiler._validate_shape_requirements(families)


def test_compiler_rejects_an_empty_operation_list():
    compiler = _load_compiler()
    families = _minimal_family(shape_required_operations={"hypotenuse": []})
    with pytest.raises(compiler.SemanticSchemaError, match="nepraznu listu"):
        compiler._validate_shape_requirements(families)


def test_compiler_accepts_the_shipped_declaration():
    compiler = _load_compiler()
    compiler._validate_shape_requirements(compiler.load_families())


def test_family_without_the_declaration_is_still_valid():
    """Porodica bez deklaracije nije greska — samo nema dokaza (fail-open)."""
    compiler = _load_compiler()
    compiler._validate_shape_requirements(_minimal_family())


def test_compiled_artifact_stays_in_step_with_sources():
    """Deklaracija je na nivou PORODICE, pa kompajlirani artefakt ostaje
    bajt-identican — nijedna lekcija nije promijenjena."""
    compiler = _load_compiler()
    import json as _json
    expected = compiler.compile_all()
    actual = _json.loads(compiler.COMPILED_PATH.read_text(encoding="utf-8"))
    assert actual == expected
