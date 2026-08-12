"""Učitavanje i keširanje kompajliranih semantičkih ugovora lekcija.

NEPROMJENJIVOST JE DIO UGOVORA: razriješen ugovor je zamrznut dataclass s
`MappingProxyType`/torkama, pa ni jedan poziv ne može promijeniti ono što je
drugi poziv vidio. Tutor i Recenzent MORAJU dobiti isti objekat — zato se
tekst prompta ne sastavlja u runtime-u nego dolazi gotov iz artefakta.

Odsustvo ugovora nije poseban slučaj: `contract_for` vrati None i cio ostatak
sistema se ponaša bajt za bajt kao prije Faze 4A.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

COMPILED_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "lesson_semantics.compiled.json"

MODE_BLOCKING = "blocking"
MODE_ADVISORY = "advisory"

_lock = threading.Lock()
_cache = None
_override = None


class SemanticContractError(ValueError):
    """Artefakt je neupotrebljiv. Nikad se ne degradira tiho."""


@dataclass(frozen=True)
class SemanticContract:
    """Jedan razriješen, nepromjenjiv ugovor lekcije."""

    lesson_id: str
    family_id: str
    family_version: int
    detector: str
    enforcement_mode: str
    activation_class: str
    parameters: MappingProxyType
    enforced_parameters: tuple
    advisory_parameters: tuple
    forbidden_neighbour_skills: tuple
    level_bounds: MappingProxyType
    archetype_definitions: MappingProxyType
    evidence_ids: tuple
    prompt_lines: tuple
    reviewer_note: str
    contract_version: str

    @property
    def blocking(self) -> bool:
        return self.enforcement_mode == MODE_BLOCKING

    def prompt_block(self) -> str:
        """Kompaktan blok koji IDENTIČNO ide i Tutoru i Recenzentu."""
        return "\n".join(self.prompt_lines)


def _freeze_parameters(raw):
    frozen = {}
    for key, value in sorted(raw.items()):
        frozen[key] = tuple(value) if isinstance(value, list) else value
    return MappingProxyType(frozen)


def _build(entry):
    try:
        return SemanticContract(
            lesson_id=str(entry["lesson_id"]),
            family_id=str(entry["family_id"]),
            family_version=int(entry["family_version"]),
            detector=str(entry["detector"]),
            enforcement_mode=str(entry["enforcement_mode"]),
            activation_class=str(entry.get("activation_class", "")),
            parameters=_freeze_parameters(entry["parameters"]),
            enforced_parameters=tuple(entry.get("enforced_parameters", ())),
            advisory_parameters=tuple(entry.get("advisory_parameters", ())),
            forbidden_neighbour_skills=tuple(entry.get("forbidden_neighbour_skills", ())),
            level_bounds=MappingProxyType(dict(entry.get("level_bounds", {}))),
            archetype_definitions=MappingProxyType(
                dict(entry.get("archetype_definitions", {}))),
            evidence_ids=tuple(entry.get("evidence_ids", ())),
            prompt_lines=tuple(entry.get("prompt_lines", ())),
            reviewer_note=str(entry.get("reviewer_note", "")),
            contract_version=str(entry.get("contract_version", "")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SemanticContractError(f"neispravan ugovor: {error}") from error


def _load():
    global _cache
    if _override is not None:
        return _override
    if _cache is None:
        with _lock:
            if _cache is None:
                if not COMPILED_PATH.exists():
                    # Odsustvo artefakta znači „nijedna lekcija nema ugovor“ —
                    # ponašanje ostaje kao prije Faze 4A.
                    _cache = {}
                else:
                    payload = json.loads(COMPILED_PATH.read_text(encoding="utf-8"))
                    _cache = {lesson_id: _build(entry)
                              for lesson_id, entry in payload.get("lessons", {}).items()}
    return _cache


def contract_for(lesson_id):
    """Ugovor za kanonski ID lekcije, ili None kad ga lekcija nema."""
    if not lesson_id:
        return None
    return _load().get(str(lesson_id))


def all_contracts():
    return dict(_load())


def reset_cache():
    """Samo za testove/CI."""
    global _cache
    with _lock:
        _cache = None


class override_contracts:
    """Privremeno zamijeni registar (testovi). Ne poznaje nijedan ID lekcije."""

    def __init__(self, contracts):
        self._contracts = dict(contracts)
        self._previous = None

    def __enter__(self):
        global _override
        self._previous = _override
        _override = self._contracts
        return self._contracts

    def __exit__(self, *exc):
        global _override
        _override = self._previous
        return False
