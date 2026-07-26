"""Minimalna zaštita od paralelnih turnova iste session_id — bez SQLite baze
još nema potpune idempotencije (to dolazi u Fazi 2), ali ovo sprječava da ISTA
sesija istovremeno pokrene više OpenAI poziva (dupli-submit, dupli tab, bug u
frontendu). Thread-safe: jedan threading.Lock po session_id, čuvan u registru
zaštićenom svojim globalnim lock-om.

VAŽNO (naučeno testiranjem, vidi tests/test_predeploy_hardening.py): dohvat-ili-
napravi lock i STVARNI pokušaj zaključavanja (`.acquire()`) moraju biti JEDNA
atomska operacija pod _registry_lock — isto važi za stvarno otključavanje i
eventualno brisanje unosa iz registra. Ranija verzija je te korake radila u
dvije odvojene registry_lock sekcije (get-lock, pa odvojeno acquire; release,
pa odvojeno provjeri-i-obriši) — to je ostavljalo mikroskopski vremenski
prozor u kojem su DVA RAZLIČITA Lock objekta mogla biti istovremeno "aktivna"
za istu session_id (jedan thread upravo dobio referencu na stari, još
neobrisan lock i čeka da ga zaključa, dok drugi thread u međuvremenu taj isti
lock obriše iz registra jer ga je u tom trenutku vidio kao slobodan). Pošto je
`Lock.acquire(blocking=False)` i `Lock.release()` uvijek brz i nikad ne čeka,
držanje _registry_lock tokom njih je bezopasno i eliminiše taj prozor u
potpunosti."""
import threading


class TurnLockRegistry:
    def __init__(self, max_tracked_sessions=20000):
        self._max_tracked_sessions = max_tracked_sessions
        self._registry_lock = threading.Lock()
        self._locks = {}

    def _evict_one_unlocked_locked(self):
        # Poziva se pod self._registry_lock. Nikad ne uklanja lock koji je
        # trenutno držan (tekući turn) — samo najstariji SLOBODAN unos.
        for key, lock in list(self._locks.items()):
            if not lock.locked():
                del self._locks[key]
                return

    def try_acquire(self, session_id):
        """Vraća True ako je ova sesija upravo dobila ekskluzivan pristup;
        False ako je već zauzeta (drugi turn u toku). Dohvat-ili-napravi lock
        i stvarni pokušaj zaključavanja su JEDNA atomska operacija — vidi
        napomenu u docstringu modula."""
        with self._registry_lock:
            lock = self._locks.get(session_id)
            if lock is None:
                if len(self._locks) >= self._max_tracked_sessions:
                    self._evict_one_unlocked_locked()
                lock = threading.Lock()
                self._locks[session_id] = lock
            return lock.acquire(blocking=False)

    def release(self, session_id):
        """Sigurno se poziva iz finally-bloka — nikad ne baca izuzetak čak i
        ako lock iz nekog razloga nije bio zaključan (npr. duplo oslobađanje).

        Stvarno otključavanje i eventualno brisanje unosa (kad je poslije
        otključavanja i dalje slobodan) su JEDNA atomska operacija pod
        _registry_lock — vidi napomenu u docstringu modula."""
        with self._registry_lock:
            lock = self._locks.get(session_id)
            if lock is None:
                return
            try:
                lock.release()
            except RuntimeError:
                pass  # već oslobođen — ne smije srušiti request
            if not lock.locked():
                del self._locks[session_id]

    def clear(self):
        """Samo za testove/dijagnostiku."""
        with self._registry_lock:
            self._locks.clear()

    def size(self):
        """Samo za testove/dijagnostiku — trenutni broj praćenih session_id."""
        with self._registry_lock:
            return len(self._locks)
