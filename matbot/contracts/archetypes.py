"""Univerzalni arhetipi zadatka — KAKO učenik radi sa zadatkom.

Podjela koja se NE smije miješati:
  • ARHETIP opisuje interakciju (izračunaj / dopuni / prepoznaj ekvivalent /
    nađi grešku). Ne zna nijednu lekciju ni oblast.
  • UGOVOR LEKCIJE opisuje koja matematika smije da se pojavi.
  • GENERATOR (matbot/contracts/generator.py) konstruiše konkretan zadatak iz
    ugovora — arhetip je samo ime recepta i nosilac politika provjere teksta.

Zato ISTI `direct_computation` opslužuje i sabiranje razlomaka jednakih
imenilaca i množenje razlomaka i sabiranje cijelih brojeva — razlika je
isključivo u vrijednostima ugovora, ne u kodu.

Arhetip je „podržan“ SAMO ako za njega postoji serverski generator koji tačan
odgovor KONSTRUIŠE (generator.IMPLEMENTED_ARCHETYPES). Ranija faza je ovdje
provjeravala i vrste modelovog dokaza — taj smjer je ukinut nakon Live96:
model više ne izvještava matematiku, pa dokaz nema šta da deklariše.
"""
from dataclasses import dataclass

from matbot.contracts import generator, verifiers
from matbot.contracts.schema import ContractSchemaError

# Politika numeričke provjere TEKSTA PITANJA (isti pojam kao u
# matbot/task_family_validation.py): arhetip čiji je predmet ispitivanja BAŠ
# pogrešan postupak smije prikazati nedosljedan lanac.
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


def implemented_ids():
    """Arhetipi za koje postoji STVARNI serverski generator (ne samo ime)."""
    return generator.IMPLEMENTED_ARCHETYPES


def assert_supported(contract):
    """Provjeri da UKLJUČEN ugovor traži samo ono što je stvarno implementirano.

    Poziva se pri UČITAVANJU (registry.load_all) — defekt ugovora je greška
    starta/CI-ja, nikad iznenađenje pred učenikom. Širenje podataka (npr.
    dodavanje find_missing_value lekciji) bez generičkog generatora pada OVDJE."""
    if contract.status != "enabled":
        return
    where = contract.canonical_topic_id
    for archetype_id in contract.effective_archetypes:
        if archetype_id not in _REGISTRY:
            raise ContractSchemaError(
                f"{where}: arhetip '{archetype_id}' nije definisan"
            )
        if archetype_id not in generator.IMPLEMENTED_ARCHETYPES:
            raise ContractSchemaError(
                f"{where}: arhetip '{archetype_id}' nema serverski generator — "
                f"uključen ugovor ne smije obećavati oblik koji server ne umije "
                f"konstruisati"
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
