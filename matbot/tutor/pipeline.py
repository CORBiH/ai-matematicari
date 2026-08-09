"""Univerzalni dvopozivni Practice turn — JEDAN aktivni put za svih 534 lekcije.

    LessonContext → Tutor poziv → Reviewer poziv → serverska validacija
    → objava → copy-on-write stanje

GRANICA POZIVA (tvrdo): najviše TAČNO DVA modela poziva po neblokiranom turnu.
  • blokirano prije modela (nepoznata lekcija, nevažeći klik) → 0 poziva
  • Tutor nacrt neparsabilan/timeout                          → 1 poziv, STOP
  • sve ostalo                                                → tačno 2 poziva
Nema retryja, repair petlje, trećeg poziva ni skrivene zamjene: kad recenzent
padne ili serverska validacija odbije, vraća se sigurna poruka.

STANJE: sesija je lokalna kopija i mijenja se TEK kad su oba poziva gotova,
konačan payload prošao šemu i sve serverske provjere, i kad zadatak nije
zastario. `store.save` je jedina commit tačka.

Ovaj modul NEMA nijednu granu po ID-ju lekcije. Sve što razlikuje lekcije
dolazi iz LessonContext-a (podaci), a semantičku kapiju opsega drži recenzent.
"""
import contextlib
import copy
import inspect
import logging
import random
import re
import time
import uuid

from matbot import (config, difficulty_level, difficulty_profiles, feedback,
                    geometrycheck, mcq_integrity, option_equivalence,
                    practice_policy)
from matbot.llm import LLMError, failure_diagnostics_kv
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.semantics import detectors as semantic_detectors
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight
from matbot.tutor import reviewer_authority
from matbot.tutor import task_identity
from matbot.tutor import prompts as tutor_prompts
from matbot import deterministic as deterministic_generators
from matbot.deterministic.core import DeterministicGenerationError
from matbot.tutor.schema import (TASK_INTENTS, TutorDraft, UnifiedOutputError,
                                 normalize_for_intent, validate_final, validate_task,
                                 validate_difficulty_evidence, validate_reviewer)

logger = logging.getLogger("matbot.tutor")

SAFE_ERROR_MESSAGE = (
    "Nešto je zapelo pri sastavljanju odgovora. Pošalji poruku ponovo za koji trenutak."
)

# Recenzentov KONAČAN paket i dalje nosi dokazan deterministički defekt. Zaseban
# kod da gate ovo razlikuje od obične završne validacije i od pada šeme.
REVIEWER_FINAL_INTEGRITY_CODE = "reviewer_final_mcq_integrity_rejection"

_NEW_TASK_INTRO = {
    "easier_task": "Evo lakšeg zadatka.",
    "harder_task": "Evo težeg zadatka.",
    "next_task": "Evo sljedećeg zadatka.",
    "generate_task": "Evo zadatka.",
}

# GRANICE NIVOA — uvod mora biti ISTINIT (produkcijski nalaz i živi talas F4F,
# scenariji F09/F10): na nivou 1 je „Daj mi lakši zadatak.“ vraćalo „Evo lakšeg
# zadatka.“, iako ispod nivoa 1 nema ničega i nivo je ostao 1. Server je znao
# tačnu tranziciju (`_target_level_for` sidri na min/max), ali je uvod biran iz
# modelove NAMJERE umjesto iz stvarne promjene.
#
# Na granici je cilj POZNAT (uvijek pravi minimum/maksimum), pa se smije
# imenovati — isto rješenje koje legacy put ima odavno
# (matbot/practice.py::_ANOTHER_INTRO_TASK_INTRO).
INTRO_AT_EASIEST_LEVEL = "Ovo je već najlakši nivo, evo još jednog uvodnog zadatka."
INTRO_AT_HARDEST_LEVEL = "Ovo je već najviši nivo, evo još jednog naprednog zadatka."


def _intro_for(intent, previous_level, target_level):
    """Uvod se bira iz STVARNE serverske tranzicije, nikad iz modelove namjere.

    `previous_level`/`target_level` su None kad je univerzalni kontroler težine
    isključen — tada je ponašanje bajt za bajt kao ranije."""
    default = _NEW_TASK_INTRO.get(intent, _NEW_TASK_INTRO["generate_task"])
    if previous_level is None or target_level is None:
        return default
    if target_level != previous_level:
        return default                      # promjena se stvarno desila
    if intent == "easier_task":
        return INTRO_AT_EASIEST_LEVEL
    if intent == "harder_task":
        return INTRO_AT_HARDEST_LEVEL
    return default


def _message_level_floor(student_message, intent):
    """Deterministički MINIMUM cilja izveden iz PORUKE učenika (Faza 4G).

    ŽIVI F4G TALAS (G03/G05; isti oblik ranije F4F F13–F15): „Daj mi zadatak
    gdje broj mora biti djeljiv i sa 6 i sa 25.“ na svježoj sesiji cilja nivo 1,
    a zadatak s dva uslova je definiciono nivo 2 — pa je svaki takav izričit
    zahtjev OBAVEZNO padao zatvoreno (recenzent: `fail_closed` ili izdaja
    jedno-pravilnog zadatka koji ne odgovara zahtjevu). Kad učenikova VLASTITA
    poruka deterministički traži složen uslov (zatvorena gramatika djeljivosti,
    bez negacije i disjunkcije), cilj za NOVI zadatak je najmanje 2.

    `easier_task`/`harder_task` ostaju čisti koraci progresije — floor se na
    njih nikad ne primjenjuje."""
    if intent not in ("generate_task", "next_task"):
        return 1
    return 2 if mcq_integrity.explicit_compound_divisor_request(student_message) else 1


def _target_level_for(session, intent, student_message=""):
    """One lesson-independent progression policy owned by the server."""
    current = min(max(int(session.get("difficulty_level", 1)), 1), 3)
    floor = _message_level_floor(student_message, intent)
    if not session.get("current_task"):
        return max(1, floor)
    if intent == "harder_task":
        return min(current + 1, 3)
    if intent == "easier_task":
        return max(current - 1, 1)
    if intent == "next_task":
        if session.get("last_result") == "full_solution":
            return max(current - 1, floor)
        if session.get("correct_streak", 0) >= 2 and not session.get("current_task_had_hint"):
            return max(min(current + 1, 3), floor)
    return max(current, floor)


def _difficulty_levels_enabled():
    """Use the shared opt-in controller flag without importing Practice."""
    return config.practice_difficulty_levels_enabled()


# ---------------------------------------------------------------------------
# EKSPLICITNA UI AKCIJA NAD AKTIVNIM ZADATKOM (živi produkcijski nalaz)
# ---------------------------------------------------------------------------
# Niz iz produkcije: objavljen zadatak → „Ne znam — daj mi hint“ (hint uredan)
# → „Uradi ga ti“ → server je objavio POTPUNO NOV zadatak i pregazio aktivan.
#
# UZROK: ovaj put nikad nije čitao `turn["intent"]`. Frontend eksplicitno šalje
# `intent=solution_request` kad učenik pritisne to dugme, ali je taj signal
# ostajao neiskorišten — namjeru je odlučivao ISKLJUČIVO model iz slobodnog
# teksta „Uradi ga ti.“, a kad vrati `next_task`, `_run_text_turn` objavi paket.
#
# ZAŠTO OVO NE KRŠI „klijentu se ne vjeruje“: vrijednost se koristi SAMO da
# SUZI ono što server smije uraditi. Ona ne može odobriti nijednu objavu koja
# inače ne bi prošla, ne bira lekciju, ne dira ocjenjivanje i ne otkriva
# odgovor; lažna vrijednost može izazvati odbijanje, nikad publikaciju. Drugi
# uslov je SERVERSKA činjenica: zabrana važi samo dok aktivan zadatak postoji.
_UI_ACTION_INTENTS = {
    "solution_request": "full_solution_request",
    "hint_request": "hint_request",
}


def _explicit_ui_action(turn, session):
    """Namjera iz šeme koju je učenik dugmetom izričito tražio, ili prazno.

    Prazno znači „kucana poruka“ — tada se ništa ne mijenja u odnosu na raniji
    tok i namjeru i dalje određuje model."""
    if not session.get("current_task"):
        return ""
    return _UI_ACTION_INTENTS.get((turn.get("intent") or "").strip().lower(), "")


def canonicalize_task_lesson_id(task, context):
    """Uska normalizacija REPREZENTACIJE identiteta lekcije (kapija na ebdccf0).

    ŽIVI NALAZ: na završnoj kapiji su i Tutor i Recenzent u
    `selected_lesson_id` vratili sastavljeni zapis „Naslov (ID)“ — eho
    ulaznog reda „- lekcija: Naslov (ID)“ — pa je potpuno ispravan zadatak
    odbijen samo zbog formata identiteta. Prihvataju se TAČNO dva zapisa:
    goli kanonski ID, i kanonski naslov iza kojeg u zagradi stoji kanonski
    ID, pri čemu OBA dijela moraju nezavisno odgovarati server-vlasničkom
    LessonContext-u; sastavljeni zapis se tada svodi na goli ID. Ovo NIJE
    fuzzy matching: bilo koji drugi oblik (pogrešan naslov, pogrešan ID,
    proza oko ID-ja, više ID-jeva, samo naslov…) prolazi netaknut i pada na
    postojećoj invarijanti u validate_task_package."""
    if task is None or task.selected_lesson_id == context.topic_id:
        return task
    if task.selected_lesson_id == f"{context.title} ({context.topic_id})":
        # Dijagnostika bez sadržaja: tip zapisa i kanonski ID, ništa učeniku.
        logger.info(
            "lesson_id_representation_normalized topic=%s form=title_parenthesized_id",
            context.topic_id)
        return task.model_copy(update={"selected_lesson_id": context.topic_id})
    return task


