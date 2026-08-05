"""Deterministički NALAZI o MCQ paketu — isti skup provjera, dva trenutka.

ZAŠTO POSTOJI (živi release gate 00bbd45, scenario 11 od 12, rotirajuća lekcija
8. razreda, traženi nivo 1):

    Tutor:      valjan zadatak nivoa 1, dokaz težine uredan
    Recenzent:  approve, math_correct=true, task_package_consistent=true
    Objava:     ODBIJENO → `semantically_duplicate_options: [(0, 2)]`

Dvije vidljive opcije bile su različiti stringovi, a ista matematička
vrijednost. `option_equivalence.find_equivalent_option_pairs` je to ISPRAVNO
uhvatio — ali TEK u objavi, poslije oba poziva. Recenzent nikad nije saznao za
nalaz, pa ga nije mogao popraviti, iako `correct` upravo to omogućava.

OVAJ MODUL NE UVODI NOVU PROVJERU. On samo SAKUPLJA nalaze postojećih
determinističkih validatora u zatvorenu, ograničenu strukturu, da bi isti skup
mogao biti upotrijebljen dvaput:

  1. PRIJE drugog poziva — kao serverski nalaz koji recenzent dobije u ulazu i
     mora popraviti u ISTOM (drugom i posljednjem) pozivu;
  2. POSLIJE drugog poziva — kao invarijanta nad recenzentovim konačnim
     paketom, prije ijedne mutacije sesije.

Nikad ne baca izuzetak i nikad ne odbija Tutorov nacrt: nacrt s nalazom je
upravo ono što recenzent treba da vidi. Odluku o odbijanju donosi pozivalac.

BEZBJEDNOST DIJAGNOSTIKE: nalaz nosi SAMO kod, ID-jeve opcija i kratak
ograničen detalj — nikad tekst zadatka, opcije, rješenje, prompt ni sirov izlaz
modela (CLAUDE.md, pravilo 7).
"""
from dataclasses import dataclass

from matbot import lesson_fidelity, mcq_integrity, option_equivalence
from matbot.semantics import detectors as semantic_detectors
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.terminology import normalize_terminology
from matbot.tutor.schema import (INCOMPLETE_TASK_TEXT_CODE, UnifiedOutputError,
                                 difficulty_evidence_errors, evidence_diagnostics,
                                 validate_task)

# Ograničenja da dijagnostika i prompt blok ostanu mali i predvidivi.
MAX_ISSUES = 8
# Dovoljno za `target Level 1; <kod validatora>; steps=… flags=…`, i dalje tvrda
# granica. Ovo je granica DIJAGNOSTIKE, ne prag težine.
_MAX_DETAIL_CHARS = 200

# Kod koji objava već koristi za isti nalaz — namjerno ISTI string, da se
# dijagnostika prije i poslije drugog poziva poklapa u logovima.
SEMANTIC_DUPLICATE_CODE = "semantically_duplicate_options"

# Dokaz težine nacrta ne zadovoljava nivo koji je nacrt SAM deklarisao.
DIFFICULTY_OUTSIDE_TARGET_CODE = "difficulty_evidence_outside_target"


@dataclass(frozen=True)
class PackageIssue:
    """Jedan zatvoren deterministički nalaz o paketu."""

    code: str
    option_ids: tuple = ()
    option_indexes: tuple = ()
    detail: str = ""

    def describe(self):
        """`code: option IDs a and c (numeric_exact)` — ograničeno i bez sadržaja."""
        parts = []
        if self.option_ids:
            parts.append("option IDs " + " and ".join(self.option_ids))
        elif self.option_indexes:
            parts.append("option indexes " + " and ".join(
                str(index) for index in self.option_indexes))
        if self.detail:
            parts.append(f"({self.detail[:_MAX_DETAIL_CHARS]})")
        return self.code if not parts else f"{self.code}: {' '.join(parts)}"


