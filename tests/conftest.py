import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# FLASK_SECRET_KEY je obavezan za produkciju (app.py odbija start bez njega).
# Testovi EKSPLICITNO postavljaju jasno označen nesiguran default — ovo je
# jedino mjesto gdje se to smije desiti (vidi matbot/config.py).
os.environ.setdefault("FLASK_SECRET_KEY", "test-only-insecure-secret-DO-NOT-USE-IN-PRODUCTION")

from matbot import auth  # noqa: E402
from matbot.llm import LLMResult, LLMTimeout, LLMUnavailable  # noqa: E402
from matbot.ratelimit import RateLimiter  # noqa: E402
from matbot.schema import (  # noqa: E402
    ExplainTurnOutput,
    NewTask,
    Option,
    PracticeTurnOutput,
    QuickImageTurnOutput,
    QuickTurnOutput,
    VisibleValue,
)
from matbot.session_store import SessionStore  # noqa: E402
from matbot.turnlock import TurnLockRegistry  # noqa: E402


def make_output(reply="U redu.", evaluation=None, gave_hint=False, new_task=None, hint=None):
    return PracticeTurnOutput(
        reply=reply, evaluation=evaluation, gave_hint=gave_hint, new_task=new_task, hint=hint
    )


def make_explain_output(reply="Evo objašnjenja."):
    return ExplainTurnOutput(reply=reply)


def make_quick_output(reply="Rezultat je $x=5$."):
    return QuickTurnOutput(reply=reply)


def make_visible_values(*triples):
    """(simbol, vrijednost, jedinica) → lista VisibleValue."""
    return [VisibleValue(symbol=s, value=v, unit=u) for s, v, u in triples]


def make_quick_image_output(
    reply="Rezultat je $x=5$.",
    readability="clear",
    all_required_symbols_visible=True,
    task_type="other",
    visible_math="",
    visible_problem_text="",
    requested_quantity="numeric_result",
    visible_values=None,
    unit="",
    answer_confidence="high",
    uncertainty_reason="",
):
    """Default je „jasna slika, model siguran“ — tj. stanje u kojem odgovor
    PROLAZI kapiju iz matbot/quick.py, pa postojeći testovi plumbinga slike
    provjeravaju isto ponašanje kao ranije."""
    return QuickImageTurnOutput(
        reply=reply,
        readability=readability,
        all_required_symbols_visible=all_required_symbols_visible,
        task_type=task_type,
        visible_math=visible_math,
        visible_problem_text=visible_problem_text,
        requested_quantity=requested_quantity,
        visible_values=visible_values or [],
        unit=unit,
        answer_confidence=answer_confidence,
        uncertainty_reason=uncertainty_reason,
    )


def make_options(*texts):
    return [Option(text=t) for t in texts]


# --- ČVOROVI SERVERSKOG KOSTURA (matbot/contracts/evidence.py) ---------------
# Male pomoćne funkcije da testovi generatora/verifikatora pišu izraze čitljivo.
# Namjerno ne znaju nijednu lekciju. (Modelov „dokaz“ više ne postoji — server
# konstruiše kostur; ovi helperi grade INTERNE čvorove, ne izlaz modela.)

def frac(numerator, denominator=1):
    """Literal čvor `num/den` (necijeli znak ide uz brojnik)."""
    from matbot.contracts import evidence as _ev
    return _ev.Node(num=numerator, den=denominator)


def node(op, left, right):
    """Binarna operacija nad dva čvora."""
    from matbot.contracts import evidence as _ev
    return _ev.Node(op=op, args=(left, right))


# Default zadatak MORA zadovoljiti ugovor porodice (matbot/task_family_validation.py):
# „proširi ... nazivnik“ + četiri razlomka = expand_to_given_denominator, a ujedno
# ne pokreće nijedan zabranjeni oblik opštih (fallback) porodica. Raniji
# placeholder („Zadatak.“ + opcije a/b/c/d) nije opisivao nikakvu stvarnu
# pedagošku operaciju i sada ga server s pravom odbija.
DEFAULT_TASK_TEXT = "Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$."
DEFAULT_TASK_OPTIONS = ("$\\frac{9}{24}$", "$\\frac{3}{24}$", "$\\frac{9}{8}$", "$\\frac{6}{24}$")