def canonicalize_task_lesson_title(task, context):
    """Make the server-owned LessonContext the sole display-title authority.

    The model must still provide the exact selected lesson ID.  Once that ID
    is known to be correct, its repeated title is only a non-authoritative
    display copy, so harmless spelling or formatting drift cannot reject an
    otherwise Reviewer-approved package.
    """
    if task is None or task.selected_lesson_id != context.topic_id:
        return task
    if task.selected_lesson_title == context.title:
        return task
    return task.model_copy(update={"selected_lesson_title": context.title})


def _canonicalize_draft_lesson_title(draft, context):
    task = getattr(draft, "new_task", None)
    canonical_task = canonicalize_task_lesson_title(
        canonicalize_task_lesson_id(task, context), context)
    return draft if canonical_task is task else draft.model_copy(update={"new_task": canonical_task})


def validate_task_package(task, context, target_level=None):
    """Universal package invariants; semantic lesson judgement stays with Reviewer."""
    task = canonicalize_task_lesson_title(
        canonicalize_task_lesson_id(task, context), context)
    validate_task(task)
    if task.selected_lesson_id != context.topic_id:
        raise UnifiedOutputError("task lesson ID does not match selected lesson")
    # Structured generation always requires a complete package, but only the
    # explicitly enabled controller owns target validation and rubric policy.
    if target_level is not None:
        if task.target_difficulty_level != target_level:
            raise UnifiedOutputError("task target difficulty does not match server target")
        # Faza F5G: profil se razrješava ISKLJUČIVO iz server-vlasničkog
        # konteksta (zamrznuta primarna porodica, lekcija bez semantičkog
        # ugovora) — payload modela ne može izabrati blažu rubriku.
        validate_difficulty_evidence(
            task, profile=difficulty_profiles.resolve_for_context(context))
    return task


def _structured_signature_record(task, context):
    canonical = task.task_signature.canonical_json()
    return {
        "lesson_id": context.topic_id,
        "structured_signature": canonical,
        "structured_signature_hash": task.task_signature.digest(),
    }


def _is_duplicate_structured_signature(record, prior_records):
    return any(
        previous.get("lesson_id") == record["lesson_id"]
        and previous.get("structured_signature_hash") == record["structured_signature_hash"]
        for previous in (prior_records or [])
    )

_LOG_LIMIT = 200


def _clip(value, limit=_LOG_LIMIT):
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _error_response(active_task=""):
    """Namjerno BEZ 'status' i BEZ 'next_state' — frontend tada čuva svoje
    stanje (isti ugovor kao i ranije).

    `task_preserved` je EKSPLICITAN metapodatak, ne nagađanje iz teksta:
    frontend je do sada brisao kartice opcija za SVAKI odgovor bez
    `status:'ready'`, iako je server aktivan zadatak zadržao. Sada mu server
    jasno kaže da zadatak i dalje stoji, pa učenik može nastaviti da odgovara."""
    return {
        "answer": SAFE_ERROR_MESSAGE,
        "last_tutor_task": active_task or "",
        "task_preserved": bool(active_task),
    }


class _TurnTimer:
    """Faza 4H (forenzika latencije): per-stage vrijeme JEDNOG turna.

    Bilježi SAMO milisekunde po imenovanoj fazi — nikad sadržaj, prompt ni
    poruku. Postoji jer se iz loga nije moglo pročitati gdje turn stvarno
    troši vrijeme: mjerena je bila samo SDK latencija, pa je svako lokalno
    vrijeme (prompt, validacija, objava, commit) bilo nevidljivo."""

    def __init__(self):
        self._start = time.perf_counter()
        self.stages = {}

    @contextlib.contextmanager
    def stage(self, name):
        begin = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0) + int(
                (time.perf_counter() - begin) * 1000)

    def note_ms(self, name, ms):
        if ms is not None:
            self.stages[name] = self.stages.get(name, 0) + int(ms)

    def total_ms(self):
        return int((time.perf_counter() - self._start) * 1000)

    def describe(self):
        return "|".join(f"{k}:{v}" for k, v in self.stages.items()) or "-"


def _context_family(context):
    contract = getattr(context, "semantic_contract", None)
    return getattr(contract, "family_id", "") or "-"


def _next_state(session):
    state = {
        "v": 1,
        "correct_streak": session["correct_streak"],
        "hint_level": session["hint_level"],
    }
    if session["current_task"]:
        task = {"question": session["current_task"]}
        if session["current_options"]:
            task["options"] = session["current_options"]
        # Serverski identitet aktivnog zadatka. Nije tajna (izveden je iz onoga
        # što učenik ionako vidi) i frontend njime veže vizuelno stanje za
        # KONKRETAN zadatak umjesto za njegov tekst.
        identity = session.get("current_task_identity") or ""
        if identity:
            task["identity"] = identity
        state["task"] = task
    return state


def _shuffle_options(texts, correct_index):
    """Server je jedini koji dodjeljuje ID-jeve opcijama i pamti tačan.
    Miješa se TAČNO JEDNOM po novom zadatku."""
    ids = ["a", "b", "c", "d"]
    pairs = list(enumerate(texts))
    random.shuffle(pairs)
    current_options, correct_option_id = [], ""
    for slot, (original_index, text) in enumerate(pairs):
        current_options.append({"id": ids[slot], "text": text})
        if original_index == correct_index:
            correct_option_id = ids[slot]
    return current_options, correct_option_id


# ---------------------------------------------------------------------------
# ZAJEDNIČKE SERVERSKE PROVJERE — identične za svih 534 lekcije
# ---------------------------------------------------------------------------

def _safe_text(raw, where, allow_wrap=False):
    """Sanitizacija + terminologija + math-safety. Baca UnifiedOutputError.

    Sam niz koraka živi u `package_preflight.safe_visible_text` — JEDNA
    implementacija, koju preflight koristi bez izuzetka, a objava uz izuzetak.

    ŽIVI F5L NALAZ (I03 forenzika): help-turn (hint/rješenje) je JEDAN poziv
    bez preflighta, pa je produkcijsko odbijanje rješenja ostajalo bez ikakvog
    koda defekta — ponavljajuća klasa (M06, M18, I03) bila je nedijagnostikljiva.
    Kodovi su isti sankcionisani log kodovi kao u preflightu (CLAUDE.md pravilo
    7): najviše ime komande, nikad sadržaj. Ponašanje prihvat/odbij se NE mijenja."""
    cleaned, safe = package_preflight.safe_visible_text(raw, allow_wrap=allow_wrap)
    if not safe:
        codes = package_preflight._notation_defect_codes(raw, allow_wrap)
        suffix = f" codes={codes}" if codes else ""
        raise UnifiedOutputError(
            f"nebezbjedan matematički zapis [{where}]{suffix}")
    return cleaned


def _reject_if_inconsistent(text, where):
    issues = find_numeric_inconsistencies(text)
    if issues:
        raise UnifiedOutputError(f"{issues[0]} [{where}]")


def _reject_if_geometry_invalid(text, context, where):
    issues = geometrycheck.find_geometry_issues(
        text, context.geometry_scope, list(context.geometry_figures)
    )
    if issues:
        raise UnifiedOutputError(f"geometry_notation: {','.join(issues)} [{where}]")


def _reject_if_semantic_contract_violated(text, context, where):
    """ODBRANA U DUBINI (Faza 4B): isti detektor koji je već pokrenut u
    preflightu i u invarijanti nad recenzentovim paketom, sada i NEPOSREDNO
    PRIJE OBJAVE — dakle prije ijedne mutacije sesije.

    Ne uvodi novi prag: `unsupported` i dalje nikad ne odbija, a blokira samo
    lekcija čiji ugovor je izričito `blocking`."""
    contract = getattr(context, "semantic_contract", None)
    if contract is None or not getattr(contract, "blocking", False):
        return
    detection = semantic_detectors.detect(contract, text)
    if detection.status == semantic_detectors.STATUS_FAIL:
        raise UnifiedOutputError(
            f"{detection.code}: {detection.reason} [{where}]")