def safe_visible_text(raw, allow_wrap=False):
    """Sanitizacija + terminologija, BEZ izuzetka. Vrati (tekst, sigurno).

    Jedina implementacija tog niza u projektu — `pipeline._safe_text` je koristi
    i samo pretvara `sigurno=False` u UnifiedOutputError."""
    cleaned, safe = sanitize_and_validate_math_text(
        (raw or "").strip(), allow_whole_expression_wrap=allow_wrap
    )
    if not safe:
        return "", False
    return normalize_terminology(cleaned), True


def _option_id(task, index):
    """Stabilan ID opcije; indeks je rezerva kad paket nema ispravne ID-jeve."""
    try:
        return task.options[index].id
    except (AttributeError, IndexError, TypeError):
        return str(index)


def collect_package_issues(task, contract=None):
    """Vrati zatvorenu torku nalaza postojećih validatora nad JEDNIM paketom.

    Prazna torka = nijedan deterministički validator ne može dokazati defekt.
    To NIJE dokaz ispravnosti (semantiku i dalje drži recenzent), nego odsustvo
    dokazane greške — isti princip kao mathcheck i option_equivalence.

    `contract` je semantički ugovor porodice (Faza 4A) ili None. None znači
    „lekcija ga nema“ i tada je rezultat bajt za bajt isti kao prije."""
    if task is None:
        return ()
    issues = []

    # 1) STRUKTURA PAKETA — postojeći `validate_task` (broj opcija, jedinstveni
    #    ID-jevi, slaganje correct_option_id/index, prazna/preduga polja...).
    #
    #    Nepotpun tekst zadatka dobija VLASTITI kod umjesto generičkog: recenzent
    #    iz „task_structure_invalid“ ne može znati da mu nedostaje baš izraz ili
    #    jednačina, a upravo to mora dopisati u ispravci (živi nalaz A25/B02/B30/B42).
    try:
        validate_task(task)
    except UnifiedOutputError as error:
        message = str(error)
        issues.append(PackageIssue(
            INCOMPLETE_TASK_TEXT_CODE if message.startswith(INCOMPLETE_TASK_TEXT_CODE)
            else "task_structure_invalid",
            detail=message))
    except Exception:  # nikad ne ruši turn zbog dijagnostike
        issues.append(PackageIssue("task_structure_invalid", detail="nepoznata struktura"))

    options = list(getattr(task, "options", None) or ())
    option_texts, unsafe_ids = [], []
    for index, option in enumerate(options):
        text, safe = safe_visible_text(getattr(option, "text", ""), allow_wrap=True)
        option_texts.append(text)
        if not safe:
            unsafe_ids.append(_option_id(task, index))
    if unsafe_ids:
        issues.append(PackageIssue("unsafe_option_notation", option_ids=tuple(unsafe_ids)))

    # 2) DOSLOVNI I SEMANTIČKI DUPLIKATI — postojeći option_equivalence.
    if option_texts and all(option_texts):
        for i, j in option_equivalence.find_textual_duplicate_pairs(option_texts):
            issues.append(PackageIssue(
                "duplicate_option_text",
                option_ids=(_option_id(task, i), _option_id(task, j)),
                option_indexes=(i, j)))
        for i, j, kind in option_equivalence.find_equivalent_option_pairs_with_types(
                option_texts):
            issues.append(PackageIssue(
                SEMANTIC_DUPLICATE_CODE,
                option_ids=(_option_id(task, i), _option_id(task, j)),
                option_indexes=(i, j),
                detail=kind or ""))

    # 3) OZNAČEN ODGOVOR I `expected_answer` — ista pravila kao u objavi.
    marked_index = getattr(task, "correct_option_index", None)
    marked_text = ""
    if isinstance(marked_index, int) and 0 <= marked_index < len(option_texts):
        marked_text = option_texts[marked_index]
    expected, expected_safe = safe_visible_text(
        getattr(task, "expected_answer", ""), allow_wrap=True)
    if not expected_safe:
        issues.append(PackageIssue("unsafe_expected_answer_notation"))
    elif marked_text and expected.strip() != marked_text.strip():
        issues.append(PackageIssue(
            "expected_answer_not_marked_option",
            option_ids=(_option_id(task, marked_index),)))

    # 3b) SEMANTIČKI ZAHTJEV KOJI NASLOV LEKCIJE DETERMINISTIČKI NAMEĆE.
    # ŽIVI RELEASE GATE (commit 0883e8c, scenario `fresh_level1`): za lekciju o
    # pravilima djeljivosti Tutor je predložio „Koji od ponuđenih brojeva je
    # djelilac broja 84?“. „N je djelilac broja M“ nije „M je djeljiv sa N“ kao
    # vidljivi zadatak: traži se primjena PRAVILA, ne traženje faktora.
    # `lesson_fidelity.semantic_task_requirement` to već zna i izvodi iz
    # NASLOVA (nikad iz ID-a lekcije), legacy put ga koristi, a i gate harness ga
    # računa za dijagnostiku — univerzalni put ga pri pivotu nije preuzeo, pa je
    # zadatak stizao do objave neprovjeren. Ovdje se poziva ISTI validator: bez
    # duplikata, bez novog praga i bez grananja po lekciji.
    requirement = lesson_fidelity.semantic_task_requirement(
        getattr(task, "selected_lesson_title", ""))
    if requirement is not None:
        visible, visible_safe = safe_visible_text(getattr(task, "text", ""))
        failure = requirement.failure_for(visible if visible_safe else
                                          getattr(task, "text", ""))
        if failure:
            issues.append(PackageIssue(failure, detail=requirement.reviewer_instruction))

    # 3c) SEMANTIČKI UGOVOR PORODICE (Faza 4A).
    # Jedan višekratni parametarski detektor po PORODICI, nikad po lekciji:
    # razlike među lekcijama nosе isključivo parametri ugovora. Blokira SAMO
    # dokazani prekršaj (`fail`) i samo kad je lekcija izričito `blocking`;
    # `unsupported` je eksplicitno „ne znam“ i nikad ne odbija paket.
    if contract is not None and getattr(contract, "blocking", False):
        visible, visible_safe = safe_visible_text(getattr(task, "text", ""))
        detection = semantic_detectors.detect(
            contract, visible if visible_safe else getattr(task, "text", ""))
        if detection.status == semantic_detectors.STATUS_FAIL:
            issues.append(PackageIssue(
                detection.code,
                detail=f"{detection.reason} [{contract.family_id}]"))

    # 4) DOKAZIVO VIŠE TAČNIH OPCIJA — postojeći uski mcq_integrity oracle.
    task_text, task_text_safe = safe_visible_text(getattr(task, "text", ""))
    if task_text_safe and option_texts and all(option_texts) and isinstance(marked_index, int):
        try:
            failure, _result = mcq_integrity.publication_failure(
                task_text, option_texts, marked_index, expected)
        except Exception:
            failure = ""
        if failure:
            issues.append(PackageIssue(failure))

    # 5) NUMERIČKA PROTIVRJEČNOST u vidljivom tekstu i rješenju — postojeći
    #    mathcheck. Distraktori se NIKAD ne provjeravaju (namjerno su pogrešni).
    # ŽIVI NALAZ (A19, B04, B33, B42): nesiguran `text`/`solution` se ovdje TIHO
    # preskakao, pa je turn potrošio oba poziva i pao tek u objavi porukom
    # „nebezbjedan matematički zapis [solution]“. Recenzent za to nikad nije
    # saznao, iako `correct` upravo omogućava da to prepiše. Opcije i
    # `expected_answer` su svoj kod već imali; sada ga imaju i ova dva polja.
    # `mathsafe` pravila se ne mijenjaju — mijenja se samo dijagnostika.
    _UNSAFE_FIELD_CODES = {
        "task_text": "unsafe_task_text_notation",
        "solution": "unsafe_solution_notation",
    }
    for label, raw, allow_wrap in (
        ("task_text", getattr(task, "text", ""), False),
        ("marked_option", getattr(task, "expected_answer", ""), True),
        ("solution", getattr(task, "solution", ""), False),
    ):
        text, safe = safe_visible_text(raw, allow_wrap=allow_wrap)
        if not safe:
            code = _UNSAFE_FIELD_CODES.get(label)
            if code:
                issues.append(PackageIssue(code, detail=label))
            continue
        found = find_numeric_inconsistencies(text)
        if found:
            issues.append(PackageIssue(
                "numeric_inconsistency", detail=f"{label} {found[0].split(':')[0]}"))

    # 6) DOKAZ TEŽINE VS DEKLARISAN NIVO (živi gate b8a0f7b)
    #    Živi pad: Tutor je za traženi nivo 1 sam prijavio `combines_concepts=true`,
    #    pa je ZAJEDNIČKI validator već tada mogao dokazati da nacrt nije nivo 1.
    #    Taj nalaz nije stizao recenzentu, pa je recenzent morao sam primijetiti
    #    neslaganje — i pogrešno je odobrio. Prag se NE mijenja: poziva se isti
    #    `difficulty_evidence_errors` koji već koriste i recenzentska invarijanta
    #    i objava. Poredi se s nivoom koji paket SAM deklariše — isto pitanje
    #    interne dosljednosti koje `validate_reviewer` postavlja konačnom paketu.
    evidence = getattr(task, "difficulty_evidence", None)
    target_level = getattr(task, "target_difficulty_level", None)
    if evidence is not None and isinstance(target_level, int):
        try:
            evidence_errors = difficulty_evidence_errors(evidence, target_level)
        except Exception:
            evidence_errors = ()
        if evidence_errors:
            issues.append(PackageIssue(
                DIFFICULTY_OUTSIDE_TARGET_CODE,
                detail=(f"target Level {target_level}; {','.join(evidence_errors)}; "
                        f"{evidence_diagnostics(evidence)}")))

    return tuple(issues[:MAX_ISSUES])


