"""Deklarativna šema ugovora lekcije (LessonContract) — rječnik, validacija i
determinističko razrješavanje nasljeđivanja.

ZAŠTO POSTOJI: ranije je svaka nova lekcija tražila novi Python (mapa porodica
po ID-ju lekcije + grana u validatoru + tekst prompta). Nad 534 lekcije to ne
skalira. Ovdje se lekcija opisuje PODACIMA; motor ostaje isti.

Ovaj modul namjerno NIŠTA ne uvozi iz ostatka paketa `contracts` — drži samo
rječnik (dozvoljene vrijednosti) i pravila oblika. Time nema kružnih uvoza, a
`archetypes`/`verifiers`/`evidence` mogu se testom provjeriti protiv rječnika
umjesto obrnuto.

DVA PRAVILA KOJA ČINE KOMPOZICIJU SIGURNOM:

  1. Nasljeđivanje SAMO SUŽAVA. Dijete smije izbaciti operaciju, arhetip ili
     dokaz iz roditeljskog skupa, ali ga nikad ne smije proširiti. Bez ovoga
     bi „mali override“ tiho otključao matematiku koju roditeljski predložak
     nikad nije odobrio, a niko ne bi ponovo pregledao cijelu naslijeđenu
     površinu.
  2. Ograničenje se ne smije olabaviti u „any“. Ako roditelj kaže
     denominator_relation="equal", dijete ne smije reći "any" — to je isti
     propust kao (1), samo unutar mape ograničenja.
"""
from dataclasses import dataclass, field, replace
from types import MappingProxyType


class ContractSchemaError(ValueError):
    """Ugovor je strukturno neispravan. Poruka je INTERNA (log/CI), nikad se ne
    prikazuje učeniku."""


class ContractConflictError(ContractSchemaError):
    """Dijete proširuje ili olabavljuje ono što je roditelj suzio."""


# ---------------------------------------------------------------------------
# RJEČNIK — sve dozvoljene vrijednosti na jednom mjestu.
# ---------------------------------------------------------------------------

STATUSES = frozenset({
    "enabled",          # motor ugovora, nikad legacy, fail-closed
    "needs_review",     # generator predložio, čovjek još nije potvrdio → legacy
    "unsupported",      # svjesna odluka: nema sigurnog ugovora → Practice nedostupan
    "legacy_pinned",    # revidiran, auditiran povratak na legacy (traži razlog)
})

# Statusi koji SMIJU koristiti legacy put. `enabled` namjerno NIJE među njima:
# neuspjeh uključenog ugovora ne smije se sakriti kao „pao je pa je otišao na
# staro“ — to bi zamaskiralo defekt motora.
LEGACY_STATUSES = frozenset({"needs_review", "legacy_pinned"})

DOMAINS = frozenset({
    "arithmetic", "rational_numbers", "decimals", "percentages", "powers_roots",
    "expressions", "equations", "inequalities", "geometry", "measurement",
    "sets", "data_probability",
})

OPERATIONS = frozenset({
    "add", "subtract", "multiply", "divide", "power", "root",
    "compare", "convert", "factorize", "substitute", "construct",
})

OPERAND_TYPES = frozenset({
    "natural", "integer", "fraction", "decimal", "mixed_number",
    "variable", "expression", "measurement", "shape", "set", "point",
})

ARCHETYPES = frozenset({
    "direct_computation", "find_missing_value", "compare", "order", "classify",
    "identify_equivalent", "identify_error", "complete_steps", "apply_formula",
    "solve_equation", "verify_solution", "translate_representation", "word_problem",
})

VERIFIERS = frozenset({
    "exact_rational", "error_category", "ordered_pair_substitution",
    "system_equivalence_rref", "substitution_exact", "formula_evaluation",
    "unit_conversion", "expression_canonical",
})