def _validate_task_server_side(task, context, previous_signature=""):
    """Sve deterministe koje su i ranije štitile objavljen zadatak.

    `previous_signature` je kanonski identitet AKTIVNOG zadatka; kad je zadat,
    paket koji je kanonski isti pada zatvoreno TAČNO OVDJE — posljednja tačka
    prije mutacije sesije (produkcijski nalaz: „Daj mi novi zadatak.“ je vratio
    isti zadatak i iste opcije).

    Vraća (tekst_zadatka, sanitizovani_tekstovi_opcija, expected, solution)."""
    task_text = _safe_text(task.text, "tekst zadatka")
    _reject_if_inconsistent(task_text, "tekst zadatka")
    _reject_if_geometry_invalid(task_text, context, "tekst zadatka")
    _reject_if_semantic_contract_violated(task_text, context, "tekst zadatka")

    option_texts = [
        _safe_text(option.text, "opcija", allow_wrap=True) for option in task.options
    ]

    duplicates = option_equivalence.find_textual_duplicate_pairs(option_texts)
    if duplicates:
        raise UnifiedOutputError(f"duple opcije: {duplicates}")
    equivalent = option_equivalence.find_equivalent_option_pairs(option_texts)
    if equivalent:
        raise UnifiedOutputError(f"semantically_duplicate_options: {equivalent}")

    # Tačna opcija i očekivani odgovor nose PRAVU matematiku — uvijek se
    # provjeravaju. Distraktori su namjerno pogrešni i NIKAD se ne provjeravaju.
    correct_text = option_texts[task.correct_option_index]
    _reject_if_inconsistent(correct_text, "tačna opcija")
    _reject_if_geometry_invalid(correct_text, context, "tačna opcija")
    expected = _safe_text(task.expected_answer, "expected_answer", allow_wrap=True)
    _reject_if_inconsistent(expected, "expected_answer")
    if expected.strip() != correct_text.strip():
        raise UnifiedOutputError("expected answer does not match marked option")
    solution = _safe_text(task.solution, "solution")
    _reject_if_inconsistent(solution, "solution")

    # POLITIKA PP-1 (audit ovlašćenja pravila): kurikularna metoda, vidljivi
    # brojevni domen i granica naprednih operacija važe za OBA autora paketa —
    # model i deterministički generator prolaze kroz OVU ISTU tačku. Uzorci su
    # namjerno uski (vidi matbot/practice_policy.py), pa binarno oduzimanje i
    # legitimna proza nikad nisu pogodak.
    policy = getattr(context, "practice_policy", None)
    if policy is not None:
        for where, surface in (("tekst zadatka", task_text),
                               ("solution", solution),
                               *(("opcija", option) for option in option_texts)):
            codes = practice_policy.text_policy_failures(policy, surface)
            if codes:
                raise UnifiedOutputError(f"{','.join(codes)} [{where}]")

    # USKI MATEMATIČKI ORAKL NEPOSREDNO PRIJE OBJAVE (živi produkcijski nalaz,
    # lekcija o pravilima djeljivosti): objavljen je MCQ bez ijednog tačnog
    # odgovora — nijedna od četiri ponuđene opcije nije zadovoljavala uslov. Isti
    # `mcq_integrity` nalaz preflight već računa i šalje recenzentu, ali je to
    # bila JEDINA kapija na ovom putu — sve ostalo u ovoj funkciji provjerava
    # zapis i međusobnu dosljednost polja, nikad samu matematiku ponuđenih
    # opcija. Legacy put ovaj orakl odavno pokreće u `_apply_new_task`; ovdje
    # nedostajao. Nalaz ovdje znači fail closed prije IJEDNE mutacije sesije i
    # bez trećeg poziva; za neprimjenjiv oblik orakl i dalje ćuti.
    mcq_failure, _mcq_result = mcq_integrity.publication_failure(
        task_text, option_texts, task.correct_option_index, expected,
    )
    if mcq_failure:
        raise UnifiedOutputError(f"mcq_integrity: {mcq_failure}")

    # KANONSKI IDENTITET — posljednja kapija prije mutacije sesije. Poredi se
    # ono što učenik VIDI, nikad `task_signature` koju model deklariše o sebi.
    if task_identity.is_same_task(
            previous_signature, task_identity.canonical_signature(task_text, option_texts)):
        raise UnifiedOutputError(
            f"{package_preflight.DUPLICATE_ACTIVE_TASK_CODE}: isti zadatak kao aktivni")

    return task_text, option_texts, expected, solution


# ---------------------------------------------------------------------------
# DVA POZIVA
# ---------------------------------------------------------------------------

def _call_tutor(llm, context, session, student_message, trusted_verdict, ui_action="",
                timer=None):
    timer = timer or _TurnTimer()
    with timer.stage("prompt_build"):
        instructions = tutor_prompts.build_tutor_instructions(context)
        input_text = tutor_prompts.build_tutor_input(
            context, session, student_message, trusted_verdict, ui_action
        )
    with timer.stage("tutor_call"):
        result = llm.tutor_turn(instructions, input_text)
    # SDK-mjerena latencija zasebno: (tutor_call − tutor_api) je lokalni režijski
    # trošak (konstrukcija zahtjeva + parsiranje odgovora u SDK-u).
    timer.note_ms("tutor_api", getattr(result, "latency_ms", None))
    return result


def _call_reviewer(llm, context, session, student_message, draft, trusted_verdict,
                   preflight_block="", ui_action="", timer=None, timeout_s=None):
    timer = timer or _TurnTimer()
    with timer.stage("prompt_build"):
        instructions = tutor_prompts.build_reviewer_instructions(context)
        draft_json = draft.model_dump_json(indent=None, exclude_none=True)
        input_text = tutor_prompts.build_reviewer_input(
            context, session, student_message, draft_json, trusted_verdict,
            preflight_block, ui_action,
        )
    with timer.stage("reviewer_call"):
        result = llm.reviewer_turn(instructions, input_text, timeout_s=timeout_s)
    timer.note_ms("reviewer_api", getattr(result, "latency_ms", None))
    return result


def _log_rejection(request_id, context, stage, detail, intent=""):
    """Interna dijagnostika — NIKAD u browser."""
    logger.warning(
        "tutor_rejected request_id=%s topic=%s stage=%s intent=%s detail=%s",
        request_id, context.topic_id if context else "", stage, intent or "-",
        _clip(detail, 300),
    )


def _log_difficulty(request_id, context, final):
    """Dijagnostika težine ide SAMO u log (učenik je nikad ne vidi)."""
    diagnostics = final.difficulty_diagnostics
    if diagnostics is None:
        return
    logger.info(
        "tutor_difficulty request_id=%s topic=%s intent=%s magnitude=%s steps=%s "
        "representation=%s sign=%s scaffolding=%s distractors=%s reasoning=%s why=%s",
        request_id, context.topic_id, final.intent,
        diagnostics.number_magnitude, diagnostics.number_of_steps,
        diagnostics.representation_complexity, diagnostics.sign_complexity,
        diagnostics.scaffolding, diagnostics.distractor_closeness,
        diagnostics.reasoning_depth, _clip(diagnostics.rationale, 200),
    )


# ---------------------------------------------------------------------------
# DETERMINISTIČKA STRATEGIJA IZVRŠENJA (Faza 4H) — ISTI orkestrator, dvije
# strategije iza njega. Ovo NIJE drugi pipeline: ruta se bira ovdje, a objava,
# sesija i svi validatori ostaju zajednički.
# ---------------------------------------------------------------------------
# Registar po SEMANTIČKOJ PORODICI — nikad po lekciji. Lekcija je pokrivena
# samo kad ima blocking ugovor čije parametre generator u potpunosti podržava.
# Registar živi u matbot/deterministic/__init__.py da dodavanje novog
# kapaciteta ne dira orkestrator (kapacitetna ekspanzija, poslije Faze 4H).
_DETERMINISTIC_GENERATORS = deterministic_generators.GENERATORS


def _generate_with_policy(generator, *, policy, **kwargs):
    """Proslijedi razriješenu politiku generatoru koji je ZNA konsumirati.

    PP-1 (audit ovlašćenja pravila): motori su do sada primali samo
    lesson_id/naslov/parametre/nivo — bez razreda i bez politike. Umjesto
    mehaničkog širenja potpisa svih 27 modula, ova JEDNA tačka prosljeđuje
    `policy` isključivo modulima čiji je `generate_package` deklariše;
    ostali rade bajt za bajt kao prije. Modul NIKAD ne izvodi razred iz
    prefiksa lesson_id-ja — politika je jedini nosilac razredne odluke."""
    if policy is not None:
        try:
            accepts = "policy" in inspect.signature(
                generator.generate_package).parameters
        except (TypeError, ValueError):
            accepts = False
        if accepts:
            return generator.generate_package(policy=policy, **kwargs)
    return generator.generate_package(**kwargs)


def _deterministic_generator_for(context):
    """Modul generatora za lekciju, ili None kad lekcija nije POTPUNO pokrivena."""
    if not config.deterministic_practice_enabled():
        return None
    contract = getattr(context, "semantic_contract", None)
    if contract is None or not getattr(contract, "blocking", False):
        return None
    module = _DETERMINISTIC_GENERATORS.get(getattr(contract, "family_id", ""))
    if module is None or not module.supports(getattr(contract, "parameters", None)):
        return None
    return module


# ZATVOREN skup jednostavnih poruka za izradu zadatka. Sve što ne stane u ovaj
# skup (dodatni uslovi, slobodna pitanja, specifični zahtjevi) ide model-putem —
# ruta se NIKAD ne bira iz modelove proze.
_SIMPLE_TASK_REQUEST_RE = re.compile(
    r"(?:daj\s+mi\s+|hoću\s+|može\s+)?"
    r"(?:jedan\s+|još\s+jedan\s+|novi\s+|sljedeći\s+|idući\s+)?"
    r"zadatak(?:\s+za\s+vježbu)?(?:\s+iz\s+ove\s+(?:teme|lekcije|oblasti))?"
    r"\s*[.!]?",
    re.IGNORECASE,
)
_EASIER_MESSAGE_RE = re.compile(
    r"(?:daj\s+mi\s+)?(?:jedan\s+)?lakši\s+zadatak\s*[.!]?", re.IGNORECASE)
