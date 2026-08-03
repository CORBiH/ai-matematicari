"""Orkestracija JEDNOG Practice turna: stanje → prompt → jedan AI poziv →
validacija → sigurna primjena rezultata → response u frontend ugovoru.

Dvije vrste interakcije (turn["interaction_type"]):
- "choice_answer": učenik je kliknuo jednu od 4 ponuđene opcije aktivnog
  zadatka. Server DETERMINISTIČKI utvrđuje je li izbor tačan (poređenje sa
  session["correct_option_id"]) — model se u istom pozivu koristi SAMO da
  napiše feedback/hint/objašnjenje dosljedan tom već utvrđenom verdiktu.
- bilo šta drugo ("student_question" ili prazno): tekstualna poruka. NIKAD se
  ne tretira kao pokušaj odgovora — 'evaluation' koji model eventualno vrati
  se ignoriše, answer_verdict u odgovoru je uvijek None, correct_streak se ne
  dira. Grading ide isključivo kroz choice_answer granu.

Pravila primjene (server, ne model):
- aktivni zadatak (tekst + 4 opcije) mijenja se ISKLJUČIVO iz new_task
  (bootstrap / "novi zadatak" / lakši / teži) — server tada i miješa
  redoslijed opcija (shuffle) tačno jednom i pamti stvarni correct_option_id.
- gave_hint → hint_level + 1 (cap), zadatak ostaje
- choice_answer: tačan klik → correct_streak + 1, zadatak završen; prvi
  pogrešan klik → zadatak ostaje aktivan, tačna opcija se NE otkriva; drugi
  pogrešan klik → zadatak završen i tačna opcija se otkriva
  (revealed_correct_option_id u responseu)
- greška AI poziva ili nevalidan output → NULA promjena stanja, kratka sigurna
  poruka BEZ 'status' i BEZ 'next_state' (frontend tada čuva svoje stanje).
"""
import copy
import logging
import random
import uuid

from matbot import (config, feedback, geometry_rules, geometrycheck,
                    option_equivalence, prompts, systemcheck, task_families)
from matbot.contracts import archetypes as contract_archetypes
from matbot.contracts import pipeline as contract_pipeline
from matbot.contracts import registry as contract_registry
from matbot.llm import LLMError, failure_diagnostics_kv
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.option_equivalence import find_equivalent_option_pairs
from matbot.schema import InvalidOutputError, NewTask, Option, validate_output
from matbot.task_family_validation import (FamilyContractError, question_geometry_policy,
                                           question_numeric_policy, validate_task_family)
from matbot.terminology import normalize_terminology
from matbot.topics import lesson_info

logger = logging.getLogger("matbot.practice")

SAFE_ERROR_MESSAGE = "Nešto je zapelo pri sastavljanju odgovora. Pošalji poruku ponovo za koji trenutak."

# Lekcija čiji ugovor je izričito označen kao `unsupported`: nema sigurnog
# načina da se za nju generiše provjerljiv zadatak. Poruka je JASNA i drukčija
# od privremene greške — i vraća se BEZ ijednog AI poziva. Nikad se tiho ne
# prelazi na legacy niti na zadatak druge lekcije.
PRACTICE_UNAVAILABLE_MESSAGE = (
    "Za ovu lekciju vježba trenutno nije dostupna. Izaberi drugu lekciju iz iste "
    "oblasti ili pređi na „Objasni mi“."
)

_NEW_TASK_INTRO = "Evo zadatka."
_HARDER_TASK_INTRO = "Evo težeg zadatka."
_EASIER_TASK_INTRO = "Evo lakšeg zadatka."
_SAME_FAMILY_RETRY_INTRO = "Evo novog zadatka za istu vještinu."


_LOG_FIELD_LIMIT = 200


def _clip_for_log(value, limit=_LOG_FIELD_LIMIT):
    """Skrati vrijednost za strukturisani log — nikad ne šalji neograničeno
    dug string u log (i nikad se ovo ne šalje u browser)."""
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _new_task_intro(turn, retry_required=False):
    """Vrati kratak server-owned uvod; modelov ``reply`` se ne prikazuje."""
    if retry_required:
        return _SAME_FAMILY_RETRY_INTRO
    difficulty_request = (turn.get("difficulty_request") or "").strip().lower()
    if difficulty_request == "harder":
        return _HARDER_TASK_INTRO
    if difficulty_request == "easier":
        return _EASIER_TASK_INTRO
    return _NEW_TASK_INTRO


def _log_duplicate_options(request_id, topic, family, diagnostics):
    """Strukturisan, pretraživ log red za semantically_duplicate_options
    odbijanja (Defekt 4 dijagnostika) — SAMO sanitizovani interni podaci,
    nikad API ključ/embed token/potpisan auth token, i nikad se ne šalje u
    browser. Svaka vrijednost je dužinski ograničena."""
    options_for_log = [_clip_for_log(o, 120) for o in diagnostics.get("options", [])]
    logger.warning(
        "practice_duplicate_options request_id=%s topic=%s family=%s pairs=%s "
        "equivalence_types=%s correct_option_index=%s question=%s options=%s "
        "expected_answer=%s",
        request_id, topic or "", family or "",
        diagnostics.get("pairs"), diagnostics.get("equivalence_types"),
        diagnostics.get("correct_option_index"),
        _clip_for_log(diagnostics.get("question")),
        options_for_log,
        _clip_for_log(diagnostics.get("expected_answer")),
    )


def _log_system_verification(request_id, topic, family, diagnostics):
    """Strukturisan log za odbijanje po supstitucijskoj provjeri sistema
    (matbot/systemcheck.py). SAMO interni, dužinski ograničeni podaci —
    nikad API ključ, auth/embed token ni neograničen izlaz modela, i NIKAD
    se ne šalje u browser."""
    logger.warning(
        "practice_system_verification request_id=%s topic=%s family=%s issue_codes=%s "
        "valid_option_indices=%s marked_option_index=%s equations=%s pairs=%s "
        "question=%s options=%s",
        request_id, topic or "", family or "",
        diagnostics.get("issue_codes"), diagnostics.get("valid_option_indices"),
        diagnostics.get("marked_option_index"), diagnostics.get("equations"),
        diagnostics.get("pairs"),
        _clip_for_log(diagnostics.get("question")),
        [_clip_for_log(o, 120) for o in diagnostics.get("options", [])],
    )


