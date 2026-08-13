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
import math
import re
from dataclasses import dataclass
from fractions import Fraction

from matbot import geometrycheck, lesson_fidelity, mcq_integrity, option_equivalence
from matbot import solution_consistency, task_archetypes
from matbot import practice_policy as practice_policy_module
from matbot import request_fidelity as request_fidelity_module
from matbot import stem_disclosure as stem_disclosure_module
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
SOLUTION_DIVERGENCE_CODE = "solution_answer_divergence"
TASK_TOO_SIMILAR_CODE = "task_too_similar"
CONTRACT_ANSWER_FORM_CODE = "contract_answer_form_violation"
CONTRACT_EQUIVALENCE_CODE = "contract_equivalence_violation"
CONTRACT_SINGLE_IRREDUCIBLE_CODE = "contract_multiple_irreducible_options"
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

# Zadatak ne ispituje izabranu lekciju (Vježbajmo V1, F5K) — vidi
# matbot/semantic_practice.py. Kod je interni (samo logovi/recenzent).
SEMANTIC_FIDELITY_CODE = "semantic_fidelity_violation"
# Tekst se poziva na sliku/crtež koji u tekstualnom UI-ju ne postoji.
FAKE_VISUAL_CODE = "fake_visual_reference"
# Učenikov zahtjev traži baš arhetip koji ugovor OVE lekcije zabranjuje
# (FINAL40 FW-G04). Lekcija je vlasnik vježbe: takav turn se odbija, nikad se
# tiho ne zamjenjuje drugim zadatkom. Vidi matbot/semantic_practice.py.
REQUEST_CONTRACT_CONFLICT_CODE = "request_contract_conflict"


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


# UGOVOR LEKCIJE KAO DETERMINISTIČKA PROVJERA (migracija K1/K3).
# Lekcije s K1/K3 ugovorom su ranije imale vlastiti modelski put čiji je motor
# provjeravao STRUKTURISAN DOKAZ (korake). Brza ruta ne traži dokaz — zato se
# ovdje čuva ono što se DA dokazati iz objavljenog paketa samog. Provjere su
# vođene UGOVORNIM PODACIMA, ne ID-jem lekcije, i preskaču kad ne mogu dokazati.
_CONTRACT_FRACTION_RE = re.compile(r"\\+t?frac\{\s*(-?\d+)\s*\}\{\s*(-?\d+)\s*\}")
# Negacija okreće smisao pitanja („koji NIJE jednak…“), pa provjera jednakosti
# tada ne bi bila dokaz nego pogađanje — u tom slučaju se preskače.
_CONTRACT_NEGATION_RE = re.compile(r"\b(nije|nisu|ne\s+pripada|neta[čc]n)", re.IGNORECASE)


def _contract_fractions(text):
    out = []
    for numerator, denominator in _CONTRACT_FRACTION_RE.findall(text or ""):
        try:
            out.append(Fraction(int(numerator), int(denominator)))
        except (ValueError, ZeroDivisionError):
            continue
    return out


def form_is_the_discriminator(lesson_constraints):
    """True kad ugovor lekcije čini OBLIK zapisa razlikovnim, ne vrijednost.

    ŽIVI NALAZ (migracija K1/K3, lekcija o skraćivanju): za „koji je nesvodivi
    oblik od 12/18?“ distraktori 4/6 i 6/9 SU jednaki po vrijednosti — i to je
    upravo poenta. Opšte pravilo „dvije opcije iste vrijednosti su duplikat“
    tu obara ispravan zadatak: 4 od 8 turnova nije objavljeno i ljestvica
    težine se nije pomjerila. Gdje ugovor traži nesvodiv oblik, jedinstvenost
    se mjeri ZAPISOM, a tačnost oblikom — što `contract_package_issues` i
    `_single_irreducible_option_issue` dokazuju."""
    representation = getattr(lesson_constraints, "representation_constraints", {}) or {}
    return (bool(getattr(lesson_constraints, "has_contract", False))
            and representation.get("answer_form") == "irreducible")