_HARDER_MESSAGE_RE = re.compile(
    r"(?:daj\s+mi\s+)?(?:jedan\s+)?(?:još\s+)?teži\s+zadatak\s*[.!]?", re.IGNORECASE)


def _deterministic_task_intent(turn, session):
    """Server-vlasnička namjera za determinističku izradu zadatka, ili ''.

    Izvodi se ISKLJUČIVO iz UI polja (`difficulty_request`) i zatvorenog skupa
    jednostavnih poruka. Dugmad pomoći (intent=hint/solution) nikad nisu
    izrada zadatka."""
    if (turn.get("intent") or "").strip():
        return ""
    difficulty = (turn.get("difficulty_request") or "").strip().lower()
    if difficulty == "easier":
        return "easier_task"
    if difficulty == "harder":
        return "harder_task"
    message = (turn.get("student_message") or "").strip()
    if _EASIER_MESSAGE_RE.fullmatch(message):
        return "easier_task"
    if _HARDER_MESSAGE_RE.fullmatch(message):
        return "harder_task"
    if _SIMPLE_TASK_REQUEST_RE.fullmatch(message):
        return "next_task" if session.get("current_task") else "generate_task"
    return ""


def _deterministic_active_annex(session):
    """Deterministički dodatak VAŽEĆI za aktivni zadatak, ili None.

    Identitet se poredi izričito: dodatak tuđeg (starijeg) zadatka ne smije
    ocijeniti, nagovijestiti ni riješiti aktivni — tada se pada na model-put."""
    annex = session.get("deterministic_task") or None
    if not annex:
        return None
    if annex.get("task_identity") != (session.get("current_task_identity") or ""):
        return None
    return annex


def _deterministic_safe_help_texts(package, context, request_id, intent):
    """Sanitizovana ljestvica nagovještaja paketa — ili UnifiedOutputError.

    ZAŠTO POSTOJI (audit ovlašćenja pravila, PHASE A): `hints` nisu dio
    `TaskPayload`-a, pa ih objavna validacija NIKAD nije vidjela. U sesiju je
    išao SIROV string generatora, a help-turn ga je slao učeniku doslovno — bez
    mathsafe provjere, bez normalizacije terminologije, bez ijednog validatora.
    To je bila jedina učeniku vidljiva površina bez ijedne kapije.

    Ovdje se NE UVODI novi sanitizer ni novi prag: poziva se ISTI
    `_safe_text` + `_reject_if_inconsistent` + `_reject_if_geometry_invalid`
    niz koji objava pokreće nad tekstom zadatka. Semantički detektor porodice
    se NAMJERNO ne pokreće nad nagovještajem: on pita „radi li ovaj tekst ono
    što lekcija ispituje“, a nagovještaj je uputa („prvo nađi zajednički
    nazivnik“), ne zadatak — to bi bio validator zadatka pušten nad prozom.

    Zove se PRIJE `_publish_task`, dakle prije ijedne mutacije sesije: kandidat
    s neupotrebljivim nagovještajem pada zatvoreno i petlja bira nove operande,
    a kad nijedan pokušaj ne uspije, stanje ostaje netaknuto."""
    try:
        safe_hints = []
        for index, hint in enumerate(package.hints):
            where = f"deterministički nagovještaj {index + 1}"
            text = _safe_text(hint, where)
            _reject_if_inconsistent(text, where)
            _reject_if_geometry_invalid(text, context, where)
            safe_hints.append(text)
    except UnifiedOutputError as error:
        _log_rejection(request_id, context, "deterministic_help_text", error, intent)
        raise
    return tuple(safe_hints)


def _deterministic_choice_reply(annex, is_correct, wrong_before):
    """Kratka povratna poruka iz POHRANJENIH činjenica paketa — bez modela.

    Prvi pogrešan klik dobija pravilo-nagovještaj (nikad rezultat); drugi
    pogrešan klik otkriva rezultat NAMJERNO — isti ugovor kao model-put."""
    # `answer_reply` je OBJAVLJEN, sanitizovan tekst tačne opcije — doslovno
    # ono što učenik vidi na kartici. Ranije se ovdje sirov `display_answer`
    # slijepo umotavao u `$…$`, pa je za porodice koje već nose vlastite
    # dolare nastajalo `$$13$ i $16$$` (pokvaren MathJax), a za mjerne
    # jedinice nebezbjedan zapis tipa `$360 min$` — nijedan od ta dva nikad
    # nije prošao ijedan validator (PHASE A).
    answer_display = annex["answer_reply"]
    if is_correct:
        return f"Tačno — rezultat je {answer_display}. Bravo!"
    if wrong_before >= 1:
        return (f"Nije tačno. Tačan rezultat je {answer_display}. "
                "Pogledaj označenu opciju, pa traži sljedeći zadatak.")
    rule_hint = (annex.get("hints") or [""])[0]
    return f"Nije tačno. {rule_hint}".strip()


def _deterministic_top_hint(session, annex, hint_text):
    """Treći (posljednji) nagovještaj: postupak PA konačan rezultat.

    ZAŠTO POSTOJI (PP-1 LIVE-150, odluka o ljestvici): model-put ljestvicu već
    ovako vodi — `_HINT_LEVEL_GUIDANCE[2]` traži „CIJELI postupak i konačan
    rezultat“, a `_guard_answer_leak` zato izričito izuzima vrh ljestvice
    (`is_hint_ladder_top`). Deterministički motori su pisali treći nagovještaj
    kao POSTUPAK BEZ rezultata, pa je isti učenik na istoj lekciji dobijao
    različitu pomoć zavisno od rute. Ovo je harmonizacija te jedne razlike.

    Rezultat se dopisuje iz `answer_reply` — OBJAVLJENOG, sanitizovanog teksta
    tačne opcije (isti izvor koji `_deterministic_choice_reply` već koristi pri
    otkrivanju), nikad iz sirovog `display_answer`: sirov zapis je za porodice
    s vlastitim dolarima pravio pokvaren MathJax (`$$13$ i $16$$`) i nebezbjedne
    jedinice (`$360 min$`). Nijedan motor se ne dira, pa identitet zadatka,
    pohranjeni nagovještaji i rješenje ostaju bajt za bajt isti.

    Kad pohranjeni nagovještaj rezultat VEĆ otkriva, ništa se ne dopisuje —
    dokazuje ISTI `feedback.leaks_answer` detektor koji čuva nivoe 1 i 2, nikad
    novi."""
    answer_reply = (annex.get("answer_reply") or "").strip()
    if not answer_reply or _reveals_committed_answer(session, hint_text):
        return hint_text
    return f"{hint_text} Konačan rezultat je {answer_reply}."


def _run_deterministic_help_turn(store, session, turn, context, request_id,
                                 ui_action, annex):
    """DETERMINISTIC_HINT / DETERMINISTIC_SOLUTION: pohranjena ljestvica
    nagovještaja i potpuno rješenje aktivnog paketa — nula poziva modela."""
    timer = _TurnTimer()
    client_turn_id = turn.get("client_turn_id") or ""
    if (client_turn_id and client_turn_id == session.get("last_help_turn_id")
            and session.get("last_help_response") is not None):
        # Idempotentan retry: identičan odgovor, bez mutacije i bez pomjeranja
        # ljestvice nagovještaja.
        return copy.deepcopy(session["last_help_response"])

    reveal = False
    if ui_action == "hint_request":
        hints = list(annex.get("hints") or ())
        if not hints:
            return _error_response(session["current_task"])
        index = min(session["hint_level"], len(hints) - 1)
        answer = hints[index]
        # Vrh ljestvice se prepoznaje ISTIM uslovom kao na model-putu
        # (`is_hint_ladder_top`): `hint_level` je broj RANIJE datih nagovještaja.
        if session["hint_level"] >= config.MAX_HINT_LEVEL - 1:
            answer = _deterministic_top_hint(session, annex, answer)
        session["hint_level"] = min(session["hint_level"] + 1, config.MAX_HINT_LEVEL)
        session["current_task_had_hint"] = True
        route, intent = "deterministic_hint", "hint_request"
    else:
        answer = annex.get("solution") or ""
        if not answer:
            return _error_response(session["current_task"])
        session["last_result"] = "full_solution"
        if session["correct_option_id"]:
            session["task_completed"] = True
            reveal = True
        route, intent = "deterministic_solution", "full_solution_request"

    session["recent_turns"].append(
        {"student": turn["student_message"][:300], "tutor": answer[:400]}
    )
    response = {
        "status": "ready",
        "answer": answer,
        "answer_verdict": None,
        "last_tutor_task": session["current_task"] or "",
        "next_state": _next_state(session),
        "session_mode": "practice",
        "effective_topic": context.topic_id,
    }
    if reveal:
        response["revealed_correct_option_id"] = session["correct_option_id"]
    if client_turn_id:
        session["last_help_turn_id"] = client_turn_id
        session["last_help_response"] = copy.deepcopy(response)
    with timer.stage("commit"):
        store.save(session)
    identity = session.get("current_task_identity") or ""
    _log_turn_diagnostics(
        request_id, context, turn, session, intent=intent, calls=0,
        published=False, task_preserved=True, state_mutated=True,
        previous_identity=identity, final_identity=identity,
        route=route, timer=timer)
    return response


