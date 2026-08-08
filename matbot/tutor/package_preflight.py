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
import re
from dataclasses import dataclass

from matbot import lesson_fidelity, mcq_integrity, option_equivalence
from matbot.semantics import detectors as semantic_detectors
from matbot.tutor import task_identity
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsafe import (sanitize_and_validate_math_text,
                             sanitize_and_validate_math_text_with_issues)
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

# Serverski izračunate vrijednosti iz mathcheck poruke: hvata se ISKLJUČIVO
# broj u zagradi neposredno iza zatvorenog navodnika izraza (`'…' (3)`), pa
# zagrada unutar samog izraza (`'(24+6):5'`) nikad ne može biti pogođena.
_MISMATCH_VALUES_RE = re.compile(r"' \(([-+0-9.eE]+)\)")
# Sporni par izraza iz iste mathcheck poruke: `'23 : 5' (4.6) != '4' (4)`.
_MISMATCH_EXPR_RE = re.compile(r"'([^']{1,80})' \(")

# Dokaz težine nacrta ne zadovoljava nivo koji je nacrt SAM deklarisao.
DIFFICULTY_OUTSIDE_TARGET_CODE = "difficulty_evidence_outside_target"

# Predloženi zadatak je kanonski ISTI kao aktivni (vidi task_identity).
DUPLICATE_ACTIVE_TASK_CODE = "duplicate_active_task"


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


def _notation_defect_codes(raw, allow_wrap):
    """Interni kodovi mathsafe defekta za JEDNO polje — ograničeno, bez sadržaja.

    ŽIVI A+B (ab-5ac723e): recenzent je za `unsafe_..._notation` dobijao samo
    ime polja, ne i šta je u njemu odbijeno, pa je u 5 od 13 turnova vratio
    ISTO polje (`unchanged=True`). Kodovi (`unknown_mathjax_command:\\ty`,
    `damaged_latex_form`, …) su već sankcionisani log kodovi po CLAUDE.md
    pravilu 7 — nose najviše ime komande, nikad rečenicu sadržaja."""
    try:
        _cleaned, codes = sanitize_and_validate_math_text_with_issues(
            (raw or "").strip(), allow_whole_expression_wrap=allow_wrap)
    except Exception:
        return ""
    return ";".join(codes[:3])[:100]


def _option_id(task, index):
    """Stabilan ID opcije; indeks je rezerva kad paket nema ispravne ID-jeve."""
    try:
        return task.options[index].id
    except (AttributeError, IndexError, TypeError):
        return str(index)