def contract_package_issues(task, lesson_constraints):
    """Nalazi koje UGOVOR lekcije dokazuje nad objavljenim paketom.

    Čita samo deklarativna polja (`representation_constraints`,
    `task_archetypes`). Bez ugovora vraća prazno i ponašanje je nepromijenjeno.
    „Ne mogu dokazati“ NIKAD ne znači „prošlo je“ — takav slučaj se preskače."""
    if lesson_constraints is None or not getattr(lesson_constraints, "has_contract", False):
        return ()
    marked_index = getattr(task, "correct_option_index", None)
    options = list(getattr(task, "options", None) or ())
    if not isinstance(marked_index, int) or not 0 <= marked_index < len(options):
        return ()
    marked_text = str(getattr(options[marked_index], "text", "") or "")
    marked = _contract_fractions(marked_text)
    if len(marked) != 1:
        return ()          # označen odgovor nije jedan razlomak → nedokazivo
    issues = []
    representation = getattr(lesson_constraints, "representation_constraints", {}) or {}
    if representation.get("answer_form") == "irreducible":
        # NESVODIV = brojnik i nazivnik NAPISANI bez zajedničkog djelioca.
        # Gleda se zapis, ne vrijednost: 4/6 i 2/3 imaju istu vrijednost, a samo
        # jedan je skraćen do kraja — a upravo to lekcija ispituje.
        numerator, denominator = _CONTRACT_FRACTION_RE.search(marked_text).groups()
        if math.gcd(abs(int(numerator)), abs(int(denominator))) > 1:
            issues.append(PackageIssue(
                CONTRACT_ANSWER_FORM_CODE,
                option_ids=(_option_id(task, marked_index),),
                detail=("ugovor lekcije traži NESVODIV razlomak, a označena "
                        f"opcija je {numerator}/{denominator}; skrati je do kraja "
                        "i uskladi expected_answer i solution")))
        # SAMO JEDAN TAČAN ODGOVOR. Kad je oblik razlikovan, tačna je opcija koja
        # je i JEDNAKA izvornoj vrijednosti i NESVODIVA. Nesvodiv razlomak DRUGE
        # vrijednosti je sasvim valjan distraktor, pa se ne broji — inače bi
        # provjera obarala ispravne zadatke.
        source_values = _contract_fractions(str(getattr(task, "text", "") or ""))
        # Bez izvornog razlomka u tekstu ne može se dokazati KOJA je opcija
        # tačna, pa se ova provjera preskače umjesto da pogađa.
        irreducible = None if not source_values else []
        for option in (options if source_values else ()):
            text = str(getattr(option, "text", "") or "")
            found = _contract_fractions(text)
            if len(found) != 1:
                continue
            numerator, denominator = _CONTRACT_FRACTION_RE.search(text).groups()
            if math.gcd(abs(int(numerator)), abs(int(denominator))) != 1:
                continue
            if source_values and found[0] not in source_values:
                continue
            irreducible.append(text)
        if irreducible is not None and len(irreducible) > 1:
            issues.append(PackageIssue(
                CONTRACT_SINGLE_IRREDUCIBLE_CODE,
                detail=("ugovor traži nesvodiv oblik, pa SAMO JEDNA opcija smije "
                        f"biti nesvodiva; nesvodivih ima {len(irreducible)}")))
    archetypes = tuple(getattr(lesson_constraints, "task_archetypes", ()) or ())
    if "identify_equivalent" in archetypes:
        text = str(getattr(task, "text", "") or "")
        if not _CONTRACT_NEGATION_RE.search(text):
            in_text = _contract_fractions(text)
            if in_text and marked[0] not in in_text:
                issues.append(PackageIssue(
                    CONTRACT_EQUIVALENCE_CODE,
                    option_ids=(_option_id(task, marked_index),),
                    detail=("arhetip `identify_equivalent`: označena opcija mora "
                            "imati ISTU vrijednost kao razlomak iz teksta zadatka")))
    return tuple(issues)


def structural_repetition_issue(task, recent_structures, intent, lesson_id="",
                                supported_archetypes=()):
    """Nalaz kad je „nov“ zadatak ista VJEŽBA s drugim brojevima.

    ZAHTJEV IZ PRODUKCIJE: promjena brojeva, imena ili redoslijeda opcija nije
    nov zadatak. Provjera se pokreće SAMO na izričit zahtjev za novim zadatkom
    (`next_task`) — na „teže“/„lakše“ struktura smije ostati ista, jer se tamo
    mijenja nivo, a ne vrsta vježbe.

    Vraća PackageIssue ili None. Nepoznata struktura = None (nedokazivo)."""
    if intent != "next_task" or not recent_structures:
        return None
    option_texts = [str(getattr(option, "text", "") or "")
                    for option in (getattr(task, "options", None) or ())]
    structure = task_identity.structural_signature(
        str(getattr(task, "text", "") or ""), option_texts)
    if not structure:
        return None
    for entry in recent_structures:
        if isinstance(entry, dict) and entry.get("signature") == structure:
            return PackageIssue(
                TASK_TOO_SIMILAR_CODE,
                detail=("isti oblik zadatka kao nedavni: promijenjeni su samo "
                        "brojevi/imena. Promijeni VRSTU vježbe, ne vrijednosti"))

    # ARHETIP JE JAČA MJERA OD ŠABLONA (nalaz: 12 različitih rečenica, a sve
    # ista vježba — kupovina i kusur). Ponavljanje oblika je nalaz SAMO kad
    # lekcija dokazano ima drugi legitiman oblik; uska lekcija se ne tjera da
    # izmišlja.
    if len(supported_archetypes or ()) >= 2:
        archetype = task_archetypes.classify(
            str(getattr(task, "text", "") or ""), option_texts)
        previous = [entry.get("archetype") for entry in recent_structures
                    if isinstance(entry, dict)]
        if archetype and previous and previous[-1] == archetype:
            alternatives = [a for a in supported_archetypes if a != archetype]
            if alternatives:
                return PackageIssue(
                    TASK_TOO_SIMILAR_CODE,
                    detail=(f"isti ARHETIP kao prethodni zadatak ({archetype}); "
                            "lekcija podržava i: "
                            + ", ".join(alternatives[:4])))
    return None