def _run_deterministic_task_turn(store, session, turn, context, request_id,
                                 intent, generator):
    """DETERMINISTIC_PACKAGE ruta: server generiše, dokaže i objavi paket —
    nula poziva modela. Objava, validatori i mutacija sesije su DOSLOVNO isti
    kod kao za model-paket (`validate_task_package` + `_publish_task`)."""
    timer = _TurnTimer()
    active_task_before = session["current_task"]
    identity_before = session.get("current_task_identity") or ""
    levels_enabled = _difficulty_levels_enabled()
    generator_level = _target_level_for(session, intent, turn["student_message"])
    target_level = generator_level if levels_enabled else None
    previous_level = session.get("difficulty_level") if levels_enabled else None

    package, task_text, safe_hints = None, "", ()
    with timer.stage("generate"):
        # Ograničeni pokušaji: kanonski/potpisni duplikat bira NOVE operande,
        # nikad model. Svaki kandidat prolazi ISTU objavnu validaciju.
        for _attempt in range(12):
            try:
                candidate = _generate_with_policy(
                    generator, policy=getattr(context, "practice_policy", None),
                    lesson_id=context.topic_id, lesson_title=context.title,
                    parameters=context.semantic_contract.parameters,
                    level=generator_level)
            except DeterministicGenerationError as error:
                _log_rejection(request_id, context, "deterministic_generation",
                               error, intent)
                break
            # POLITIKA PP-1 nad CIJELIM kandidatom (uklj. hintove i metodsku
            # provenijenciju) — prije objave, unutar ograničene petlje: prekršaj
            # bira NOVE operande, nikad ne stiže do učenika. Objava ispod ista
            # polja provjerava JOŠ jednom (odbrana u dubini, isti kod za model).
            policy_codes = practice_policy.package_policy_failures(
                getattr(context, "practice_policy", None),
                candidate.question, candidate.option_texts, candidate.hints,
                candidate.solution, getattr(candidate, "method_id", ""))
            if policy_codes:
                _log_rejection(request_id, context, "deterministic_policy",
                               ",".join(policy_codes), intent)
                continue
            payload = candidate.task_payload()
            final = TutorDraft(intent=intent, reply="",
                               lesson_focus="deterministic generator",
                               new_task=payload)
            try:
                with timer.stage("publish"):
                    validate_task_package(payload, context, target_level)
                    # Pomoćni tekstovi prolaze kapiju PRIJE objave: nagovještaj
                    # koji učeniku ne smije biti prikazan obara KANDIDATA, a ne
                    # tek kasniji help-turn (tada bi zadatak već bio objavljen,
                    # a pomoć nedostupna).
                    safe_hints = _deterministic_safe_help_texts(
                        candidate, context, request_id, intent)
                    task_text = _publish_task(session, context, final,
                                              request_id, target_level)
            except UnifiedOutputError:
                continue
            package = candidate
            break

    if package is None:
        _log_turn_diagnostics(
            request_id, context, turn, session, intent=intent, calls=0,
            published=False, task_preserved=bool(active_task_before),
            state_mutated=False, previous_identity=identity_before,
            final_identity=identity_before,
            rejection_code="deterministic_generation_failed",
            route="deterministic_package", timer=timer)
        return _error_response(active_task_before)

    # Deterministički dodatak sesije: ljestvica nagovještaja i potpuno rješenje
    # su SERVERSKE činjenice objavljenog paketa — kasniji hint/rješenje/klik za
    # OVAJ identitet ne treba nijedan poziv modela.
    #
    # PHASE A: svaki OVDJE pohranjen učeniku vidljiv tekst je već prošao
    # serversku kapiju. Nagovještaji nose `safe_hints` (sanitizovani iznad,
    # prije objave), a rješenje i tačan odgovor se čitaju iz sesije — to su
    # DOSLOVNO tekstovi koje je `_publish_task` prihvatio, pa je nemoguće da
    # objava prihvati jednu kopiju, a help-turn kasnije pošalje drugu (sirovu).
    # `display_answer`/`accepted_answers` ostaju kanonske VRIJEDNOSTI paketa i
    # nikad se ne šalju učeniku.
    session["deterministic_task"] = {
        "generator_version": package.generator_version,
        "family_id": package.family_id,
        "operation": package.operation,
        "hints": list(safe_hints),
        "solution": session["solution_summary"],
        "display_answer": package.display_answer,
        "answer_reply": session["expected_answer_summary"],
        "accepted_answers": list(package.accepted_answers),
        "task_identity": session["current_task_identity"],
    }

    intro_intent = intent if target_level is not None else "generate_task"
    intro = _intro_for(intro_intent, previous_level, target_level)
    answer = intro + "\n\nZadatak: " + task_text
    session["recent_turns"].append(
        {"student": turn["student_message"][:300], "tutor": answer[:400]}
    )

    response = {
        "status": "ready",
        "answer": answer,
        "answer_verdict": None,
        "last_tutor_task": session["current_task"] or "",
        "next_state": _next_state(session),
        "session_mode": "practice",
        "effective_topic": context.topic_id,
    }
    with timer.stage("commit"):
        store.save(session)
    logger.info(
        "tutor_turn request_id=%s topic=%s intent=%s calls=0",
        request_id, context.topic_id, intent,
    )
    _log_turn_diagnostics(
        request_id, context, turn, session, intent=intent, calls=0,
        published=True, task_preserved=True, state_mutated=True,
        previous_identity=identity_before,
        final_identity=session.get("current_task_identity") or "",
        route="deterministic_package", timer=timer)
    return response


# ---------------------------------------------------------------------------
# ULAZNA TAČKA
# ---------------------------------------------------------------------------

def run_turn(store, llm, turn):
    """Jedan Practice turn za BILO KOJU od 534 lekcije."""
    request_id = uuid.uuid4().hex[:12]

    context = lesson_context_module.build(turn["grade"], turn["selected_topic"])
    if context is None:
        # Nepouzdan kurikularni kontekst → 0 poziva, bez stanja.
        logger.warning("tutor_turn request_id=%s invalid_curriculum_context", request_id)
        return _error_response()

    session = store.load(
        session_id=turn["session_id"],
        grade=turn["grade"],
        lesson_id=context.topic_id,
        lesson_title=context.title,
        oblast_id=context.oblast_id,
        oblast=context.oblast,
        mode="practice",
    )

    if turn.get("interaction_type") == "choice_answer":
        return _run_choice_turn(store, llm, session, turn, context, request_id)
    return _run_text_turn(store, llm, session, turn, context, request_id)


def _resolve_choice(session, turn):
    """Deterministička obrada klika PRIJE ijednog poziva.

    Vraća (trusted_verdict, cached_response, blocked). Tačnost klika je
    SERVERSKA činjenica — model je saopštava, nikad ne utvrđuje."""
    if not session["current_options"] or not session["correct_option_id"]:
        return None, None, True
    client_turn_id = turn.get("client_turn_id") or ""
    if (client_turn_id and client_turn_id == session["last_choice_turn_id"]
            and session["last_choice_response"] is not None):
        # Idempotentan retry: identičan odgovor, bez poziva i bez mutacije.
        return None, copy.deepcopy(session["last_choice_response"]), False
    if session["task_completed"]:
        return None, None, True

    selected_id = turn.get("selected_option_id") or ""
    options_by_id = {option["id"]: option for option in session["current_options"]}
    if selected_id not in options_by_id:
        return None, None, True

    return {
        "selected_text": options_by_id[selected_id]["text"],
        "selected_id": selected_id,
        "is_correct": selected_id == session["correct_option_id"],
        "wrong_attempts": len(session["wrong_option_ids"]),
    }, None, False


