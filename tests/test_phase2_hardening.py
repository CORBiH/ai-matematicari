r"""HARDENING ARHITEKTURE POMOĆI PRIJE ŽIVOG TALASA. ZERO poziva modela.

Tri nalaza iz pregleda Faze 2, svaki sa vlastitim odjeljkom:

  A. KRATAK SIMBOLIČKI ODGOVOR NIJE DOKAZ RAČUNSKOG ZADATKA.
     Prvi oblik klasifikacije je glasio „najviše dvije prozne riječi izvan
     matematike ⇒ RAČUNSKI“. Legitimni zadaci PREPOZNAVANJA imaju upravo takve
     opcije — `$p \perp \alpha$`, `$A \subset B$`, `$A\cap B=\varnothing$`,
     `$\mathbb{Z}$`, `$\alpha+\beta=180^\circ$` — pa bi svi dobili
     MODEL-AUTORSKE nagovještaje 1 i 2, dakle upravo klasu koju je Faza 2
     zatvorila samo za prozne tvrdnje. Pravilo se okreće: `COMPUTATIONAL` traži
     POZITIVAN dokaz vrijednosnog oblika; sve ostalo ide serverskoj ljestvici.

  B. GENERIČKI PROTIVPRIMJER NA NIVOU 2 NIJE UNIVERZALNO VALJAN.
     „Zamisli situaciju u kojoj opcija NE bi vrijedila; jedan protivprimjer je
     dovoljan da tvrdnja padne“ vrijedi za univerzalne tvrdnje, a ne za
     egzistencijalne („prava i ravan MOGU imati…“ — takav distraktor stoji u
     živom TR-B1 paketu), ni za tvrdnje o KONKRETNOJ konfiguraciji, ni za
     prepoznavanje definicije. Server ne smije učiti nevaljanu metodu.

  C. OZNAKA SCENARIJA NIJE POKRIVENOST GRANE.
     Oblik opcija bira model pri generisanju, pa se klasa zna TEK poslije
     objave. Talas zato bilježi STVARNU serversku klasu; nepoklapanje je RUPA U
     POKRIVENOSTI, nikad kvar proizvoda.

Sve što je Faza 2 već dokazala ostaje u `tests/test_phase2_hint_architecture.py`
i ovdje se ne ponavlja.
"""
from __future__ import annotations

import copy

import pytest

from matbot import config, feedback, hint_policy
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE
from tests.conftest import FakeLLM, make_task_payload, make_tutor_draft, queue_two_call
from tools.practice_eval import checks as check_lib
from tools.practice_eval import release_contract