def make_task(text=DEFAULT_TASK_TEXT, expected="$\\frac{9}{24}$", difficulty="standard",
              options=None, correct_option_index=0, task_family=None,
              student_must_find=None, answer_kind=None, task_form=None):
    if options is None:
        options = make_options(*DEFAULT_TASK_OPTIONS)
    return NewTask(
        text=text, expected_answer=expected, difficulty=difficulty,
        options=options, correct_option_index=correct_option_index,
        task_family=task_family, student_must_find=student_must_find,
        answer_kind=answer_kind, task_form=task_form,
    )


# Zadaci koji zadovoljavaju ugovor SVAKE porodice — koriste ih integracijski
# testovi koji generišu više zadataka zaredom (server svaki put dodijeli drugu
# porodicu, pa svaki zadatak mora odgovarati SVOJOJ porodici).
_FAMILY_TASK_TEMPLATES = {
    # --- razlomci ---
    "expand_to_given_denominator": (
        "Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.",
        ("$\\frac{9}{24}$", "$\\frac{3}{24}$", "$\\frac{9}{8}$", "$\\frac{6}{24}$"),
    ),
    "find_expansion_factor": (
        "Razlomak $\\frac{2}{5}$ proširen je na $\\frac{8}{20}$. Kojim brojem je proširen?",
        ("4", "2", "3", "5"),
    ),
    "find_missing_numerator": (
        "Dopuni jednakost: $\\frac{3}{7} = \\frac{?}{35}$.",
        ("15", "10", "12", "21"),
    ),
    "recognize_equivalent_fraction": (
        "Koji od navedenih razlomaka je jednak razlomku $\\frac{4}{9}$?",
        ("$\\frac{16}{36}$", "$\\frac{4}{36}$", "$\\frac{12}{36}$", "$\\frac{20}{36}$"),
    ),
    "fraction_operation": (
        "Izračunaj $\\frac{2}{7} + \\frac{3}{7}$.",
        ("$\\frac{5}{7}$", "$\\frac{5}{14}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$"),
    ),
    "compare_fractions": (
        "Koji je razlomak veći: $\\frac{3}{5}$ ili $\\frac{5}{8}$?",
        ("$\\frac{5}{8}$", "$\\frac{3}{5}$", "$\\frac{4}{5}$", "$\\frac{2}{8}$"),
    ),
    "detect_student_error": (
        "Učenik je napisao $\\frac{3}{7}=\\frac{12}{21}$. Šta je pogriješio?",
        ("Brojnik je pomnožio sa 4, a nazivnik sa 3; oba treba pomnožiti istim brojem.",
         "Pogrešno je skratio tačan rezultat.",
         "Zamijenio je brojnik i nazivnik.",
         "Trebao je sabrati brojnik i nazivnik."),
    ),
    "fraction_word_problem": (
        "Amar je pojeo $\\frac{2}{8}$ torte, a Lejla $\\frac{3}{8}$ torte. "
        "Koliko su torte ukupno pojeli zajedno?",
        ("$\\frac{5}{8}$", "$\\frac{5}{16}$", "$\\frac{6}{8}$", "$\\frac{1}{8}$"),
    ),
    # --- opšte (fallback) ---
    "direct_computation": (
        "Izračunaj $12 \\cdot 4$.",
        ("48", "16", "36", "44"),
    ),
    "find_missing_value": (
        "Dopuni: $7 + \\square = 15$.",
        ("8", "7", "9", "22"),
    ),
    "recognize_correct_statement": (
        "Koja tvrdnja o uniji skupova je tačna?",
        ("Unija sadrži sve elemente oba skupa.",
         "Unija sadrži samo zajedničke elemente.",
         "Unija je uvijek prazan skup.",
         "Unija sadrži samo elemente prvog skupa."),
    ),
    "compare_or_order": (
        "Koji je broj veći: $-5$ ili $-3$?",
        ("-3", "-5", "Jednaki su.", "Nije moguće odrediti."),
    ),
    "word_problem": (
        "Amar ima 12 KM i kupi svesku za 5 KM u prodavnici. Koliko mu novca ostaje?",
        ("7", "17", "5", "12"),
    ),
}


