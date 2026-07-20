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

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

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
    return index


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