@pytest.fixture(autouse=True)
def _runtime(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")


# ---------------------------------------------------------------------------
# A1. POZITIVAN DOKAZ VRIJEDNOSNOG OBLIKA — po jednoj opciji
# ---------------------------------------------------------------------------

VALUE_SHAPES = (
    ("goli broj", "150"),
    ("negativan broj", "$-3$"),
    ("broj s jedinicom", "$12$ cm"),
    ("broj s imenicom", "$5$ učenika"),
    ("razlomak", r"$\frac{3}{4}$"),
    ("korijen", r"$\sqrt{2}$"),
    ("stepen", "$2^3$"),
    ("umnožak s pi", r"$4\pi$"),
    ("sam pi", r"$\pi$"),
    ("vrijednost promjenljive", "$x=3$"),
    ("nejednakost", "$x>3$"),
    ("nejednakost s komandom", r"$x\ge 4$"),
    ("dvostrana nejednakost", "$-2<x<0$"),
    ("interval s beskonačnošću", r"$(3,\infty)$"),
    ("brojevni skup", r"$\{1,2,3\}$"),
    ("skup s domenom", r"$\{x\in\mathbb{Q}\mid x>3\}$"),
    ("rješenje sistema", "$x=2, y=3$"),
    ("uređeni par", "$(2,3)$"),
    ("površina s oznakom", "$P=24$ cm"),
    ("ugao s oznakom", r"$\angle ABC=60^\circ$"),
    ("algebarski izraz s koeficijentom", "$x^2+1$"),
)

RECOGNITION_SHAPES = (
    ("okomitost prave i ravni", r"$p \perp \alpha$"),
    ("paralelnost prave i ravni", r"$p \parallel \alpha$"),
    ("pripadanje prave ravni", r"$p \subset \alpha$"),
    ("podskup", r"$A \subset B$"),
    ("jednakost skupova", "$A=B$"),
    ("disjunktnost", r"$A\cap B=\varnothing$"),
    ("unija kao zapis", r"$A\cup B$"),
    ("računata unija s imenima", r"$A\cap B=\{2\}$"),
    ("jednakost dva ugla", r"$\alpha=\beta$"),
    ("suplementnost dva ugla", r"$\alpha+\beta=180^\circ$"),
    ("brojevni domen", r"$\mathbb{Z}$"),
    ("čisto simbolički izraz", "$a+b$"),
    ("podudarnost", r"$\triangle ABC \cong \triangle DEF$"),
    ("prozna oznaka", "sjecište simetrala stranica"),
    ("prozna tvrdnja", "Unija sadrži sve elemente oba skupa."),
)


@pytest.mark.parametrize("label,option", VALUE_SHAPES, ids=lambda value: value)
def test_a_value_result_shape_is_positively_proven(label, option):
    assert hint_policy.value_shaped(option) is True, label


@pytest.mark.parametrize("label,option", RECOGNITION_SHAPES, ids=lambda value: value)
def test_a_recognition_shape_is_never_accepted_as_a_value(label, option):
    """NALAZ A, jezgro: kratak simbolički zapis NE dokazuje vrijednost."""
    assert hint_policy.value_shaped(option) is False, label


def test_every_condition_of_the_value_proof_is_load_bearing():
    """Nijedan od četiri uslova nije suvišan — svaki sam obara dokaz."""
    # 1. previše proze
    assert hint_policy.value_shaped("$12$ centimetara po svakoj stranici") is False
    # 2. nema broja ni brojevne konstante
    assert hint_policy.value_shaped("$x$") is False
    assert hint_policy.value_shaped(r"$\mathbb{R}$") is False
    # 3. komanda koja tvrdi odnos imenovanih objekata
    assert hint_policy.value_shaped(r"$3 \parallel 4$") is False
    # 4. dva imenovana objekta u istom dijelu odgovora
    assert hint_policy.value_shaped(r"$\alpha+\beta=180^\circ$") is False
    # …a razdvojene vrijednosti su i dalje vrijednosti
    assert hint_policy.value_shaped(r"$\alpha=60^\circ, \beta=120^\circ$") is True


# ---------------------------------------------------------------------------
# A2. KLASIFIKACIJA NAD CIJELIM SKUPOM OPCIJA (tražene regresije A–H)
# ---------------------------------------------------------------------------

SOLVE_INEQUALITY_TASK = r"Riješi nejednačinu: $x-1>2$."
SOLVE_EQUATION_TASK = r"Riješi jednačinu: $x+2=5$."

CLASSIFICATION_MATRIX = (
    # (oznaka, tekst zadatka, opcije, indeks označene, očekivana efektivna klasa)
    ("A goli brojevi", "Izračunaj: $75+75$.", ("150", "60", "75", "90"), 0,
     hint_policy.COMPUTATIONAL),
    ("B broj s jedinicom", "Koliki je obim trougla?",
     ("$12$ cm", "$10$ cm", "$7$ cm", "$60$ cm"), 0, hint_policy.COMPUTATIONAL),
    ("C izračunat razlomak", r"Izračunaj: $\frac{1}{2}+\frac{1}{4}$.",
     (r"$\frac{3}{4}$", r"$\frac{1}{4}$", r"$\frac{3}{8}$", r"$\frac{4}{3}$"), 0,
     hint_policy.COMPUTATIONAL),
    ("D simbolički geometrijski odnos",
     r"Prava $p$ nema zajedničkih tačaka s ravni $\alpha$. Koji zapis to opisuje?",
     (r"$p \perp \alpha$", r"$p \parallel \alpha$", r"$p \subset \alpha$",
      r"$p \cap \alpha = \{A\}$"), 0, hint_policy.PROPOSITIONAL),
    ("E simbolički odnos skupova",
     "Svaki element skupa $A$ je i element skupa $B$. Koji zapis to opisuje?",
     (r"$A\subset B$", "$A=B$", r"$A\cap B=\varnothing$", r"$B\subset A$"), 0,
     hint_policy.PROPOSITIONAL),
    ("F prozne tvrdnje", "Koja tvrdnja o uniji skupova je tačna?",
     ("Unija sadrži sve elemente oba skupa.",
      "Unija sadrži samo zajedničke elemente oba skupa.",
      "Unija je uvijek prazan skup kada su skupovi različiti.",
      "Unija sadrži samo elemente prvog skupa."), 0, hint_policy.PROPOSITIONAL),
    ("G mješovit dvosmislen skup", "Koliki je obim trougla?",
     ("$12$ cm", "Nije moguće odrediti bez podataka o svim stranicama.",
      "$10$ cm", "$7$ cm"), 0, hint_policy.PROPOSITIONAL),
    ("H1a vrijednost promjenljive uz DOKAZAN solve", SOLVE_EQUATION_TASK,
     ("$x=3$", "$x=7$", "$x=-3$", "$x=1$"), 0, hint_policy.COMPUTATIONAL),
    ("H1b isti oblik BEZ dokaza",
     "Koji zapis znači da je vrijednost promjenljive jednaka broju $3$?",
     ("$x=3$", "$x=7$", "$x=-3$", "$x=1$"), 0, hint_policy.PROPOSITIONAL),
    ("H2a skup rješenja uz DOKAZAN solve", SOLVE_INEQUALITY_TASK,
     ("$x>3$", r"$x\ge 4$", "$x>5$", "$x>1$"), 0, hint_policy.COMPUTATIONAL),
    ("H2b isti oblik BEZ dokaza",
     "Koja nejednačina opisuje sve brojeve desno od $3$, bez samog $3$?",
     ("$x>3$", r"$x\ge 4$", "$x>5$", "$x>1$"), 0, hint_policy.PROPOSITIONAL),
    ("domenska klasifikacija", "Kojem skupu pripada broj $-3$?",
     (r"$\mathbb{N}$", r"$\mathbb{Z}$", r"$\mathbb{Q}$", r"$\mathbb{R}$"), 1,
     hint_policy.PROPOSITIONAL),
    ("kratke prozne oznake", "Šta je centar opisane kružnice trougla?",
     ("sjecište simetrala stranica", "sjecište visina", "sjecište težišnica",
      "sjecište simetrala uglova"), 0, hint_policy.PROPOSITIONAL),
    ("rješenje sistema (orakl ćuti)", r"Riješi sistem: $x+y=5$ i $x-y=-1$.",
     ("$x=2, y=3$", "$x=3, y=2$", "$x=1, y=4$", "$x=4, y=1$"), 0,
     hint_policy.PROPOSITIONAL),
)


@pytest.mark.parametrize("label,task,options,marked,expected", CLASSIFICATION_MATRIX,
                         ids=lambda value: value if isinstance(value, str) else "")
def test_classification_matrix(label, task, options, marked, expected):
    assert hint_policy.effective_task_class(task, list(options), marked) == expected, \
        label


def test_the_same_symbolic_form_can_mean_a_value_or_a_proposition():
    """TRAŽENI DOKAZ ZAVRŠNOG PREGLEDA: ISTE opcije pod DVA teksta zadatka.

    Oblik opcije sam po sebi NE MOŽE odlučiti — razliku nosi tekst zadatka, a
    dokaz daje serverski orakl koji zadatak SAM RIJEŠI."""
    equation_options = ("$x=3$", "$x=7$", "$x=-3$", "$x=1$")
    inequality_options = ("$x>3$", r"$x\ge 4$", "$x>5$", "$x>1$")

    # PAR 1 — jednačina.
    assert hint_policy.effective_task_class(
        SOLVE_EQUATION_TASK, list(equation_options), 0) == hint_policy.COMPUTATIONAL
    assert hint_policy.effective_task_class(
        "Koji zapis znači da je vrijednost promjenljive jednaka broju $3$?",
        list(equation_options), 0) == hint_policy.PROPOSITIONAL

    # PAR 2 — nejednačina.
    assert hint_policy.effective_task_class(
        SOLVE_INEQUALITY_TASK, list(inequality_options), 0) == hint_policy.COMPUTATIONAL
    assert hint_policy.effective_task_class(
        "Na brojevnoj pravoj prikazani su svi brojevi desno od $3$, bez samog "
        "$3$. Koja nejednačina to opisuje?",
        list(inequality_options), 0) == hint_policy.PROPOSITIONAL

    # Oba skupa imaju NULA proznih riječi i identičan oblik opcija — ni jedna
    # mjera nad SAMIM opcijama ih ne može razlikovati.
    for options in (equation_options, inequality_options):
        for option in options:
            assert hint_policy.prose_word_count(option) == 0
            assert hint_policy.value_shaped(option) is True
            assert hint_policy.quantity_shaped(option) is False


def test_a_relation_form_is_never_computational_from_syntax_alone():
    """ZABRANJENI ISHOD: ni jedan relacijski oblik ne smije postati računski
    samo zato što sadrži broj."""
    relational = ("$x=3$", "$x>3$", r"$x\ge 4$", "$y=2x+1$", r"$A\cap B=\{2\}$",
                  r"$\{x\in\mathbb{Q}\mid x>3\}$", r"$\alpha=60^\circ$",
                  "$P=24$ cm")
    for option in relational:
        assert hint_policy.quantity_shaped(option) is False, option
        # Bez dokaza o zadatku — konzervativno propoziciono.
        assert hint_policy.effective_task_class(
            "Neki tekst zadatka bez čitljive relacije.", [option] * 4,
            0) == hint_policy.PROPOSITIONAL, option


def test_a_set_relation_and_a_computed_set_are_separated_by_the_same_rule():
    """Druga tražena dvojnost: `$\\{1,2,3\\}$` je izračunata veličina, a
    `$A\\cup B$` je zapis odnosa (prepoznavanje)."""
    computed = (r"$\{1,2,3\}$", r"$\{2\}$", r"$\{1,3\}$", r"$\{1,2\}$")
    notation = (r"$A\cup B$", r"$A\cap B$", r"$A\setminus B$", r"$B\setminus A$")
    assert hint_policy.effective_task_class(
        r"Neka je $A=\{1,2\}$ i $B=\{2,3\}$. Odredi $A\cup B$.",
        list(computed), 0) == hint_policy.COMPUTATIONAL
    assert hint_policy.effective_task_class(
        "Koji zapis označava skup svih elemenata koji su u bar jednom od skupova?",
        list(notation), 0) == hint_policy.PROPOSITIONAL


def test_the_task_text_is_part_of_the_classification_input():
    """ZAVRŠNA POPRAVKA: tekst zadatka je sada PRVI parametar klasifikatora.

    Bez toga je odluka gledala samo `(option_texts, marked_index)` i nije mogla
    razlikovati „riješi i odaberi $x>3$“ od „prepoznaj $x>3$“."""
    import inspect

    for function in (hint_policy.classify_visible_task,
                     hint_policy.effective_task_class):
        parameters = list(inspect.signature(function).parameters)
        assert parameters == ["task_text", "option_texts", "marked_index"], \
            (function, parameters)


def test_no_model_declared_field_can_choose_the_supervision_mode():
    """`task_type` / `answer_type` NEMAJU serversku validaciju vrijednosti, pa
    ne smiju birati ljestvicu. Dokaz: klasifikator prima samo OBJAVLJEN SADRŽAJ
    (tekst zadatka, opcije) i SERVERSKI indeks označene opcije."""
    import inspect

    signature_source = inspect.getsource(hint_policy.classify_visible_task)
    for forbidden in ("answer_type", "task_type", "task_signature",
                      "difficulty_evidence"):
        assert forbidden not in signature_source, forbidden
    # I invarijanta iz `validate_task`: vrijednost `answer_type` se nikad ne sudi.
    from matbot.tutor import schema

    source = inspect.getsource(schema.validate_task)
    assert "answer_type" not in source


def test_the_lesson_title_is_never_the_classifier():
    """Naslov lekcije SMIJE ući u tekst za učenika, ali NE u odluku o klasi:
    ista lekcija mora moći dati obje klase (invarijanta Faze 2)."""
    import inspect

    source = inspect.getsource(hint_policy.classify_visible_task)
    for forbidden in ("title", "lesson", "topic"):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# A3. TABELA AUTORA NE MOŽE PONOVO OTVORITI KLASU CURENJA
# ---------------------------------------------------------------------------

def test_every_non_computational_class_is_server_authored_at_every_level():
    for task_class in (hint_policy.PROPOSITIONAL, hint_policy.UNCERTAIN):
        for level in range(1, config.MAX_HINT_LEVEL + 1):
            assert hint_policy.hint_author(
                task_class, level, config.MAX_HINT_LEVEL) == hint_policy.SERVER, \
                (task_class, level)
    for level in range(1, config.MAX_HINT_LEVEL):
        assert hint_policy.hint_author(
            hint_policy.COMPUTATIONAL, level, config.MAX_HINT_LEVEL) == hint_policy.MODEL
    assert hint_policy.hint_author(
        hint_policy.COMPUTATIONAL, config.MAX_HINT_LEVEL,
        config.MAX_HINT_LEVEL) == hint_policy.SERVER


@pytest.mark.parametrize("label,option", RECOGNITION_SHAPES, ids=lambda value: value)
@pytest.mark.parametrize("task", [
    "Koji zapis opisuje opisani položaj?",
    # I uz tekst koji IZGLEDA kao računski zadatak, oblik odgovora nije
    # vrijednost, pa dokaz o izvođenju ne postoji.
    r"Izračunaj i odaberi tačan zapis. Riješi: $x-1>2$.",
], ids=("recognition_text", "compute_looking_text"))
def test_no_recognition_shape_can_reach_a_model_authored_hint(task, label, option):
    """Sve simboličke opcije istog oblika → svaki nivo je serverski."""
    options = [option] * 4
    task_class = hint_policy.effective_task_class(task, options, 0)
    assert task_class == hint_policy.PROPOSITIONAL, label
    for level in range(1, config.MAX_HINT_LEVEL + 1):
        assert hint_policy.hint_author(
            task_class, level, config.MAX_HINT_LEVEL) == hint_policy.SERVER, (label, level)


# ---------------------------------------------------------------------------
# A4. ŽIVA REGRESIJA — SIMBOLIČKO PREPOZNAVANJE TROŠI NULA POZIVA
# ---------------------------------------------------------------------------

SYMBOLIC_LESSON, SYMBOLIC_GRADE = "9-02-006", 9
SYMBOLIC_TASK = (r"Prava $p$ ne leži u ravni $\alpha$ i nema s njom nijednu "
                 r"zajedničku tačku. Koji zapis opisuje taj položaj?")
SYMBOLIC_OPTIONS = (r"$p \parallel \alpha$", r"$p \perp \alpha$",
                    r"$p \subset \alpha$", r"$p \cap \alpha = \{A\}$")
SYMBOLIC_SOLUTION = (r"Položaj bez zajedničkih tačaka zapisuje se kao "
                     r"$p \parallel \alpha$.")


def _turn(message, session_id="sym", grade=SYMBOLIC_GRADE,
          selected_topic=SYMBOLIC_LESSON, **changes):
    payload = {
        "session_id": session_id, "grade": grade,
        "selected_topic": selected_topic, "selected_oblast": "",
        "student_message": message, "intent": "", "difficulty_request": "",
        "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def _publish_symbolic(store, fake):
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=SYMBOLIC_TASK, options=SYMBOLIC_OPTIONS,
                                   correct_option_index=0,
                                   expected=SYMBOLIC_OPTIONS[0],
                                   solution=SYMBOLIC_SOLUTION)))
    response = run_practice_turn(store, fake, _turn("Daj mi zadatak."))
    assert response["status"] == "ready", response.get("answer")
    return response