def make_task_for_family(family_id, difficulty="standard", suffix=""):
    """Vrati NewTask koji ZADOVOLJAVA ugovor date porodice.

    `suffix` dopisuje kratak tekst da dva zadatka iste porodice ne budu
    doslovno identična (duplicate-signature zaštita)."""
    template = _FAMILY_TASK_TEMPLATES.get(family_id)
    if template is None:
        raise AssertionError(f"Nema test-template za porodicu {family_id!r}")
    text, options = template
    return make_task(
        text=text + suffix,
        expected="interno rješenje",
        difficulty=difficulty,
        options=make_options(*options),
        correct_option_index=0,
        task_family=family_id,
    )


# --- UNIVERZALNI DVOPOZIVNI PUT (matbot/tutor/) -----------------------------
# Pomoćne funkcije da testovi pišu Tutor nacrt i Reviewer odluku čitljivo.
# Ne znaju nijednu lekciju — isti helperi opslužuju svih 534.

DEFAULT_TUTOR_TASK_TEXT = "Izračunaj: $\\frac{2}{7} + \\frac{3}{7}$."
DEFAULT_TUTOR_OPTIONS = ("$\\frac{5}{7}$", "$\\frac{5}{14}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$")


def make_task_payload(text=DEFAULT_TUTOR_TASK_TEXT, options=None,
                      correct_option_index=0, expected="$\\frac{5}{7}$",
                      difficulty="standard"):
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter, TaskPayload,
                                     TaskSignature, TutorOption)

    texts = options if options is not None else DEFAULT_TUTOR_OPTIONS
    return TaskPayload(
        selected_lesson_id="__fixture__",
        selected_lesson_title="__fixture__",
        target_difficulty_level=1,
        text=text,
        task_type="multiple_choice",
        options=[TutorOption(id="abcd"[index], text=t) for index, t in enumerate(texts)],
        correct_option_index=correct_option_index,
        correct_option_id="abcd"[correct_option_index],
        expected_answer=expected,
        solution=expected,
        difficulty=difficulty,
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False,
        ),
        task_signature=TaskSignature(
            task_family="fixture", operation_or_relation="fixture_operation",
            normalized_parameters=[], required_conditions=[], relevant_objects=[],
            answer_type="multiple_choice",
        ),
    )


def make_difficulty_diagnostics(direction="lower", rationale="Manji brojevi."):
    """Sve dimenzije `same` osim veličine brojeva — najmanji validan pomak."""
    from matbot.tutor.schema import DifficultyDiagnostics

    return DifficultyDiagnostics(
        number_magnitude=direction,
        number_of_steps="same",
        representation_complexity="same",
        sign_complexity="same",
        scaffolding="same",
        distractor_closeness="same",
        reasoning_depth="same",
        rationale=rationale,
    )


def make_tutor_draft(intent="generate_task", reply="Evo zadatka.",
                     lesson_focus="sabiranje razlomaka jednakih imenilaca",
                     new_task=..., hint=None, worked_solution=None,
                     grading=None, difficulty_diagnostics=None):
    from matbot.tutor.schema import TutorDraft

    from matbot.tutor.schema import TASK_INTENTS

    if new_task is ...:
        new_task = make_task_payload() if intent in TASK_INTENTS else None
    return TutorDraft(
        intent=intent, reply=reply, lesson_focus=lesson_focus,
        new_task=new_task, hint=hint, worked_solution=worked_solution,
        grading=grading, difficulty_diagnostics=difficulty_diagnostics,
    )


def make_reviewer_checks(independent_answer="$\\frac{5}{7}$", **overrides):
    from matbot.tutor.schema import ReviewerChecks

    values = {
        "math_correct": True,
        "marked_option_correct": True,
        "inside_lesson": True,
        "intent_handled": True,
        "difficulty_direction_correct": True,
        "response_addresses_student": True,
        "task_solvable_and_unambiguous": True,
        "mathjax_valid": True,
        "language_age_appropriate": True,
        "independently_solved": True,
        "independent_answer": independent_answer,
        "task_package_consistent": True,
        "difficulty_evidence_valid": True,
        "task_signature_consistent": True,
    }
    values.update(overrides)
    return ReviewerChecks(**values)