def _log_equivalent_system_verification(request_id, topic, family, diagnostics):
    """Ograničena interna dijagnostika uskog RREF verifiera."""
    logger.warning(
        "practice_equivalent_system_verification request_id=%s topic=%s family=%s "
        "issue_codes=%s equivalent_option_indices=%s marked_option_index=%s "
        "original_rref=%s option_rrefs=%s",
        request_id, topic or "", family or "",
        diagnostics.get("issue_codes"), diagnostics.get("equivalent_option_indices"),
        diagnostics.get("marked_option_index"), diagnostics.get("original_rref"),
        diagnostics.get("option_rrefs"),
    )


def _log_ordered_pair_verification(request_id, topic, family, diagnostics):
    """Ograničena interna dijagnostika četverostatusnog pair verifiera."""
    logger.warning(
        "practice_ordered_pair_verification request_id=%s topic=%s family=%s "
        "issue_codes=%s computed_status=%s matching_indices=%s marked_index=%s "
        "truth_values=%s mapped_statuses=%s",
        request_id, topic or "", family or "", diagnostics.get("issue_codes"),
        diagnostics.get("computed_pair_status"), diagnostics.get("matching_option_indices"),
        diagnostics.get("marked_option_index"), diagnostics.get("equation_truth_values"),
        diagnostics.get("mapped_option_statuses"),
    )


def _error_response(active_task=""):
    # Namjerno BEZ 'status' i BEZ 'next_state': frontend čita last_tutor_task
    # SAMO kad je status === 'ready' (templates/index.html:1843-1846), pa je ova
    # vrijednost trenutno inertna dok god status izostaje. Ipak šaljemo STVARNI
    # aktivni zadatak (ne prazan string) da odgovor ostane istinit i otporan na
    # buduće izmjene frontend logike koje bi mogle početi čitati ovo polje i bez
    # statusa — prazan string bi tada mogao izgledati kao "zadatak obrisan".
    return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": active_task or ""}


def _next_state(session):
    # Samo sigurni UI podaci. NIKAD expected_answer_summary, correct_option_id
    # ni interna polja (wrong_option_ids, task_completed su server-only).
    state = {
        "v": 1,
        "correct_streak": session["correct_streak"],
        "hint_level": session["hint_level"],
    }
    if session["current_task"]:
        task = {"question": session["current_task"]}
        if session["current_options"]:
            task["options"] = session["current_options"]  # [{"id","text"}] — bez correct
        state["task"] = task
    return state


def _shuffle_options(texts, correct_index):
    """texts: 4 već sanitizovana teksta opcija. Vraća (current_options
    browser-safe [{"id","text"}] u NOVOM redoslijedu, correct_option_id).
    Shuffle se izvodi TAČNO JEDNOM po novom zadatku — rezultat se sprema u
    sesiju i nikad se ne ponavlja za isti zadatak (drugi pokušaj/retry ne
    smije ponovo promiješati opcije)."""
    ids = ["a", "b", "c", "d"]
    pairs = list(enumerate(texts))
    random.shuffle(pairs)
    current_options = []
    correct_option_id = ""
    for slot, (orig_index, text) in enumerate(pairs):
        option_id = ids[slot]
        current_options.append({"id": option_id, "text": text})
        if orig_index == correct_index:
            correct_option_id = option_id
    return current_options, correct_option_id


def _geometry_context(session):
    """(scope, figures) iz CANONICAL (oblast, lesson_title) sesije.

    Nikad iz učenikove poruke i nikad iz metapodataka modela — isti trusted
    izvor koji već bira geometrijske blokove za prompt (matbot/rules.py)."""
    return geometry_rules.route_geometry_topic(session["oblast"], session["lesson_title"])


def _reject_if_geometry_notation_invalid(text, scope, figures, where,
                                          policy=geometrycheck.POLICY_CHECK):
    """Odbij AUTORITATIVAN tekst koji krši projektnu geometrijsku konvenciju
    (matbot/geometrycheck.py). Živi nalaz: „Krug ima prečnik $D=10$“ — račun
    tačan, oznaka zabranjena ($R$ je prečnik, $D$ je prostorna dijagonala).

    Interni kodovi idu SAMO u InvalidOutputError poruku (server log); učenik
    uvijek vidi postojeći SAFE_ERROR_MESSAGE. Bez drugog AI poziva."""
    issues = geometrycheck.find_geometry_issues(text, scope, figures, policy=policy)
    if issues:
        raise InvalidOutputError(f"geometry_notation: {','.join(issues)} [{where}]")


def _reject_if_numerically_inconsistent(text, where):
    """Odbij tekst u kojem je DOKAZANO nedosljedan numerički lanac jednakosti
    (matbot/mathcheck.py). Živi nalaz: „$\\frac{3\\cdot16\\sqrt{3}}{2}=48\\sqrt{3}$“.

    Provjera se poziva TEK NAKON sanitizacije, normalizacije terminologije i
    popravke zalutale zagrade — dakle nad tačno onim tekstom koji bi učenik
    vidio. Bez drugog AI poziva: nedosljednost = InvalidOutputError, koju
    pozivalac već hvata i pretvara u postojeći SAFE_ERROR_MESSAGE."""
    issues = find_numeric_inconsistencies(text)
    if issues:
        raise InvalidOutputError(f"{issues[0]} [{where}]")


def _log_contract_rejection(request_id, diagnostics):
    """Strukturisan log odbijanja po ugovoru lekcije.

    SAMO interni, dužinski ograničeni podaci — nikad API ključ, auth token ni
    neograničen izlaz modela, i NIKAD se ne šalje u browser (učenik vidi
    postojeći SAFE_ERROR_MESSAGE)."""
    logger.warning(
        "practice_contract_rejected request_id=%s topic=%s contract_version=%s skill=%s "
        "archetype=%s stage=%s code=%s engaged=%s details=%s",
        request_id, diagnostics.get("topic"), diagnostics.get("contract_version"),
        diagnostics.get("skill"), diagnostics.get("archetype"), diagnostics.get("stage"),
        diagnostics.get("code"), diagnostics.get("engaged"),
        _clip_for_log(diagnostics.get("details"), 400),
    )


def _task_from_skeleton(skeleton):
    """Serverski kostur (matbot/contracts/generator.py) → NewTask za
    _apply_new_task. Jedina tačka na kojoj kostur postaje objavljiv zadatak —
    model ovdje nema nikakav ulaz."""
    return NewTask(
        text=skeleton.question_text,
        expected_answer=skeleton.expected_answer,
        difficulty=skeleton.difficulty_label,
        options=[Option(text=text) for text in skeleton.option_texts],
        correct_option_index=skeleton.correct_index,
    )


def _active_task_texts(session):
    """Server-owned tekstovi aktivnog zadatka — izvor dozvoljenih vrijednosti
    za kapiju vjernosti proze (contract_pipeline.verify_prose_fidelity)."""
    return [
        session["current_task"] or "",
        session["expected_answer_summary"] or "",
        *[option["text"] for option in session["current_options"] or []],
    ]