# Zatvoren skup kategorija greške. Kategorija se izvodi STRUKTURNO iz dokaza
# (vidi verifiers.py) — nikad iz proze. Ugovor koji navede kategoriju bez
# registrovanog izvođača pada na UČITAVANJU, ne u runtime-u.
ERROR_CATEGORIES = frozenset({
    "combined_denominators", "unequal_scaling", "wrong_product",
    "missed_reciprocal", "wrong_numerator", "incorrect_conversion",
    "wrong_operation", "wrong_reduction",
})

DIFFICULTY_DIMENSIONS = frozenset({
    "operand_magnitude", "term_count", "reasoning_steps",
    "representation_complexity", "sign_complexity", "distractor_similarity",
    "context_complexity", "scaffolding", "unknown_count", "unit_conversion_depth",
})

PROGRESSION_POLICIES = frozenset({"rotate_archetypes", "primary_first", "retry_same"})

# --- vrijednosti unutar operand_constraints -------------------------------
DENOMINATOR_RELATIONS = frozenset({"any", "equal", "different", "one_divides_another"})
SIGN_POLICIES = frozenset({"any", "non_negative", "positive", "allow_negative"})
FRACTION_FORMS = frozenset({"any", "proper", "improper", "mixed"})
SCALING_DIRECTIONS = frozenset({"any", "expand", "reduce"})
ANSWER_FORMS = frozenset({"any", "irreducible"})

_ENUM_CONSTRAINTS = {
    "denominator_relation": DENOMINATOR_RELATIONS,
    "sign_policy": SIGN_POLICIES,
    "fraction_form": FRACTION_FORMS,
    "scaling_direction": SCALING_DIRECTIONS,
}
_RANGE_CONSTRAINTS = frozenset({"integer_range", "term_count", "unknown_count"})
_SCALAR_CONSTRAINTS = frozenset({"equation_degree", "unit_dimension", "shape_type"})
CONSTRAINT_KEYS = (
    frozenset(_ENUM_CONSTRAINTS) | _RANGE_CONSTRAINTS | _SCALAR_CONSTRAINTS
)

_ENUM_REPRESENTATION = {"answer_form": ANSWER_FORMS}
REPRESENTATION_KEYS = frozenset(_ENUM_REPRESENTATION)

# Neutralna vrijednost ograničenja: „nema zahtjeva“. Dijete koje roditeljsko
# ograničenje vrati na ovu vrijednost pokušava OLABAVITI, ne suziti.
_NEUTRAL = "any"


# ---------------------------------------------------------------------------
# FAZA A — šta je STVARNO deterministički provjerljivo.
#
# Uključen (`enabled`) ugovor smije koristiti SAMO ovo. Arhetip čija se šema
# može isparsirati, ali čija se tačnost ne može nezavisno izračunati, NIJE
# podržan — „šema se parsira“ nije dokaz ispravnosti.
# ---------------------------------------------------------------------------

STAGE_A_ARCHETYPES = frozenset({
    "direct_computation", "find_missing_value", "identify_equivalent", "identify_error",
})
STAGE_A_VERIFIERS = frozenset({"exact_rational", "error_category"})

_MAX_INHERITANCE_DEPTH = 3


@dataclass(frozen=True)
class DifficultyBound:
    minimum: int
    maximum: int
    default: int

    def contains(self, level):
        return self.minimum <= level <= self.maximum


