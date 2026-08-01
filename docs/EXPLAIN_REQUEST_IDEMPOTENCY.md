# Explain/Quick request idempotency — design note (C-6)

**Status: design only. No code in this document has been implemented.**
Tracked as C-6 in [CURRENT_STATE.md](CURRENT_STATE.md), explicitly deferred by
the Phase F scope of the 2026-08-01 Explain-fix pass.

## The risk, precisely

`streamTutorRequest` (`templates/index.html`) POSTs to `/chat/stream`. The
server (`matbot/api.py:_guarded_chat_turn`) runs the full guard chain, makes
the **one** model call, and returns a single `event: done` SSE frame
(`api.py:289`). The risk window is entirely on the transport side, *after*
that model call has already happened and been paid for:

1. The model call succeeds server-side.
2. The SSE response is being written back, but the connection drops (client
   navigates away, mobile network hiccup, proxy timeout, browser tab
   backgrounded) *before* the `done` frame is fully received.
3. `streamTutorRequest` returns `null` in this case (`index.html:2110`:
   "prekid toka: sa djelimičnim tekstom završi lokalno... bez teksta →
   fallback").
4. The caller (`sendTutorMsg`) falls back to `jsonTutorRequest`, POSTing
   `/chat` with the **same payload** — which runs the full guard chain again
   and makes a **second** model call for what the student experienced as one
   turn.

`client_turn_id` already exists in the payload and is validated
(`validation.py:_check_id_format`), but today it is only *used* for
idempotency in one place: `practice.py:_handle_choice_answer` caches the
response for a repeated `choice_answer` turn (`last_choice_turn_id` /
`last_choice_response` in `session_store.py`). Explain and Quick are
stateless by design (no `SessionStore` entry) and never consult
`client_turn_id` at all.

Known-blocking statuses (400/401/403/409/413/429) are already excluded from
the fallback (`isKnownBlockingStatus`, `index.html:2027`) — those are cheap
and correctly never double-call. The gap is specifically: **model call
succeeded, response transport failed.**

## Options considered

### 1. Never fall back to `/chat` once SSE bytes have been received

Distinguish "stream never started" (network/5xx before any byte — safe to
fall back, no call happened yet) from "stream started but didn't finish"
(bytes were received, so the model call almost certainly ran — do **not**
retry; show a "connection lost, refresh to see if it went through" message
instead).

- **Pros:** smallest possible change — a few lines in `streamTutorRequest`;
  no server changes; no new storage; nothing to expire; works identically
  under any number of workers, because it never depends on server memory.
- **Cons:** the student loses the answer they already paid for. They must
  retype the question (though not lose the OpenAI spend, just the UX). Also
  imperfect: a connection can drop *after* the `done` event starts writing
  but the client only received the SSE headers, not the `event: done` line —
  "received some bytes" isn't the same guarantee as "received the answer."

### 2. Server-side bounded response cache keyed by `(session_id, client_turn_id)`

Before calling the model, check the cache for this key. If present, return
the cached response instead of calling again. After a successful call, store
`{result_dict, timestamp}` for a short TTL (e.g. 60–120 s — long enough to
cover a realistic SSE-then-JSON-fallback retry, short enough that it can't
serve a stale answer to a much later, unrelated message that happens to
reuse an id). Apply this uniformly to Explain and Quick, generalizing the
mechanism `practice.py` already has for `choice_answer`.

- **Pros:** the student actually gets their answer on the fallback request,
  with **zero** additional model calls. Directly extends an existing,
  already-tested pattern (`last_choice_turn_id`/`last_choice_response`)
  instead of inventing a new mechanism.
- **Cons:** more moving parts — a bounded, thread-safe, TTL-evicting cache
  (`matbot/turnlock.py`'s registry-with-eviction pattern is the right
  template, not a new dependency). Must key correctly (see below) to avoid
  serving turn N's answer to turn N+1. In-memory only, so it does **not**
  survive a restart or work across multiple gunicorn workers (see
  "Concurrency" below) — acceptable today (`WEB_CONCURRENCY=1` by
  deliberate architecture choice, see [DEPLOYMENT.md](DEPLOYMENT.md)), but a
  real constraint if that ever changes.

### 3. One transport for Explain/Quick (drop SSE, always use plain `/chat`)

Remove the SSE attempt for these two modes; only Practice (if it ever needs
progressive rendering) keeps it, or drop SSE outright everywhere.

- **Pros:** eliminates the *entire class* of risk — there is no second
  transport to fall back to, so there is no fallback-triggered duplicate call.
  Zero new server state.
- **Cons:** loses the progressive-rendering UX benefit SSE gives (though
  today it's already "one `done` frame," not token-by-token — see
  `ARCHITECTURE.md`: "Faza 1: bez token-po-token delta događaja"). This is a
  bigger behavioral/architecture change than the bug warrants, and it
  removes a working feature to fix an edge case that options 1 and 2 fix
  more narrowly.

### 4. Explicit request-state machine (`pending` / `completed` / `unknown`)

Server records `pending` for `(session_id, client_turn_id)` before the model
call, `completed` (with the result) after. A retry with the same id: if
`completed`, return the cached result (like option 2); if `pending` (a
genuinely concurrent duplicate, e.g., double-submit), reject with 409 (the
turn lock already does this); if absent, proceed normally.

- **Pros:** most complete and most correct under concurrent/racing retries
  — cleanly answers "is this a replay or a genuinely new request?"
- **Cons:** is really option 2 plus the state machine already partially
  implemented by `TurnLockRegistry` (which *is* a pending/absent state
  today, just without the `completed` cache half). Building this as a
  *separate* mechanism from the turn lock would duplicate logic; building it
  by *extending* the turn lock is materially the same amount of work as
  option 2, just described more formally.

## Recommendation

**Option 2, implemented as a small extension of the existing turn-lock
registry, not a new subsystem.** Concretely (for a future pass, not this one):

- Extend `TurnLockRegistry` (or add a sibling `TurnResultCache` following its
  exact concurrency pattern — one registry lock guarding a dict, same as
  `matbot/turnlock.py`) to store `{session_id: {client_turn_id, result,
  stored_at}}` — **one slot per session**, not one per turn, since only the
  *immediately preceding* turn can plausibly be retried; this bounds memory
  identically to today's `SessionStore` (one slot per active session, capped
  by `MAX_SESSIONS_IN_MEMORY`).