def _reject_if_prose_invents_mathematics(text, session, contract, where):
    """Odbij vidljiv tekst koji uvodi broj kojeg SERVERSKI zadatak ne objašnjava.

    Živi nalaz (probni turn nad Fazom 1): kad učenik usred zadatka pita „kako da
    riješim ovo?“, model je vratio „Rezultat je $\\frac{47}{99}$“ — broj koji nije
    ni tačan odgovor, ni ijedna opcija, ni bilo šta izvedivo iz zadatka.
    mathcheck to NE hvata (nema lanca jednakosti koji bi bio nedosljedan), a na
    motoru ugovora matematiku posjeduje server — pa model nema pravo uvesti
    vlastiti broj.

    Primjenjuje se SAMO uz aktivan zadatak s uključenim ugovorom: bez zadatka
    nema čemu biti vjeran, a legacy put zadržava zatečeno ponašanje. Odbijanje
    je isto kao svako drugo na ovom nivou (numerička/geometrijska provjera):
    sigurna poruka, bez mutacije sesije, bez drugog AI poziva."""
    if contract is None or not session["current_task"]:
        return
    faithful, offending = contract_pipeline.verify_prose_fidelity(
        text, _active_task_texts(session)
    )
    if not faithful:
        raise InvalidOutputError(
            f"prose_fidelity: nepoznate vrijednosti {list(offending)[:6]} [{where}]"
        )


