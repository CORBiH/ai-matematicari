"""Kvalitet determinističkih PORODICA → izbor rute. Čitalac artefakta.

ZAŠTO POSTOJI: nulti poziv, nula sekundi i serverski dokazana matematika imaju
veliku vrijednost — ali ne po svaku cijenu. Mjereno nad 352 determinističke
lekcije: u 21 porodici učenik na „daj novi“ i na sva tri nivoa težine dobija
ISTU rečenicu s drugim brojevima. Za vježbanje to nije ljestvica nego
ponavljanje, a mjereno je i da takva porodica često uopšte ne može objaviti
nov zadatak (A/B: objava 0.80 naspram 0.98 na Luni).

ŠTA SE PROMIJENILO U ODNOSU NA PRVU VERZIJU OVOG MODULA:
prva verzija je vodila spisak LEKCIJA i mjerila samo koliko su tekstovi
doslovno različiti. Oboje je bilo pogrešno. „Različit broj“ nije različit
zadatak, pa se sada mjeri ŠABLON (tekst s maskiranim brojevima); a odluka po
lekciji je spisak izuzetaka prerušen u podatak, pa se sada odlučuje po
PORODICI — generator je porodica, i ako je slab, slab je za svaku svoju
lekciju. Dodavanje ili popravak generatora mijenja MJERENJE, ne Python.

GRANICA: ovaj modul ne odlučuje ništa sam. On čita
`data/deterministic_routing.json` (proizvod `scripts/build_deterministic_*.py`)
i odgovara na jedno pitanje: „ide li ova porodica na modelsku rutu?“.
Bez artefakta ponašanje je bajt-identično determinističkom.
"""
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_ARTIFACT = Path(__file__).resolve().parent.parent / "data" / "deterministic_routing.json"

# Vrijednost koja se seli na model. Ostale klase ostaju determinističke:
#   KEEP_DETERMINISTIC                    — mjereno dobra raznolikost
#   KEEP_DETERMINISTIC_FOR_REPRESENTATION — koncept traži prikaz (brojevna prava,
#                                           dijagram) koji tekstualni MCQ ne bi dao
#   NEEDS_MORE_EVIDENCE                   — signal na granici; ne dira se
MIGRATE_ROUTE = "MIGRATE_TO_LUNA"


def _enabled() -> bool:
    """Prekidač rute. `enabled` seli mjereno slabe porodice na modelsku rutu.

    PRODUKCIJSKU VRIJEDNOST POSTAVLJA DEPLOY, ne ovaj podrazumijevani izraz —
    isti obrazac koji već važi za `MATBOT_FAST_SINGLE_CALL_SCOPE`. Ugrađivanje
    „uključeno“ ovdje prevelo bi cijelu testnu svitu na modelsku rutu i ugasilo
    117 dokaza determinističkih generatora, koji ostaju ROLLBACK put i moraju
    ostati provjereni.

    Ime varijable je zadržano radi kontinuiteta, ali joj je ZNAČENJE PROŠIRENO:
    više ne govori o „raznolikosti teksta po lekciji“ nego o klasifikaciji
    kvaliteta PORODICE iz `data/deterministic_routing.json`. `disabled` (i
    odsustvo) znači: nijedna porodica se ne seli — potpun rollback."""
    return os.environ.get("MATBOT_DETERMINISTIC_VARIETY_GATE", "disabled") == "enabled"


@lru_cache(maxsize=1)
def _payload() -> dict:
    try:
        return json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Nema mjerenja → nema odluke. Odsustvo podatka nikad ne mijenja rutu.
        return {}


@lru_cache(maxsize=1)
def _migrated_families() -> frozenset:
    payload = _payload()
    families = payload.get("families") or {}
    named = payload.get("migrate_to_luna_families")
    if named:
        return frozenset(named)
    return frozenset(name for name, row in families.items()
                     if row.get("route") == MIGRATE_ROUTE)


def family_routes_to_model(family_id) -> bool:
    """True samo kad je porodica MJERENA i klasifikovana za selidbu."""
    if not family_id or not _enabled():
        return False
    return family_id in _migrated_families()


def classification(family_id) -> dict:
    return dict((_payload().get("families") or {}).get(family_id) or {})


def coverage():
    """(izmjerenih porodica, porodica na modelskoj ruti)."""
    return len(_payload().get("families") or {}), len(_migrated_families())
