"""Thread-safe in-memory Practice sesije (Faza 1 — bez baze).

Stanje se smije izgubiti na restart. Očekivani odgovor (expected_answer_summary)
živi SAMO ovdje — nikad se ne šalje browseru niti vraća u next_state.
"""
import copy
import threading

from matbot import config
from matbot.contracts import registry
from matbot.topics import oblast_id_for_topic


def _fresh_session(session_id, curriculum_fingerprint, grade, lesson_id,
                   lesson_title, oblast_id, oblast, mode):
    return {
        "session_id": session_id,
        "context_key": curriculum_fingerprint,
        "curriculum_fingerprint": curriculum_fingerprint,
        "grade": grade,
        "mode": mode,
        "lesson_id": lesson_id,
        "lesson_title": lesson_title,
        "oblast_id": oblast_id,
        "oblast": oblast,
        "current_task": "",
        "expected_answer_summary": "",
        "hint_level": 0,
        "difficulty": "standard",
        # Univerzalni troslojni kontroler težine (matbot/difficulty_level.py),
        # 1/2/3 — server-owned, dijeli ga SVIH 534 lekcija. Polje postoji i
        # kad je MATBOT_PRACTICE_DIFFICULTY_LEVELS isključen (podrazumijevano):
        # dok je isključen, nijedan turn ga ne mijenja niti čita za odluku —
        # vidi matbot/practice.py.
        "difficulty_level": 1,
        "correct_streak": 0,
        "recent_tasks": [],   # max MAX_RECENT_TASKS tekstova prethodnih zadataka
        "recent_turns": [],   # max MAX_RECENT_TURNS parova {"student":..., "tutor":...}
        "current_options": [],       # [{"id": "a", "text": "..."}, ...] POST-shuffle
        "correct_option_id": "",     # npr. "b" — nikad se ne šalje browseru prije reveala
        "wrong_option_ids": [],      # ids kliknuti i pogrešni, redoslijedom
        "task_completed": False,     # True nakon tačnog klika / 2. pogrešnog / "uradi ga ti"
        "last_choice_turn_id": "",   # client_turn_id zadnjeg obrađenog choice_answer
        "last_choice_response": None,  # cache odgovora za idempotentan retry
        # --- napredovanje kroz porodice zadataka (vidi matbot/task_families.py) ---
        # Sva ova polja žive UNUTAR sesije, a context_key sadrži lesson_id — pa
        # promjena lekcije automatski daje svježe napredovanje (izolacija po temi).
        "current_family": "",            # porodica AKTIVNOG zadatka
        "recently_used_families": [],    # hronološki, max MAX_RECENT_FAMILIES
        "correctly_completed_families": [],  # porodice savladane tačnim odgovorom
        "retry_required": False,         # True nakon netačnog → ista porodica ponovo
        "last_result": "",               # "", "correct" ili "incorrect"
        "recent_task_signatures": [],    # max MAX_RECENT_SIGNATURES potpisa zadataka
    }


class SessionStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def load(self, session_id, grade, lesson_id, lesson_title, oblast, mode,
             oblast_id=""):
        """Vrati KOPIJU sesije za dati kontekst — NIKAD referencu na objekat
        koji živi u internom storeu. Promjena razreda/lekcije/moda resetuje
        Practice stanje (novi kontekst = svjež zadatak).

        Copy-on-write garancija: pozivalac (practice.run_practice_turn) smije
        slobodno mutirati vraćeni dict prije/poslije AI poziva — te izmjene NE
        utiču na store dok se eksplicitno ne pozove save(). Oba grantica ispod
        vraćaju svježe izgrađene objekte: _fresh_session() pravi nov dict, a
        copy.deepcopy(existing) pravi potpuno nezavisnu kopiju (uključujući
        ugniježdene liste recent_tasks/recent_turns)."""
        oblast_id = oblast_id or oblast_id_for_topic(lesson_id)
        # Verzija ugovora je dio otiska: izmjena ugovora lekcije mora poništiti
        # aktivni zadatak i napredovanje kroz POSTOJEĆI mehanizam ispod, bez
        # ijednog novog puta invalidacije. Prazno za lekcije bez ugovora.
        contract_version = registry.contract_version_for(lesson_id)
        curriculum_fingerprint = f"{grade}|{oblast_id}|{lesson_id}|{mode}|{contract_version}"
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is None:
                return _fresh_session(
                    session_id, curriculum_fingerprint, grade, lesson_id,
                    lesson_title, oblast_id, oblast, mode,
                )
            if (existing.get("curriculum_fingerprint", existing.get("context_key"))
                    != curriculum_fingerprint):
                fresh = _fresh_session(
                    session_id, curriculum_fingerprint, grade, lesson_id,
                    lesson_title, oblast_id, oblast, mode,
                )
                # Promjena kurikularnog konteksta je sama po sebi autoritativna
                # invalidacija. Ne čekamo uspješan AI odgovor: inače bi povratak
                # na staru lekciju mogao oživjeti njen zadatak i napredovanje.
                self._sessions.pop(session_id, None)
                self._sessions[session_id] = copy.deepcopy(fresh)
                while len(self._sessions) > config.MAX_SESSIONS_IN_MEMORY:
                    oldest = next(iter(self._sessions))
                    del self._sessions[oldest]
                return copy.deepcopy(fresh)
            return copy.deepcopy(existing)

    def save(self, session):
        """Upisuje sesiju; primjenjuje limite historije i limit ukupnog broja sesija."""
        session["recent_tasks"] = session["recent_tasks"][-config.MAX_RECENT_TASKS:]
        session["recent_turns"] = session["recent_turns"][-config.MAX_RECENT_TURNS:]
        session["recently_used_families"] = \
            session["recently_used_families"][-config.MAX_RECENT_FAMILIES:]
        session["recent_task_signatures"] = \
            session["recent_task_signatures"][-config.MAX_RECENT_SIGNATURES:]
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