def test_the_published_symbolic_recognition_task_is_classified_propositional():
    store, fake = SessionStore(), FakeLLM()
    _publish_symbolic(store, fake)
    session = store.peek("sym")
    marked = next(option["text"] for option in session["current_options"]
                  if option["id"] == session["correct_option_id"])

    # PROVENIJENCIJA NALAZA A: stari kriterij („<= 2 prozne riječi ⇒ RAČUNSKI“)
    # bi ovaj zadatak poslao na model-autorsku ljestvicu.
    assert hint_policy.prose_word_count(marked) == 0
    assert hint_policy.prose_word_count(marked) <= hint_policy.MAX_VALUE_PROSE_WORDS
    # Poslije hardeninga oblik NIJE dokazana vrijednost, pa klasa je propoziciona.
    assert hint_policy.value_shaped(marked) is False
    assert hint_policy.marked_answer_is_short_symbolic(marked) is True
    from matbot.tutor import prompts as tutor_prompts
    assert tutor_prompts.session_task_class(session) == hint_policy.PROPOSITIONAL


def test_a_short_symbolic_recognition_ladder_spends_zero_model_calls():
    """NAJVAŽNIJA REGRESIJA HARDENINGA: nagovještaji 1 i 2 nad simboličkim
    prepoznavanjem NE SMIJU stići do modela."""
    store, fake = SessionStore(), FakeLLM()
    _publish_symbolic(store, fake)
    calls_after_publish = fake.call_count
    served = []
    for index in range(1, config.MAX_HINT_LEVEL + 1):
        before = fake.call_count
        response = run_practice_turn(store, fake, _turn(
            "Ne znam.", intent="hint_request", interaction_phase="practice_help",
            client_turn_id=f"sym-h{index}"))
        assert response["status"] == "ready", (index, response.get("answer"))
        assert response["answer"] != SAFE_ERROR_MESSAGE
        assert fake.call_count == before, f"nivo {index} je pozvao model"
        served.append(response["answer"])

    assert fake.call_count == calls_after_publish
    assert store.peek("sym")["hint_level"] == config.MAX_HINT_LEVEL
    assert len(set(served)) == 3
    # Nivoi 1 i 2 ne prepisuju nijednu opciju i ne otkrivaju označeni zapis.
    marked = SYMBOLIC_OPTIONS[0]
    for text in served[:2]:
        for option in SYMBOLIC_OPTIONS:
            assert option not in text
        assert "parallel" not in text
        assert not feedback.leaks_answer(text, marked, marked, task_text=SYMBOLIC_TASK)
    # Vrh ljestvice je kompozicija PROVJERENOG artefakta.
    assert served[2] == hint_policy.compose_top_hint(
        SYMBOLIC_SOLUTION, SYMBOLIC_OPTIONS[0], hint_policy.PROPOSITIONAL)


