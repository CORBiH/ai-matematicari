"""Runtime topic-ID → canonical NPP tema identity.

The UI/embed can send a RUNTIME topic identifier that is not the canonical NPP id
(production sent ``12880`` for "Proširivanje razlomaka"). Template selection keys
on tema identity, so an unresolved runtime id silently meant "no coverage" and the
request fell through to free GPT generation — which produced an equation under a
fractions tema.

This module builds the id→tema index from the CONTENT LOADER (the curriculum Excel
is the source of truth). Nothing is hardcoded: every identifier column the loader
exposes is indexed, so new runtime ids work as soon as the sheet carries them.
"""
from __future__ import annotations

import importlib.util
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

from matbot.content_loader import load_master_content

# Columns that may carry an identifier the runtime could send.
_TOPIC_ID_FIELDS = ("npp_topic_id", "topic", "existing_ai_topic_ids")
_VIDEO_ID_FIELDS = ("thinkific_lesson_id", "linked_ai_topic_id", "lesson_url")
_TOPIC_NAME_FIELDS = ("tema_ui", "display_name", "selector_label")
_VIDEO_NAME_FIELDS = ("lesson_title",)

_cache: dict[Any, dict[str, "CanonicalTopic"]] = {}


def _fold(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _keys(value: Any) -> list[str]:
    """Split a cell into individual identifier tokens (cells may be lists)."""
    raw = _fold(value)
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[;,|]", raw) if p.strip()]
    return parts or [raw]


@dataclass(frozen=True)
class CanonicalTopic:
    npp_id: str
    oblast: str
    tema: str
    matched_by: str = ""

    @property
    def probe(self) -> str:
        """Tema probe for template selection: canonical id + human title."""
        return f"{self.npp_id} {self.tema}".strip()


@dataclass(frozen=True)
class TopicIdentity:
    """The ONE topic object Practice, Exam, template selection, validation and
    telemetry all consume.

    Keeping the runtime id, the canonical NPP id and the human titles as separate
    explicit fields is deliberate: production conflated them, so an unresolved
    runtime id ("29073") was indistinguishable from "no topic selected" and the
    request silently widened to the whole oblast.
    """
    grade: Any
    runtime_id: str = ""            # exactly what the client sent
    npp_id: str = ""                # canonical curriculum id, "" when unresolved
    oblast: str = ""
    tema: str = ""
    skill_ids: tuple[str, ...] = () # deterministic templates covering this tema
    resolved: bool = False          # runtime_id mapped to a canonical tema
    covered: bool = False           # a deterministic template exists for it

    @property
    def probe(self) -> str:
        return f"{self.npp_id} {self.tema}".strip()

    @property
    def is_exact_tema(self) -> bool:
        """A specific tema was selected (as opposed to a bare oblast)."""
        return bool(self.npp_id or self.tema)

    def to_dict(self) -> dict:
        return {
            "grade": self.grade, "runtime_id": self.runtime_id,
            "npp_id": self.npp_id, "oblast": self.oblast, "tema": self.tema,
            "skill_ids": list(self.skill_ids), "resolved": self.resolved,
            "covered": self.covered,
        }


#: Signature of a template-coverage lookup: ``(grade, oblast, probe) -> skill ids``.
SkillProvider = Callable[[Any, str, str], "tuple[str, ...]"]


def _legacy_skill_provider(grade: Any, oblast: str, probe: str) -> tuple[str, ...]:
    """Template coverage from the frozen legacy generator set.

    Distinguishes two different failure shapes on purpose:

      * ``task_templates`` itself does not exist — the intended state once it is
        deleted after the modes are cut over. That means "no known deterministic
        coverage", an honest, already-supported state (see ``TopicIdentity.covered``),
        so it is suppressed here.
      * ``task_templates`` exists but fails to import or execute (e.g. one of ITS
        OWN dependencies is broken). That is a real bug in retained code, unrelated
        to this module's deliberate decoupling, and must not be swallowed.

    ``importlib.util.find_spec`` answers "does this module exist" WITHOUT
    executing it, which is exactly the distinction needed: only genuine absence
    is caught; a broken import raises normally.
    """
    if importlib.util.find_spec("matbot.task_templates") is None:
        return ()
    from matbot import task_templates
    return tuple(t.skill_id for t in task_templates.select_templates(grade, oblast, probe))


def identify(grade: Any, raw_topic: Any = "", oblast: Any = "",
             fallback_name: Any = "",
             skill_provider: SkillProvider | None = None) -> TopicIdentity:
    """Build the canonical identity for a selected topic.

    ``skill_provider`` is injected so this module never *requires* the frozen
    ``task_templates``; omitting it keeps the existing legacy behavior exactly.
    """
    runtime = str(raw_topic or "").strip()
    resolved = resolve_topic(grade, runtime) if runtime else None
    if resolved is None and fallback_name:
        resolved = resolve_topic(grade, fallback_name)
    npp = resolved.npp_id if resolved else ""
    tema = resolved.tema if resolved else ""
    obl = (resolved.oblast if resolved else "") or str(oblast or "").strip()
    probe = f"{npp} {tema}".strip()
    # ``select_templates`` already implements the tema-first precedence: with an
    # empty probe it falls back to the OBLAST, which is exactly right for an
    # oblast-only selection. Computing coverage only when a tema exists silently
    # broke that path, so always ask it.
    skills = (skill_provider or _legacy_skill_provider)(grade, obl, probe)
    return TopicIdentity(
        grade=grade, runtime_id=runtime, npp_id=npp, oblast=obl, tema=tema,
        skill_ids=skills, resolved=resolved is not None, covered=bool(skills),
    )


def _build_index(grade: Any) -> dict[str, CanonicalTopic]:
    master = load_master_content(grade=grade)
    index: dict[str, CanonicalTopic] = {}

    def _add(key: Any, topic: CanonicalTopic) -> None:
        for k in _keys(key):
            # first writer wins: canonical ids are registered before aliases
            index.setdefault(k, topic)

    rows = master.get("topics") or []
    by_npp: dict[str, CanonicalTopic] = {}
    for row in rows:
        npp = str(row.get("npp_topic_id") or row.get("topic") or "").strip()
        if not npp:
            continue
        topic = CanonicalTopic(
            npp_id=npp,
            oblast=str(row.get("oblast_ui") or row.get("oblast") or "").strip(),
            tema=str(row.get("tema_ui") or row.get("display_name") or "").strip(),
        )
        by_npp[npp] = topic
        for field in _TOPIC_ID_FIELDS:
            _add(row.get(field), topic)
        for field in _TOPIC_NAME_FIELDS:
            _add(row.get(field), topic)

    # Video/Thinkific rows carry the RUNTIME lesson identifiers.
    for npp, videos in (master.get("videos_by_topic") or {}).items():
        topic = by_npp.get(str(npp).strip())
        if topic is None:
            continue
        for video in videos or []:
            if not isinstance(video, dict):
                continue
            for field in _VIDEO_ID_FIELDS:
                _add(video.get(field), topic)
            for field in _VIDEO_NAME_FIELDS:
                _add(video.get(field), topic)

    # THINKIFIC_RESOURCES maps a runtime lesson id to the NPP temas it serves
    # (``linked_npp_topic_ids`` may list several; the first that exists wins).
    for row in (master.get("thinkific_resources") or []):
        if not isinstance(row, dict):
            continue
        linked = _keys(row.get("linked_npp_topic_ids"))
        target = next((by_npp[c] for c in
                       (l.strip().upper() for l in linked) if c in by_npp), None)
        if target is None:
            # ids are folded to lowercase by _keys; match case-insensitively
            for cand in linked:
                for npp_key, topic_obj in by_npp.items():
                    if npp_key.lower() == cand:
                        target = topic_obj
                        break
                if target is not None:
                    break
        if target is None:
            continue
        for field in ("thinkific_lesson_id", "lesson_url", "old_ai_topic_id"):
            _add(row.get(field), target)
        _add(row.get("lesson_title"), target)

    # Operator-maintained overrides for runtime ids the workbook does not (yet)
    # carry. Data, not logic: adding an id never requires a code change.
    for runtime_id, npp in _load_overrides(grade).items():
        topic = by_npp.get(npp)
        if topic is not None:
            index[_fold(runtime_id)] = topic
    return index


#: ``{"<grade>": {"<runtime_id>": "<npp_topic_id>"}}`` — see the file's own note.
_OVERRIDES_ENV = "MATBOT_RUNTIME_TOPIC_MAP"
_OVERRIDES_FILENAME = "runtime_topic_overrides.json"


def _load_overrides(grade: Any) -> dict[str, str]:
    """Runtime-id → NPP id overrides for this grade, or {}.

    Sourced from ``$MATBOT_RUNTIME_TOPIC_MAP`` if set, else
    ``data/runtime_topic_overrides.json``. Never raises: a missing or malformed
    file simply means no overrides.
    """
    import json
    import os
    from pathlib import Path

    path = os.getenv(_OVERRIDES_ENV) or str(
        Path(__file__).resolve().parent.parent / "data" / _OVERRIDES_FILENAME)
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    section = raw.get(str(grade)) or raw.get(str(normalize_grade_safe(grade))) or {}
    if not isinstance(section, dict):
        return {}
    return {str(k): str(v) for k, v in section.items() if k and v}


def normalize_grade_safe(grade: Any) -> Any:
    try:
        return int(grade)
    except (TypeError, ValueError):
        return grade


def _index(grade: Any) -> dict[str, CanonicalTopic]:
    key = str(grade)
    if key not in _cache:
        _cache[key] = _build_index(grade)
    return _cache[key]


def reset_cache() -> None:
    _cache.clear()


def resolve_topic(grade: Any, raw: Any) -> CanonicalTopic | None:
    """Resolve any runtime topic identifier (or tema title) to canonical identity.

    Returns ``None`` when the value cannot be mapped — callers must then handle
    the missing coverage EXPLICITLY (never substitute an unrelated task)."""
    probe = _fold(raw)
    if not probe:
        return None
    index = _index(grade)
    hit = index.get(probe)
    if hit is not None:
        return CanonicalTopic(hit.npp_id, hit.oblast, hit.tema, matched_by="exact")
    # tolerate composite probes ("6-04-035 Proširivanje razlomaka")
    for token in _keys(probe) + probe.split():
        hit = index.get(token)
        if hit is not None:
            return CanonicalTopic(hit.npp_id, hit.oblast, hit.tema, matched_by="token")
    return None


def canonical_tema_probe(grade: Any, raw: Any, fallback: Any = "") -> str:
    """Tema probe for template selection.

    Resolved → ``"<npp_id> <tema title>"`` (so both id and keyword matching work).
    Unresolved → the raw value plus any fallback (e.g. lesson title) unchanged, so
    existing behavior is preserved."""
    topic = resolve_topic(grade, raw)
    if topic is not None:
        return topic.probe
    return " ".join(x for x in (str(raw or "").strip(), str(fallback or "").strip()) if x)