@dataclass(frozen=True)
class LessonContract:
    """Razriješen (potpuno naslijeđen) ugovor jedne lekcije.

    `canonical_topic_id` je JEDINO mjesto u cijelom motoru gdje identitet
    lekcije uopšte postoji — i to kao PODATAK učitan iz JSON-a, nikad kao
    literal u kodu."""

    canonical_topic_id: str
    grade: int
    contract_version: str
    status: str
    domain: str
    skill: str
    inherits: str = ""
    allowed_operations: tuple = ()
    operand_types: tuple = ()
    allowed_task_archetypes: tuple = ()
    prohibited_task_archetypes: tuple = ()
    answer_verifier: str = ""
    error_category_set: tuple = ()
    invariant_constraints: tuple = ()
    progression_policy: str = "rotate_archetypes"
    terminology_profile: str = "bs_ijekavica_default"
    notation_profile: str = "arithmetic_default"
    pinned_reason: str = ""
    pinned_at: str = ""
    operand_constraints: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    representation_constraints: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    difficulty_dimensions: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))

    @property
    def effective_archetypes(self):
        """Arhetipi koje lekcija stvarno smije koristiti (dozvoljeni minus
        zabranjeni), determinističkim redoslijedom iz ugovora."""
        prohibited = set(self.prohibited_task_archetypes)
        return tuple(a for a in self.allowed_task_archetypes if a not in prohibited)

    @property
    def uses_engine(self):
        return self.status == "enabled"

    def constraint(self, key, default=None):
        return self.operand_constraints.get(key, default)

    def representation(self, key, default=None):
        return self.representation_constraints.get(key, default)


# ---------------------------------------------------------------------------
# RAZRJEŠAVANJE NASLJEĐIVANJA
# ---------------------------------------------------------------------------

# NAPOMENA: "required_evidence" je uklonjeno nakon Live96 — lekcija zna svoju
# MATEMATIKU, a ne kojim bi JSON kontejnerom model trebao da je opiše. Model
# više ne izvještava dokaz (server generiše kostur), pa polje nema šta da
# ograničava. Ključ se u podacima i dalje prepoznaje kao POZNAT i ODBIJA s
# jasnom porukom (vidi resolve()) da stari red ne bi tiho značio „bez uslova“.
_REMOVED_KEYS = frozenset({"required_evidence"})

_NARROWING_LISTS = (
    "allowed_operations", "operand_types", "allowed_task_archetypes",
    "error_category_set",
)
_WIDENING_LISTS = ("prohibited_task_archetypes", "invariant_constraints")
_SCALARS = (
    "contract_version", "domain", "skill", "answer_verifier", "progression_policy",
    "terminology_profile", "notation_profile", "status", "grade",
    "canonical_topic_id", "pinned_reason", "pinned_at",
)
_KNOWN_KEYS = frozenset(
    _SCALARS + _NARROWING_LISTS + _WIDENING_LISTS
    + ("inherits", "operand_constraints", "representation_constraints",
       "difficulty_dimensions")
)


def _merge_lists(parent, child, key, where):
    if child is None:
        return parent
    child = tuple(child)
    if parent is None:
        return child
    extra = [item for item in child if item not in parent]
    if extra:
        raise ContractConflictError(
            f"{where}: '{key}' proširuje naslijeđeni skup vrijednostima {sorted(extra)} "
            f"(nasljeđivanje smije samo SUŽAVATI)"
        )
    return child


def _merge_constraints(parent, child, where, label="operand_constraints"):
    merged = dict(parent or {})
    for key, value in (child or {}).items():
        previous = merged.get(key)
        if previous is not None and previous != _NEUTRAL and value == _NEUTRAL:
            raise ContractConflictError(
                f"{where}: '{label}.{key}' olabavljuje naslijeđeno "
                f"ograničenje '{previous}' na '{_NEUTRAL}'"
            )
        merged[key] = value
    return merged


def _merge_difficulty(parent, child, where):
    merged = dict(parent or {})
    for name, bound in (child or {}).items():
        previous = merged.get(name)
        if previous is not None:
            if bound["min"] < previous["min"] or bound["max"] > previous["max"]:
                raise ContractConflictError(
                    f"{where}: difficulty_dimensions.{name} "
                    f"[{bound['min']},{bound['max']}] izlazi izvan naslijeđenog "
                    f"[{previous['min']},{previous['max']}]"
                )
        merged[name] = dict(bound)
    return merged