# ---------------------------------------------------------------------------
# A5. ŽIVA REGRESIJA — DVOSMISLEN RELACIJSKI OBLIK TROŠI NULA POZIVA
# ---------------------------------------------------------------------------

AMBIGUOUS_LESSON, AMBIGUOUS_GRADE = "9-04-017", 9
AMBIGUOUS_TASK = (r"Na brojevnoj pravoj prikazani su svi brojevi desno od $3$, "
                  r"pri čemu $3$ nije uključen. Koja nejednačina opisuje taj skup?")
AMBIGUOUS_OPTIONS = (r"$x>3$", r"$x\ge 3$", r"$x<3$", r"$x>5$")
AMBIGUOUS_SOLUTION = r"Prazan kružić na $3$ i strelica desno znače $x>3$."


def _publish_ambiguous(store, fake):
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=AMBIGUOUS_TASK, options=AMBIGUOUS_OPTIONS,
                                   correct_option_index=0,
                                   expected=AMBIGUOUS_OPTIONS[0],
                                   solution=AMBIGUOUS_SOLUTION)))
    response = run_practice_turn(store, fake, _turn(
        "Daj mi zadatak.", session_id="amb", grade=AMBIGUOUS_GRADE,
        selected_topic=AMBIGUOUS_LESSON))
    assert response["status"] == "ready", response.get("answer")
    return response


def test_an_ambiguous_relational_recognition_ladder_spends_zero_model_calls():
    """AUTOR-SAFETY (tačka 10): dvosmislen relacijski oblik ide serverskoj
    ljestvici na SVA TRI nivoa, i to se dokazuje end-to-end.

    Opcije su ISTE kao u „riješi nejednačinu“ zadatku; jedina razlika je tekst
    zadatka, koji ne daje dokaz o izvođenju."""
    store, fake = SessionStore(), FakeLLM()
    _publish_ambiguous(store, fake)
    session = store.peek("amb")
    assert hint_policy.session_task_class(session) == hint_policy.PROPOSITIONAL
    # Oblik je vrijednosni, ali NIJE veličina — zato je potreban dokaz o zadatku.
    assert hint_policy.value_shaped(AMBIGUOUS_OPTIONS[0]) is True
    assert hint_policy.quantity_shaped(AMBIGUOUS_OPTIONS[0]) is False

    calls_after_publish = fake.call_count
    served = []
    for index in range(1, config.MAX_HINT_LEVEL + 1):
        before = fake.call_count
        response = run_practice_turn(store, fake, _turn(
            "Ne znam.", session_id="amb", grade=AMBIGUOUS_GRADE,
            selected_topic=AMBIGUOUS_LESSON, intent="hint_request",
            interaction_phase="practice_help", client_turn_id=f"amb-h{index}"))
        assert response["status"] == "ready", (index, response.get("answer"))
        assert response["answer"] != SAFE_ERROR_MESSAGE
        assert fake.call_count == before, f"nivo {index} je pozvao model"
        served.append(response["answer"])

    assert fake.call_count == calls_after_publish
    assert store.peek("amb")["hint_level"] == config.MAX_HINT_LEVEL
    for text in served[:2]:
        for option in AMBIGUOUS_OPTIONS:
            assert option not in text
        assert not feedback.leaks_answer(text, AMBIGUOUS_OPTIONS[0],
                                         AMBIGUOUS_OPTIONS[0],
                                         task_text=AMBIGUOUS_TASK)
    assert served[2] == hint_policy.compose_top_hint(
        AMBIGUOUS_SOLUTION, AMBIGUOUS_OPTIONS[0], hint_policy.PROPOSITIONAL)


def test_a_computational_task_on_the_same_lesson_still_reaches_the_model():
    """Hardening NE gasi računsku granu: ista lekcija s brojevnim odgovorom
    zadržava model-autorske nagovještaje 1 i 2."""
    store, fake = SessionStore(), FakeLLM()
    numeric_task = (r"Prava $p$ siječe ravan $\alpha$. Koliko najviše "
                    r"zajedničkih tačaka mogu imati?")
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=numeric_task,
                                   options=("$1$", "$2$", "$3$", "$0$"),
                                   correct_option_index=0, expected="$1$",
                                   solution="Prava koja siječe ravan ima s njom "
                                            "tačno jednu zajedničku tačku.")))
    assert run_practice_turn(store, fake, _turn(
        "Daj mi zadatak.", session_id="num"))["status"] == "ready"

    fake.queue(make_tutor_draft(
        intent="hint_request", new_task=None, reply="Idemo korak po korak.",
        hint="Zamisli pravu koja probija ravan i prati u koliko je tačaka dira."))
    before = fake.call_count
    response = run_practice_turn(store, fake, _turn(
        "Ne znam.", session_id="num", intent="hint_request",
        interaction_phase="practice_help", client_turn_id="num-h1"))

    assert response["status"] == "ready"
    assert fake.call_count - before == 1, "računska grana MORA ostati modelova"