def _apply_new_task(session, new_task, task_family="", request_id="",
                    contract=None, archetype=""):
    """Sanitizuje tekst zadatka i sve 4 opcije, promiješa opcije i primjenjuje
    svježe stanje na sesiju (server je jedini koji dodjeljuje ID-jeve opcijama
    i pamti koji je tačan). Vraća sanitizovan tekst zadatka.

    Svaki dio (pitanje, svaka opcija) prolazi kroz
    sanitize_and_validate_math_text — ako BILO KOJI dio ostane nebezbjedan
    (sirov \\frac/\\sqrt/\\text/\\cdot/\\begin/\\end izvan $...$, vidljiv "\\n",
    zabranjen kontrolni znak ili prepoznat oštećen LaTeX oblik i nakon
    pokušaja sigurne reparacije), CIO zadatak se odbija — baca se
    InvalidOutputError koju pozivalac (run_practice_turn) već hvata i vraća
    postojeći sigurni fallback, BEZ mutacije sesije i BEZ drugog AI poziva.
    """
    task_text, task_safe = sanitize_and_validate_math_text(new_task.text.strip())
    if not task_safe:
        raise InvalidOutputError("nebezbjedan matematički zapis u tekstu zadatka")
    task_text = normalize_terminology(task_text)

    # Numerička dosljednost TEKSTA PITANJA: politika je po porodici (server-
    # derived, matbot/task_family_validation.py). Većina porodica predstavlja
    # pitanje kao ČINJENICU (provjerava se); porodice gdje je pitanje NAMJERNO
    # pogrešan predmet ispitivanja („Učenik je napisao... Šta je pogriješio?“,
    # „Da li uređeni par zadovoljava sistem?“ kad namjerno NE zadovoljava)
    # preskaču ovu provjeru — vidi question_numeric_policy().
    # Politika je server-derived: za lekciju s ugovorom nosi je ARHETIP
    # (matbot/contracts/archetypes.py), inače porodica — u oba slučaja nikad
    # metapodatak modela ni formulacija pitanja.
    contract_archetype = (
        contract_archetypes.archetype_for(archetype) if contract is not None else None
    )
    if contract_archetype is not None:
        numeric_policy = contract_archetype.question_numeric_policy
        geometry_policy = contract_archetype.question_geometry_policy
    else:
        numeric_policy = question_numeric_policy(task_family)
        geometry_policy = question_geometry_policy(task_family)

    # Za lekciju s ugovorom je `new_task` SERVERSKI render kostura
    # (matbot/contracts/generator.py) — model ga nije pisao. Sve provjere ispod
    # ipak OSTAJU i za taj put: one su druga, jeftina odbrambena mreža protiv
    # defekta samog generatora, i drže engine i legacy tekst pod identičnim
    # pravilima (sanitizacija, terminologija, duplikati, potpis ponavljanja).

    if numeric_policy != "allow_intentional_mismatch":
        _reject_if_numerically_inconsistent(task_text, "tekst zadatka")

    # Geometrijska notacija TEKSTA PITANJA — ista logika kao numerička: oblik
    # čiji je predmet ispitivanja BAŠ pogrešna oznaka („Učenik je napisao
    # $O=\pi D$. Gdje je greška?“) je smije prikazati.
    geometry_scope, geometry_figures = _geometry_context(session)
    _reject_if_geometry_notation_invalid(
        task_text, geometry_scope, geometry_figures, "tekst zadatka",
        policy=geometry_policy,
    )

    sanitized_texts = []
    for opt in new_task.options:
        opt_text, opt_safe = sanitize_and_validate_math_text(
            opt.text.strip(), allow_whole_expression_wrap=True
        )
        if not opt_safe:
            raise InvalidOutputError("nebezbjedan matematički zapis u opciji zadatka")
        sanitized_texts.append(normalize_terminology(opt_text))

    # DOSLOVNA jedinstvenost — nad SANITIZOVANIM tekstom (tačno onim koji
    # učenik vidi) i CASE-SENSITIVE. Ovdje, a ne u schema._validate_options,
    # jer se tek nakon sanitizacije zna konačan vidljivi tekst. Hvata ono što
    # semantička provjera ispod ne može: dvije identične PROZNE opcije.
    textual_duplicates = option_equivalence.find_textual_duplicate_pairs(sanitized_texts)
    if textual_duplicates:
        raise InvalidOutputError(f"duple opcije: parovi {textual_duplicates}")

    # Semantička (ne samo tekstualna) jednakost opcija (Defekt 4, živi nalaz):
    # dvije vizuelno različite opcije mogu predstavljati ISTU vrijednost
    # ("$8\sqrt{2}\,\text{cm}$" i "$11,3\,\text{cm}$") ili biti algebarski
    # identične ("$d=a\sqrt{2}$" i "$d=\sqrt{2}a$") — takav zadatak nema
    # tačno JEDAN tačan odgovor i mora se odbiti PRIJE mutacije sesije.
    duplicate_pairs = find_equivalent_option_pairs(sanitized_texts)
    if duplicate_pairs:
        err = InvalidOutputError(f"semantically_duplicate_options: parovi {duplicate_pairs}")
        # Dijagnostika za strukturisani log (vidi run_practice_turn) — NIKAD ne
        # ide u browser, samo interni server log. Pozivalac dodaje request_id/
        # topic/family (nema ih ovdje) prije logovanja.
        err.duplicate_options_diagnostics = {
            "question": task_text,
            "options": list(sanitized_texts),
            "pairs": duplicate_pairs,
            "equivalence_types": [
                option_equivalence.classify_equivalence(sanitized_texts[i], sanitized_texts[j])
                for i, j in duplicate_pairs
            ],
            "correct_option_index": new_task.correct_option_index,
            "expected_answer": new_task.expected_answer,
        }
        raise err

    # Tačna opcija i interni očekivani odgovor predstavljaju PRAVU matematiku
    # (ne izmišljen predmet ispitivanja) — UVIJEK se provjeravaju, bez obzira
    # na porodicu. Pogrešne opcije (distraktori) se NIKAD numerički ne
    # provjeravaju — namjerno su pogrešne po dizajnu multiple-choice zadatka
    # (živi nalaz: distraktor „$3\cdot16/2=48$“ ne smije srušiti cio zadatak).
    if 0 <= new_task.correct_option_index < len(sanitized_texts):
        _reject_if_numerically_inconsistent(
            sanitized_texts[new_task.correct_option_index], "tačna opcija"
        )
        # TAČNA opcija je AUTORITATIVNA i za notaciju — uvijek "check", bez
        # obzira na porodicu. Pogrešne opcije (distraktori) se NAMJERNO ne
        # provjeravaju: one po dizajnu smiju nositi pogrešnu formulu/oznaku
        # (isti princip kao numerička provjera distraktora iznad).
        _reject_if_geometry_notation_invalid(
            sanitized_texts[new_task.correct_option_index],
            geometry_scope, geometry_figures, "tačna opcija",
        )
    _reject_if_numerically_inconsistent(new_task.expected_answer.strip(), "expected_answer")
    _reject_if_geometry_notation_invalid(
        new_task.expected_answer.strip(), geometry_scope, geometry_figures, "expected_answer",
    )

    # --- UGOVOR PORODICE (server-side, deterministički) --------------------
    # Prompt je samo sugestija: uživo je potvrđeno da model za dodijeljenu
    # porodicu ume vratiti zadatak DRUGE porodice. Ovdje se to odbija PRIJE
    # ikakve mutacije sesije i bez drugog AI poziva. Provjerava se VIDLJIV
    # tekst (nakon sanitizacije i normalizacije terminologije) — ne samo ono
    # što je model deklarisao o sebi.
    # Za lekciju s ugovorom OVDJE nema dodatne provjere: zadatak je serverski
    # kostur, konstruisan i verifikovan PRIJE jedinog AI poziva
    # (contract_pipeline.prepare_task), a modelov new_task je odbačen u
    # _handle_text_turn. UKLJUČEN ugovor i dalje NIKAD ne pada nazad na legacy
    # — defekt generatora se vidi kao odbijanje, ne kao tihi povratak na staro.
    if contract is None:
        # --- LEGACY PUT (lekcije bez ugovora) — nepromijenjen ---------------
        try:
            validate_task_family(
                task_family,
                question=task_text,
                option_texts=sanitized_texts,
                correct_option_index=new_task.correct_option_index,
                expected_answer=new_task.expected_answer,
                difficulty=new_task.difficulty,
                declared={
                    "task_family": new_task.task_family,
                    "student_must_find": new_task.student_must_find,
                    "answer_kind": new_task.answer_kind,
                    "task_form": new_task.task_form,
                },
            )
        except FamilyContractError as e:
            raise InvalidOutputError(f"family_contract_mismatch: {e}") from e

    # --- DETERMINISTIČKA PROVJERA SISTEMA (matbot/systemcheck.py) ----------
    # Živi nalaz: model je prikazao jedan sistem, a riješio drugi, pa NIJEDNA
    # od četiri opcije nije zadovoljavala obje prikazane jednačine — a
    # `expected_answer` je ponovio istu grešku, pa se slaganje te dvije
    # vrijednosti NE smije uzeti kao dokaz. Jedini dokaz je uvrštavanje svakog
    # ponuđenog para u jednačine koje učenik STVARNO vidi.
    # Radi se PRIJE miješanja opcija, dodjele ID-jeva i bilo kakve mutacije.
    system_result = None
    if task_family == "solve_system":
        system_result = systemcheck.verify_solve_system(
            task_text, sanitized_texts, new_task.correct_option_index,
            expected_answer=new_task.expected_answer,
        )
        if system_result.status == systemcheck.STATUS_INVALID:
            err = InvalidOutputError(
                f"system_verification: {','.join(system_result.issue_codes)}"
            )
            # Dijagnostika za strukturisani log (nikad u browser) — pozivalac
            # dodaje request_id/topic prije logovanja.
            err.system_diagnostics = {
                "issue_codes": list(system_result.issue_codes),
                "valid_option_indices": list(system_result.valid_option_indices),
                "marked_option_index": system_result.marked_option_index,
                "question": task_text,
                "options": list(sanitized_texts),
                "equations": [[str(v) for v in eq] for eq in (system_result.parsed_equations or ())],
                "pairs": [None if p is None else [str(p[0]), str(p[1])]
                          for p in (system_result.parsed_options or ())],
            }
            raise err
        # "unsupported" NIJE dokaz ispravnosti — zadatak prolazi kao i do sada
        # (nepromijenjeno ponašanje), ali se u logu jasno razlikuje od
        # "verified" da se u izvještajima ne bi računao kao nezavisno provjeren.
        logger.info(
            "practice_system_verification request_id=%s status=%s valid_option_indices=%s",
            request_id, system_result.status, list(system_result.valid_option_indices),
        )
    elif task_family == "identify_equivalent_system":
        equivalent_result = systemcheck.verify_equivalent_system_options(
            task_text, sanitized_texts, new_task.correct_option_index,
        )
        equivalent_diagnostics = {
            "issue_codes": list(equivalent_result.issue_codes),
            "equivalent_option_indices": list(equivalent_result.equivalent_option_indices),
            "marked_option_index": equivalent_result.marked_option_index,
            "original_rref": [[str(v) for v in row]
                              for row in (equivalent_result.original_rref or ())],
            "option_rrefs": [
                None if matrix is None else [[str(v) for v in row] for row in matrix]
                for matrix in (equivalent_result.option_rrefs or ())
            ],
        }
        if equivalent_result.status == systemcheck.STATUS_INVALID:
            err = InvalidOutputError(
                f"equivalent_system_verification: {','.join(equivalent_result.issue_codes)}"
            )
            err.equivalent_system_diagnostics = equivalent_diagnostics
            raise err
        if (equivalent_result.status == systemcheck.STATUS_UNSUPPORTED
                and equivalent_result.original_rref is not None):
            # Ova porodica nudi ISKLJUČIVO četiri sistema. Kad je original
            # dokazano parsabilan, ali makar jedna opcija nije, server ne može
            # dokazati jedinstven ekvivalentan odgovor — zato fail closed prije
            # potpisa, shufflea, ID-jeva i bilo kakve mutacije sesije.
            err = InvalidOutputError(
                f"equivalent_system_verification: {','.join(equivalent_result.issue_codes)}"
            )
            err.equivalent_system_diagnostics = equivalent_diagnostics
            raise err
        if equivalent_result.status == systemcheck.STATUS_UNSUPPORTED:
            logger.info(
                "practice_equivalent_system_verification request_id=%s status=%s "
                "issue_codes=%s marked_option_index=%s",
                request_id, equivalent_result.status,
                list(equivalent_result.issue_codes), equivalent_result.marked_option_index,
            )
    elif task_family == "verify_ordered_pair":
        ordered_pair_result = systemcheck.verify_ordered_pair_options(
            task_text, sanitized_texts, new_task.correct_option_index,
        )
        ordered_pair_diagnostics = {
            "issue_codes": list(ordered_pair_result.issue_codes),
            "computed_pair_status": ordered_pair_result.computed_pair_status,
            "matching_option_indices": list(ordered_pair_result.matching_option_indices),
            "marked_option_index": ordered_pair_result.marked_option_index,
            "equation_truth_values": list(ordered_pair_result.equation_truth_values or ()),
            "mapped_option_statuses": list(ordered_pair_result.mapped_option_statuses or ()),
        }
        if ordered_pair_result.status == systemcheck.STATUS_INVALID:
            err = InvalidOutputError(
                f"ordered_pair_verification: {','.join(ordered_pair_result.issue_codes)}"
            )
            err.ordered_pair_diagnostics = ordered_pair_diagnostics
            raise err
        if ordered_pair_result.status == systemcheck.STATUS_UNSUPPORTED:
            logger.info(
                "practice_ordered_pair_verification request_id=%s status=%s "
                "issue_codes=%s marked_option_index=%s",
                request_id, ordered_pair_result.status,
                list(ordered_pair_result.issue_codes), ordered_pair_result.marked_option_index,
            )

    # Zaštita od ponavljanja — dva nezavisna sloja, oba PRIJE mutacije sesije:
    #   1. doslovan tekst pitanja (hvata identičan zadatak)
    #   2. pedagoški oblik bez brojeva (hvata „isti zadatak, drugi brojevi“
    #      kad ga vrate DVIJE različite porodice — živi nalaz)
    # Odbijeni zadatak ne mijenja napredovanje, a pozivalac vraća postojeći
    # sigurni fallback bez 2. AI poziva.
    signature = task_families.task_signature(
        task_family, task_text, session["lesson_id"], new_task.difficulty
    )
    if task_families.is_duplicate_signature(signature, session["recent_task_signatures"]):
        raise InvalidOutputError("ponovljen tekst zadatka u istoj sesiji")
    if task_families.is_duplicate_shape(
        signature, session["recent_task_signatures"],
        retry_required=session["retry_required"],
    ):
        raise InvalidOutputError(
            f"pedagogical_shape_repeat: {task_family or '(bez porodice)'}"
        )

    current_options, correct_option_id = _shuffle_options(sanitized_texts, new_task.correct_option_index)

    session["current_task"] = task_text
    session["expected_answer_summary"] = new_task.expected_answer.strip()
    session["difficulty"] = new_task.difficulty
    session["hint_level"] = 0
    session["recent_tasks"].append(task_text)
    session["current_options"] = current_options
    session["correct_option_id"] = correct_option_id
    session["wrong_option_ids"] = []
    session["task_completed"] = False
    session["last_choice_turn_id"] = ""
    session["last_choice_response"] = None

    # Napredovanje: novi zadatak nosi porodicu koju je izabrao SERVER.
    if task_family:
        session["current_family"] = task_family
        if not session["recently_used_families"] or session["recently_used_families"][-1] != task_family:
            session["recently_used_families"].append(task_family)
    session["recent_task_signatures"].append(signature)
    return task_text