def make_reviewer_final(decision="approve", final=..., checks=None,
                        fail_reason_code=None, reviewed_difficulty_evidence=..., **draft_kwargs):
    """Reviewer odluka. `final=...` znači „isti nacrt kakav bi Tutor dao“."""
    from matbot.tutor.schema import ReviewerFinal

    if final is ...:
        final = None if decision == "fail_closed" else make_tutor_draft(**draft_kwargs)
    if reviewed_difficulty_evidence is ...:
        task = getattr(final, "new_task", None)
        reviewed_difficulty_evidence = (
            task.difficulty_evidence if task is not None else None
        )
    return ReviewerFinal(
        decision=decision,
        checks=checks or make_reviewer_checks(),
        fail_reason_code=fail_reason_code,
        final=final,
        reviewed_difficulty_evidence=reviewed_difficulty_evidence,
    )


def queue_two_call(fake, draft=None, reviewer=None, **draft_kwargs):
    """Pripremi TAČNO dva odgovora: Tutor nacrt pa Reviewer odluku."""
    draft = draft if draft is not None else make_tutor_draft(**draft_kwargs)
    if reviewer is None:
        reviewer = make_reviewer_final(final=draft)
    fake.queue(draft)
    # Ordinary Tutor-only turns deliberately consume one call.  Task
    # publication is the only universal path that reaches the Reviewer.
    from matbot.tutor.schema import TASK_INTENTS
    if draft.intent in TASK_INTENTS:
        fake.queue(reviewer)
    return draft, reviewer


# --- RECENZENT VJERNOSTI LEKCIJI (matbot/lesson_fidelity.py) ----------------

def make_fidelity_checks(skill="vještina iz naslova lekcije", **overrides):
    from matbot.lesson_fidelity import FidelityChecks

    values = {
        "math_correct": True,
        "tests_exact_lesson": True,
        "required_task_form": True,
        "grade_appropriate": True,
        "solvable_and_unambiguous": True,
        "answer_correct": True,
        "marked_option_correct": True,
        "options_unique": True,
        "difficulty_direction_correct": True,
        "difficulty_level_appropriate": True,
        "lesson_skill_summary": skill,
    }
    values.update(overrides)
    return FidelityChecks(**values)


def make_fidelity_review(decision="approve", corrected_task=None,
                         fail_reason_code=None, checks=None, **check_overrides):
    from matbot.lesson_fidelity import LessonFidelityReview

    return LessonFidelityReview(
        decision=decision,
        checks=checks or make_fidelity_checks(**check_overrides),
        fail_reason_code=fail_reason_code,
        corrected_task=corrected_task,
    )


def queue_generation(fake, new_task, review=None, reply="Evo zadatka."):
    """Pripremi TAČNO dva odgovora: Tutor nacrt pa recenzent vjernosti."""
    fake.queue(make_output(reply=reply, new_task=new_task))
    fake.queue(review if review is not None else make_fidelity_review())