# ---------------------------------------------------------------------------
# B. PROPOZICIONI NIVO 2 NE SMIJE PROPISATI NEVALJANU METODU
# ---------------------------------------------------------------------------
# Zatvorena lista fraza koje su OBORENE ovim hardeningom. Svaka od njih izriče
# pravilo zaključivanja koje NIJE univerzalno valjano za zadatke prepoznavanja.
FORBIDDEN_H2_METHOD_PHRASES = (
    "protivprimjer",
    "protivprimjerom",
    "NE bi vrijedila",
    "oboriti jednim",
    "jedan primjer je dovoljan",
    "dovoljan da tvrdnja padne",
    "vidi vrijedi li tvrdnja i tada",
    "zamijeni pretpostavku i zaključak pa vidi",
)

# Sedam oblika prepoznavanja iz tražene matrice. Sadržaj opcija ovdje NIJE
# ulaz šablona (šablon ih ne čita) — nabrojani su da matrica dokazano pokriva
# sve oblike za koje nivo 2 mora ostati matematički primjeren.
RECOGNITION_KINDS = (
    ("geometrijski odnos",
     (r"$p \perp \alpha$", r"$p \parallel \alpha$", r"$p \subset \alpha$",
      r"$p \cap \alpha = \{A\}$")),
    ("odnos skupova",
     (r"$A\subset B$", "$A=B$", r"$A\cap B=\varnothing$", r"$B\subset A$")),
    ("direktna proporcionalnost",
     ("Ako se jedna veličina poveća dva puta, druga se također poveća dva puta.",
      "Ako se jedna veličina poveća dva puta, druga se smanji dva puta.",
      "Ako se jedna veličina poveća, druga ostaje uvijek jednaka.",
      "Ako se jedna veličina poveća dva puta, druga se poveća za dva.")),
    ("klasifikacija trougla",
     ("Trougao s jednim tupim uglom ne može imati i pravi ugao.",
      "Trougao može imati dva prava ugla ako je jednakokraki.",
      "Trougao s jednim pravim uglom ima i jedan tup ugao.",
      "Trougao može imati tri tupa ugla ako su stranice različite.")),
    ("univerzalna tvrdnja tačno/netačno",
     ("Svaki kvadrat je pravougaonik.", "Svaki pravougaonik je kvadrat.",
      "Nijedan kvadrat nije pravougaonik.",
      "Svaki pravougaonik ima sve stranice jednake.")),
    ("tvrdnja o KONKRETNOJ konfiguraciji",
     ("U ovom trouglu je stranica $a$ najdulja jer je nasuprot najvećem uglu.",
      "U ovom trouglu je stranica $a$ najkraća jer je nasuprot najvećem uglu.",
      "U ovom trouglu su sve stranice jednake bez obzira na uglove.",
      "U ovom trouglu se dužina stranice ne može uporediti s uglovima.")),
    ("EGZISTENCIJALNA tvrdnja („može“)",
     ("Prava i ravan mogu imati tačno jednu zajedničku tačku.",
      "Prava i ravan mogu imati tačno dvije zajedničke tačke.",
      "Prava i ravan mogu imati tačno tri zajedničke tačke.",
      "Prava i ravan nikad ne mogu imati zajedničku tačku.")),
)


@pytest.mark.parametrize("label,options", RECOGNITION_KINDS, ids=lambda value: value)
def test_the_propositional_level_two_prescribes_no_invalid_method(label, options):
    """NALAZ B: nivo 2 ne smije naložiti metodu koja za ovaj oblik ne vrijedi.

    Egzistencijalna („može“) i konfiguracijska tvrdnja su ključni slučajevi:
    jedan protivprimjer ih NE obara, a bivši šablon je to nalagao."""
    text = hint_policy.compose_propositional_hint(2, "Naslov lekcije", list(options))
    lowered = text.lower()
    for phrase in FORBIDDEN_H2_METHOD_PHRASES:
        assert phrase.lower() not in lowered, (label, phrase)
    # Ono što nivo 2 SMIJE i MORA: poređenje sa zadanim uslovima i s definicijom,
    # uz izričit uslov za odbacivanje.
    assert "uslovima koji su navedeni u samom zadatku" in text, label
    assert "definicijom" in text, label
    assert "odbaci SAMO kad nađeš konkretan sukob" in text, label


@pytest.mark.parametrize("label,options", RECOGNITION_KINDS, ids=lambda value: value)
def test_the_propositional_ladder_stays_safe_and_useful_for_every_kind(label, options):
    marked, distractors = options[0], list(options[1:])
    first = hint_policy.compose_propositional_hint(1, "Naslov lekcije", list(options))
    second = hint_policy.compose_propositional_hint(2, "Naslov lekcije", list(options))

    for text in (first, second):
        # SIGURNO: nijedno slovo iz opcija, nijedna dokaziva objava tvrdnje.
        for option in options:
            assert option not in text, (label, option[:30])
        assert not hint_policy.proposition_disclosure(
            text, marked, distractors).disclosed, label
        # UPOTREBLJIVO: skela, ne podsticaj; imenuje lekciju i poređenje.
        assert hint_policy.empty_help_code(text) == "", label
        assert "Naslov lekcije" in text, label
        assert "upored" in text.lower(), label
        assert len(text) >= 120, label
        # NE IZRIČE PRESUDU: nijedna opcija se ne proglašava tačnom ni netačnom.
        for verdict in ("tačna opcija je", "netačna je", "odgovor je"):
            assert verdict not in text.lower(), (label, verdict)

    # NIVO 2 JE STROGO JAČI, a ne parafraza nivoa 1.
    assert first != second, label
    assert check_lib._token_overlap(first, second) < 0.85, label
    assert len(second) > len(first) * 0.9, label


def test_the_conditional_note_is_a_reading_aid_and_not_an_inference_rule():
    """Jedina specijalizacija koja je OSTALA smije samo razdvojiti strukturu.

    Obrat implikacije se opisuje kao DRUGA tvrdnja koju treba provjeriti zasebno
    — nikad kao dokaz da je opcija netačna (obrat tačne implikacije ponekad i
    sam vrijedi)."""
    conditional = [f"Ako vrijedi uslov {index}, onda vrijedi zaključak."
                   for index in range(4)]
    plain = [f"Tvrdnja broj {index} o istom pojmu." for index in range(4)]
    assert hint_policy.implication_shaped(conditional) is True
    assert hint_policy.implication_shaped(plain) is False

    note_level_2 = hint_policy.compose_propositional_hint(2, "T", conditional)
    without = hint_policy.compose_propositional_hint(2, "T", plain)
    added = note_level_2[len(without):]
    assert "razdvoji pretpostavku od zaključka" in added
    assert "DRUGA tvrdnja" in added
    assert "mora se provjeriti zasebno" in added
    for phrase in FORBIDDEN_H2_METHOD_PHRASES:
        assert phrase.lower() not in added.lower(), phrase


