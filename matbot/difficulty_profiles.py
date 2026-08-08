"""Lekcijski-relativni profili težine za modelsku rutu (Faza F5G).

ZAŠTO POSTOJI: globalna rubrika nivoa 1–3 (matbot/tutor/schema.py,
`difficulty_evidence_errors`) mjeri SVAKU lekciju istim pragovima. Četiri
ponovljena živa sudara (dva na lekciji tekstualnih zadataka sa sistemom, po
jedan na praktičnoj Pitagori i na zapremini trostrane piramide, uključujući
završnu kapiju na 6e91db8) dokazala su da je za neke lekcije NAJJEDNOSTAVNIJI
legitiman zadatak već iznad globalnih pragova nivoa 1: direktna primjena
kanonske geometrijske formule iskreno nosi tri povezane operacije, a
tekstualni zadatak sa sistemom dva uslova i jednu promjenu zapisa. Model je
odgovarao iskreno — rubrika je bila strukturno nedostižna.

RJEŠENJE: nivo 1 znači „najjednostavniji valjan uvodni zadatak UNUTAR izabrane
lekcije“, ne „jedna operacija globalno“. Lekcije čija kurikulumska semantika
to dokazuje dobijaju PROFIL — deklarativne granice po nivou — iz
`data/difficulty_profiles.json`. Profil se ključa po ZAMRZNUTOJ primarnoj
porodici lekcije (matbot/task_families.py): to je server-vlasnički podatak
izveden iz kurikuluma, nikad modelova proza i nikad ID lekcije u kodu.

GRANICE VAŽENJA:
  • profil se razrješava ISKLJUČIVO po primarnoj porodici lekcije
    (server-vlasnički zamrznut podatak) — važi za OBJE strategije
    izvršenja, pa lekcija ima tačno jedan autoritet težine (Batch #4;
    deterministički dokazi po nivou dokazano zadovoljavaju profile);
  • lekcija bez dodijeljenog profila zadržava globalnu rubriku — laka
    lekcija se NE popušta;
  • server je jedini autoritet: razrješenje čita samo LessonContext,
    payload modela ne može izabrati blažu rubriku.

Odsustvo artefakta nije greška: bez fajla nijedna lekcija nema profil i cio
sistem se ponaša kao prije. Neispravan artefakt baca izuzetak — nikad se ne
degradira tiho na pogrešne granice.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "difficulty_profiles.json"

# Zatvoren skup mjerljivih polja dokaza — isti kao u DifficultyEvidence.
_NUMERIC_FIELDS = ("reasoning_steps", "condition_count", "operation_count",
                   "representation_change_count")
_FLAG_FIELDS = ("requires_explanation", "requires_comparison",
                "requires_construction", "requires_proof_or_justification",
                "combines_concepts")

_lock = threading.Lock()
_cache = None
_override = None


class DifficultyProfileError(ValueError):
    """Artefakt profila je neupotrebljiv. Nikad se ne degradira tiho."""


@dataclass(frozen=True)
class LevelSpec:
    """Granice jednog nivoa: kapaciteti, zabranjene zastavice i POD."""

    max_counts: MappingProxyType
    forbidden_flags: tuple
    # Torke ("field", ime, minimum) ili ("flag", ime, None): bar jedna mora
    # važiti da dokaz uopšte PRIPADA nivou (prazno = bez poda, nivo 1).
    require_any: tuple


@dataclass(frozen=True)
class DifficultyProfile:
    profile_id: str
    description: str
    levels: MappingProxyType
    prompt_lines: tuple

    def prompt_block(self) -> str:
        """Identičan blok ide i Tutoru i Recenzentu — jedan izvor teksta."""
        return "\n".join(self.prompt_lines)


def _build_level(profile_id, level_key, raw):
    try:
        max_counts = {}
        for name, cap in dict(raw.get("max", {})).items():
            if name not in _NUMERIC_FIELDS:
                raise DifficultyProfileError(
                    f"profil {profile_id}: nepoznato polje kapaciteta {name!r}")
            max_counts[name] = int(cap)
        forbidden = tuple(raw.get("forbid_flags", ()))
        for flag in forbidden:
            if flag not in _FLAG_FIELDS:
                raise DifficultyProfileError(
                    f"profil {profile_id}: nepoznata zastavica {flag!r}")
        atoms = []
        for atom in raw.get("require_any", ()):
            if "field" in atom:
                name = str(atom["field"])
                if name not in _NUMERIC_FIELDS:
                    raise DifficultyProfileError(
                        f"profil {profile_id}: nepoznato polje poda {name!r}")
                atoms.append(("field", name, int(atom["min"])))
            elif "flag" in atom:
                name = str(atom["flag"])
                if name not in _FLAG_FIELDS:
                    raise DifficultyProfileError(
                        f"profil {profile_id}: nepoznata zastavica poda {name!r}")
                atoms.append(("flag", name, None))
            else:
                raise DifficultyProfileError(
                    f"profil {profile_id}: neispravan atom poda {atom!r}")
        return LevelSpec(
            max_counts=MappingProxyType(max_counts),
            forbidden_flags=forbidden,
            require_any=tuple(atoms),
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, DifficultyProfileError):
            raise
        raise DifficultyProfileError(
            f"profil {profile_id}: neispravan nivo {level_key}: {error}") from error


def _build_profile(profile_id, raw):
    levels = {}
    for level_key in ("1", "2", "3"):
        if level_key not in raw.get("levels", {}):
            raise DifficultyProfileError(
                f"profil {profile_id}: nedostaje nivo {level_key}")
        levels[int(level_key)] = _build_level(profile_id, level_key,
                                              raw["levels"][level_key])
    return DifficultyProfile(
        profile_id=str(profile_id),
        description=str(raw.get("description", "")),
        levels=MappingProxyType(levels),
        prompt_lines=tuple(raw.get("prompt_lines", ())),
    )


def _load():
    global _cache
    if _override is not None:
        return _override
    if _cache is None:
        with _lock:
            if _cache is None:
                if not DATA_PATH.exists():
                    # Bez artefakta nijedna lekcija nema profil — ponašanje
                    # ostaje kao prije uvođenja profila.
                    _cache = ({}, {})
                else:
                    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
                    profiles = {
                        profile_id: _build_profile(profile_id, raw)
                        for profile_id, raw in payload.get("profiles", {}).items()
                    }
                    assignments = {}
                    for row in payload.get("assignments", ()):
                        family = str(row.get("primary_family", ""))
                        profile_id = str(row.get("profile", ""))
                        if not family or profile_id not in profiles:
                            raise DifficultyProfileError(
                                f"neispravna dodjela profila: {row!r}")
                        assignments[family] = profile_id
                    _cache = (profiles, assignments)
    return _cache


def all_profiles():
    return dict(_load()[0])


def family_assignments():
    return dict(_load()[1])


def resolve_for_context(context):
    """Profil za OVU lekciju, ili None → globalna rubrika ostaje mjerodavna.

    Čita ISKLJUČIVO server-vlasnički LessonContext: primarnu porodicu iz
    zamrznutog mapiranja (plus deklarativni izuzeci u
    data/routing_overrides.json). Nikad ne prima ništa iz modelovog
    payloada — model ne može izabrati blažu rubriku.

    Batch #4: profil važi za SVAKU lekciju dodijeljene porodice, bez obzira
    na strategiju izvršenja. U F5G je važio samo za lekcije bez semantičkog
    ugovora (konzervativna zaštita tadašnjih 272 deterministički pokrivene
    lekcije); aktivacijom profiliranih lekcija (praktična Pitagora, sistemske
    priče, udaljenost tačaka) ta bi ograda VRATILA globalni sudar na
    model-put istih lekcija, a deterministički dokazi po nivou dokazano
    zadovoljavaju profile (tests/test_batch4_deterministic.py). Ista granica
    za obje strategije = jedan autoritet težine po lekciji."""
    if context is None:
        return None
    primary = getattr(context, "primary_family", "") or ""
    if not primary:
        return None
    profiles, assignments = _load()
    profile_id = assignments.get(primary)
    return profiles.get(profile_id) if profile_id else None


def level_errors(profile, evidence, target_level):
    """Deterministička provjera dokaza prema profilu — ista semantika poziva
    kao globalna `difficulty_evidence_errors`, granice iz podataka lekcije.

    Kodovi su interni (samo logovi, CLAUDE.md pravilo 7) i čine zatvoren skup:
    negative_<polje>, invalid_target_difficulty_level,
    level_<n>_below_lesson_relative_floor,
    level_<n>_above_lesson_relative_cap,
    level_<n>_forbids_flag:<zastavica>."""
    errors = []
    for field in _NUMERIC_FIELDS:
        if getattr(evidence, field) < 0:
            errors.append(f"negative_{field}")
    spec = profile.levels.get(target_level) if target_level in (1, 2, 3) else None
    if spec is None:
        errors.append("invalid_target_difficulty_level")
        return tuple(errors)
    if spec.require_any and not any(
            (kind == "field" and getattr(evidence, name) >= minimum)
            or (kind == "flag" and getattr(evidence, name))
            for kind, name, minimum in spec.require_any):
        errors.append(f"level_{target_level}_below_lesson_relative_floor")
    if any(getattr(evidence, name) > cap for name, cap in spec.max_counts.items()):
        errors.append(f"level_{target_level}_above_lesson_relative_cap")
    for flag in spec.forbidden_flags:
        if getattr(evidence, flag):
            errors.append(f"level_{target_level}_forbids_flag:{flag}")
    return tuple(errors)


def reset_cache():
    """Samo za testove/CI."""
    global _cache
    with _lock:
        _cache = None


class override_profiles:
    """Privremeno zamijeni registar (testovi). Ne poznaje nijedan ID lekcije."""

    def __init__(self, profiles, assignments):
        self._value = (dict(profiles), dict(assignments))
        self._previous = None

    def __enter__(self):
        global _override
        self._previous = _override
        _override = self._value
        return self._value

    def __exit__(self, *exc):
        global _override
        _override = self._previous
        return False