def _run_choice_turn(store, llm, session, turn, context, request_id):
    active_task_before = session["current_task"]
    timer = _TurnTimer()
    verdict, cached, blocked = _resolve_choice(session, turn)
    if cached is not None:
        return cached
    if blocked:
        return _error_response(active_task_before)

    is_correct = verdict["is_correct"]
    wrong_before = verdict["wrong_attempts"]

    # Napredovanje se računa na KOPIJI; commit ide tek na kraju.
    if is_correct:
        session["correct_streak"] = (
            session["correct_streak"] + 1 if not session.get("current_task_had_hint") else 0
        )
        session["task_completed"] = True
        session["last_result"] = "correct"
        session["retry_required"] = False
        family = session["current_family"]
        if family and family not in session["correctly_completed_families"]:
            session["correctly_completed_families"].append(family)
    else:
        session["correct_streak"] = 0
        session["wrong_option_ids"].append(verdict["selected_id"])
        session["last_result"] = "incorrect"
        session["retry_required"] = True
        if wrong_before >= 1:
            session["task_completed"] = True

    # Faza 4H: DETERMINISTIC_GRADING — verdikt je oduvijek serverska činjenica;
    # za server-generisan zadatak i povratna poruka dolazi iz pohranjenih
    # činjenica paketa, pa modela nema.
    annex = _deterministic_active_annex(session)
    if annex is not None:
        reply = _deterministic_choice_reply(annex, is_correct, wrong_before)
        calls = 0
        route = "deterministic_grading"
    else:
        route = "model_tutor_reviewer"
        final, calls = _two_call(
            llm, context, session, turn["student_message"], request_id, verdict,
            timer=timer,
        )
        if final is None:
            return _error_response(active_task_before)

        try:
            reply = _safe_text(final.reply, "reply")
            _reject_if_inconsistent(reply, "reply")
            _reject_if_geometry_invalid(reply, context, "reply")
        except UnifiedOutputError as error:
            _log_rejection(request_id, context, "choice_reply", error, final.intent)
            return _error_response(active_task_before)

        # PRVI pogrešan klik ne smije otkriti odgovor — isto pravilo kao za
        # slobodan tekst. Drugi pogrešan klik ga otkriva NAMJERNO (vidi niže),
        # pa je gate aktivan samo dok otkrivanje još nije zasluženo.
        if not is_correct and wrong_before < 1:
            reply = _guard_answer_leak(session, request_id, context,
                                       "answer_attempt", reply,
                                       student_message=turn["student_message"])

    session["recent_turns"].append({
        "student": f"[izabrao opciju: {verdict['selected_text']}]"[:300],
        "tutor": reply[:400],
    })

    response = {
        "status": "ready",
        "answer": reply,
        "answer_verdict": "correct" if is_correct else "incorrect",
        "last_tutor_task": session["current_task"] or "",
        "next_state": _next_state(session),
        "session_mode": "practice",
        "effective_topic": context.topic_id,
    }
    # Tačna opcija se otkriva SAMO na drugi pogrešan klik.
    if not is_correct and wrong_before >= 1:
        response["revealed_correct_option_id"] = session["correct_option_id"]

    client_turn_id = turn.get("client_turn_id") or ""
    if client_turn_id:
        session["last_choice_turn_id"] = client_turn_id
        session["last_choice_response"] = copy.deepcopy(response)

    store.save(session)   # JEDINA commit tačka
    logger.info(
        "tutor_choice request_id=%s topic=%s family=%s route=%s is_correct=%s "
        "calls=%s total_ms=%s stage_ms=%s",
        request_id, context.topic_id, _context_family(context),
        route, is_correct, calls, timer.total_ms(),
        timer.describe(),
    )
    return response


def _log_turn_diagnostics(request_id, context, turn, session, *, intent, calls,
                          published, task_preserved, state_mutated,
                          previous_identity, final_identity, rejection_code="",
                          reviewer_decision="", previous_level=None,
                          target_level=None, route="model_tutor_reviewer",
                          timer=None):
    """JEDAN strukturisan red po Practice turnu — zatvoren skup SIGURNIH polja.

    ZAŠTO POSTOJI: dijagnostika je do sada bila razasuta po nekoliko redova, pa
    se za jedan turn nije moglo iz loga pročitati šta je učenik tražio, koji je
    zadatak bio aktivan, šta je objavljeno i da li je stanje mutirano. Ovdje su
    sva ta polja na jednom mjestu.

    NIKAD ne ulazi: API ključ, tajna, prompt, sirov izlaz modela, tekst zadatka,
    tekst opcija, učenikova poruka ni historija. Identiteti zadatka su hashevi
    (izvedeni iz onoga što učenik ionako vidi), a session_id se skraćuje."""
    session_id = (turn.get("session_id") or "")
    # Faza 4H: ruta, porodica i per-stage vrijeme — SAMO kodovi i milisekunde,
    # nikad sadržaj. `route` je serverska odluka o strategiji izvršenja
    # (deterministic_* ili model_tutor_reviewer), ne modelova.
    logger.info(
        "tutor_turn_diagnostics request_id=%s session=%s topic=%s family=%s "
        "route=%s client_turn_id=%s "
        "intent=%s interaction_phase=%s ui_intent=%s calls=%s published=%s "
        "task_preserved=%s state_mutated=%s previous_identity=%s final_identity=%s "
        "previous_level=%s target_level=%s committed_level=%s reviewer_decision=%s "
        "rejection_code=%s total_ms=%s stage_ms=%s",
        request_id, session_id[:8] or "-", context.topic_id,
        _context_family(context), route,
        _clip(turn.get("client_turn_id") or "-", 64),
        intent or "-", _clip(turn.get("interaction_phase") or "-", 40),
        _clip(turn.get("intent") or "-", 40), calls, published,
        task_preserved, state_mutated,
        (previous_identity or "-")[:12], (final_identity or "-")[:12],
        previous_level if previous_level is not None else "-",
        target_level if target_level is not None else "-",
        session.get("difficulty_level", "-"), reviewer_decision or "-",
        rejection_code or "-",
        timer.total_ms() if timer is not None else "-",
        timer.describe() if timer is not None else "-",
    )


def _run_text_turn(store, llm, session, turn, context, request_id):
    active_task_before = session["current_task"]
    had_active_task = bool(active_task_before)
    identity_before = session.get("current_task_identity") or ""
    ui_action = _explicit_ui_action(turn, session)

    # Faza 4H: dugme pomoći nad aktivnim DETERMINISTIČKIM zadatkom — ljestvica
    # nagovještaja i rješenje su pohranjene serverske činjenice paketa.
    if ui_action:
        annex = _deterministic_active_annex(session)
        if annex is not None:
            return _run_deterministic_help_turn(
                store, session, turn, context, request_id, ui_action, annex)

    # Faza 4H: DETERMINISTIC_PACKAGE ruta — samo server-vlasničke činjenice
    # (lekcija s potpunim generatorom + UI polje ili zatvoren skup poruka).
    generator = _deterministic_generator_for(context)
    if generator is not None and not ui_action:
        deterministic_intent = _deterministic_task_intent(turn, session)
        if deterministic_intent:
            return _run_deterministic_task_turn(
                store, session, turn, context, request_id, deterministic_intent,
                generator)

    timer = _TurnTimer()

    final, calls = _two_call(
        llm, context, session, turn["student_message"], request_id, None, ui_action,
        timer=timer,
    )
    if final is None:
        # Odbijeno prije objave (nacrt, recenzent ili invarijanta nad konačnim
        # paketom). Sesija je lokalna kopija i nije commitovana.
        _log_turn_diagnostics(
            request_id, context, turn, session, intent="", calls=calls,
            published=False, task_preserved=bool(active_task_before),
            state_mutated=False, previous_identity=identity_before,
            final_identity=identity_before,
            rejection_code="rejected_before_publication", timer=timer)
        return _error_response(active_task_before)

    try:
        with timer.stage("publish"):
            reply = _safe_text(final.reply, "reply")
            _reject_if_inconsistent(reply, "reply")
            _reject_if_geometry_invalid(reply, context, "reply")

        task_text = active_task_before
        if final.intent in TASK_INTENTS:
            # ODBRANA U DUBINI: `_two_call` isti nacrt već odbija prije
            # recenzenta, ali objava je posljednja tačka prije mutacije sesije
            # i ne smije ovisiti o tome da je raniji sloj odradio svoje.
            if ui_action:
                raise UnifiedOutputError(
                    f"ui_action_forbids_new_task: {ui_action} vs {final.intent}")
            with timer.stage("publish"):
                target_level = (_target_level_for(session, final.intent,
                                                  turn["student_message"])
                                if _difficulty_levels_enabled() else None)
                canonical_task = validate_task_package(final.new_task, context,
                                                       target_level)
                if canonical_task is not final.new_task:
                    final = final.model_copy(update={"new_task": canonical_task})
                previous_level = (session.get("difficulty_level")
                                  if target_level is not None else None)
                task_text = _publish_task(session, context, final, request_id,
                                          target_level)
            intro_intent = final.intent if target_level is not None else "generate_task"
            intro = _intro_for(intro_intent, previous_level, target_level)
            answer = intro + "\n\nZadatak: " + task_text
        else:
            answer = _compose_visible_help(final, reply, context)
            # Treći hint po prompt ljestvici SMIJE pokazati cijeli postupak i
            # rezultat; `hint_level` je ovdje još broj RANIJE datih hintova.
            answer = _guard_answer_leak(
                session, request_id, context, final.intent, answer,
                student_message=turn["student_message"],
                is_hint_ladder_top=(final.intent == "hint_request"
                                    and session["hint_level"] >= config.MAX_HINT_LEVEL - 1),
            )
            if final.intent == "hint_request":
                session["hint_level"] = min(
                    session["hint_level"] + 1, config.MAX_HINT_LEVEL
                )
                session["current_task_had_hint"] = True
            elif final.intent == "full_solution_request":
                session["last_result"] = "full_solution"
    except UnifiedOutputError as error:
        _log_rejection(request_id, context, "publication", error, final.intent)
        _log_turn_diagnostics(
            request_id, context, turn, session, intent=final.intent, calls=calls,
            published=False, task_preserved=bool(active_task_before),
            state_mutated=False, previous_identity=identity_before,
            final_identity=identity_before, rejection_code=str(error)[:60],
            timer=timer)
        return _error_response(active_task_before)

    session["recent_turns"].append(
        {"student": turn["student_message"][:300], "tutor": answer[:400]}
    )

    response = {
        "status": "ready",
        "answer": answer,
        "answer_verdict": None,      # tekstualni turn se nikad ne ocjenjuje
        "last_tutor_task": session["current_task"] or "",
        "next_state": _next_state(session),
        "session_mode": "practice",
        "effective_topic": context.topic_id,
    }
    # „Uradi ga ti“: server deterministički završava zadatak i otkriva opciju.
    if (final.intent == "full_solution_request" and had_active_task
            and session["correct_option_id"]):
        session["task_completed"] = True
        response["revealed_correct_option_id"] = session["correct_option_id"]

    with timer.stage("commit"):
        store.save(session)   # JEDINA commit tačka
    logger.info(
        "tutor_turn request_id=%s topic=%s intent=%s calls=%s",
        request_id, context.topic_id, final.intent, calls,
    )
    _log_turn_diagnostics(
        request_id, context, turn, session, intent=final.intent, calls=calls,
        published=final.intent in TASK_INTENTS, task_preserved=True,
        state_mutated=True, previous_identity=identity_before,
        final_identity=session.get("current_task_identity") or "", timer=timer)
    return response