def resolve(raw, templates, where=""):
    """Spoji `raw` (override lekcije) sa lancem predložaka i vrati sirov dict.

    Lanac se gradi od korijena prema djetetu, pa se svaki nivo primjenjuje kao
    SUŽAVANJE prethodnog. Dubina je ograničena (`_MAX_INHERITANCE_DEPTH`) da
    ciklus ili predugačak lanac ne prođu tiho."""
    where = where or raw.get("canonical_topic_id") or "(bez id-ja)"
    chain, seen, node = [], set(), raw
    while True:
        chain.append(node)
        parent_id = (node.get("inherits") or "").strip()
        if not parent_id:
            break
        if parent_id in seen:
            raise ContractSchemaError(f"{where}: ciklus u nasljeđivanju kod '{parent_id}'")
        seen.add(parent_id)
        if parent_id not in templates:
            raise ContractSchemaError(f"{where}: nepoznat predložak '{parent_id}'")
        node = templates[parent_id]
        if len(chain) > _MAX_INHERITANCE_DEPTH + 1:
            raise ContractSchemaError(
                f"{where}: lanac nasljeđivanja dublji od {_MAX_INHERITANCE_DEPTH}"
            )

    for level in chain:
        # Nepoznat ključ je TIPFELER, ne proširenje. Da ga tiho ignorišemo,
        # „operand_constraint“ umjesto „operand_constraints“ bi značilo lekciju
        # bez ijednog ograničenja — tačno onaj tihi default koji ne smije
        # postojati. Ključevi s '_' su namjerni komentari u podacima.
        removed = sorted(key for key in level if key in _REMOVED_KEYS)
        if removed:
            raise ContractSchemaError(
                f"{where}: polja {removed} su uklonjena iz šeme ugovora — "
                f"lekcija opisuje matematiku, ne mehaniku modelovog izlaza"
            )
        unknown = sorted(
            key for key in level
            if not key.startswith("_") and key not in _KNOWN_KEYS
        )
        if unknown:
            raise ContractSchemaError(f"{where}: nepoznata polja ugovora {unknown}")

    merged = {}
    for level in reversed(chain):  # korijen → dijete
        for key in _SCALARS:
            if key in level:
                merged[key] = level[key]
        for key in _NARROWING_LISTS:
            if key in level:
                merged[key] = _merge_lists(merged.get(key), level[key], key, where)
        for key in _WIDENING_LISTS:
            if key in level:
                merged[key] = tuple(dict.fromkeys(tuple(merged.get(key, ())) + tuple(level[key])))
        if "operand_constraints" in level:
            merged["operand_constraints"] = _merge_constraints(
                merged.get("operand_constraints"), level["operand_constraints"], where
            )
        if "representation_constraints" in level:
            merged["representation_constraints"] = _merge_constraints(
                merged.get("representation_constraints"),
                level["representation_constraints"], where, "representation_constraints",
            )
        if "difficulty_dimensions" in level:
            merged["difficulty_dimensions"] = _merge_difficulty(
                merged.get("difficulty_dimensions"), level["difficulty_dimensions"], where
            )
    merged["inherits"] = (raw.get("inherits") or "").strip()
    return merged


# ---------------------------------------------------------------------------
# VALIDACIJA
# ---------------------------------------------------------------------------

def _require_subset(values, allowed, key, where):
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ContractSchemaError(f"{where}: '{key}' sadrži nepoznate vrijednosti {unknown}")


def _validate_constraints(constraints, where):
    unknown = sorted(set(constraints) - CONSTRAINT_KEYS)
    if unknown:
        raise ContractSchemaError(f"{where}: nepoznata operand ograničenja {unknown}")
    for key, allowed in _ENUM_CONSTRAINTS.items():
        if key in constraints and constraints[key] not in allowed:
            raise ContractSchemaError(
                f"{where}: operand_constraints.{key}='{constraints[key]}' nije dozvoljena vrijednost"
            )
    for key in _RANGE_CONSTRAINTS:
        if key not in constraints:
            continue
        value = constraints[key]
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or not all(isinstance(v, int) for v in value) or value[0] > value[1]):
            raise ContractSchemaError(
                f"{where}: operand_constraints.{key} mora biti [min, max] cijelih brojeva"
            )