class FakeLLM:
    """Deterministički LLM za testove: redom vraća pripremljene odgovore ili
    baca pripremljene izuzetke; broji pozive i pamti promptove.
    call_count broji SVE pozive (practice + explain) — testovi „tačno jedan
    LLM poziv po turnu“ time hvataju i eventualni skriveni poziv drugog moda."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []           # (instructions, input_text) — svi pozivi redom
        self.explain_calls = []   # samo explain pozivi (podskup calls)
        self.quick_calls = []     # samo quick pozivi (podskup calls)
        self.quick_images = []    # ValidatedImage | None po quick pozivu
        self.tutor_calls = []     # samo Tutor pozivi (podskup calls)
        self.reviewer_calls = []  # samo Reviewer pozivi (podskup calls)
        self.fidelity_calls = []  # samo recenzent vjernosti lekciji (podskup calls)
        self.practice_calls = []  # samo Tutor pozivi stabilnog puta (podskup calls)

    def queue(self, item):
        self.results.append(item)

    @property
    def call_count(self):
        """SVI modelski pozivi (uključujući recenzenta vjernosti)."""
        return len(self.calls)

    @property
    def practice_call_count(self):
        """Samo TUTOROVI pozivi stabilnog Practice puta.

        Zatečeni regresijski testovi ovim čuvaju ono zbog čega su i pisani:
        „jedan tutor poziv po turnu, bez retryja i repair petlje“. Ukupan
        trošak turna (tutor + recenzent) provjerava
        tests/test_lesson_fidelity_reviewer.py."""
        return len(self.practice_calls)

    def _next(self, instructions, input_text):
        self.calls.append((instructions, input_text))
        if not self.results:
            raise AssertionError("FakeLLM: nije pripremljen odgovor za ovaj poziv")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(output=item, latency_ms=5, usage={"input_tokens": 100, "output_tokens": 50})

    def practice_turn(self, instructions, input_text):
        """Tutor poziv stabilnog puta. Bilježi se i u `practice_calls` da testovi
        koji ispituju TUTOROV prompt ne moraju pogađati indeks u `calls` — od
        uvođenja recenzenta vjernosti zadnji poziv u turnu zna biti recenzentov."""
        self.practice_calls.append((instructions, input_text))
        return self._next(instructions, input_text)

    def lesson_fidelity_turn(self, instructions, input_text):
        """DRUGI poziv stabilnog puta — samo za turn koji pravi zadatak.

        Kad test nije SAM pripremio recenziju, podrazumijeva se neutralno
        `approve`. Razlog: većina zatečenih Practice testova ispituje obradu
        TUTOROVOG nacrta, a ne recenzenta — ne smiju se rušiti na tuđem sloju.
        Poziv se i dalje UREDNO BROJI (`call_count`, `fidelity_calls`), pa
        nijedan test ne može slučajno previdjeti stvaran broj poziva; testovi
        koji recenzenta stvarno ispituju pripremaju odgovor eksplicitno
        (vidi tests/test_lesson_fidelity_reviewer.py)."""
        from matbot.lesson_fidelity import LessonFidelityReview

        self.fidelity_calls.append((instructions, input_text))
        if not self.results or not isinstance(self.results[0], (LessonFidelityReview, Exception)):
            self.calls.append((instructions, input_text))
            return LLMResult(output=make_fidelity_review(), latency_ms=5,
                             usage={"input_tokens": 100, "output_tokens": 50})
        return self._next(instructions, input_text)

    def tutor_turn(self, instructions, input_text):
        """PRVI poziv univerzalnog Practice puta."""
        self.tutor_calls.append((instructions, input_text))
        result = self._next(instructions, input_text)
        self._bind_universal_fixture_metadata(result.output, input_text)
        return result

    def reviewer_turn(self, instructions, input_text):
        """DRUGI (i posljednji) poziv univerzalnog Practice puta."""
        self.reviewer_calls.append((instructions, input_text))
        result = self._next(instructions, input_text)
        self._bind_universal_fixture_metadata(result.output, input_text)
        return result

    @staticmethod
    def _bind_universal_fixture_metadata(output, input_text):
        """Keep old fixture builders concise while exercising the strict package.

        This is test-double plumbing only: real structured model output has no
        placeholders and production validation always checks exact values.
        """
        from matbot.tutor.schema import SignatureParameter

        match = re.search(r"- lekcija: (.+) \((\d-\d{2}-\d{3})\)", input_text)
        if not match:
            return
        title, lesson_id = match.group(1), match.group(2)
        level_match = re.search(r"SERVER COMMITTED DIFFICULTY LEVEL: (\d)", input_text)
        level = int(level_match.group(1)) if level_match else 1

        def bind(draft):
            if draft is None or getattr(draft, "new_task", None) is None:
                return
            task = draft.new_task
            if task.selected_lesson_id != "__fixture__":
                return
            target = level
            if draft.intent == "harder_task":
                target = min(level + 1, 3)
            elif draft.intent == "easier_task":
                target = max(level - 1, 1)
            elif "AKTIVNI ZADATAK: ne postoji" in input_text:
                target = 1
            evidence = task.difficulty_evidence.model_copy(update=(
                {"reasoning_steps": 2, "condition_count": 2}
                if target == 2 else
                {"reasoning_steps": 3, "condition_count": 3,
                 "requires_proof_or_justification": True, "combines_concepts": True}
                if target == 3 else {}
            ))
            draft.new_task = task.model_copy(update={
                "selected_lesson_id": lesson_id,
                "selected_lesson_title": title,
                "target_difficulty_level": target,
                "difficulty_evidence": evidence,
                "task_signature": task.task_signature.model_copy(update={
                    "task_family": "fixture_" + lesson_id,
                    "normalized_parameters": [SignatureParameter(name="text", value=task.text)],
                }),
            })
        if hasattr(output, "final"):
            bind(output.final)
        else:
            bind(output)

    def explain_turn(self, instructions, input_text):
        self.explain_calls.append((instructions, input_text))
        return self._next(instructions, input_text)

    def quick_turn(self, instructions, input_text, image=None):
        self.quick_calls.append((instructions, input_text))
        self.quick_images.append(image)
        return self._next(instructions, input_text)


# ---------------------------------------------------------------------------
# IZBOR PRACTICE PUTA U TESTOVIMA
# ---------------------------------------------------------------------------
# Nakon rollbacka (2026-08-03) AKTIVAN i podrazumijevan je STABILAN jednopozivni
# put. Univerzalni dvopozivni put postoji iza eksplicitne zastavice
# `MATBOT_PRACTICE_PIPELINE=universal_two_call` i NIJE aktivan u produkciji.
#
# Zato je podrazumijevano stanje u testovima isto kao u produkciji (varijabla
# obrisana → stabilan put), a moduli koji testiraju univerzalni put moraju se
# EKSPLICITNO prijaviti ispod. Time nijedan test ne može slučajno tvrditi da je
# neprovjeren put aktivan.
_UNIVERSAL_TWO_CALL_MODULES = frozenset({
    "test_universal_tutor_pipeline",
    "test_universal_tutor_diagnosis",
    "test_structured_practice_packages",
    "test_reviewer_target_level_consistency",
    "test_reviewer_mcq_preflight",
    "test_reviewer_difficulty_preflight",
    # Runner live dijagnostike (tools/practice_eval) se testira u TAČNO onoj
    # konfiguraciji u kojoj se i pokreće — inače bi unit test tvrdio nešto o
    # putu koji kampanja nikad ne vozi.
    "test_practice_eval_runner",
    # Nepotpun tekst zadatka (živi nalaz A25/B02/B30/B42) je defekt univerzalnog
    # dvopozivnog puta — integracijski dio testa mora voziti taj put.
    "test_incomplete_task_text",
})


@pytest.fixture(autouse=True)
def _practice_pipeline_selection(request, monkeypatch):
    """Pripni put koji dati modul zaista testira.

    Podrazumijevano se briše varijabla — test tada vidi TAČNO ono što vidi
    produkcija."""
    module = request.node.module.__name__.rsplit(".", 1)[-1]
    if module in _UNIVERSAL_TWO_CALL_MODULES:
        monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    else:
        monkeypatch.delenv("MATBOT_PRACTICE_PIPELINE", raising=False)


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def store():
    return SessionStore()


@pytest.fixture
def flask_app(fake_llm, store):
    from app import app

    app.testing = True
    app.config["MATBOT_LLM"] = fake_llm
    app.config["MATBOT_SESSION_STORE"] = store
    # Svjež limiter/lock-registry PO TESTU — bez ovoga bi testovi dijelili
    # brojače kroz zajednički (module-level) Flask app singleton i lažno
    # trošili jedni drugima rate-limit budžet. Limiti su namjerno VISOKI da
    # regresijski testovi (koji nisu O rate limitingu) nikad slučajno okinu 429.
    app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(per_minute=100000, per_hour=100000)
    app.config["MATBOT_IP_LIMITER"] = RateLimiter(per_minute=100000, per_hour=100000)
    app.config["MATBOT_TURN_LOCKS"] = TurnLockRegistry()
    yield app
    app.config.pop("MATBOT_LLM", None)
    app.config.pop("MATBOT_SESSION_STORE", None)
    app.config.pop("MATBOT_SESSION_LIMITER", None)
    app.config.pop("MATBOT_IP_LIMITER", None)
    app.config.pop("MATBOT_TURN_LOCKS", None)


@pytest.fixture
def client(flask_app):
    """Test klijent sa VAŽEĆIM tokenom već postavljenim na svaki zahtjev —
    ogleda stvarno frontend ponašanje (token se šalje na svaki API poziv) bez
    potrebe da svaki postojeći test ručno dodaje header. Testovi koji SPECIFIČNO
    provjeravaju auth ponašanje prave svoj test_client() bez ovog defaulta."""
    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()
    return c


# eksporti za testove
__all__ = [
    "FakeLLM", "make_output", "make_task", "make_options", "make_task_for_family",
    "make_explain_output", "make_quick_output", "LLMTimeout", "LLMUnavailable",
    "DEFAULT_TASK_TEXT", "DEFAULT_TASK_OPTIONS",
]