def collect_package_issues(task, contract=None, previous_signature="",
                           difficulty_profile=None):
    """Vrati zatvorenu torku nalaza postojećih validatora nad JEDNIM paketom.

    Prazna torka = nijedan deterministički validator ne može dokazati defekt.
    To NIJE dokaz ispravnosti (semantiku i dalje drži recenzent), nego odsustvo
    dokazane greške — isti princip kao mathcheck i option_equivalence.

    `contract` je semantički ugovor porodice (Faza 4A) ili None. None znači
    „lekcija ga nema“ i tada je rezultat bajt za bajt isti kao prije.

    `difficulty_profile` (Faza F5G) je lekcijski-relativni profil težine ili
    None; provjera dokaza težine koristi iste granice kao recenzentska
    invarijanta i objava. None = globalna rubrika, nepromijenjeno."""
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
    option_texts, unsafe_ids, first_unsafe_option_raw = [], [], None
    for index, option in enumerate(options):
        raw_option = getattr(option, "text", "")
        text, safe = safe_visible_text(raw_option, allow_wrap=True)
        option_texts.append(text)
        if not safe:
            unsafe_ids.append(_option_id(task, index))
            if first_unsafe_option_raw is None:
                first_unsafe_option_raw = raw_option
    if unsafe_ids:
        issues.append(PackageIssue(
            "unsafe_option_notation", option_ids=tuple(unsafe_ids),
            detail=_notation_defect_codes(first_unsafe_option_raw, True)))

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
        issues.append(PackageIssue(
            "unsafe_expected_answer_notation",
            detail=_notation_defect_codes(getattr(task, "expected_answer", ""), True)))
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
    # Nalaz nosi i ono što je server STVARNO pročitao iz uslova. Živi talas F4E
    # (E01): recenzent je dobio goli kod `divisibility_condition_ambiguous`,
    # vratio `correct` i proizveo paket s POTPUNO ISTIM nalazom — nije mogao
    # znati gdje je parser stao. Broj pročitanih djelilaca je serverski izveden
    # podatak, ne sadržaj zadatka, pa smije u dijagnostiku (pravilo 7).
    task_text, task_text_safe = safe_visible_text(getattr(task, "text", ""))
    if task_text_safe and option_texts and all(option_texts) and isinstance(marked_index, int):
        try:
            failure, result = mcq_integrity.publication_failure(
                task_text, option_texts, marked_index, expected)
        except Exception:
            failure, result = "", None
        if failure:
            # Faza 4G: isti poziv sada pokriva i orakl direktnog računa —
            # njegov nalaz nosi serverski IZRAČUNATU vrijednost izraza, ne
            # listu djelilaca. Vrijednost je izveden podatak (pravilo 7).
            if result is not None and hasattr(result, "relation"):
                detail = (f"server derived relation '{result.relation}'"
                          if result.relation else "comparison values unproven")
            elif result is not None and hasattr(result, "computed_value"):
                value = result.computed_value
                detail = (f"server computed value {value:.6g}" if value is not None
                          else "task arithmetic is invalid")
            else:
                read = ",".join(str(divisor)
                                for divisor in getattr(result, "divisors", ()) or ())
                detail = (f"server read divisors: {read}" if read
                          else "server could not read any divisor")
            issues.append(PackageIssue(failure, detail=detail))

    # 4b) ISTI ZADATAK KAO AKTIVNI (produkcijski nalaz: „Daj mi novi zadatak.“
    # je vratio doslovno isti zadatak i iste opcije). Poredi se SERVERSKI izveden
    # kanonski potpis vidljivog paketa — nikad `task_signature` koju model
    # deklariše o sebi. Vidi matbot/tutor/task_identity.py.
    if previous_signature and task_text_safe and option_texts and all(option_texts):
        proposed = task_identity.canonical_signature(task_text, option_texts)
        if task_identity.is_same_task(previous_signature, proposed):
            issues.append(PackageIssue(
                DUPLICATE_ACTIVE_TASK_CODE,
                detail="canonically identical to the active task"))

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
                defects = _notation_defect_codes(raw, allow_wrap)
                issues.append(PackageIssue(
                    code, detail=f"{label} {defects}".strip()))
            continue
        found = find_numeric_inconsistencies(text)
        if found:
            # Recenzent je dobijao goli kod bez vrijednosti, pa nije mogao znati
            # KOJI korak lanca je pao (živi gate 5ac723e, grade9). Vrijednosti su
            # SERVERSKI izračunati brojevi (%.6g iz mathcheck poruke), nikad izraz
            # ili tekst iz sadržaja — isti princip kao broj pročitanih djelilaca.
            detail = f"{label} {found[0].split(':')[0]}"
            values = _MISMATCH_VALUES_RE.findall(found[0])
            expressions = _MISMATCH_EXPR_RE.findall(found[0])
            if len(values) >= 2:
                detail += (f" (server evaluated {values[0]} vs {values[1]},"
                           " expected equal")
                if len(expressions) >= 2:
                    detail += (f"; offending equality "
                               f"'{expressions[0][:60]}' = '{expressions[1][:60]}'")
                detail += ")"
            issues.append(PackageIssue("numeric_inconsistency", detail=detail))

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
            evidence_errors = difficulty_evidence_errors(
                evidence, target_level, profile=difficulty_profile)
        except Exception:
            evidence_errors = ()
        if evidence_errors:
            # Ime profila u detalju: recenzent i logovi vide KOJE su granice
            # bile mjerodavne (lekcijske ili globalne) — kod ostaje isti.
            profile_note = (f"; profile={difficulty_profile.profile_id}"
                            if difficulty_profile is not None else "")
            issues.append(PackageIssue(
                DIFFICULTY_OUTSIDE_TARGET_CODE,
                detail=(f"target Level {target_level}; {','.join(evidence_errors)}; "
                        f"{evidence_diagnostics(evidence)}{profile_note}")))

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
        "only plain $...$ notation and known commands, and change nothing else. The "
        "issue detail names the exact rejected command or defect (for example "
        "`unknown_mathjax_command:\\ty` or `damaged_latex_form`): remove or replace "
        "exactly that construct, do not return the field unchanged. "
        # ŽIVI TALAS F4E (E01, E12): za kodove uskog matematičkog orakla nije
        # postojao nijedan lijek u ovom bloku, pa je recenzent dobijao goli kod
        # i vraćao `correct` s istim nalazom. Uputstvo ne mijenja prag orakla —
        # samo omogućava ispravku u ISTOM drugom pozivu, kako `correct` i služi.
        "For `divisibility_condition_ambiguous` the server could not read the whole "
        "divisibility condition out of the task text, so it cannot decide which option "
        "is correct: REWRITE the question so the required divisors are stated once, "
        "plainly and completely, in the form `djeljiv sa 6 i sa 25` (or `djeljiv sa 2, "
        "3 i 5`). In that same sentence do not restate the condition as a product, do "
        "not put any other number after the divisor list, do not use `ili`, and do not "
        "use a negation such as `nije djeljiv`. "
        # ŽIVI GATE 5ac723e (scenario grade9, lekcija 9. razreda o sistemu
        # bez ijednog rješenja — ni naslov ni ID lekcije se ovdje ne
        # ispisuju, motor ih ne smije nositi): za
        # `numeric_inconsistency` nije postojao NIJEDAN lijek u ovom bloku, pa je
        # recenzent vraćao `correct` s istim nalazom. Lijek pokriva i namjernu
        # kontradikciju: bez markera lažnosti u ISTOJ rečenici server je ne može
        # razlikovati od aritmetičke greške.
        "For `numeric_inconsistency` an equality chain inside $...$ in the named "
        "field is numerically false: recompute every step and rewrite that field so "
        "every shown equality holds. When a false equality is the DELIBERATE point "
        "of the lesson (a contradiction proof, a system with no solutions), keep it "
        "but state in the SAME sentence that it is false — for example `$3=5$, što "
        "nije tačno` — because a bare false equality is rejected as an arithmetic "
        "error. For `division_by_zero_in_task` the visible expression divides by "
        "zero, so no answer exists: REPLACE the task with one whose divisor is "
        "nonzero and recompute every field. For `no_correct_option` none of the four "
        "options satisfies the stated condition: compute values FROM that condition and "
        "replace the options so that exactly one of them satisfies it. For "
        "`multiple_correct_options` keep exactly one satisfying value and replace every "
        "other satisfying option. For `marked_option_math_mismatch` mark the option that "
        "actually satisfies the condition and copy that option's text into "
        "expected_answer. "
        # Produkcijski nalaz: „Daj mi novi zadatak.“ je vratio doslovno isti
        # zadatak i iste opcije. Recenzent mora znati da kozmetika nije dovoljna.
        f"For `{DUPLICATE_ACTIVE_TASK_CODE}` the proposed task is the SAME task the "
        "student already has on screen: the server compares the visible question and "
        "the set of option values, so reordering the options, renaming option IDs, "
        "or rewording the sentence changes NOTHING. REPLACE it with a genuinely "
        "different task for the same lesson skill — different numbers and a different "
        "correct value — and recompute every field for that new task. "
        "Then recompute correct_option_id, "
        "correct_option_index, expected_answer (an exact copy of the marked option's "
        "text), solution, difficulty evidence for the task you actually return, and "
        "task_signature where structural parameters changed. Keep "
        "exactly one correct visible option, and keep the exact selected lesson. Do not "
        "merely reformat an equivalent value, do not merely lower the reported counts, "
        "do not merely relabel the level, "
        "and do not change parts of the task that are already valid. "
        # ŽIVI GATE 2a2a204 (harder_level2): recenzent je na dokazan numerički
        # nalaz vratio `correct` s NEPROMIJENJENIM poljem. Zatvaranje nalaza
        # je serverska invarijanta — recite mu to izričito.
        "THE SERVER RE-RUNS THESE SAME DETERMINISTIC CHECKS ON THE PACKAGE YOU "
        "RETURN: if any issue listed above is still present, your `correct` "
        "decision is rejected automatically and nothing is published — "
        "returning a listed field unchanged can never succeed. When a shown "
        "division leaves a remainder, never write a bare `a : b = q`: either "
        "state the remainder in the same sentence (`$23 : 5 = 4$, ostatak "
        "$3$`) or write the check form `$23 = 5 \cdot 4 + 3$`. If you cannot "
        "confidently repair every listed issue, return `fail_closed`."
    )
    return "\n".join(lines)


def describe_issues(issues):
    """Kratak, ograničen zapis za log — bez ijednog vidljivog sadržaja."""
    return "; ".join(issue.describe() for issue in issues)[:300]
