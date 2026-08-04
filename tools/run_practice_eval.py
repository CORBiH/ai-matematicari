"""Ulazna tačka za live dijagnostiku moda „Vježbaj sa mnom“ (FAZA 1).

Namjerno je odvojena od `tools/run_live_release_gate.py`: release gate ostaje
netaknut, sa svojih 12 scenarija i 19 poziva, i ovaj program ga ni na koji
način ne mijenja, ne zaobilazi i ne zamjenjuje.

Kao i release gate:
  • koristi ISKLJUČIVO okruženje ovog procesa i NIKAD ne učitava `.env`;
  • nikad ne ispisuje ni jednu vrijednost tajne;
  • `--dry-run` i `--list` ne prave nijedan SDK poziv.

Primjeri:
    python tools/run_practice_eval.py --list
    python tools/run_practice_eval.py --wave A --dry-run
    python tools/run_practice_eval.py --wave A --max-model-calls 130
"""
from __future__ import annotations

import io
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def install_utf8_streams():
    """Konzola mora pisati UTF-8, inače bosanski tekst izađe kao mojibake.

    ZAŠTO POSTOJI (Talas A): sami izlazni fajlovi su ispravan UTF-8 — provjereno
    bajt po bajt. Ali Windows konzola pod PowerShell-om 5.1 podrazumijeva ANSI
    kodnu stranicu, pa `print` istog tog teksta daje `JednaÄine`, `kruÅ¾nica`,
    `NeÅ¡to`. Ovo je zato ISKLJUČIVO transportna popravka toka stdout/stderr;
    nijedan zapis na disk se ne mijenja.

    Vraća True kad su oba toka na UTF-8 (već bila ili upravo prebačena)."""
    ok = True
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") == "utf8":
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            try:
                buffer = getattr(stream, "buffer", None)
                if buffer is None:
                    ok = False
                    continue
                setattr(sys, name, io.TextIOWrapper(buffer, encoding="utf-8",
                                                    errors="replace", line_buffering=True))
            except Exception:
                ok = False
    return ok


def _prepare_environment():
    """Postavi SAMO ono bez čega se aplikacija ne može ni instancirati.

    `FLASK_SECRET_KEY` potpisuje embed token koji ista ova instanca odmah i
    verifikuje. Kad ga u okruženju nema, kuje se EFEMERAN ključ za ovaj proces:
    to je jedini način da se aplikacija podigne bez ijednog dodira `.env`-a.
    Vrijednost se nigdje ne ispisuje i nigdje ne zapisuje."""
    if not (os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY")):
        os.environ["FLASK_SECRET_KEY"] = "ephemeral-practice-eval-" + secrets.token_urlsafe(32)
        return "ephemeral (generated for this process only)"
    return "inherited from the process environment"


def main(argv=None):
    utf8_ok = install_utf8_streams()
    secret_source = _prepare_environment()
    # Uvoz TEK POSLIJE pripreme okruženja: matbot.config čita env pri uvozu.
    from tools.practice_eval import runner

    if not utf8_ok:
        print("WARNING: console is not UTF-8 — Bosnian output may render as mojibake; "
              "the files on disk are unaffected")
    print(f"secret key: {secret_source}")
    print("environment: process only — .env is never read by this program")
    return runner.main(argv)


if __name__ == "__main__":
    sys.exit(main())