def run_practice_turn(store, llm, turn):
    """turn: očišćeni dict iz api.py (session_id, grade, selected_topic,
    selected_oblast, student_message, intent, difficulty_request,
    interaction_phase, last_tutor_task, interaction_type, selected_option_id,
    client_turn_id). Vraća JSON-spreman dict."""
    request_id = uuid.uuid4().hex[:12]

    lesson = lesson_info(turn["grade"], turn["selected_topic"])
    if lesson is None:
        # Direktni pozivaoci i zastarjeli/zlonamjerni klijenti ne smiju dobiti
        # fallback porodicu niti zadržati zadatak prethodne lekcije.
        logger.warning("practice_turn request_id=%s invalid_curriculum_context", request_id)
        return _error_response()
    lesson_id = lesson["id"]
    lesson_title = lesson["title"]
    oblast_id = lesson["oblast_id"]
    oblast = lesson["oblast"]

    session = store.load(
        session_id=turn["session_id"],
        grade=turn["grade"],
        lesson_id=lesson_id,
        lesson_title=lesson_title,
        oblast_id=oblast_id,
        oblast=oblast,
        mode="practice",
    )

    if turn.get("interaction_type") == "choice_answer":
        return _handle_choice_answer(store, llm, session, turn, lesson_id, request_id)
    return _handle_text_turn(store, llm, session, turn, lesson_id, request_id)