def collect_package_issues(task, contract=None, previous_signature="",
                           difficulty_profile=None, practice_contract=None,
                           practice_policy=None, student_message="",
                           lesson_constraints=None):
    """Vrati zatvorenu torku nalaza postojećih validatora nad JEDNIM paketom.

    Prazna torka = nijedan deterministički validator ne može dokazati defekt.
    To NIJE dokaz ispravnosti (semantiku i dalje drži recenzent), nego odsustvo
    dokazane greške — isti princip kao mathcheck i option_equivalence.

    `contract` je semantički ugovor porodice (Faza 4A) ili None. None znači
    „lekcija ga nema“ i tada je rezultat bajt za bajt isti kao prije.

    `difficulty_profile` (Faza F5G) je lekcijski-relativni profil težine ili
    None; provjera dokaza težine koristi iste granice kao recenzentska
    invarijanta i objava. None = globalna rubrika, nepromijenjeno.

    `practice_contract` (Vježbajmo V1, F5K) je semantički ugovor VJEŽBE
    lekcije ili None: dokazan prekršaj (zadatak ne radi ono što lekcija
    ispituje — npr. grafička lekcija bez ijednog grafičkog sadržaja) je
    blokirajući nalaz. Lažno pozivanje na nepostojeću sliku je globalan
    nalaz i bez ugovora.

    `practice_policy` (PP-1) je razriješena pedagoška politika lekcije ili
    None: kurikularna metoda (npr. 6. razred bez prebacivanja), vidljivi
    brojevni domen i granica naprednih operacija. Nalaz ide recenzentu da ga
    popravi u ISTOM drugom pozivu — objava iste provjere svakako ponavlja.

    `student_message` (Task 3) je poruka učenika iz TEKUĆEG turna ili prazno:
    kad nosi IZRIČIT matematički uslov (domen, relaciju, vrstu zadatka),
    objavljen zadatak ga mora sačuvati. Prazno ili nedokazivo = provjera se
    preskače. Nema ljepljivog stanja: prethodni turnovi se ne čitaju."""
    if task is None:
        return ()
    issues = []
    # 0) UGOVOR LEKCIJE (migracija K1/K3): ono što je ranije dokazivao zaseban
    #    ugovorni motor, a dokazivo je i iz samog objavljenog paketa.
    issues.extend(contract_package_issues(task, lesson_constraints))

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
        form_discriminates = form_is_the_discriminator(lesson_constraints)
        for i, j, kind in option_equivalence.find_equivalent_option_pairs_with_types(
                option_texts):
            if form_discriminates and option_texts[i].strip() != option_texts[j].strip():
                # Ista vrijednost, RAZLIČIT zapis — u ovoj lekciji to je
                # namjeran distraktor, ne duplikat. Doslovni duplikat i dalje
                # pada gore (`duplicate_option_text`).
                continue
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

    # 3a) OZNAČEN ODGOVOR MORA SLIJEDITI IZ VLASTITOG RJEŠENJA (produkcijski
    #     nalaz, ručni QA): objavljen paket je računao „ukupno 10,50; kusur
    #     9,50“, a označio 11,50 — učenik je izabrao tačnih 9,50 i dobio
    #     netačno. Provjera je uska i preskače kad ne može dokazati; mjereno
    #     nad 1056 determinističkih paketa: nijedna lažna uzbuna.
    divergence_code, divergence_detail = solution_consistency.divergence(
        marked_text, getattr(task, "solution", ""))
    if divergence_code:
        issues.append(PackageIssue(
            divergence_code,
            option_ids=(_option_id(task, marked_index),) if isinstance(marked_index, int) else (),
            detail=divergence_detail))

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
            if result is not None and hasattr(result, "solution_display"):
                # Orakl rješavanja (PP-1 LIVE-150, F008): recenzent mora vidjeti
                # SERVERSKI izveden skup rješenja, ne goli kod — isti razlog kao
                # F4E nalaz iznad (goli kod → recenzent vrati isti defekt).
                detail = (f"server solved: {result.solution_display}"
                          if result.solution_display else "relation unproven")
            elif result is not None and hasattr(result, "relation"):
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

    # 4a2) TEKST ZADATKA SAM OTKRIVA OZNAČENU OPCIJU (FINAL40 FW-G03).
    # Zadatak može biti matematički tačan, s četiri različite opcije i tačno
    # jednom tačnom — a da učenik ne rasuđuje nego prepisuje, jer deklarativni
    # dio teksta tvrdi BAŠ onu osobinu koju upitna rečenica pita, i to BAŠ za
    # označeni entitet. Nijedna postojeća kapija to nije mjerila: zaštita od
    # curenja gleda ODGOVOR tutora i izričito izuzima sve što već stoji u
    # tekstu zadatka. Vidi matbot/stem_disclosure.py.
    if task_text_safe and option_texts and all(option_texts) and isinstance(
            marked_index, int):
        disclosure = stem_disclosure_module.stem_answer_disclosure(
            task_text, option_texts, marked_index)
        if disclosure:
            issues.append(PackageIssue(
                stem_disclosure_module.STEM_ANSWER_DISCLOSURE_CODE,
                option_ids=(_option_id(task, marked_index),),
                detail=disclosure))

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

    # 4c) VJERNOST IZRIČITOM ZAHTJEVU UČENIKA (Task 3, živi DISC A009/A010/
    # A020/A023). Učenik postavi jednoznačan uslov (domen, relaciju, vrstu),
    # Tutor ga tiho promijeni, a paket ostane iznutra ispravan — pa ga nijedna
    # postojeća kapija ne vidi. Najteži slučaj: traženo N={1,2,3,...},
    # objavljeno Z, i označeni {0} je tačan nad Z a nad N skup je PRAZAN.
    # Poredi se ISTOM zatvorenom gramatikom kojom se čita i sam zadatak
    # (matbot/request_fidelity.py); nedokaziv zahtjev se preskače.
    if task_text_safe and student_message:
        for detail in request_fidelity_module.request_fidelity_failures(
                student_message, task_text):
            issues.append(PackageIssue(
                request_fidelity_module.REQUEST_FIDELITY_CODE, detail=detail))

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
    # 6b) SEMANTIČKA VJERNOST LEKCIJI (Vježbajmo V1, F5K — audit: 14 P1)
    #     Ugovor vježbe se provjerava nad VIDLJIVIM tekstom i opcijama —
    #     matematički ispravan zadatak POGREŠNE lekcije je dokazan defekt.
    #     Lažna slika je globalna zabrana i bez ugovora.
    from matbot import semantic_practice as _semantic_practice

    raw_task_text = getattr(task, "text", "") or ""
    if _semantic_practice.fake_visual_reference(raw_task_text):
        issues.append(PackageIssue(FAKE_VISUAL_CODE,
                                   detail="task text references an absent picture"))

    # 6b2) PROTIVRJEČNA GEOMETRIJSKA PREMISA (ciljani blokator FW-G03).
    # Objava ovaj nalaz ionako ponavlja kroz `_reject_if_geometry_invalid`, ali
    # tamo je prekasno da ga IKO popravi: turn je već potrošio oba poziva.
    # Ovdje ide recenzentu kao serverska činjenica, pa ispravka staje u ISTI
    # drugi poziv — tačno ono zbog čega `correct` postoji. Zove se ISTA čista
    # funkcija koju objava zove (matbot/geometrycheck.py); nema drugog praga i
    # nema kopije detektora. Ostale geometrijske provjere (konvencija simbola)
    # se OVDJE namjerno ne pokreću — one zavise od scope-a lekcije koji ovaj
    # motor ne poznaje, a ova protivrječnost je egzaktna i bez njega.
    for reason in geometrycheck.geometry_relation_contradictions(raw_task_text):
        issues.append(PackageIssue(
            geometrycheck.GEOMETRY_RELATION_CONTRADICTION, detail=reason))
    if practice_contract is not None:
        options_joined = " ".join(
            getattr(option, "text", "") for option in
            (getattr(task, "options", None) or ()))
        fidelity = _semantic_practice.fidelity_failures(
            practice_contract, raw_task_text, options_joined)
        if fidelity:
            issues.append(PackageIssue(
                SEMANTIC_FIDELITY_CODE,
                detail=(f"{practice_contract.requirement_type}: "
                        + ",".join(fidelity))))
        # 6b3) ZAHTJEV TRAŽI ONO ŠTO UGOVOR LEKCIJE ZABRANJUJE (FINAL40 FW-G04).
        # Lekcija je vlasnik vježbe (`request_alignment: lesson_overrides`), pa
        # takav turn mora biti ODBIJEN — nikad tiho zamijenjen nepovezanim
        # zadatkom, kako se uživo dogodilo. Zove se ISTI zatvoreni provjerivač
        # osobina, samo nad porukom; nedokaziva poruka se preskače.
        for conflict in _semantic_practice.request_conflicts(
                practice_contract, student_message):
            issues.append(PackageIssue(
                REQUEST_CONTRACT_CONFLICT_CODE,
                detail=f"{practice_contract.requirement_type}: {conflict}"))

    # 6c) POLITIKA PP-1 (audit ovlašćenja pravila): metoda razreda, vidljivi
    #     brojevni domen, napredne operacije — isti detektori koje objava
    #     pokreće; ovdje SAMO kao nalaz da bi ispravka stala u drugi poziv.
    if practice_policy is not None:
        policy_surfaces = [("task_text", raw_task_text),
                           ("solution", getattr(task, "solution", "") or "")]
        policy_surfaces.extend(
            ("option", getattr(option, "text", "") or "")
            for option in (getattr(task, "options", None) or ()))
        seen_policy_codes = set()
        for label, surface in policy_surfaces:
            for code in practice_policy_module.text_policy_failures(
                    practice_policy, surface):
                if code not in seen_policy_codes:
                    seen_policy_codes.add(code)
                    issues.append(PackageIssue(code, detail=label))

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