- On entry to `_guarded_chat_turn`, if `client_turn_id` matches the cached
  entry for this `session_id` **and** the cached entry is within TTL, return
  the cached result immediately — before acquiring the turn lock, before any
  model call.
- On a successful turn (any mode), store the result keyed by this session's
  `client_turn_id`, overwriting the previous entry (only the latest turn
  needs to be replayable).
- Reuse `client_turn_id` exactly as already validated and threaded through
  today (`_build_turn`, `validation._check_id_format`) — no new client
  changes needed; the frontend already generates and resends the same id for
  the SSE attempt and its JSON fallback (`index.html`: "isti `client_turn_id`
  ... ponovo korišten za JSON fallback ISTOG slanja").
- Combine with a **cheap version of option 1** on the frontend regardless:
  don't blindly fall back on *every* stream failure — only fall back when
  it's plausible no `done` event was ever sent, to avoid burning a round
  trip on the cache lookup for cases that are obviously a pre-call failure
  (this is a UX/latency refinement, not a correctness requirement, since the
  cache makes even an unnecessary fallback harmless).

This is the smallest change that actually recovers the student's answer
(unlike option 1 alone) without discarding a working feature (unlike option
3) or building a second state machine next to one that already exists
(unlike option 4 as a separate mechanism).

## Addressing the required concerns

- **Concurrency.** Same discipline as `TurnLockRegistry`: one `threading.Lock`
  guards the whole registry; get-or-create and read/write of a slot happen
  under that lock, never in two separate critical sections (the exact bug
  class documented in `turnlock.py`'s own docstring must not be
  reintroduced).
- **Process-local vs. multi-worker storage.** In-memory, therefore
  **process-local** — correct only under the current `WEB_CONCURRENCY=1`
  architecture (see [DEPLOYMENT.md](DEPLOYMENT.md); `SessionStore`,
  `RateLimiter`, and `TurnLockRegistry` all share this exact constraint
  already). If concurrency is ever raised, this cache (and the other three)
  would need a shared store (e.g. Redis) — out of scope until that
  architectural change is actually made.
- **Cache expiry.** Short TTL (60–120 s is enough to cover an SSE-drop +
  immediate JSON fallback; the frontend's own abort timeout is 60 s, so
  nothing legitimate retries later than that). Expire lazily on next access
  (compare `stored_at`), same style as `RateLimiter`'s windows — no
  background sweep thread needed.
- **Duplicate simultaneous requests.** Already handled by the existing turn
  lock (`try_acquire` returns `False` → 409) for genuinely concurrent
  requests; the new cache only matters for **sequential** retries after the
  first request already released the lock.
- **Replay after response loss.** This is the primary case the cache exists
  for: the model call completed, the *response never reached the browser*,
  the client retries with the same `client_turn_id`, and the cache returns
  the already-computed answer with no new call.
- **No storage of images/base64.** Unaffected — the cached value is the
  *outgoing JSON response dict* (`{"answer": ..., "status": ..., ...}`),
  never the request payload, and Quick's image is already never persisted
  anywhere (`imageinput.py`, `quick.py`: "slika VAŽI SAMO ZA OVAJ turn"). The
  cache must store only the response, never the request.
- **No accidental reuse across grades/modes/messages.** The cache key is
  `(session_id, client_turn_id)`. `client_turn_id` is generated fresh by the
  frontend for every user send (`crypto.randomUUID()`), including grade/exam
  changes — a different message always gets a different id, so a stale
  cache entry can only ever match its own exact retry, never a different
  turn. The one-slot-per-session design means even a *forgotten* stale entry
  is overwritten by the next real turn, not accumulated.
- **Behavior after infrastructure failure.** In-memory cache is lost on
  restart — same as `SessionStore` today. A retry after a restart simply
  proceeds as a normal (uncached) turn and makes a fresh model call; this is
  a *correctness-neutral* fallback (worst case: one duplicate call across a
  restart, which is the status quo everywhere else in the system, not a
  regression).