def _handle_text_turn(store, llm, session, turn, lesson_id, request_id):
    # Browserov last_tutor_task je samo kompatibilno transportno polje. Nikad
    # ne obnavlja serverski zadatak: bez očekivanog odgovora, opcija i otiska
    # izvornog kurikuluma nije dokaz identiteta, posebno nakon promjene lekcije.

    # Snimljeno PRIJE AI poziva: ako bilo šta ispod baci grešku, ovo je jedina
    # istina koju smijemo vratiti — session je lokalna kopija i NIKAD se ne
    # commituje (store.save) osim na uspješnom kraju ove funkcije.
    active_task_before_llm = session["current_task"]
    retry_required_before_llm = bool(session["retry_required"])

    # Oblik sljedećeg zadatka bira SERVER, prije jedinog AI poziva. Dva puta,
    # bez preklapanja i bez tihog fallbacka između njih:
    #   • lekcija s UKLJUČENIM ugovorom → arhetip iz ugovora (motor)
    #   • lekcija bez ugovora           → porodica (nepromijenjen legacy put)
    contract = contract_registry.contract_for(session["lesson_id"])
    contract_state = contract_registry.practice_state(contract)

    if contract_state == contract_registry.STATE_UNAVAILABLE:
        # Ugovor je izričito označen kao nepodržan: nema sigurnog načina da se
        # generiše provjerljiv zadatak. Jasna poruka, BEZ AI poziva, bez
        # prelaska na legacy i bez zadatka druge lekcije.
        logger.info(
            "practice_turn request_id=%s practice_unavailable topic=%s", request_id, lesson_id
        )
        return {"answer": PRACTICE_UNAVAILABLE_MESSAGE, "last_tutor_task": active_task_before_llm}

    prepared_skeleton = None
    if contract_state == contract_registry.STATE_ENGINE:
        selected_family = ""
        # Server-owned PLAN: učenikova izričita molba (zatvorena intent tabela)
        # ima prednost kad je ugovor dozvoljava, inače rotacija. Kostur zadatka
        # se konstruiše i verifikuje PRIJE jedinog AI poziva — pad pripreme na
        # bootstrapu (nema aktivnog zadatka, zadatak je nužan) vraća sigurnu
        # poruku BEZ ijednog potrošenog poziva i bez mutacije stanja.
        plan = contract_pipeline.build_plan(
            contract,
            student_message=turn["student_message"],
            recently_used=session["recently_used_families"],
            current=session["current_family"],
            retry_required=session["retry_required"],
            difficulty_request=turn["difficulty_request"],
        )
        selected_archetype = plan.archetype_id
        selected_shape = selected_archetype
        prepared = contract_pipeline.prepare_task(
            contract, plan,
            difficulty_request=turn["difficulty_request"],
            avoid_texts=session["recent_tasks"],
        )
        if prepared.ok:
            prepared_skeleton = prepared.skeleton
            logger.info(
                "practice_plan request_id=%s archetype=%s source=%s requested=%s",
                request_id, plan.archetype_id, plan.source, plan.requested or "-",
            )
        else:
            _log_contract_rejection(
                request_id,
                contract_pipeline.diagnostics(contract, selected_archetype, prepared),
            )
            if not active_task_before_llm:
                # Bootstrap bez kostura nema šta da objavi — odbij odmah.
                return _error_response(active_task_before_llm)
            # Aktivni zadatak postoji: turn smije nastaviti kao razgovor o
            # njemu, ali novi zadatak se u ovom turnu NE može izdati.
    else:
        contract = None
        selected_archetype = ""
        applicable = task_families.applicable_families(
            turn["grade"], session["oblast"], session["lesson_title"],
            lesson_id=session["lesson_id"],
        )
        selected_family = task_families.select_family(
            applicable,
            recently_used=session["recently_used_families"],
            completed_families=session["correctly_completed_families"],
            retry_required=session["retry_required"],
            current_family=session["current_family"],
            difficulty_request=turn["difficulty_request"],
        )
        selected_shape = selected_family

    instructions = prompts.build_instructions(
        turn["grade"], lesson_title=session["lesson_title"], oblast=session["oblast"]
    )
    input_text = prompts.build_input(
        session,
        student_message=turn["student_message"],
        intent=turn["intent"],
        difficulty_request=turn["difficulty_request"],
        interaction_phase=turn["interaction_phase"],
        task_family=selected_family,
        task_family_description=task_families.describe(selected_family),
        contract=contract,
        archetype=selected_archetype,
        skeleton=prepared_skeleton,
    )

    try:
        result = llm.practice_turn(instructions, input_text)
        # Za lekciju s ugovorom modelov new_task SADRŽAJ ništa ne znači (server
        # objavljuje vlastiti kostur), pa se ni ne provjerava — new_task je tada
        # samo signal „u ovom turnu se izdaje novi zadatak“. Nesavršena kopija
        # ne smije srušiti turn koji server ionako objavljuje iz svog kostura.
        validate_output(result.output, ignore_new_task_content=contract is not None)
        out = result.output

        # NAPOMENA: out.evaluation se OVDJE NIKAD ne koristi. Tekstualna poruka
        # (pitanje, "ne znam", "uradi ga ti", ...) nije pokušaj odgovora —
        # ocjenjivanje ide ISKLJUČIVO kroz _handle_choice_answer. correct_streak
        # se ovdje ne dira.
        if out.gave_hint and out.new_task is None:
            session["hint_level"] = min(session["hint_level"] + 1, config.MAX_HINT_LEVEL)

        task_text = active_task_before_llm
        if out.new_task is not None and contract is not None:
            # SERVERSKI KOSTUR JE JEDINA ISTINA: modelov new_task se odbacuje u
            # cijelosti, a objavljuje se render pripremljen prije poziva. Ako
            # priprema nije uspjela, novi zadatak se u ovom turnu ne može
            # izdati — fail closed, bez mutacije i bez drugog poziva.
            if prepared_skeleton is None:
                raise InvalidOutputError("new_task_without_prepared_skeleton")
            task_text = _apply_new_task(
                session, _task_from_skeleton(prepared_skeleton),
                task_family=selected_shape, request_id=request_id,
                contract=contract, archetype=selected_archetype,
            )
        elif out.new_task is not None:
            task_text = _apply_new_task(
                session, out.new_task, task_family=selected_shape,
                request_id=request_id, contract=contract, archetype=selected_archetype,
            )

        # Novi zadatak uvijek dobija kratak server-owned uvod. Modelov slobodni
        # `reply` se tada ne prikazuje i ne može prokrijumčariti hint prije prvog
        # pokušaja; tekst zadatka ostaje zaseban i neizmijenjen ovim pravilom.
        if out.new_task is not None:
            reply = _new_task_intro(turn, retry_required_before_llm)
            answer = reply + "\n\nZadatak: " + task_text
        else:
            reply, reply_safe = sanitize_and_validate_math_text(out.reply.strip())
            if not reply_safe:
                raise InvalidOutputError("nebezbjedan matematički zapis u odgovoru")
            reply = normalize_terminology(reply)
            _reject_if_numerically_inconsistent(reply, "reply")
            # Tutorov vidljivi tekst je AUTORITATIVAN (objašnjenje/odgovor na
            # pitanje) — uvijek "check", nikad politika porodice.
            _scope, _figures = _geometry_context(session)
            _reject_if_geometry_notation_invalid(reply, _scope, _figures, "reply")
            # Na motoru ugovora matematiku posjeduje SERVER: proza o aktivnom
            # zadatku ne smije uvesti vlastiti broj (vidi funkciju iznad).
            _reject_if_prose_invents_mathematics(reply, session, contract, "reply")
            answer = reply

        session["recent_turns"].append(
            {"student": turn["student_message"][:300], "tutor": answer[:400]}
        )

        response = {
            "status": "ready",
            "answer": answer,
            "answer_verdict": None,          # tekst se nikad ne ocjenjuje
            "last_tutor_task": session["current_task"] or "",
            "next_state": _next_state(session),
            "session_mode": "practice",
            "effective_topic": lesson_id or "",
        }

        # "Uradi ga ti" (intent="solution_request", isti mehanizam kao postojeći
        # hint_request chip): model je upravo dao puni postupak u 'reply' —
        # server sad deterministički završava zadatak i otkriva tačnu opciju.
        # Nije pogrešan klik: wrong_option_ids/correct_streak se ne diraju.
        if turn["intent"] == "solution_request" and session["correct_option_id"]:
            session["task_completed"] = True
            response["revealed_correct_option_id"] = session["correct_option_id"]

        store.save(session)  # JEDINA commit tačka u cijeloj funkciji

        logger.info(
            "practice_turn request_id=%s ok latency_ms=%s usage=%s",
            request_id, result.latency_ms, result.usage,
        )
        return response
    except LLMError as e:
        logger.warning(
            "practice_turn request_id=%s category=%s topic=%s family=%s mode=practice %s",
            request_id, e.category, lesson_id or "", selected_shape or "",
            failure_diagnostics_kv(e),
        )
        return _error_response(active_task_before_llm)
    except InvalidOutputError as e:
        logger.warning("practice_turn request_id=%s category=invalid_output detail=%s", request_id, e)
        contract_diagnostics = getattr(e, "contract_diagnostics", None)
        if contract_diagnostics:
            _log_contract_rejection(request_id, contract_diagnostics)
        diagnostics = getattr(e, "duplicate_options_diagnostics", None)
        if diagnostics:
            _log_duplicate_options(request_id, lesson_id, selected_shape, diagnostics)
        system_diagnostics = getattr(e, "system_diagnostics", None)
        if system_diagnostics:
            _log_system_verification(request_id, lesson_id, selected_shape, system_diagnostics)
        equivalent_diagnostics = getattr(e, "equivalent_system_diagnostics", None)
        if equivalent_diagnostics:
            _log_equivalent_system_verification(
                request_id, lesson_id, selected_shape, equivalent_diagnostics
            )
        ordered_pair_diagnostics = getattr(e, "ordered_pair_diagnostics", None)
        if ordered_pair_diagnostics:
            _log_ordered_pair_verification(
                request_id, lesson_id, selected_shape, ordered_pair_diagnostics
            )
        return _error_response(active_task_before_llm)
    except Exception:
        # Zadnja linija odbrane za NEOČEKIVANE greške u obradi ovog turna
        # (bug u primjeni rezultata, itd.) — store.save() gore nikad nije
        # dosegnut ako je izuzetak nastao prije njega, pa je stanje netaknuto.
        logger.exception("practice_turn request_id=%s unexpected_error", request_id)
        return _error_response(active_task_before_llm)


