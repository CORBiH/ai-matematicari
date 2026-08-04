"""Jedan generički cjevovod PRIPREME zadatka za SVE lekcije s uključenim ugovorom.

    razriješi lekciju → učitaj ugovor → sastavi PLAN (učenikova izričita molba
    kroz zatvorenu intent tabelu, inače rotacija) → SERVER GENERIŠE kostur
    (operandi, istina, distraktori, označeni indeks) → samoprovjera istim
    deterministima → server renderuje SAV vidljivi matematički sadržaj
    → JEDAN AI poziv (samo bosanska proza) → objava kroz copy-on-write stanje

SMJER JE OBRNUT u odnosu na raniju fazu (Live96, pozivi 503-598): model više NE
izmišlja matematiku niti je prepričava u strukturisani dokaz — server je
konstruiše IZ UGOVORA, pa su tačnost, vjernost lekciji i označeni odgovor tačni
po konstrukciji, a cijela klasa „reprezentacijskih“ odbijanja (pogrešna vrsta
dokaza, rupa koju dokaz ne dozvoljava, sukob molbe i dodijeljenog oblika)
nestaje. Ranija validacija (constraints/difficulty/verifiers) NIJE obrisana:
ista funkcija sada provjerava serversku konstrukciju (generator.self_verify).

SVAKA faza vraća EKSPLICITNO `applicable/engaged/valid`. Prazna lista problema
nikad ne znači „provjereno“ (D35T-2). Neuspjeh UKLJUČENOG ugovora NIKAD ne
prelazi na legacy: rezultat je odbijanje bez mutacije stanja — i, pošto se
kostur pravi PRIJE jedinog AI poziva, bez ijednog potrošenog poziva.
"""
import re
from dataclasses import dataclass, field
from fractions import Fraction

from matbot.contracts import generator, intent
from matbot.mathsafe import sanitize_and_validate_math_text


@dataclass(frozen=True)
class StageResult:
    stage: str
    applicable: bool
    engaged: bool
    valid: bool
    code: str = "ok"
    details: dict = field(default_factory=dict)

    @property
    def passed(self):
        return (not self.applicable) or (self.engaged and self.valid)


@dataclass(frozen=True)
class PreparedTask:
    """Ishod pripreme: ili verifikovan kostur, ili odbijanje s fazom pada."""
    ok: bool
    code: str
    stages: tuple
    skeleton: object = None    # generator.TaskSkeleton kad je ok
    plan: object = None

    @property
    def failed_stage(self):
        for stage in self.stages:
            if not stage.passed:
                return stage
        return None


@dataclass(frozen=True)
class GenerationPlan:
    """Server-owned plan JEDNOG turna: koji arhetip i zašto.

    `source`: "student_request" (izričita molba kroz intent tabelu),
    "rotation" (normalno napredovanje) ili "retry" (ponovni pokušaj iste
    vještine). `requested` je uvijek zabilježen radi loga, i kad nije uslišen."""
    archetype_id: str
    source: str
    requested: str = ""


def _ok(stage, **details):
    return StageResult(stage, True, True, True, "ok", details)


def _reject(stage, code, engaged=True, **details):
    return StageResult(stage, True, engaged, False, code, details)


def select_archetype(contract, recently_used=(), current="", retry_required=False,
                     difficulty_request=""):
    """Rotacija arhetipa — nepromijenjena serverska logika napredovanja.

    Kod ponovnog pokušaja i kod zahtjeva za težim/lakšim ostaje se na istoj
    vještini; težinu mijenja difficulty.target_levels, ne izbor arhetipa."""
    allowed = contract.effective_archetypes
    if not allowed:
        return ""
    if retry_required and current in allowed:
        return current
    if (difficulty_request or "").strip().lower() in ("harder", "easier"):
        return allowed[0]
    if contract.progression_policy == "primary_first":
        return allowed[0]
    if contract.progression_policy == "retry_same" and current in allowed:
        return current

    history = list(recently_used)
    candidates = [item for item in allowed if item != current] or list(allowed)

    def rank(archetype_id):
        try:
            return history.index(archetype_id)
        except ValueError:
            return -1

    return min(candidates, key=lambda item: (rank(item), candidates.index(item)))


