"""Učitavanje, razrješavanje i keširanje ugovora lekcija.

Validacija se dešava DVA PUTA i identično: pri build-u (scripts/) i pri
učitavanju u aplikaciji. Neispravan ugovor je tvrda greška — nikad se ne
degradira tiho na legacy, jer bi to zamaskiralo defekt motora.

Stanje Practice moda po lekciji (jedina tri, bez preklapanja):

    "engine"      — status 'enabled': ISKLJUČIVO motor ugovora, fail-closed
    "legacy"      — nema reda / 'needs_review' / 'legacy_pinned'
    "unavailable" — status 'unsupported': jasna sigurna poruka, bez AI poziva

`needs_review` → legacy je SVJESNA odluka: red je generatorov prijedlog koji
čovjek još nije potvrdio. Tretirati ga kao 'enabled' značilo bi objaviti
nerevidiranu matematiku; tretirati ga kao 'unsupported' značilo bi ugasiti
Practice na lekciji koja danas radi. Zato ide na legacy, ali se u izvještaju
BROJI ODVOJENO od 'legacy_uncontracted' da nedovršena migracija ne može da se
sakrije iza „još nije počelo“.
"""
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from matbot.contracts.schema import (ContractSchemaError, LEGACY_STATUSES,
                                     resolve_and_build)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TEMPLATES_PATH = DATA_DIR / "contract_templates.json"
CONTRACTS_PATH = DATA_DIR / "lesson_contracts.json"

STATE_ENGINE = "engine"
STATE_LEGACY = "legacy"
STATE_UNAVAILABLE = "unavailable"

# Izvještajne oznake (nikad se ne pišu u JSON — izvode se).
REPORT_LEGACY_UNCONTRACTED = "legacy_uncontracted"

_lock = threading.Lock()
_cache = None
_override = None


def _read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _strip_comments(mapping):
    """Ključevi koji počinju s '_' su komentari u podacima, ne ugovori."""
    return {k: v for k, v in mapping.items() if not k.startswith("_")}


def load_all(templates_path=TEMPLATES_PATH, contracts_path=CONTRACTS_PATH,
             stage_a_only=True):
    """Vrati {topic_id: LessonContract}. Baca ContractSchemaError na defekt."""
    templates = _strip_comments(_read_json(templates_path))
    payload = _read_json(contracts_path)
    rows = payload.get("contracts", []) if isinstance(payload, dict) else payload

    contracts, seen = {}, set()
    for raw in rows:
        topic_id = (raw.get("canonical_topic_id") or "").strip()
        if topic_id in seen:
            raise ContractSchemaError(f"dupli canonical_topic_id '{topic_id}'")
        seen.add(topic_id)
        contract = resolve_and_build(raw, templates, stage_a_only=stage_a_only)
        # Uvoz je namjerno lokalan: `archetypes` uvozi `verifiers`, pa bi uvoz na
        # vrhu modula napravio krug kroz registar.
        from matbot.contracts import archetypes

        archetypes.assert_supported(contract)
        conflicts = _difficulty_invariant_conflicts(contract)
        if conflicts:
            raise ContractSchemaError(
                f"{contract.canonical_topic_id}: {conflicts} su i nepromjenjiva "
                f"ograničenja i dimenzije težine — 'teže' bi ih smjelo pomjeriti"
            )
        contracts[contract.canonical_topic_id] = contract
    return contracts


def _difficulty_invariant_conflicts(contract):
    from matbot.contracts import difficulty

    return difficulty.invariant_conflicts(contract)


def _registry():
    global _cache
    if _override is not None:
        return _override
    if _cache is None:
        with _lock:
            if _cache is None:
                _cache = load_all()
    return _cache


def contract_for(topic_id):
    """Ugovor za kanonski ID lekcije, ili None kad reda nema."""
    if not topic_id:
        return None
    return _registry().get(topic_id)


def practice_state(contract):
    """Jedno od STATE_* — bez preklapanja i bez tihog fallbacka.

    POVLAČENJE (2026-08-14): globalni prekidač `MATBOT_CONTRACT_ENGINE` je
    uklonjen zajedno sa starim motorom koji je gasio. Stanje se sada čita
    ISKLJUČIVO iz statusa ugovora — dakle iz podataka, a ne iz okruženja, pa
    zaostala vrijednost u `.env`-u ne može promijeniti klasifikaciju."""
    if contract is None:
        return STATE_LEGACY
    if contract.status == "enabled":
        return STATE_ENGINE
    if contract.status == "unsupported":
        return STATE_UNAVAILABLE
    if contract.status in LEGACY_STATUSES:
        return STATE_LEGACY
    # Nepoznat status nikad ne smije značiti „radi nešto razumno“.
    raise ContractSchemaError(
        f"{contract.canonical_topic_id}: status '{contract.status}' nema definisano ponašanje"
    )


def state_for_topic(topic_id):
    return practice_state(contract_for(topic_id))


def contract_version_for(topic_id):
    """Komponenta kurikularnog otiska. Prazno kad lekcija nema ugovor — time
    izmjena ugovora invalidira aktivni zadatak kroz POSTOJEĆI mehanizam u
    matbot/session_store.py, bez novog puta invalidacije."""
    contract = contract_for(topic_id)
    return contract.contract_version if contract else ""


def report_status(topic_id):
    """Oznaka za izvještaj pokrivenosti (nikad za odluke u runtime-u)."""
    contract = contract_for(topic_id)
    return contract.status if contract else REPORT_LEGACY_UNCONTRACTED


def all_contracts():
    return dict(_registry())


def reset_cache():
    """Samo za testove/CI — sljedeći pristup ponovo učitava s diska."""
    global _cache
    with _lock:
        _cache = None


@contextmanager
def override_contracts(contracts):
    """Privremeno zamijeni registar (testovi).

    Postoji da bi se moglo dokazati da motor obrađuje POTPUNO NOVU lekciju bez
    ijedne izmjene izvornog koda: test ubaci sintetički ugovor i provuče cio
    Practice turn kroz njega. Sama funkcija ne zna nijedan ID lekcije."""
    global _override
    previous = _override
    _override = dict(contracts)
    try:
        yield _override
    finally:
        _override = previous