def _compose_visible_help(final, reply, context):
    """Sastavi ono što učenik STVARNO vidi za hint/rješenje.

    ZAŠTO POSTOJI (ručni test, 2026-08-03): na „Ne znam“ je model najavu stavio
    u `reply` („Evo ti uputa koja će ti pomoći.“), a KORISTAN sadržaj u zasebno
    polje `hint`. Frontend prikazuje isključivo `answer`, pa je učenik dobio
    obećanje hinta bez hinta. Isti rizik nosi `worked_solution`.

    Zato se korisno polje PRIPAJA vidljivom tekstu kad ga tekst već ne sadrži.
    Polja i dalje prolaze istu sanitizaciju kao svaki vidljivi tekst."""
    extra = None
    if final.intent == "hint_request":
        extra = (final.hint or "").strip()
    elif final.intent == "full_solution_request":
        extra = (final.worked_solution or "").strip()
    if not extra:
        return reply

    safe_extra = _safe_text(extra, final.intent)
    _reject_if_inconsistent(safe_extra, final.intent)
    _reject_if_geometry_invalid(safe_extra, context, final.intent)

    # Model je ponekad već ugradio hint u reply — tada se ne duplira.
    if safe_extra and safe_extra in reply:
        return reply
    return (reply.rstrip() + "\n\n" + safe_extra).strip() if reply.strip() else safe_extra


# ---------------------------------------------------------------------------
# ZAŠTITA OD CURENJA ODGOVORA (živi nalaz B53, dvije kampanje)
# ---------------------------------------------------------------------------
# Na pogrešan slobodan pokušaj („Mislim da je rješenje x=3.“) tutor je vratio
# cijeli postupak i doslovno committed rješenje $x=7$ — koje je istovremeno bilo
# i označena opcija na ekranu. Zadatak je ostao otvoren, pa je učeniku preostalo
# samo da klikne ponuđeni odgovor. Ponovilo se i na drugi pokušaj.
#
# Legacy Practice put ovo hvata (`feedback.shape_first_wrong_feedback`), a
# univerzalni put je pri pivotu ostao BEZ ijednog determinističkog anti-leak
# sloja. Ovdje se koristi ISTI `feedback.leaks_answer`, ne novi detektor.
#
# Gate je namjerno uzak, jer treći korak istog scenarija (koristan hint bez
# rezultata) NIJE procurio i mora ostati netaknut:
#   • primjenjuje se samo uz AKTIVAN i NEZAVRŠEN zadatak,
#   • nikad na `full_solution_request` (tamo je otkrivanje ispravno),
#   • nikad na treći hint (prompt ljestvica ga izričito traži),
#   • nikad kad ista vrijednost već stoji u tekstu zadatka — tada je to
#     prepričavanje, ne curenje, i ne može se dokazati.
# Na pozitivan nalaz NEMA drugog poziva modela: opasan tekst se zamjenjuje
# sigurnim sljedećim korakom, a serverski verdikt i stanje ostaju netaknuti.
_LEAK_GUARDED_INTENTS = frozenset({
    "answer_attempt", "hint_request", "explanation_request", "clarification",
})

LEAK_BLOCKED_REPLY = "Hajde da to riješimo korak po korak.\n\n" + feedback.GENERIC_HINT


def _committed_answer(session):
    """Tačna opcija i očekivani odgovor — serverska istina, nikad modelova."""
    correct_id = session.get("correct_option_id") or ""
    marked = next((option.get("text", "") for option in (session.get("current_options") or [])
                   if isinstance(option, dict) and option.get("id") == correct_id), "")
    return marked, session.get("expected_answer_summary") or ""


def _reveals_committed_answer(session, text, student_message=""):
    """True samo kad se curenje MOŽE dokazati nad serverski committed odgovorom.

    Dvije eksplicitne iznimke — u obje vrijednost NIJE došla od tutora:
      • već stoji u tekstu samog zadatka (prepričavanje, ne otkrivanje),
      • učenik ju je sam napisao (uključujući TAČAN pokušaj, koji se smije
        potvrditi)."""
    marked, expected = _committed_answer(session)
    if not marked and not expected:
        return False
    task = session.get("current_task") or ""
    # `task` ide detektoru da broj koji već stoji u zadatku ne bi bio dokaz.
    if not feedback.leaks_answer(text, marked, expected, task_text=task):
        return False
    if feedback.leaks_answer(task, marked, expected):
        return False
    return not feedback.leaks_answer(student_message or "", marked, expected, task_text=task)


def _guard_answer_leak(session, request_id, context, intent, answer, *,
                       student_message="", is_hint_ladder_top=False):
    """Vrati bezbjedan tekst umjesto onog koji otkriva committed odgovor."""
    if intent not in _LEAK_GUARDED_INTENTS or is_hint_ladder_top:
        return answer
    if not session.get("current_task") or session.get("task_completed"):
        return answer
    if not _reveals_committed_answer(session, answer, student_message):
        return answer
    logger.warning(
        "tutor_answer_leak_blocked request_id=%s topic=%s intent=%s hint_level=%s",
        request_id, context.topic_id, intent, session.get("hint_level"),
    )
    return LEAK_BLOCKED_REPLY


def _publish_task(session, context, final, request_id, target_level=None):
    """Provjeri i primijeni nov zadatak. Baca UnifiedOutputError (fail closed)."""
    task = final.new_task
    task_text, option_texts, expected, solution = _validate_task_server_side(
        task, context, previous_signature=session.get("current_task_identity") or "")

    current_options, correct_option_id = _shuffle_options(
        option_texts, task.correct_option_index
    )
    signature_record = _structured_signature_record(task, context)
    if _is_duplicate_structured_signature(signature_record, session["recent_task_signatures"]):
        raise UnifiedOutputError("duplicate structured task signature")
    session["current_task"] = task_text
    # Faza 4H: model-paket NIKAD ne nasljeđuje deterministički dodatak starog
    # zadatka — hint/rješenje/ocjena tuđeg identiteta bi bila pogrešna
    # matematika. Deterministička ruta ga ponovo postavlja POSLIJE objave.
    session["deterministic_task"] = None
    # Kanonski identitet ide u sesiju I u browser: UI stanje (crveno/zeleno,
    # otkriven odgovor, hint) mora biti vezano za IDENTITET zadatka, ne za
    # njegov tekst — vidi templates/index.html.
    session["current_task_identity"] = task_identity.canonical_signature(
        task_text, option_texts)
    session["expected_answer_summary"] = expected
    session["solution_summary"] = solution
    if target_level is not None:
        session["difficulty"] = difficulty_level.LEVEL_TO_LABEL[target_level]
        session["difficulty_level"] = target_level
    session["hint_level"] = 0
    session["recent_tasks"].append(task_text)
    session["current_options"] = current_options
    session["correct_option_id"] = correct_option_id
    session["wrong_option_ids"] = []
    session["task_completed"] = False
    session["current_task_had_hint"] = False
    session["last_choice_turn_id"] = ""
    session["last_choice_response"] = None

    # Napredovanje: oblik zadatka za ovu lekciju dolazi iz konteksta lekcije.
    family = context.primary_family
    if family:
        session["current_family"] = family
        recent = session["recently_used_families"]
        if not recent or recent[-1] != family:
            recent.append(family)
    session["current_task_signature"] = signature_record
    session["current_task_difficulty_evidence"] = task.difficulty_evidence.model_dump()
    session["recent_task_signatures"].append(signature_record)
    _log_difficulty(request_id, context, final)
    return task_text


def _log_sdk_entry(request_id, context, stage, call_index, result):
    """JEDAN strukturisan red PO MODELSKOM POZIVU.

    Postoji zbog računovodstva dvopozivnog puta: uspješan turn mora ostaviti
    TAČNO DVA ovakva reda (stage=tutor call=1, stage=reviewer call=2), pa se
    broj poziva i trošak mogu prebrojati iz loga bez pogađanja. Loguje se samo
    latencija i usage — nikad prompt, izlaz modela ni ijedan sadržaj."""
    logger.info(
        "tutor_sdk_call request_id=%s topic=%s stage=%s call=%s latency_ms=%s usage=%s",
        request_id, context.topic_id, stage, call_index,
        getattr(result, "latency_ms", None), getattr(result, "usage", None),
    )