def test_one_non_conditional_option_removes_the_conditional_note():
    """Dodatak se dopisuje SAMO kad ga nose SVE opcije — inače bi izdvajao."""
    mixed = ["Ako vrijedi A, onda vrijedi B.", "Ako vrijedi C, onda vrijedi D.",
             "Ako vrijedi E, onda vrijedi F.", "Tvrdnja bez uslova."]
    assert hint_policy.implication_shaped(mixed) is False
    for level in (1, 2):
        assert "ako … onda" not in hint_policy.compose_propositional_hint(
            level, "T", mixed)


# ---------------------------------------------------------------------------
# C. POKRIVENOST GRANA U ŽIVOM TALASU
# ---------------------------------------------------------------------------

def test_the_task_class_check_is_resolvable_only_for_real_classes():
    assert check_lib.resolve("task_class:propositional") is not None
    assert check_lib.resolve("task_class:computational") is not None
    assert check_lib.resolve("task_class:uncertain") is None
    assert check_lib.resolve("task_class:") is None
    assert check_lib.root_cause("task_class:propositional") == "help_branch_coverage"
    assert "task_class:CLASS" in check_lib.known_check_names()


def _observation(options, marked_id, answer="Tekst pomoći.", solution="Postupak.",
                 task_text=SYMBOLIC_TASK):
    session = {
        "lesson_id": SYMBOLIC_LESSON,
        "current_task": task_text,
        "current_options": [{"id": oid, "text": text} for oid, text in options],
        "correct_option_id": marked_id,
        "expected_answer_summary": next(text for oid, text in options
                                        if oid == marked_id),
        "solution_summary": solution,
    }
    return check_lib.TurnObservation(
        scenario_id="H01", step_index=0, step_kind="text",
        topic_id=SYMBOLIC_LESSON, grade=SYMBOLIC_GRADE,
        request_payload={"student_message": "Daj mi zadatak.", "intent": ""},
        http_status=200,
        response={"status": "ready", "answer": answer, "session_mode": "practice",
                  "effective_topic": SYMBOLIC_LESSON},
        session_before=None, session_after=session, sdk_calls=2)


def test_a_matching_branch_passes_and_a_mismatch_is_a_coverage_gap_not_a_failure():
    """NALAZ C, jezgro: nepoklapanje je SKIP (rupa u pokrivenosti), nikad FAIL."""
    symbolic = _observation(tuple(zip("abcd", SYMBOLIC_OPTIONS)), "a")
    numeric = _observation(tuple(zip("abcd", ("$1$", "$2$", "$3$", "$0$"))), "a")

    propositional = check_lib.resolve("task_class:propositional")
    computational = check_lib.resolve("task_class:computational")

    assert propositional(symbolic).outcome == check_lib.PASS
    assert computational(numeric).outcome == check_lib.PASS

    missed = computational(symbolic)
    assert missed.outcome == check_lib.SKIP
    assert "coverage gap, not a product" in missed.detail
    assert propositional(numeric).outcome == check_lib.SKIP


def test_production_and_evaluator_use_one_classifier_with_one_context():
    """PARITET (tačka 11): produkcija i evaluator NE SMIJU imati dva
    klasifikatora, ni jedan slabiji „samo po opcijama“.

    Dokaz je konstruisan tako da bi klasifikacija SAMO PO OPCIJAMA dala drugu
    klasu: opcije su `$x>3$`-oblika, pa bi oblik sam rekao „računski“, a tekst
    zadatka to opovrgava. Oba puta moraju vidjeti ISTU klasu."""
    from matbot.tutor import prompts as tutor_prompts

    session = {
        "lesson_id": AMBIGUOUS_LESSON,
        "current_task": AMBIGUOUS_TASK,
        "current_options": [{"id": oid, "text": text}
                            for oid, text in zip("abcd", AMBIGUOUS_OPTIONS)],
        "correct_option_id": "a",
        "expected_answer_summary": AMBIGUOUS_OPTIONS[0],
        "solution_summary": AMBIGUOUS_SOLUTION,
    }
    expected = hint_policy.session_task_class(session)
    assert expected == hint_policy.PROPOSITIONAL

    # Produkcijski put pomoći.
    assert tutor_prompts.session_task_class(session) == expected
    from matbot.tutor import pipeline as tutor_pipeline
    assert tutor_pipeline._help_task_class(session) == expected

    # Evaluator, nad ISTOM snimljenom sesijom.
    observation = _observation(tuple(zip("abcd", AMBIGUOUS_OPTIONS)), "a",
                               task_text=AMBIGUOUS_TASK,
                               solution=AMBIGUOUS_SOLUTION)
    assert observation.help_task_class == expected

    # A ISTE opcije uz „riješi“ tekst daju računsku klasu na OBA puta — dakle
    # razlika stvarno dolazi iz konteksta, ne iz dva različita pravila.
    solving = dict(session, current_task=SOLVE_INEQUALITY_TASK)
    assert hint_policy.session_task_class(solving) == hint_policy.COMPUTATIONAL
    assert tutor_prompts.session_task_class(solving) == hint_policy.COMPUTATIONAL
    solving_observation = _observation(tuple(zip("abcd", AMBIGUOUS_OPTIONS)), "a",
                                      task_text=SOLVE_INEQUALITY_TASK,
                                      solution=AMBIGUOUS_SOLUTION)
    assert solving_observation.help_task_class == hint_policy.COMPUTATIONAL


def test_the_branch_checks_read_the_same_class_as_the_help_path():
    """`task_class:<klasa>` mjeri ono što server STVARNO izabere."""
    recognition = _observation(tuple(zip("abcd", AMBIGUOUS_OPTIONS)), "a",
                               task_text=AMBIGUOUS_TASK,
                               solution=AMBIGUOUS_SOLUTION)
    solving = _observation(tuple(zip("abcd", AMBIGUOUS_OPTIONS)), "a",
                           task_text=SOLVE_INEQUALITY_TASK,
                           solution=AMBIGUOUS_SOLUTION)
    propositional = check_lib.resolve("task_class:propositional")
    computational = check_lib.resolve("task_class:computational")

    assert propositional(recognition).outcome == check_lib.PASS
    assert computational(recognition).outcome == check_lib.SKIP
    assert computational(solving).outcome == check_lib.PASS
    assert propositional(solving).outcome == check_lib.SKIP


def test_the_symbolic_marked_answer_check_only_fires_on_symbolic_shapes():
    symbolic = _observation(tuple(zip("abcd", SYMBOLIC_OPTIONS)), "a")
    prose = _observation(tuple(zip("abcd", (
        "Unija sadrži sve elemente oba skupa.",
        "Unija sadrži samo zajedničke elemente.",
        "Unija je uvijek prazan skup.",
        "Unija sadrži samo elemente prvog skupa."))), "a")
    assert check_lib.check_symbolic_marked_answer(symbolic).outcome == check_lib.PASS
    assert check_lib.check_symbolic_marked_answer(prose).outcome == check_lib.SKIP


