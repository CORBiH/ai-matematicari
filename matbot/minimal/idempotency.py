# -*- coding: utf-8 -*-
"""Transport-level replay protection, keyed by ``client_turn_id``.

Production bug: the browser's SSE attempt and its JSON fallback for the SAME
user submission carry an IDENTICAL payload — including ``previous_next_state``,
since the frontend builds the payload once and reuses it for both attempts. If
the SSE request reached the engine and created a task but the response never
reached the browser (network drop mid-stream), the JSON fallback resent the
same ``previous_next_state`` and produced a SECOND task from what looks, at
the engine's pure-function level, like a perfectly normal turn.

``SessionState`` cannot resolve this on its own: a genuine retry sends the
PRE-turn state, unchanged, so nothing inside the round-tripped state can prove
"I already handled this." The one thing that stays constant across the retry
that the engine's own state does NOT capture is the physical session talking
to the server, so this cache lives here — outside the round-tripped state,
addressed by ``session_id`` — and remembers only the LATEST turn per session.

This is deliberately NOT a request-history subsystem: one slot per session,
holding only what is needed to replay a response.

SINGLE-FLIGHT. ``recall`` and ``remember`` are each individually locked, but
the actual turn processing happens BETWEEN them, unprotected — two calls for
the same (session, turn) arriving concurrently (an SSE attempt and a nearly
simultaneous JSON fallback, not just a sequential retry) can both ``recall``
and see nothing, both process the turn, and both ``remember`` (last write
wins). That is two engine invocations and two Sheets rows for one logical
turn — a real gap, not a theoretical one, since nothing serialized the check
against the process step.

``claim``/``release`` close that gap: the FIRST caller for a key becomes its
owner and proceeds to process; any concurrent caller for the SAME key gets
back the owner's ``_Waiter`` and blocks on it instead of touching the cache
or the engine at all. The owner calls ``release`` exactly once, on every exit
path (success or failure), which both frees the key and wakes any waiters
with the outcome. A waiter that is released with a ``None`` outcome (the
owner declined, or crashed hard enough to leave the slot stale beyond
``_STALE_SECONDS``) falls through and processes independently — never blocks
forever, and never re-enters the lock it is currently waiting on.
"""
from __future__ import annotations

import copy
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any

#: Distinct sessions remembered at once. A tutoring session belongs to one
#: student at a time, so this bounds memory, not correctness.
MAX_SESSIONS = 2000

#: A waiter that has sat this long is assumed to belong to an owner that
#: crashed without ever calling ``release`` (e.g. a killed worker process) —
#: the NEXT caller for that key discards it rather than waiting forever on a
#: slot nothing will ever complete. Ordinary requests finish in well under a
#: second, so this only ever fires on an already-broken turn.
_STALE_SECONDS = 60.0

_lock = Lock()
_cache: "OrderedDict[str, tuple[str, dict]]" = OrderedDict()


@dataclass
class _Waiter:
    """One in-flight (session, turn). Exactly one owner, any number of
    waiters, exactly one ``release``."""
    event: Event = field(default_factory=Event)
    result: dict | None = None
    started_at: float = field(default_factory=time.monotonic)


_inflight_lock = Lock()
_inflight: dict[tuple[str, str], _Waiter] = {}


def _key(session_id: Any, client_turn_id: Any) -> tuple[str, str] | None:
    session_id = str(session_id or "").strip()
    client_turn_id = str(client_turn_id or "").strip()
    if not session_id or not client_turn_id:
        return None
    return (session_id, client_turn_id)


def claim(session_id: Any, client_turn_id: Any) -> tuple[bool, "_Waiter | None"]:
    """Atomically decide who processes this turn.

    Returns ``(True, None)`` when there is no key to coordinate on (a caller
    with no turn id gets no protection, same as before this existed) or when
    THIS call is the owner. Returns ``(False, waiter)`` when another call is
    already processing this exact (session, turn); the caller should block on
    ``waiter.event`` and read ``waiter.result`` once it is set.
    """
    key = _key(session_id, client_turn_id)
    if key is None:
        return True, None
    with _inflight_lock:
        existing = _inflight.get(key)
        if existing is not None:
            if (time.monotonic() - existing.started_at) < _STALE_SECONDS:
                return False, existing
            # Abandoned by a crashed owner that never released it — discard
            # and claim fresh rather than wait on a slot nothing will finish.
            del _inflight[key]
        waiter = _Waiter()
        _inflight[key] = waiter
        return True, waiter


def release(session_id: Any, client_turn_id: Any, result: dict | None) -> None:
    """The owner's counterpart to ``claim`` — called exactly once, always.

    Frees the key (so the NEXT distinct turn is never blocked by this one)
    and wakes any waiters with the outcome, success or not.
    """
    key = _key(session_id, client_turn_id)
    if key is None:
        return
    with _inflight_lock:
        waiter = _inflight.pop(key, None)
    if waiter is not None:
        waiter.result = result
        waiter.event.set()


def recall(session_id: Any, client_turn_id: Any) -> dict | None:
    """The cached response for this exact (session, turn), or ``None``.

    ``None`` whenever either id is blank — a caller with no turn id gets no
    replay protection, which is the same behaviour this cache was added
    alongside, not a regression of it.
    """
    session_id = str(session_id or "").strip()
    client_turn_id = str(client_turn_id or "").strip()
    if not session_id or not client_turn_id:
        return None
    with _lock:
        entry = _cache.get(session_id)
        if entry is None or entry[0] != client_turn_id:
            return None
        _cache.move_to_end(session_id)
        return entry[1]


def remember(session_id: Any, client_turn_id: Any, response: dict) -> None:
    """Record the response produced for this (session, turn).

    Overwrites whatever was stored for the session before — only the LATEST
    turn is kept, by design.
    """
    session_id = str(session_id or "").strip()
    client_turn_id = str(client_turn_id or "").strip()
    if not session_id or not client_turn_id:
        return
    with _lock:
        _cache[session_id] = (client_turn_id, response)
        _cache.move_to_end(session_id)
        while len(_cache) > MAX_SESSIONS:
            _cache.popitem(last=False)


def mark_replay(cached: dict, client_turn_id: Any) -> dict:
    """A deep copy of ``cached`` with replay telemetry overlaid.

    Never mutates the stored entry — a second waiter reading the same cached
    response must not see a first waiter's overlay.
    """
    response = copy.deepcopy(cached)
    telemetry = dict(response.get("minimal_telemetry") or {})
    telemetry.update(idempotency_replay=True, task_transition="replayed",
                     client_turn_id=str(client_turn_id or ""))
    response["minimal_telemetry"] = telemetry
    routing = dict(response.get("minimal_routing") or {})
    routing.update(telemetry)
    response["minimal_routing"] = routing
    return response


def reset() -> None:
    """Test-only: clear all remembered turns AND any in-flight claims."""
    with _lock:
        _cache.clear()
    with _inflight_lock:
        _inflight.clear()