def _two_call(llm, context, session, student_message, request_id, trusted_verdict,
              ui_action="", timer=None):
    """Tutor → Reviewer. Vraća (final_draft | None, broj_poziva).

    Nijedna grana ne pravi treći poziv. Kad Tutor nacrt ne može ni da se
    isparsira, DRUGI POZIV SE NE DEŠAVA — nema šta da se recenzira.

    `ui_action` je namjera koju je učenik izričito tražio dugmetom nad AKTIVNIM
    zadatkom (vidi `_explicit_ui_action`). Kad postoji, nov zadatak se u ovom
    turnu ne smije izdati — pa se nacrt s takvom namjerom odbija ODMAH, prije
    recenzenta: nema šta recenzirati kad paket ionako ne smije biti objavljen."""
    calls = 0
    timer = timer or _TurnTimer()
    try:
        tutor_result = _call_tutor(
            llm, context, session, student_message, trusted_verdict, ui_action,
            timer=timer,
        )
        calls += 1
        _log_sdk_entry(request_id, context, "tutor", calls, tutor_result)
    except LLMError as error:
        logger.warning(
            "tutor_call request_id=%s topic=%s stage=tutor call=1 %s",
            request_id, context.topic_id, failure_diagnostics_kv(error),
        )
        return None, calls

    with timer.stage("tutor_validate"):
        draft = normalize_for_intent(tutor_result.output)
        has_active_task = bool(session["current_task"])
        try:
            validate_final(draft, has_active_task=has_active_task)
        except UnifiedOutputError as error:
            # Neupotrebljiv nacrt: recenzent se NE poziva (nema validnog
            # predmeta), pa turn staje na jednom pozivu.
            _log_rejection(request_id, context, "tutor_draft", error, draft.intent)
            return None, calls

    # Reviewer sees the canonical server title; raw Tutor output remains on
    # the wrapper for safe offline diagnostics only.
    draft = _canonicalize_draft_lesson_title(draft, context)

    # Only task publication needs the independent generation Reviewer.  A
    # normal answer, hint, explanation, or full-solution turn is complete
    # after Tutor semantically interpreted the free-form student message.
    if draft.intent not in TASK_INTENTS:
        return draft, calls

    # KONTINUITET AKTIVNOG ZADATKA JE SERVERSKA ODLUKA, NE MODELOVA.
    if ui_action:
        _log_rejection(
            request_id, context, "ui_action_forbids_new_task",
            f"ui_action={ui_action} draft_intent={draft.intent}", draft.intent,
        )
        return None, calls

    # ------------------------------------------------------------------
    # PREFLIGHT NAD TUTOROVIM NACRTOM (živi gate 00bbd45)
    # ------------------------------------------------------------------
    # Isti deterministički validatori koje objava ionako pokreće, ali SADA —
    # dok drugi poziv još nije napravljen. Nalaz se NE koristi za odbijanje
    # nacrta (nacrt s nalazom je upravo ono što recenzent treba da popravi),
    # nego ulazi u recenzentov ulaz kao serverska činjenica.
    previous_signature = session.get("current_task_identity") or ""
    # Faza F5G: lekcijski-relativni profil težine — jedno razrješenje po turnu,
    # iz server-vlasničkog konteksta, pa ISTE granice vide preflight,
    # recenzentska invarijanta i objava (validate_task_package ga razrješava
    # sam iz istog konteksta).
    difficulty_profile = difficulty_profiles.resolve_for_context(context)
    with timer.stage("preflight"):
        draft_issues = package_preflight.collect_package_issues(
            draft.new_task, contract=context.semantic_contract,
            previous_signature=previous_signature,
            difficulty_profile=difficulty_profile,
            practice_contract=context.practice_contract,
            practice_policy=getattr(context, "practice_policy", None),
            student_message=student_message)
    if draft_issues:
        logger.info(
            "tutor_draft_preflight request_id=%s topic=%s intent=%s issues=%s",
            request_id, context.topic_id, draft.intent,
            package_preflight.describe_issues(draft_issues),
        )

    # Faza 4H (Workstream L): eksplicitni rok CIJELOG turna. Skoro istekao
    # Tutor poziv ne smije slijepo započeti dug recenzentski poziv — bolje
    # siguran pad odmah nego 90-sekundno čekanje učenika. Podrazumijevani rok
    # (2×AI_TIMEOUT_S) ne mijenja zatečeno ponašanje ni za milisekundu.
    remaining_s = config.practice_turn_deadline_s() - timer.total_ms() / 1000.0
    if remaining_s < config.MIN_STAGE_BUDGET_S:
        _log_rejection(
            request_id, context, "turn_deadline",
            f"remaining={remaining_s:.1f}s before reviewer call", draft.intent)
        return None, calls

    try:
        reviewer_result = _call_reviewer(
            llm, context, session, student_message, draft, trusted_verdict,
            package_preflight.format_for_reviewer(draft_issues), timer=timer,
            timeout_s=min(config.AI_TIMEOUT_S, remaining_s),
        )
        calls += 1
        _log_sdk_entry(request_id, context, "reviewer", calls, reviewer_result)
    except LLMError as error:
        logger.warning(
            "reviewer_call request_id=%s topic=%s stage=reviewer call=2 %s",
            request_id, context.topic_id, failure_diagnostics_kv(error),
        )
        return None, calls

    reviewer = reviewer_result.output
    try:
        with timer.stage("reviewer_validate"):
            validate_reviewer(reviewer, draft, difficulty_profile=difficulty_profile)
    except UnifiedOutputError as error:
        _log_rejection(request_id, context, "reviewer_payload", error, draft.intent)
        return None, calls

    if reviewer.decision == "fail_closed":
        _log_rejection(
            request_id, context, "reviewer_fail_closed",
            reviewer.fail_reason_code, draft.intent,
        )
        return None, calls

    # Faza 4H (Workstream J): na `approve` se objavljuje UPRAVO odobreni nacrt.
    # Recenzent na odobrenju više ne vraća eho paketa (medijalno ~1400 izlaznih
    # tokena ≈ 15+ sekundi generisanja); eventualni poslani `final` se IGNORIŠE
    # — recenzent nacrt na `approve` ne može tiho izmijeniti.
    if reviewer.decision == "approve":
        if reviewer.final is not None:
            logger.info(
                "reviewer_approve_echo_ignored request_id=%s topic=%s",
                request_id, context.topic_id,
            )
        basis = draft
    else:
        basis = reviewer.final

    final = normalize_for_intent(basis)
    if final.new_task is not None:
        # The first-call wrapper retains the Tutor's self-description. Only
        # the independently returned Reviewer evidence becomes authoritative.
        final = final.model_copy(update={
            "new_task": final.new_task.model_copy(update={
                "difficulty_evidence": reviewer.reviewed_difficulty_evidence,
            }),
        })
    try:
        validate_final(final, has_active_task=has_active_task)
    except UnifiedOutputError as error:
        _log_rejection(request_id, context, "reviewer_final", error, final.intent)
        return None, calls

    # Defense in depth for a Reviewer-provided final package title.
    final = _canonicalize_draft_lesson_title(final, context)

    # ------------------------------------------------------------------
    # INVARIJANTA NAD RECENZENTOVIM KONAČNIM PAKETOM (živi gate 00bbd45)
    # ------------------------------------------------------------------
    # Isti skup provjera, sad nad onim što bi se STVARNO objavilo. Odbija se
    # PRIJE ijedne mutacije sesije, i to sa specifičnim kodom — da se u gateu
    # razlikuje od obične završne validacije. Provjera u objavi OSTAJE
    # (odbrana u dubini): ovo je raniji, precizniji sloj, ne zamjena.
    if final.new_task is not None:
        final_issues = package_preflight.collect_package_issues(
            final.new_task, contract=context.semantic_contract,
            previous_signature=previous_signature,
            difficulty_profile=difficulty_profile,
            practice_contract=context.practice_contract,
            student_message=student_message)
        if final_issues:
            # `unchanged=True` znači: recenzent je vidio nalaz i vratio paket s
            # POTPUNO ISTIM nalazima — dakle nije ni pokušao ispravku.
            unchanged = bool(draft_issues) and (
                package_preflight.describe_issues(final_issues)
                == package_preflight.describe_issues(draft_issues))
            _log_rejection(
                request_id, context, "reviewer_final_mcq",
                f"{REVIEWER_FINAL_INTEGRITY_CODE}: decision={reviewer.decision} "
                f"in_tutor_preflight={bool(draft_issues)} unchanged={unchanged} "
                f"{package_preflight.describe_issues(final_issues)}",
                final.intent,
            )
            return None, calls

    # Netačne provjere koje po matrici autoriteta NE obaraju turn i dalje se
    # LOGUJU — signal se ne smije izgubiti samo zato što ne blokira.
    diagnostic = reviewer_authority.diagnostic_failed_checks(reviewer.checks)
    if diagnostic:
        logger.info(
            "reviewer_non_blocking_checks request_id=%s topic=%s decision=%s checks=%s",
            request_id, context.topic_id, reviewer.decision, ",".join(diagnostic),
        )
    if reviewer.decision == "correct":
        logger.info(
            "tutor_corrected request_id=%s topic=%s intent=%s",
            request_id, context.topic_id, final.intent,
        )
    return final, calls