def build_plan(contract, student_message="", recently_used=(), current="",
               retry_required=False, difficulty_request=""):
    """Plan oblika PRIJE jedinog AI poziva.

    Učenikova izričita molba (zatvorena intent tabela, bez modela i bez
    slobodnog pogađanja) ima prednost SAMO kad je ugovor dozvoljava i kad za
    nju postoji implementiran generator; retry zadržava tekuću vještinu.
    Sve ostalo pada na normalnu rotaciju — nikad na grešku."""
    requested = intent.requested_archetype(student_message)
    if (requested
            and not retry_required
            and requested in contract.effective_archetypes
            and requested in generator.IMPLEMENTED_ARCHETYPES):
        return GenerationPlan(requested, "student_request", requested)
    rotated = select_archetype(
        contract, recently_used=recently_used, current=current,
        retry_required=retry_required, difficulty_request=difficulty_request,
    )
    source = "retry" if (retry_required and rotated == current and current) else "rotation"
    return GenerationPlan(rotated, source, requested)


def prepare_task(contract, plan, difficulty_request="", target_level=None,
                 rng=None, avoid_texts=()):
    """Konstruiši, samoprovjeri i renderuj kostur zadatka — SVE prije poziva.

    Odbijanje ovdje znači: sigurna poruka, bez mutacije stanja i bez ijednog
    potrošenog AI poziva (kostur se pravi prije jedinog poziva).

    `target_level`: čist pass-through u generator.generate() (vidi tamo) —
    None zadržava postojeći relativni put bajt za bajt."""
    stages = []

    if contract is None:
        stages.append(_reject("contract_loaded", "contract_missing", engaged=False))
        return PreparedTask(False, "contract_missing", tuple(stages), plan=plan)
    if contract.status != "enabled":
        stages.append(_reject("contract_loaded", "contract_not_enabled", engaged=False,
                              status=contract.status))
        return PreparedTask(False, "contract_not_enabled", tuple(stages), plan=plan)
    stages.append(_ok("contract_loaded", topic=contract.canonical_topic_id,
                      version=contract.contract_version))

    archetype_id = plan.archetype_id if plan else ""
    if archetype_id not in contract.effective_archetypes:
        stages.append(_reject("archetype_allowed", "archetype_not_allowed",
                              archetype=archetype_id,
                              allowed=list(contract.effective_archetypes)))
        return PreparedTask(False, "archetype_not_allowed", tuple(stages), plan=plan)
    if archetype_id not in generator.IMPLEMENTED_ARCHETYPES:
        stages.append(_reject("archetype_allowed", "archetype_not_implemented",
                              engaged=False, archetype=archetype_id))
        return PreparedTask(False, "archetype_not_implemented", tuple(stages), plan=plan)
    stages.append(_ok("archetype_allowed", archetype=archetype_id, source=plan.source,
                      requested=plan.requested))

    try:
        skeleton = generator.generate(
            contract, archetype_id, difficulty_request=difficulty_request,
            target_level=target_level, rng=rng, avoid_texts=avoid_texts,
        )
    except generator.GenerationError as error:
        stages.append(_reject("skeleton_generated", "generation_failed",
                              engaged=False, detail=str(error)))
        return PreparedTask(False, "generation_failed", tuple(stages), plan=plan)
    stages.append(_ok("skeleton_generated", archetype=archetype_id))

    # Samoprovjera se ponavlja i OVDJE (generator je već provjerio) da faza
    # bude eksplicitno zabilježena u logu — konstrukcija nema povlasticu.
    verified, code = generator.self_verify(contract, skeleton)
    if not verified:
        stages.append(_reject("skeleton_self_verified", code))
        return PreparedTask(False, code, tuple(stages), plan=plan)
    stages.append(_ok("skeleton_self_verified", truth=str(skeleton.truth)))

    # Serverski render mora bajt-za-bajt preživjeti ISTU sanitizaciju kroz
    # koju prolazi sav vidljivi tekst — render koji bi se mijenjao ili pao
    # znači defekt generatora i pada zatvoreno.
    for label, text in (("question", skeleton.question_text),
                        ("expected_answer", skeleton.expected_answer),
                        *(("option", option) for option in skeleton.option_texts)):
        sanitized, safe = sanitize_and_validate_math_text(
            text, allow_whole_expression_wrap=(label == "option")
        )
        if not safe or sanitized != text:
            stages.append(_reject("rendered_safe", "server_render_not_safe",
                                  where=label))
            return PreparedTask(False, "server_render_not_safe", tuple(stages), plan=plan)
    stages.append(_ok("rendered_safe", options=len(skeleton.option_texts)))

    return PreparedTask(True, "ok", tuple(stages), skeleton=skeleton, plan=plan)


def diagnostics(contract, archetype_id, prepared):
    """Ograničena interna dijagnostika za strukturisani log. NIKAD u browser."""
    stage = prepared.failed_stage
    return {
        "topic": contract.canonical_topic_id if contract else "",
        "contract_version": contract.contract_version if contract else "",
        "skill": contract.skill if contract else "",
        "archetype": archetype_id or "",
        "stage": stage.stage if stage else "",
        "code": prepared.code,
        "engaged": stage.engaged if stage else None,
        "details": dict(stage.details) if stage else {},
    }


