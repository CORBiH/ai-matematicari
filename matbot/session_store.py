"""Thread-safe in-memory Practice sesije (Faza 1 — bez baze).

Stanje se smije izgubiti na restart. Očekivani odgovor (expected_answer_summary)
živi SAMO ovdje — nikad se ne šalje browseru niti vraća u next_state.
"""
import copy
import threading

from matbot import config


def _fresh_session(session_id, context_key, grade, lesson_id, lesson_title, oblast):
    return {
        "session_id": session_id,
        "context_key": context_key,
        "grade": grade,
        "lesson_id": lesson_id,
        "lesson_title": lesson_title,
        "oblast": oblast,
        "current_task": "",
        "expected_answer_summary": "",
        "hint_level": 0,
        "difficulty": "standard",
        "correct_streak": 0,
        "recent_tasks": [],   # max MAX_RECENT_TASKS tekstova prethodnih zadataka
        "recent_turns": [],   # max MAX_RECENT_TURNS parova {"student":..., "tutor":...}
        "current_options": [],       # [{"id": "a", "text": "..."}, ...] POST-shuffle
        "correct_option_id": "",     # npr. "b" — nikad se ne šalje browseru prije reveala
        "wrong_option_ids": [],      # ids kliknuti i pogrešni, redoslijedom
        "task_completed": False,     # True nakon tačnog klika / 2. pogrešnog / "uradi ga ti"
        "last_choice_turn_id": "",   # client_turn_id zadnjeg obrađenog choice_answer
        "last_choice_response": None,  # cache odgovora za idempotentan retry
    }


class SessionStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def load(self, session_id, grade, lesson_id, lesson_title, oblast, mode):
        """Vrati KOPIJU sesije za dati kontekst — NIKAD referencu na objekat
        koji živi u internom storeu. Promjena razreda/lekcije/moda resetuje
        Practice stanje (novi kontekst = svjež zadatak).

        Copy-on-write garancija: pozivalac (practice.run_practice_turn) smije
        slobodno mutirati vraćeni dict prije/poslije AI poziva — te izmjene NE
        utiču na store dok se eksplicitno ne pozove save(). Oba grantica ispod
        vraćaju svježe izgrađene objekte: _fresh_session() pravi nov dict, a
        copy.deepcopy(existing) pravi potpuno nezavisnu kopiju (uključujući
        ugniježdene liste recent_tasks/recent_turns)."""
        context_key = f"{grade}|{mode}|{lesson_id}|{oblast}"
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is None or existing["context_key"] != context_key:
                fresh = _fresh_session(session_id, context_key, grade, lesson_id, lesson_title, oblast)
                return fresh
            return copy.deepcopy(existing)

    def save(self, session):
        """Upisuje sesiju; primjenjuje limite historije i limit ukupnog broja sesija."""
        session["recent_tasks"] = session["recent_tasks"][-config.MAX_RECENT_TASKS:]
        session["recent_turns"] = session["recent_turns"][-config.MAX_RECENT_TURNS:]
        with self._lock:
            self._sessions.pop(session["session_id"], None)
            self._sessions[session["session_id"]] = copy.deepcopy(session)
            while len(self._sessions) > config.MAX_SESSIONS_IN_MEMORY:
                oldest = next(iter(self._sessions))
                del self._sessions[oldest]

    def peek(self, session_id):
        """Samo za testove/dijagnostiku: kopija sesije ili None."""
        with self._lock:
            s = self._sessions.get(session_id)
            return copy.deepcopy(s) if s else None

    def clear(self):
        with self._lock:
            self._sessions.clear()