def _validate_representation(constraints, where):
    unknown = sorted(set(constraints) - REPRESENTATION_KEYS)
    if unknown:
        raise ContractSchemaError(f"{where}: nepoznata representation ograničenja {unknown}")
    for key, allowed in _ENUM_REPRESENTATION.items():
        if key in constraints and constraints[key] not in allowed:
            raise ContractSchemaError(
                f"{where}: representation_constraints.{key}='{constraints[key]}' nije dozvoljena"
            )


def _validate_difficulty(dimensions, where):
    _require_subset(dimensions, DIFFICULTY_DIMENSIONS, "difficulty_dimensions", where)
    for name, bound in dimensions.items():
        missing = {"min", "max", "default"} - set(bound)
        if missing:
            raise ContractSchemaError(
                f"{where}: difficulty_dimensions.{name} nema {sorted(missing)}"
            )
        if not (bound["min"] <= bound["default"] <= bound["max"]):
            raise ContractSchemaError(
                f"{where}: difficulty_dimensions.{name} default izvan [min, max]"
            )


def build(merged, where="", stage_a_only=True):
    """Provjeri razriješen dict i vrati zamrznut LessonContract.

    `stage_a_only=True` (podrazumijevano za UKLJUČENE ugovore) dodatno traži da
    su arhetip, verifier i dokazi među onima koji su STVARNO deterministički
    provjerljivi u ovoj fazi. Ugovor koji to prekrši pada ovdje — na
    učitavanju/CI-ju, ne pred učenikom."""
    where = where or merged.get("canonical_topic_id") or "(bez id-ja)"

    topic_id = (merged.get("canonical_topic_id") or "").strip()
    if not topic_id:
        raise ContractSchemaError(f"{where}: nedostaje canonical_topic_id")

    status = merged.get("status")
    if status not in STATUSES:
        raise ContractSchemaError(f"{where}: nepoznat status '{status}'")
    if status == "legacy_pinned" and not (merged.get("pinned_reason") or "").strip():
        raise ContractSchemaError(
            f"{where}: status 'legacy_pinned' zahtijeva pinned_reason "
            f"(povratak na legacy mora biti auditiran, nikad tih)"
        )

    grade = merged.get("grade")
    if not isinstance(grade, int) or not 6 <= grade <= 9:
        raise ContractSchemaError(f"{where}: grade mora biti cio broj 6-9, dobijeno {grade!r}")
    if not (merged.get("contract_version") or "").strip():
        raise ContractSchemaError(f"{where}: nedostaje contract_version")
    if merged.get("domain") not in DOMAINS:
        raise ContractSchemaError(f"{where}: nepoznat domain '{merged.get('domain')}'")
    if not (merged.get("skill") or "").strip():
        raise ContractSchemaError(f"{where}: nedostaje skill")
    if merged.get("progression_policy", "rotate_archetypes") not in PROGRESSION_POLICIES:
        raise ContractSchemaError(
            f"{where}: nepoznat progression_policy '{merged.get('progression_policy')}'"
        )

    operations = tuple(merged.get("allowed_operations", ()))
    archetypes = tuple(merged.get("allowed_task_archetypes", ()))
    prohibited = tuple(merged.get("prohibited_task_archetypes", ()))
    categories = tuple(merged.get("error_category_set", ()))
    verifier = merged.get("answer_verifier") or ""

    _require_subset(operations, OPERATIONS, "allowed_operations", where)
    _require_subset(merged.get("operand_types", ()), OPERAND_TYPES, "operand_types", where)
    _require_subset(archetypes, ARCHETYPES, "allowed_task_archetypes", where)
    _require_subset(prohibited, ARCHETYPES, "prohibited_task_archetypes", where)
    _require_subset(categories, ERROR_CATEGORIES, "error_category_set", where)
    _require_subset(
        merged.get("invariant_constraints", ()),
        CONSTRAINT_KEYS | {"allowed_operations", "allowed_task_archetypes"},
        "invariant_constraints", where,
    )
    if verifier and verifier not in VERIFIERS:
        raise ContractSchemaError(f"{where}: nepoznat answer_verifier '{verifier}'")

    constraints = dict(merged.get("operand_constraints", {}))
    representation = dict(merged.get("representation_constraints", {}))
    dimensions = dict(merged.get("difficulty_dimensions", {}))
    _validate_constraints(constraints, where)
    _validate_representation(representation, where)
    _validate_difficulty(dimensions, where)

    if status == "enabled":
        if not verifier:
            raise ContractSchemaError(f"{where}: uključen ugovor bez answer_verifier")
        effective = [a for a in archetypes if a not in set(prohibited)]
        if not effective:
            raise ContractSchemaError(
                f"{where}: uključen ugovor bez ijednog dozvoljenog arhetipa "
                f"(sve je zabranjeno kroz prohibited_task_archetypes)"
            )
        if stage_a_only:
            unsupported = sorted(set(effective) - STAGE_A_ARCHETYPES)
            if unsupported:
                raise ContractSchemaError(
                    f"{where}: arhetipi {unsupported} nemaju determinističku provjeru "
                    f"u ovoj fazi i ne smiju biti uključeni"
                )
            if verifier not in STAGE_A_VERIFIERS:
                raise ContractSchemaError(
                    f"{where}: verifier '{verifier}' nije deterministički implementiran u ovoj fazi"
                )
        if "identify_error" in effective and not categories:
            raise ContractSchemaError(
                f"{where}: arhetip 'identify_error' zahtijeva neprazan error_category_set "
                f"(kategorija se izvodi strukturno, nikad iz proze)"
            )

    return LessonContract(
        canonical_topic_id=topic_id,
        grade=grade,
        contract_version=str(merged["contract_version"]),
        status=status,
        domain=merged["domain"],
        skill=merged["skill"],
        inherits=merged.get("inherits", ""),
        allowed_operations=operations,
        operand_types=tuple(merged.get("operand_types", ())),
        allowed_task_archetypes=archetypes,
        prohibited_task_archetypes=prohibited,
        answer_verifier=verifier,
        error_category_set=categories,
        invariant_constraints=tuple(merged.get("invariant_constraints", ())),
        progression_policy=merged.get("progression_policy", "rotate_archetypes"),
        terminology_profile=merged.get("terminology_profile", "bs_ijekavica_default"),
        notation_profile=merged.get("notation_profile", "arithmetic_default"),
        pinned_reason=merged.get("pinned_reason", ""),
        pinned_at=merged.get("pinned_at", ""),
        operand_constraints=MappingProxyType(constraints),
        representation_constraints=MappingProxyType(representation),
        difficulty_dimensions=MappingProxyType({
            name: DifficultyBound(bound["min"], bound["max"], bound["default"])
            for name, bound in dimensions.items()
        }),
    )


def resolve_and_build(raw, templates, stage_a_only=True):
    where = raw.get("canonical_topic_id") or "(bez id-ja)"
    return build(resolve(raw, templates, where), where, stage_a_only=stage_a_only)


__all__ = [
    "ContractSchemaError", "ContractConflictError", "LessonContract", "DifficultyBound",
    "resolve", "build", "resolve_and_build", "replace",
    "STATUSES", "LEGACY_STATUSES", "DOMAINS", "OPERATIONS", "OPERAND_TYPES",
    "ARCHETYPES", "VERIFIERS", "ERROR_CATEGORIES",
    "DIFFICULTY_DIMENSIONS", "CONSTRAINT_KEYS",
    "STAGE_A_ARCHETYPES", "STAGE_A_VERIFIERS",
]