def semantic_duplicate_index_pairs(issues):
    """Parovi indeksa dokazanih semantičkih duplikata — za invarijantu poslije
    drugog poziva (isti par ne smije preživjeti recenzentovu ispravku)."""
    return tuple(issue.option_indexes for issue in issues
                 if issue.code == SEMANTIC_DUPLICATE_CODE and issue.option_indexes)


def format_for_reviewer(issues):
    """Deterministički blok za ULAZ recenzenta. Prazno = nema nalaza."""
    if not issues:
        return ""
    lines = ["SERVER-DETECTED DRAFT ISSUES (deterministic server findings, not suggestions):"]
    lines.extend("- " + issue.describe() for issue in issues)
    lines.append(
        "You MUST NOT return `approve` while any issue above remains. Return `correct` "
        "with a COMPLETE corrected package: replace the offending distractor(s) so all "
        "four options are semantically distinct, and when the difficulty evidence is "
        "outside the target level REPLACE THE TASK with one that genuinely belongs at "
        f"that level. For `{INCOMPLETE_TASK_TEXT_CODE}` the task text asks the student to "
        "compute or solve something that it never shows: write the missing expression, "
        "equation, or system into the task text itself, inside $...$, so the task is "
        "solvable from its own text. For any `unsafe_..._notation` issue the named "
        "field carries MathJax the server cannot accept: REWRITE that exact field using "
        "only plain $...$ notation and known commands, and change nothing else. "
        "Then recompute correct_option_id, "
        "correct_option_index, expected_answer (an exact copy of the marked option's "
        "text), solution, difficulty evidence for the task you actually return, and "
        "task_signature where structural parameters changed. Keep "
        "exactly one correct visible option, and keep the exact selected lesson. Do not "
        "merely reformat an equivalent value, do not merely lower the reported counts, "
        "do not merely relabel the level, "
        "and do not change parts of the task that are already valid. If you cannot "
        "correct it safely, return `fail_closed`."
    )
    return "\n".join(lines)


def describe_issues(issues):
    """Kratak, ograničen zapis za log — bez ijednog vidljivog sadržaja."""
    return "; ".join(issue.describe() for issue in issues)[:300]