def _record(scenario_id, passed=(), skipped=()):
    results = [{"name": name, "outcome": "pass", "detail": ""} for name in passed]
    results += [{"name": name, "outcome": "skip", "detail": ""} for name in skipped]
    return {"id": scenario_id, "status": "REVIEW",
            "turns": [{"step_index": 0, "check_results": results}],
            "failed_checks": [], "skipped_checks": [], "rubrics": []}


def _ladder_record(scenario_id, task_class, symbolic=False):
    passed = [f"task_class:{task_class}", "hint_top_from_verified_solution"]
    if symbolic:
        passed.append("symbolic_marked_answer")
    return _record(scenario_id, passed=passed)


def test_branch_coverage_requires_both_ladders_and_a_symbolic_recognition_task():
    records = [
        _ladder_record("H01", hint_policy.PROPOSITIONAL, symbolic=True),
        _ladder_record("H02", hint_policy.PROPOSITIONAL),
        _ladder_record("H03", hint_policy.PROPOSITIONAL),
        _ladder_record("H07", hint_policy.COMPUTATIONAL),
        _ladder_record("H08", hint_policy.COMPUTATIONAL),
        _ladder_record("H09", hint_policy.COMPUTATIONAL),
    ]
    coverage = release_contract.hint_branch_coverage(records)
    assert coverage.complete is True
    assert coverage.propositional == ("H01", "H02", "H03")
    assert coverage.computational == ("H07", "H08", "H09")
    assert coverage.symbolic_propositional == ("H01",)
    assert coverage.gaps == ()
    assert coverage.notes == ()


def test_branch_coverage_is_incomplete_without_a_symbolic_recognition_task():
    records = [_ladder_record(f"H{index:02d}", hint_policy.PROPOSITIONAL)
               for index in range(1, 4)]
    records += [_ladder_record(f"H{index:02d}", hint_policy.COMPUTATIONAL)
                for index in range(7, 10)]
    coverage = release_contract.hint_branch_coverage(records)
    assert coverage.complete is False
    assert coverage.symbolic_propositional == ()
    assert any("short-symbolic" in note for note in coverage.notes)


def test_a_missed_branch_is_recorded_as_a_gap_and_never_counted():
    records = [
        _ladder_record("H01", hint_policy.PROPOSITIONAL, symbolic=True),
        # Cilj je bio propozicioni, server je klasifikovao računski.
        _record("H02", skipped=["task_class:propositional"]),
    ]
    coverage = release_contract.hint_branch_coverage(records)
    assert coverage.propositional == ("H01",)
    assert coverage.gaps == ("H02:task_class:propositional",)
    assert coverage.complete is False


def test_a_matched_class_without_proven_top_hint_provenance_is_not_coverage():
    """Grana se broji SAMO kad je cijela ljestvica poslužena — klasa sama nije
    dovoljna, jer vrh ljestvice nosi provenijencijski dokaz."""
    records = [_record("H01", passed=[f"task_class:{hint_policy.PROPOSITIONAL}"])]
    coverage = release_contract.hint_branch_coverage(records)
    assert coverage.propositional == ()
    assert any("ladder top was not proven server-composed" in gap
               for gap in coverage.gaps)


# ---------------------------------------------------------------------------
# C2. KONFIGURACIJA PROŠIRENOG TALASA
# ---------------------------------------------------------------------------

def _hint_wave():
    from pathlib import Path

    from tools.practice_eval.scenario import load_scenarios
    path = (Path(__file__).resolve().parent.parent / "tools" / "practice_eval"
            / "scenarios" / "family" / "wave_hint2.jsonl")
    return load_scenarios(path)


def test_the_wave_declares_a_branch_target_on_every_publication_step():
    scenarios = _hint_wave()
    assert len(scenarios) == 12
    targets = {hint_policy.PROPOSITIONAL: [], hint_policy.COMPUTATIONAL: []}
    for scenario in scenarios:
        publish = scenario.steps[0]
        declared = [name for name in publish["checks"]
                    if name.startswith("task_class:")]
        assert len(declared) == 1, scenario.id
        assert "symbolic_marked_answer" in publish["checks"], scenario.id
        targets[declared[0].split(":", 1)[1]].append(scenario.id)
    # Ciljeva je više od minimuma jer oblik opcija bira model.
    assert len(targets[hint_policy.PROPOSITIONAL]) >= \
        release_contract.REQUIRED_PROPOSITIONAL_LADDERS + 2
    assert len(targets[hint_policy.COMPUTATIONAL]) >= \
        release_contract.REQUIRED_COMPUTATIONAL_LADDERS + 2


def test_exactly_one_recognition_scenario_asks_for_a_symbolic_option_set():
    """RUPA U POKRIVENOSTI IZ PRVOG F6H TALASA: short-symbolic propositional 0/1.

    Uzrok NIJE klasifikator: svih šest lekcija prepoznavanja dobilo je golo „Daj
    mi zadatak.“, pa je model svaki put napisao OPISNE opcije. Zatvara se
    ISKLJUČIVO učenikovom porukom na JEDNOJ lekciji — proizvod, klasifikacija i
    pragovi se ne diraju, a ostalih pet meta ostaje prozno, pa se propoziciona
    pokrivenost ne smanjuje.

    Poruka i dalje mora proći Tutora, recenzenta, objavu i klasifikator; ako
    model ipak napiše prozu, `symbolic_marked_answer` opet preskače i rupa
    OSTAJE rupa (`hint_branch_coverage` je i dalje jedini sudija)."""
    import json
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent / "tools" / "practice_eval"
            / "scenarios" / "family" / "wave_hint2.jsonl")
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
    asking = [record for record in records
              if "symbolic_request" in (record.get("tags") or ())]
    assert len(asking) == 1
    scenario = asking[0]
    assert scenario["topic_id"] == "9-02-006"       # doslovna FW-X03/TR-B1 lekcija
    message = scenario["steps"][0]["message"]
    assert "simbolički" not in message              # traži se OBLIK, ne naša oznaka
    assert "matematički zapis" in message and "bez opisnih rečenica" in message
    # Klasni cilj i mjerači su NEPROMIJENJENI — mijenja se samo poruka.
    assert f"task_class:{hint_policy.PROPOSITIONAL}" in scenario["steps"][0]["checks"]
    assert "symbolic_marked_answer" in scenario["steps"][0]["checks"]
    assert len(scenario["steps"]) == 1 + config.MAX_HINT_LEVEL
    others = [record for record in records if record is not scenario
              and "recognition" in (record.get("tags") or ())]
    assert len(others) == 5
    assert all(step["message"] == "Daj mi zadatak."
               for record in others for step in record["steps"][:1])