# ---------------------------------------------------------------------------
# KAPIJA VJERNOSTI PROZE — model smije mijenjati SAMO okolnu bosansku prozu.
#
# Server je jedini autor matematike, pa svaki BROJ u modelovoj prozi o zadatku
# mora biti objašnjiv iz kostura: doslovna vrijednost iz zadatka/opcija, ili
# vrijednost očigledno izvediva iz njih (zajednički imenilac, prošireni
# brojnici, međuzbir, unakrsni proizvodi), ili mali broj za nabrajanje (0-12).
# Broj koji se ne da objasniti = proza koja izmišlja matematiku → taj tekst se
# NE objavljuje (pozivalac pada na deterministički siguran tekst), bez drugog
# poziva i bez mutacije stanja.
# ---------------------------------------------------------------------------

_FRAC_VALUE_RE = re.compile(r"\\[dt]?frac\{(-?\d+)\}\{(-?\d+)\}")
_DECIMAL_COMMA_RE = re.compile(r"(?<![\d,])(\d+),(\d+)(?![\d,])")
_INTEGER_RE = re.compile(r"(?<![\d,.])(\d+)(?![\d,.])")

_SMALL_ENUMERATION_LIMIT = 12


def extract_values(text):
    """Sve brojčane vrijednosti (kao egzaktni Fraction) iz jednog teksta."""
    values = set()
    remainder = text or ""

    def _take_frac(match):
        den = int(match.group(2))
        if den:
            values.add(Fraction(int(match.group(1)), den))
        return " "

    def _take_decimal(match):
        whole, digits = match.group(1), match.group(2)
        values.add(Fraction(int(whole + digits), 10 ** len(digits)))
        return " "

    remainder = _FRAC_VALUE_RE.sub(_take_frac, remainder)
    remainder = _DECIMAL_COMMA_RE.sub(_take_decimal, remainder)
    for match in _INTEGER_RE.finditer(remainder):
        values.add(Fraction(int(match.group(1))))
    return values


def _closure(base_values):
    """Ograničeno zatvaranje: vrijednosti koje korektan hint smije pomenuti,
    a nisu doslovno u zadatku (zajednički imenilac, prošireni brojnici,
    međurezultati, unakrsni proizvodi). Namjerno malo i determinističko."""
    derived = set(base_values)
    fractions = [v for v in base_values if v.denominator > 1]
    for value in fractions:
        derived.add(Fraction(value.numerator))
        derived.add(Fraction(value.denominator))
    for a in fractions:
        for b in fractions:
            if a is b:
                continue
            lcm = a.denominator * b.denominator // _gcd(a.denominator, b.denominator)
            derived.add(Fraction(lcm))
            derived.add(Fraction(a.numerator * (lcm // a.denominator)))
            derived.add(Fraction(b.numerator * (lcm // b.denominator)))
            derived.add(Fraction(
                a.numerator * (lcm // a.denominator)
                + b.numerator * (lcm // b.denominator)
            ))
            derived.add(abs(Fraction(
                a.numerator * (lcm // a.denominator)
                - b.numerator * (lcm // b.denominator)
            )))
            derived.add(Fraction(a.numerator * b.numerator))
            derived.add(Fraction(a.denominator * b.denominator))
            derived.add(Fraction(a.numerator * b.denominator))
            derived.add(Fraction(a.denominator * b.numerator))
            for combined in (a + b, a * b):
                derived.add(combined)
            if b != 0:
                derived.add(a / b)
            if a >= b:
                derived.add(a - b)
    return derived


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def verify_prose_fidelity(prose, task_texts):
    """(ok, offending) — smije li se modelova proza prikazati uz OVAJ zadatak.

    `task_texts`: server-owned tekstovi aktivnog zadatka (pitanje, opcije,
    interni očekivani odgovor). Proza bez brojeva uvijek prolazi — riječi
    provjeravaju postojeći slojevi (sanitizacija, terminologija, mathcheck)."""
    prose_values = extract_values(prose)
    if not prose_values:
        return True, ()
    base = set()
    for text in task_texts:
        base |= extract_values(text)
    allowed = _closure(base)
    allowed |= {Fraction(n) for n in range(_SMALL_ENUMERATION_LIMIT + 1)}
    offending = sorted(v for v in prose_values if v not in allowed)
    if offending:
        return False, tuple(str(v) for v in offending)
    return True, ()