# CILJNI NIVO TEŽINE JE SERVERSKA ČINJENICA (živi nalaz brze jednopozivne
# rute): nacrt je tri puta deklarisao nivo 2 dok je server tražio 3, pa je paket
# prolazio preflight i padao TEK u objavi — bez ijedne prilike za ispravku.
# Nalaz je popravljiv: recenzent u ISTOM drugom pozivu smije vratiti paket s
# ispravnim ciljem, pa ovaj kod ide istim kanalom kao svaki drugi nalaz.
DIFFICULTY_TARGET_MISMATCH_CODE = "difficulty_target_mismatch"


def difficulty_target_issue(task, target_level):
    """Nalaz kad nacrt deklariše DRUGI ciljni nivo od serverskog, ili None.

    Provjerava se ISTA jednakost koju objava (`validate_task_package`) ionako
    zahtijeva — ovdje samo RANIJE, da bi bila popravljiva."""
    if task is None or target_level is None:
        return None
    declared = getattr(task, "target_difficulty_level", None)
    if declared == target_level:
        return None
    return PackageIssue(
        DIFFICULTY_TARGET_MISMATCH_CODE,
        detail=f"draft declared level {declared}, server target is {target_level}")


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
        # ŽIVI NALAZ BRZE RUTE: nacrt je deklarisao nivo 2 dok je
        # server tražio 3. Cilj je SERVERSKA činjenica — recenzent je ne bira,
        # nego paket dovodi u sklad s njom.
        f"For `{DIFFICULTY_TARGET_MISMATCH_CODE}` the draft declared a different "
        "target difficulty level than the server requires. THE SERVER TARGET IN "
        "THE ISSUE DETAIL IS AUTHORITATIVE: return a complete package whose "
        "`target_difficulty_level` is exactly that number and whose task and "
        "difficulty evidence genuinely belong at that level — never lower the "
        "target to match the draft. "
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
        # VJEŽBAJMO V1 (F5K): semantička vjernost lekciji ima svoj lijek —
        # ZAMJENSKI zadatak koji stvarno ispituje lekciju, nikad prepričan isti.
        f"For `{SEMANTIC_FIDELITY_CODE}` the task is mathematically fine but does "
        "NOT exercise the selected lesson (the detail names the missing/forbidden "
        "semantic feature): REPLACE THE TASK with one that genuinely performs the "
        "lesson's required action described in the lesson's semantic contract "
        "block — never just reword the same task. "
        # ŽIVI CILJANI BLOKATOR FW-G03: recept mora reći da se mijenja PREMISA,
        # a ne mjere ni odgovor — inače recenzent „popravi“ uglove i objavi
        # isti nemogući raspored.
        f"For `{geometrycheck.GEOMETRY_RELATION_CONTRADICTION}` the task states "
        "a geometric configuration that cannot exist. The detail names the "
        f"class: `{geometrycheck.COINCIDENT_RAYS_NONZERO_ANGLE}` means the text "
        "says one ray LIES ON (or coincides with) another ray from the same "
        "vertex, and then gives a NONZERO angle between exactly those two rays. "
        "Two rays that lie on each other are the SAME ray, so the angle between "
        "them is exactly zero — the premise is false, no matter how correct the "
        "marked answer is. KEEP the candidate rays, KEEP every given angle "
        "measure and KEEP the question: fix only the FALSE PREMISE. Rays drawn "
        "from the vertex INSIDE the angle are what such a task needs, so write "
        "that instead — for example that the rays start at the vertex and lie "
        "inside the angle, with their measures taken from one arm. Never write "
        "that a ray lies ON an arm while also giving it a nonzero angle to that "
        "arm, and do not change the marked option to repair this. If you cannot "
        "state a configuration that really exists, return `fail_closed`. "
        f"For `{FAKE_VISUAL_CODE}` the text references a picture that does not "
        "exist in this text-only UI: rewrite the task so every needed object is "
        "stated in the text itself (coordinates, table, list of faces), or replace "
        "the task. "
        # ŽIVI FINAL40 FW-G04: recenzent je zahtjev koji lekcija ZABRANJUJE
        # tiho zamijenio nepovezanim zadatkom druge vještine i objavio ga.
        # Jedini ispravan ishod je odbijanje — zato recept NEMA popravku.
        f"For `{REQUEST_CONTRACT_CONFLICT_CODE}` the STUDENT'S OWN REQUEST asks "
        "for exactly the task archetype this lesson's contract forbids. This is "
        "NOT repairable: the lesson owns the exercise, so the request cannot be "
        "served here. Do NOT silently substitute a different task to satisfy the "
        "server — publishing some other task instead of the requested one is the "
        "defect this finding exists to stop. Return `fail_closed`. "
        # Task 3 (živi DISC A009/A010/A020/A023): recept MORA imenovati tačan
        # traženi domen/relaciju. Generičko „neka bude relevantno“ je upravo
        # ono što je pustilo N→Z drift da se objavi.
        f"For `{request_fidelity_module.REQUEST_FIDELITY_CODE}` the task silently "
        "changed an explicit mathematical constraint the student stated in THIS "
        "message; the issue detail names the requested value and the one the task "
        "actually used. For `domain_mismatch` REWRITE the task so it solves in "
        "EXACTLY the requested number set and SAYS SO in the task text, then "
        "recompute every option for that set: N is {1,2,3,...} and EXCLUDES 0, N0 "
        "is {0,1,2,...}, and Z, Q, R are all different from both — never swap one "
        "for another and never drop the domain sentence. For `relation_mismatch` "
        "solve the EXACT equation or inequality the student wrote (an equivalent "
        "rearrangement with the identical solution set is fine, a different "
        "solution set is not) and recompute every option. "
        # Živi FINAL-40 lažni prolaz: „Na obje strane originalne nejednačine
        # dodan je isti nenulti cijeli broj 2. Riješite dobijenu nejednačinu…“
        # bez ijedne nejednačine u tekstu. Recept mora tražiti DOPISIVANJE
        # relacije, ne preformulaciju rečenice.
        f"For `{request_fidelity_module.MISSING_REQUESTED_RELATION}` the task "
        "text points at a relation (`originalna`/`dobijena`/`nastala` "
        "jednačina or nejednačina) that it NEVER writes down, so the student "
        "has nothing to solve and the task is not self-contained: WRITE THE "
        "COMPLETE resulting relation into the task text itself, inside $...$, "
        "with every side fully written out — a task that says `Riješi dobijenu "
        "nejednačinu` while showing no relation can never be published. Keep "
        "the solution set the student asked for unchanged, then recompute "
        "correct_option_id, correct_option_index, expected_answer and every "
        "option for the relation you actually wrote. "
        # Živi ciljani nalaz: objavljeno „…dodajemo 2 lijevo i 4 desno pa
        # dobijamo x+2>7“ uz traženo x>3. Recept mora reći ZAŠTO je to drugi
        # zadatak, inače recenzent „popravi“ opcije umjesto relacije.
        f"For `{request_fidelity_module.TRANSFORMED_RELATION_MISMATCH}` the task "
        "itself announces a transformed/resulting relation (`dobijamo`, "
        "`dobijena`, `nastala`, `preoblikovana`…), but that relation does NOT "
        "have the same solution set as the one the student asked for, so the "
        "student is being asked to solve a DIFFERENT problem: the student asked "
        "for an EQUIVALENT reformulation, and adding or subtracting DIFFERENT "
        "amounts on the two sides changes the solution set. REWRITE the "
        "announced relation so its solution set is EXACTLY the requested one — "
        "for the requested $x>3$, adding $2$ to both sides gives $x+2>5$, which "
        "is valid, while $x+2>7$ is a different inequality and is not. Also fix "
        "the sentence that describes the step so it matches what you actually "
        "wrote, then recompute correct_option_id, correct_option_index, "
        "expected_answer and every option for the corrected relation. If the "
        "student explicitly asked for two DIFFERENT amounts on the two sides, "
        "that request cannot be honoured as an equivalent reformulation: return "
        "`fail_closed`. "
        # Živi FINAL-40 lažni prolaz: „Riješi nejednačinu DOBIJENU dodavanjem
        # iste nenulte cijele konstante na obje strane originalne relacije
        # $x>3$“ — tvrdi dobijenu relaciju, a napiše samo polaznu. Recept mora
        # reći da je prepisivanje originala neispunjen zahtjev, inače recenzent
        # „popravi“ opcije (koje su ionako tačne za $x>3$) i ništa se ne mijenja.
        f"For `{request_fidelity_module.MISSING_DISTINCT_TRANSFORMED_RELATION}` "
        "the student explicitly asked for a DIFFERENT but EQUIVALENT written "
        "form of the relation, and your task announces a transformed/resulting "
        "relation (`dobijena`, `nastala`, `preoblikovana`…) while the ONLY "
        "relation it writes down is the student's original one: merely copying "
        "the original relation never satisfies that request, no matter how "
        "correct the marked answer is. WRITE THE TRANSFORMED RELATION ITSELF "
        "into the task text, inside $...$, with both sides fully written out — "
        "for the requested $x>3$, adding $2$ to both sides gives $x+2>5$, which "
        "is a valid reformulation, while repeating $x>3$ is not. Its solution "
        "set must stay EXACTLY equal to the requested one, then recompute "
        "correct_option_id, correct_option_index, expected_answer and every "
        "option for the relation you actually wrote. If no safe equivalent "
        "reformulation can be produced, return `fail_closed`. "
        # Živi ciljani recheck (T3): objavljeno „…dodavanjem 2 na obje strane
        # … dobijenu relaciju $x+2>7$“ uz polaznu $x>3$. Recept mora reći da je
        # sama TVRDNJA netačna — inače recenzent „popravi“ opcije za $x+2>7$.
        f"For `{request_fidelity_module.TRANSFORMED_RELATION_NOT_EQUIVALENT}` the "
        "task text contradicts ITSELF: it states an original relation, narrates a "
        "step performed on it (adding, subtracting, multiplying or dividing both "
        "sides), and then writes a resulting relation whose solution set is NOT "
        "the same. The narrated step is therefore false mathematics and must "
        "never be shown to a student as a correct derivation. Either RECOMPUTE "
        "the resulting relation so it really follows from the original — the same "
        "amount on BOTH sides, so $x>3$ with $+2$ gives $x+2>5$, never $x+2>7$ — "
        "or change the stated original so the written result really does follow. "
        "Then recompute correct_option_id, correct_option_index, expected_answer "
        "and every option for the corrected relation. Never keep both the "
        "original and a result that does not follow from it, and if you cannot "
        "make the derivation true, return `fail_closed`. "
        "For `task_type_mismatch` "
        "keep the requested kind: an inequality request must stay an inequality "
        "and an equation request must stay an equation. If honouring the request "
        "would break the selected lesson's semantic contract, return "
        "`fail_closed` — the lesson always wins over the request. "
        # PP-1 (audit ovlašćenja pravila): kurikularna metoda, vidljivi domen
        # i granica naprednih operacija imaju svoj lijek — bez njega bi
        # recenzent vraćao `correct` s istim nalazom (obrazac F4E E01).
        f"For `{practice_policy_module.FORBIDDEN_METHOD_CODE}` the named field "
        "teaches a solving method this grade's curriculum forbids (moving terms "
        "across the equals sign / operating on both sides): rewrite the "
        "explanation using the unknown-member relations from the grade rules "
        "block above (e.g. nepoznati sabirak = zbir minus poznati sabirak), and "
        "never use words like `prebaci` or `obje strane`. "
        f"For `{practice_policy_module.VISIBLE_DOMAIN_CODE}` the named field "
        "shows a negative number in a lesson whose number domain has no "
        "negatives: choose new values so every VISIBLE number (task, options, "
        "solution steps) stays in the lesson's domain, and recompute every "
        "field. "
        f"For `{practice_policy_module.ADVANCED_SCOPE_CODE}` the named field "
        "introduces an operation outside the primary-school curriculum "
        "(sin/cos/tg/log): replace the task or rewrite the field using only "
        "methods this lesson teaches. "
        # ŽIVI FINAL40 FW-G06: bez izričitog recepta recenzent „popravi“
        # brojeve i vrati paket s ISTIM nalazom (obrazac F4E E01). Sam recept
        # živi uz granicu koju opisuje (matbot/practice_policy.py) — ovaj motor
        # po svojoj arhitektonskoj kapiji ne smije nositi konkretan zapis.
        + practice_policy_module.grade_capability_repair_text() +
        # ŽIVI FINAL40 FW-G03: recenzent je nacrt s ovim defektom proglasio
        # `correct` i sam ga objavio. Recept mora reći da se briše TVRDNJA,
        # ne entitet — inače recenzent ukloni BD iz teksta i zadatak postane
        # nerješiv.
        f"For `{stem_disclosure_module.STEM_ANSWER_DISCLOSURE_CODE}` the task "
        "text itself already states, about the marked option, the very "
        "property the question asks the student to determine — including when "
        "a point's stated angular position directly identifies the option ray "
        "from the named vertex — so the student only has to copy it back. "
        "Do NOT delete the entity or candidate option from the task: it "
        "is legitimate data and the task needs it. DELETE THE SENTENCE OR "
        "CLAUSE THAT ASSERTS THE DECISIVE PROPERTY (and the clause that denies "
        "it for another option, which discloses the answer just as much), and "
        "instead give the student the neutral data from which that property "
        "FOLLOWS — measures, positions, coordinates, a definition to apply. "
        "Keep the question, candidates and marked answer unchanged where you "
        "can; never silently change the marked answer merely to evade this "
        "finding. Then "
        "recompute correct_option_id, correct_option_index and expected_answer "
        "for the rewritten text. If the property cannot be made derivable "
        "without stating it, return `fail_closed`. "
        # ŽIVI FINAL40 FW-F03/FW-F06: isti kod, ali otkrivanje je u IMENOVANJU
        # klase („Data je FUNKCIJA … predstavlja li ovo FUNKCIJU?"). Recept
        # mora imenovati baš tu radnju, jer „obriši tvrdnju" recenzent ovdje
        # čita kao „obriši podatke".
        "When the detail says `semantic-class assertion`, the stem NAMES the "
        "object as the very class the question asks about (for example it says "
        "a function is given and then asks whether the data represent a "
        "function), and the marked option is the affirmative one. Rename the "
        "object with a NEUTRAL word that does not decide the question — the "
        "given data, the set of pairs, the table, the relation — and keep every "
        "number and the question exactly as they are. Never keep the class word "
        "in the declarative part while asking for that same class. "
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
        # Targeted live verifikacija: {-5}/{-1} objavljeni kao „rješenje“
        # intervala — orakl sada zna jednočlane skupove, a NEPOZNAT zapis opcije
        # pada zatvoreno umjesto da ugasi provjeru. Recenzentu treba recept.
        f"For `{mcq_integrity.UNVERIFIABLE_SOLUTION_OPTION_CODE}` at least one option "
        "is not written in a verifiable answer form for this solve task. Do NOT "
        "reach for a different exotic notation — REBUILD every option using the "
        "SIMPLEST supported form: a relation such as `x<5` or `x\\ge 4`, a chain "
        "such as `-2<x<0`, an interval such as `(3,\\infty)` or `[4,\\infty)`, a "
        "set-builder that names its domain EXPLICITLY such as "
        "`\\{x\\in\\mathbb{Q} \\mid x>3\\}`, or a plain value / singleton set like "
        "`3` / `\\{3\\}` for an equation. A set-builder WITHOUT a domain "
        "(`\\{x \\mid x>3\\}`), an enumeration without braces, a decorated option "
        "(`\\subset`), two conditions in one option, or an answer written in words "
        "can never be read. For an infinite solution set over Q or R use a "
        "relation, an interval or a domain-explicit set-builder; over Z, N or N0 a "
        "simple relation such as `x\\ge 4` is preferred and the integer "
        "enumeration `\\{4,5,6,...\\}` is also accepted. The server detail names the "
        "solution it derived, so make exactly one option mean EXACTLY that set, "
        "keep the other three meaning different sets, and recompute "
        "correct_option_id, correct_option_index and expected_answer. "
        # Živi ciljani recheck (T1): lekcija o racionalnim brojevima, rješenje
        # $x>3$, a sve četiri opcije cjelobrojna nabrajanja; označeno
        # $\{4,5,6,\dots\}$ — nedostaje npr. $7/2$. Recept mora imenovati
        # DOMENSKU semantiku, inače recenzent samo pomjeri granicu nabrajanja.
        f"For `{mcq_integrity.DISCRETE_OPTIONS_FOR_CONTINUOUS_SOLUTION_CODE}` the "
        "solution set of this task is CONTINUOUS (the lesson works over the "
        "rational or real numbers, or the task declares no integer domain at "
        "all), but the options offer only integer enumerations such as "
        "`{4,5,6,...}`. An integer list can NEVER be the complete solution set "
        "of a continuous inequality: between $3$ and $4$ there are rationals "
        "like $7/2$ that satisfy it and appear in no enumeration, so no option "
        "is correct and shifting the first listed integer fixes nothing. Write "
        "the marked option as the COMPLETE set over the task's own domain — a "
        "relation such as `x>3`, an interval such as `(3,\\infty)`, or a "
        "set-builder such as `\\{x\\in Q \\mid x>3\\}` — keep the distractors in "
        "that same style, and recompute correct_option_id, correct_option_index "
        "and expected_answer. Use an integer enumeration ONLY when the task "
        "itself restricts the unknown to whole numbers and SAYS SO; never change "
        "the lesson's domain just to make an enumeration fit. "
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