def test_every_wave_step_that_can_show_an_option_letter_measures_the_binding():
    """Živi H12: provenijencija je bila PASS, a slovo opcije netačno. Zato svaki
    korak koji učeniku može pokazati oznaku nosi I `solution_option_binding_
    consistent` — nikad umjesto provenijencije, uvijek uz nju."""
    for scenario in _hint_wave():
        for index, step in enumerate(scenario.steps):
            assert "solution_option_binding_consistent" in step["checks"], \
                (scenario.id, index)
        top_steps = [step for step in scenario.steps
                     if "hint_top_from_verified_solution" in step["checks"]]
        assert top_steps, scenario.id
        for step in top_steps:
            assert "solution_option_binding_consistent" in step["checks"], scenario.id


def test_every_wave_scenario_can_reach_a_full_ladder_or_a_full_solution():
    for scenario in _hint_wave():
        intents = [step.get("intent") or "" for step in scenario.steps]
        assert intents[0] == "", scenario.id
        if "solution_request" in intents:
            assert intents.count("solution_request") == 1, scenario.id
        else:
            assert intents.count("hint_request") == config.MAX_HINT_LEVEL, scenario.id
        # Nijedan scenario ne ponavlja isti korak radi „još jednog pokušaja“.
        assert len(scenario.steps) == len(set(
            (step.get("kind"), step.get("message"), step.get("intent"))
            for step in scenario.steps)) or intents.count("hint_request") == 3


def test_the_wave_budget_stays_bounded_and_honestly_declared():
    scenarios = _hint_wave()
    declared = sum(scenario.max_model_calls for scenario in scenarios)
    assert declared <= 46
    # Realan trošak je manji: propoziciona ljestvica potroši samo objavu.
    minimum = sum(1 if step["expect_calls"] else 0
                  for scenario in scenarios for step in scenario.steps)
    assert minimum <= declared


def test_the_wave_still_only_uses_model_route_lessons():
    from matbot.contracts import registry as contract_registry
    from matbot.semantics import contracts as semantic_contracts
    from matbot.tutor import lesson_context as lesson_context_module
    from matbot.tutor import pipeline as tutor_pipeline

    for scenario in _hint_wave():
        context = lesson_context_module.build(scenario.grade, scenario.topic_id)
        assert context is not None, scenario.topic_id
        assert tutor_pipeline._deterministic_generator_for(context) is None, \
            scenario.topic_id
        legacy = (contract_registry.contract_for(scenario.topic_id) is not None
                  and semantic_contracts.contract_for(scenario.topic_id) is None)
        assert not legacy, scenario.topic_id


# ---------------------------------------------------------------------------
# D. STANJE I OČUVANJE PRIHVAĆENE ARHITEKTURE
# ---------------------------------------------------------------------------

def test_the_hardening_did_not_change_the_state_machine_for_symbolic_tasks():
    store, fake = SessionStore(), FakeLLM()
    _publish_symbolic(store, fake)
    before = copy.deepcopy(store.peek("sym"))
    snapshot = {key: before[key] for key in (
        "current_task", "current_task_identity", "correct_option_id",
        "expected_answer_summary", "solution_summary", "difficulty_level",
        "lesson_id", "current_task_signature")}

    for index in range(1, config.MAX_HINT_LEVEL + 1):
        run_practice_turn(store, fake, _turn(
            "Ne znam.", intent="hint_request", interaction_phase="practice_help",
            client_turn_id=f"state-h{index}"))
        assert store.peek("sym")["hint_level"] == index

    after = store.peek("sym")
    assert {key: after[key] for key in snapshot} == snapshot
    assert after["task_completed"] is False


def test_the_full_solution_on_a_symbolic_task_is_still_the_verified_artifact():
    store, fake = SessionStore(), FakeLLM()
    _publish_symbolic(store, fake)
    calls_before = fake.call_count

    response = run_practice_turn(store, fake, _turn(
        "Uradi ga ti.", intent="solution_request",
        interaction_phase="practice_help", client_turn_id="sym-s1"))

    assert fake.call_count == calls_before
    assert response["answer"] == hint_policy.compose_full_solution(
        SYMBOLIC_SOLUTION, SYMBOLIC_OPTIONS[0], hint_policy.PROPOSITIONAL)
    session = store.peek("sym")
    assert session["task_completed"] is True
    assert response["revealed_correct_option_id"] == session["correct_option_id"]


def test_the_f6h_campaign_runs_with_the_canonical_forty_five_second_timeout():
    """NALAZ ZAVRŠNOG PREGLEDA: dry-run je prijavio `timeout_seconds = 30.0`.

    Uzrok NIJE produkt: `matbot/config.py::AI_TIMEOUT_S` čita `AI_TUTOR_TIMEOUT`
    s podrazumijevanih 30 s, a dry-run je bio pokrenut kroz `runner` DIREKTNO,
    bez kanonskog launchera — pa nijedan campaign override nije primijenjen.
    Kanonski kandidat za živu validaciju (FINAL40) traži 45 s, pa F6H mora
    dobiti isti runtime. Produkcijski kod rokova se NE dira."""
    from matbot import config
    from tools.practice_eval import campaign_config

    # Produktni podrazumijevani rok je 30 s i to ostaje netaknuto.
    assert config.AI_TIMEOUT_S == 30.0

    assert "f6h" in campaign_config.CAMPAIGNS
    for name, overrides in campaign_config.CAMPAIGNS.items():
        assert overrides["AI_TUTOR_TIMEOUT"] == \
            campaign_config.CANONICAL_CAMPAIGN_TIMEOUT_S, name
    # F6H vozi DOSLOVNO runtime kanonskog kandidata.
    assert campaign_config.CAMPAIGNS["f6h"] == campaign_config.CAMPAIGNS["final40"]

    environ = {}
    applied = campaign_config.apply_campaign_environment("f6h", environ=environ)
    assert environ["AI_TUTOR_TIMEOUT"] == "45"
    assert applied["MATBOT_PRACTICE_PIPELINE"] == "universal_two_call"
    assert applied["MATBOT_PRACTICE_DIFFICULTY_LEVELS"] == "enabled"
    # Launcher ne smije ubrizgati rollback determinističke rute.
    assert campaign_config.DETERMINISTIC_FLAG not in environ


def test_the_f6h_campaign_is_reachable_from_the_canonical_launcher():
    from tools.practice_eval import launch

    parser = launch.build_parser()
    args, _rest = parser.parse_known_args(
        ["--campaign", "f6h", "--scenarios",
         "tools/practice_eval/scenarios/family/wave_hint2.jsonl", "--dry-run"])
    assert args.campaign == "f6h"


def test_the_notation_scope_gate_claim_is_documented_as_notation_only():
    """Kapija zapisa se NE širi i NE smije tvrditi više nego što dokazuje."""
    spot = release_contract.blind_spot("help_out_of_grade_technique")
    assert spot.strength == release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED
    assert "advanced NOTATION" in spot.owner
    assert "proves nothing about semantic grade appropriateness" in spot.owner
    assert "manual live-review duty" in spot.owner
    # Skup ostaje ZATVOREN i uzak — nije katalog prirodnog jezika.
    assert len(hint_policy.ADVANCED_MACHINERY_COMMANDS) <= 30
    assert len(hint_policy.RECOGNITION_RELATION_COMMANDS) <= 30