def _handle_choice_answer(store, llm, session, turn, lesson_id, request_id):
    active_task_before = session["current_task"]

    # Nema aktivnog MC zadatka (npr. sesija istekla/restart bez last_tutor_task
    # obnove) → deterministički odbij, bez AI poziva, bez promjene stanja.
    if not session["current_options"] or not session["correct_option_id"]:
        return _error_response(active_task_before)

    client_turn_id = turn.get("client_turn_id") or ""

    # Idempotentan retry: ISTI client_turn_id kao zadnji OBRAĐENI choice_answer
    # → vrati identičan (keširan) odgovor, BEZ novog AI poziva i BEZ mutacije
    # stanja (mreža/dupli tab retry ne smije duplo brojati pokušaj).
    if client_turn_id and client_turn_id == session["last_choice_turn_id"] \
            and session["last_choice_response"] is not None:
        return copy.deepcopy(session["last_choice_response"])

    # Zadatak je već završen (tačan klik / drugi pogrešan / "uradi ga ti") —
    # svaki NOVI (ne-idempotentan) klik na završen zadatak je nevažeći: bez AI
    # poziva, bez promjene stanja, bez otkrivanja bilo čega dodatnog.
    if session["task_completed"]:
        return _error_response(active_task_before)

    selected_option_id = turn.get("selected_option_id") or ""
    options_by_id = {opt["id"]: opt for opt in session["current_options"]}
    if selected_option_id not in options_by_id:
        # Nevažeći ID za aktivni zadatak: odbij deterministički, NIKAD ne
        # pozivaj model, NIKAD ne mijenjaj stanje, NIKAD ne otkrivaj tačan odgovor.
        return _error_response(active_task_before)

    selected_text = options_by_id[selected_option_id]["text"]
    is_correct = (selected_option_id == session["correct_option_id"])
    wrong_attempts_before = len(session["wrong_option_ids"])

    if is_correct:
        session["correct_streak"] += 1
        session["task_completed"] = True
        # Napredovanje: porodica je savladana → sljedeći zadatak MORA biti druga
        # porodica (select_family to garantuje), a eventualni retry se poništava.
        session["last_result"] = "correct"
        session["retry_required"] = False
        current_family = session["current_family"]
        if current_family and current_family not in session["correctly_completed_families"]:
            session["correctly_completed_families"].append(current_family)
    else:
        session["correct_streak"] = 0
        session["wrong_option_ids"].append(selected_option_id)
        # Netačno → ista porodica se ponavlja s drugim vrijednostima i istom
        # težinom. Porodica se NE upisuje u savladane.
        session["last_result"] = "incorrect"
        session["retry_required"] = True
        if wrong_attempts_before >= 1:
            session["task_completed"] = True  # drugi pogrešan klik → kraj zadatka

    instructions = prompts.build_instructions(turn["grade"])
    input_text = prompts.build_input(
        session,
        student_message=turn["student_message"],
        intent=turn["intent"],
        difficulty_request=turn["difficulty_request"],
        interaction_phase=turn["interaction_phase"],
        trusted_choice_verdict={
            "selected_text": selected_text,
            "is_correct": is_correct,
            "wrong_attempts": wrong_attempts_before,
        },
    )

    # Prvi pogrešan klik: server SAM sastavlja vidljiv odgovor iz 'hint'
    # („Netačno.“ + hint, vidi matbot/feedback.py) — prazan 'reply' je tu
    # bezopasan dok god je 'hint' prisutan. Svaki drugi ishod (tačno, drugi
    # pogrešan/reveal) i dalje zahtijeva neprazan 'reply' kao stvaran sadržaj.
    first_wrong = (not is_correct) and (wrong_attempts_before == 0)

    try:
        result = llm.practice_turn(instructions, input_text)
        validate_output(result.output, require_reply=not first_wrong)
        out = result.output

        # Server verdikt UVIJEK ima prednost. Model smije samo objašnjavati —
        # ako suprotstavi ('evaluation' kontradiktoran is_correct, ili vrati
        # new_task na klik), to se ignoriše i bilježi (bez sirovog teksta), bez
        # drugog/repair poziva.
        if out.new_task is not None:
            logger.warning("practice_choice request_id=%s unexpected_new_task_ignored", request_id)
        expected_word = "correct" if is_correct else "incorrect"
        if out.evaluation is not None and out.evaluation != expected_word:
            logger.warning("practice_choice request_id=%s verdict_mismatch", request_id)

        reply, reply_safe = sanitize_and_validate_math_text(out.reply.strip())
        if not reply_safe:
            raise InvalidOutputError("nebezbjedan matematički zapis u odgovoru")
        reply = normalize_terminology(reply)

        # Netačan klik: server SAM oblikuje vidljivi tekst (vidi matbot/feedback.py).
        # Prvi pogrešan → „Netačno.“ + jedan sažet hint, bez dokazivanja i bez
        # otkrivanja tačne opcije. Drugi pogrešan → „Netačno.“ + postojeće
        # otkrivanje rješenja. Hint prolazi ISTU math-safety granicu kao reply.
        geo_scope, geo_figures = _geometry_context(session)

        if not is_correct:
            if wrong_attempts_before >= 1:
                reply = feedback.shape_final_wrong_prefix(reply)
            else:
                # PRVI pogrešan klik: hint s pogrešnom geometrijskom oznakom se
                # NE odbija kao cio turn — tiho pada na siguran generički hint
                # (isti mehanizam kao curenje odgovora / nedosljedan račun u
                # matbot/feedback.py), bez drugog AI poziva. Isto važi i za
                # 'reply' kao rezervni izvor hinta: ako i on krši konvenciju,
                # oba izvora se prazne pa feedback.py koristi GENERIC_HINT.
                # KAPIJA VJERNOSTI PROZE (samo lekcija s ugovorom): hint o
                # SERVERSKOM zadatku ne smije uvoditi brojeve koji se ne daju
                # objasniti iz samog zadatka — takav hint se NE objavljuje nego
                # tiho pada na siguran generički (isti mehanizam kao pogrešna
                # geometrijska oznaka ispod), bez drugog AI poziva.
                contract_session = (
                    contract_registry.practice_state(
                        contract_registry.contract_for(session["lesson_id"])
                    ) == contract_registry.STATE_ENGINE
                )
                task_texts = _active_task_texts(session)

                def _prose_faithful(candidate):
                    if not contract_session:
                        return True
                    faithful, offending = contract_pipeline.verify_prose_fidelity(
                        candidate, task_texts
                    )
                    if not faithful:
                        logger.warning(
                            "practice_choice request_id=%s prose_fidelity_hint_replaced "
                            "offending=%s", request_id, list(offending)[:6],
                        )
                    return faithful

                hint_source = ""
                if out.hint:
                    hint_text, hint_safe = sanitize_and_validate_math_text(out.hint.strip())
                    if hint_safe:
                        candidate = normalize_terminology(hint_text)
                        if (geometrycheck.is_geometry_clean(candidate, geo_scope, geo_figures)
                                and _prose_faithful(candidate)):
                            hint_source = candidate
                        elif not geometrycheck.is_geometry_clean(candidate, geo_scope, geo_figures):
                            logger.warning(
                                "practice_choice request_id=%s geometry_notation_hint_replaced",
                                request_id,
                            )
                reply_source = reply
                if not geometrycheck.is_geometry_clean(reply, geo_scope, geo_figures):
                    reply_source = ""
                elif not _prose_faithful(reply):
                    reply_source = ""
                reply = feedback.shape_first_wrong_feedback(
                    hint_source,
                    reply_source,
                    correct_option_text=options_by_id[session["correct_option_id"]]["text"],
                    expected_answer=session["expected_answer_summary"],
                )

        # Provjera numeričke dosljednosti nad KONAČNIM vidljivim tekstom (nakon
        # oblikovanja feedbacka/reveala) — pogrešan račun u otkrivenom rješenju
        # jednako je štetan kao u zadatku.
        _reject_if_numerically_inconsistent(reply, "choice_feedback")
        # Notacija KONAČNOG vidljivog teksta. Za prvi pogrešan klik je iznad već
        # osiguran čist izvor (ili GENERIC_HINT), pa je ovo tu no-op; za TAČAN
        # odgovor i za OTKRIVANJE rješenja (drugi pogrešan) ovo je autoritativno
        # objašnjenje i pogrešna oznaka ga odbija u cijelosti.
        _reject_if_geometry_notation_invalid(reply, geo_scope, geo_figures, "choice_feedback")

        session["recent_turns"].append({
            "student": f"[izabrao opciju: {selected_text}]"[:300],
            "tutor": reply[:400],
        })

        response = {
            "status": "ready",
            "answer": reply,
            "answer_verdict": "correct" if is_correct else "incorrect",  # server-truth
            "last_tutor_task": session["current_task"] or "",
            "next_state": _next_state(session),
            "session_mode": "practice",
            "effective_topic": lesson_id or "",
        }
        # Otkrivanje tačne opcije SAMO na drugi pogrešan klik — nikad na prvi,
        # nikad na tačan klik (tamo je kliknuta opcija već poznato tačna).
        if not is_correct and wrong_attempts_before >= 1:
            response["revealed_correct_option_id"] = session["correct_option_id"]

        if client_turn_id:
            session["last_choice_turn_id"] = client_turn_id
            session["last_choice_response"] = copy.deepcopy(response)

        store.save(session)  # JEDINA commit tačka u cijeloj funkciji

        logger.info(
            "practice_choice request_id=%s ok is_correct=%s wrong_attempts=%s latency_ms=%s usage=%s",
            request_id, is_correct, wrong_attempts_before, result.latency_ms, result.usage,
        )
        return response
    except LLMError as e:
        logger.warning(
            "practice_choice request_id=%s category=%s topic=%s mode=practice %s",
            request_id, e.category, lesson_id or "", failure_diagnostics_kv(e),
        )
        return _error_response(active_task_before)
    except InvalidOutputError as e:
        logger.warning("practice_choice request_id=%s category=invalid_output detail=%s", request_id, e)
        return _error_response(active_task_before)
    except Exception:
        logger.exception("practice_choice request_id=%s unexpected_error", request_id)
        return _error_response(active_task_before)
