"""Univerzalni arhetipi zadatka — KAKO učenik radi sa zadatkom.

Podjela koja se NE smije miješati:
  • ARHETIP opisuje interakciju (izračunaj / dopuni / prepoznaj ekvivalent /
    nađi grešku). Ne zna nijednu lekciju ni oblast.
  • UGOVOR LEKCIJE opisuje koja matematika smije da se pojavi.

Zato ISTI `direct_computation` opslužuje i sabiranje razlomaka jednakih
imenilaca i množenje razlomaka i sabiranje cijelih brojeva — razlika je
isključivo u vrijednostima ugovora, ne u kodu.

ŠTA JE OVDJE NESTALO S POVLAČENJEM STAROG MOTORA (2026-08-14): arhetip je
ranije bio „podržan“ samo ako za njega postoji SERVERSKI GENERATOR koji tačan
odgovor konstruiše (`generator.IMPLEMENTED_ARCHETYPES`). Tog generatora više
nema — zadatak piše model, a server ga provjerava — pa je i ta provjera
uklonjena: obećanje koje niko više ne daje se ne može prekršiti.

Ostaje provjera KATEGORIJA GREŠKE, i ona i dalje vrijedi: `error_category_set`
uključenog ugovora ide kao PODATAK u aktivni Luna prompt
(matbot/tutor/lesson_context.py), pa kategorija koju projekat ne poznaje ne bi
bila bezopasna.
"""
from dataclasses import dataclass

from matbot.contracts import verifiers
from matbot.contracts.schema import ContractSchemaError

# Politika numeričke provjere TEKSTA PITANJA: arhetip čiji je predmet
# ispitivanja BAŠ pogrešan postupak smije prikazati nedosljedan lanac.
POLICY_CHECK = "check"
POLICY_ALLOW_MISMATCH = "allow_intentional_mismatch"
GEOMETRY_POLICY_CHECK = "check"
GEOMETRY_POLICY_ALLOW = "allow_intentional_violation"


@dataclass(frozen=True)
class Archetype:
    archetype_id: str
    question_numeric_policy: str = POLICY_CHECK
    question_geometry_policy: str = GEOMETRY_POLICY_CHECK
    # Kratak opis onoga što učenik određuje — ide u prompt da model zna o čemu
    # piše hint/feedback. Nikad ne nosi matematiku konkretnog zadatka.
    prompt_unknown: str = ""


_REGISTRY = {}


def _register(archetype):
    _REGISTRY[archetype.archetype_id] = archetype
    return archetype


_register(Archetype(
    archetype_id="direct_computation",
    prompt_unknown="vrijednost izračunatog izraza",
))

_register(Archetype(
    archetype_id="find_missing_value",
    prompt_unknown="jedna nedostajuća vrijednost u jednakosti",
))

_register(Archetype(
    archetype_id="identify_equivalent",
    prompt_unknown="koji od ponuđenih zapisa ima ISTU vrijednost kao zadani",
))

_register(Archetype(
    archetype_id="identify_error",
    # Pitanje NAMJERNO prikazuje pogrešan lanac kao predmet ispitivanja.
    question_numeric_policy=POLICY_ALLOW_MISMATCH,
    question_geometry_policy=GEOMETRY_POLICY_ALLOW,
    prompt_unknown="gdje je greška u prikazanom postupku",
))


def archetype_for(archetype_id):
    return _REGISTRY.get(archetype_id)


def assert_supported(contract):
    """Provjeri da UKLJUČEN ugovor navodi samo pojmove koje projekat poznaje.

    Poziva se pri UČITAVANJU (registry.load_all) — defekt ugovora je greška
    starta/CI-ja, nikad iznenađenje pred učenikom."""
    if contract.status != "enabled":
        return
    where = contract.canonical_topic_id
    for archetype_id in contract.effective_archetypes:
        if archetype_id not in _REGISTRY:
            raise ContractSchemaError(
                f"{where}: arhetip '{archetype_id}' nije definisan"
            )
    unknown = sorted(set(contract.error_category_set) - verifiers.DERIVABLE_ERROR_CATEGORIES)
    if unknown:
        raise ContractSchemaError(
            f"{where}: kategorije greške {unknown} nemaju strukturni izvođač — "
            f"bez njega se kategorija može samo pogađati iz proze"
        )
    unlabelled = sorted(
        category for category in contract.error_category_set
        if not verifiers.ERROR_CATEGORY_LABELS.get(category)
    )
    if unlabelled:
        raise ContractSchemaError(
            f"{where}: kategorije greške {unlabelled} nemaju projektni tekst opcije — "
            f"server ne bi mogao sam sastaviti vidljivu opciju, pa bi tačnost "
            f"zavisila od neprovjerene proze modela"
        )
