"""Thread-safe in-memory rate limiter — fiksni prozori (minut + sat) po ključu.

Namjerno bez Redis-a i bez baze: radi ispravno pod trenutnim Gunicorn
podešavanjem (1 worker, više threadova) jer svi threadovi dijele isti proces i
istu memoriju. Brojači se gube na restart — prihvatljivo u ovoj fazi (dokumentovano
ograničenje, ne bug). Provjera i uvećanje brojača dešavaju se ATOMSKI unutar
jednog lock-a, pa paralelni zahtjevi ne mogu "provući" više prolaza nego što
limit dozvoljava (race condition zaštićena).
"""
import threading
import time


class _Window:
    __slots__ = ("count", "bucket")

    def __init__(self, bucket):
        self.count = 0
        self.bucket = bucket


class RateLimiter:
    def __init__(self, per_minute, per_hour, max_tracked_keys=20000):
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._max_tracked_keys = max_tracked_keys
        self._lock = threading.Lock()
        self._minute = {}
        self._hour = {}

    def check(self, key, now=None):
        """Vraća (allowed: bool, retry_after_seconds: int). Brojač se uvećava
        SAMO kad je zahtjev dozvoljen — odbijen pokušaj ne troši budžet, tako
        da legitiman naredni pokušaj (poslije čekanja) prolazi normalno."""
        now = now if now is not None else time.time()
        minute_bucket = int(now // 60)
        hour_bucket = int(now // 3600)

        with self._lock:
            self._evict_if_too_large(self._minute)
            self._evict_if_too_large(self._hour)

            mw = self._minute.get(key)
            if mw is None or mw.bucket != minute_bucket:
                mw = _Window(minute_bucket)
                self._minute[key] = mw
            hw = self._hour.get(key)
            if hw is None or hw.bucket != hour_bucket:
                hw = _Window(hour_bucket)
                self._hour[key] = hw

            if mw.count >= self.per_minute:
                retry_after = max(1, 60 - int(now % 60))
                return False, retry_after
            if hw.count >= self.per_hour:
                retry_after = max(1, 3600 - int(now % 3600))
                return False, retry_after

            mw.count += 1
            hw.count += 1
            return True, 0

    def _evict_if_too_large(self, bucket_dict):
        # Poziva se pod self._lock. Bez pozadinskog thread-a: samo pri
        # prekoračenju kapaciteta uklanja najstarije unose (FIFO — Python
        # dict čuva redoslijed umetanja).
        while len(bucket_dict) > self._max_tracked_keys:
            oldest_key = next(iter(bucket_dict))
            del bucket_dict[oldest_key]

    def reset(self):
        """Samo za testove/dijagnostiku — brišе sve brojače."""
        with self._lock:
            self._minute.clear()
            self._hour.clear()
