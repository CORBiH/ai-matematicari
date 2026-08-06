"""Vozi DOM-nivo testove stvarnih rukovalaca iz templates/index.html.

ZAŠTO POSTOJI: projekt nema JS test alat (nema package.json, jsdom, jest,
vitest ni playwright), a Faza 4E je popravila TAČNO ono što živi u pregledaču —
klik na MCQ opciju i payload chipa „Uradi ga ti“. Statička provjera stringova
ne dokazuje ponašanje dugmeta, a backend FakeLLM testovi ne dodiruju DOM.

`tests/frontend/` izvršava IZVORNU skriptu iz index.html u `node:vm` nad malim
stubom pregledača (`tests/frontend/browser_stub.js`), koristeći isključivo
Node-ove ugrađene module — nijedan paket se ne instalira.

GRANICA (izričito): ovo nije pravi pregledač. Nema layouta, CSS-a, stvarne
isporuke događaja, MathJaxa ni mreže. Dokazuje se logika rukovaoca — koliko
zahtjeva ode, šta je u payloadu i kakvo stanje ostane — ne vizuelni rezultat.
Kad Node nije dostupan, test se PRESKAČE: preskočeno nikad nije dokaz.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUITE_DIR = ROOT / "tests" / "frontend"
SUITES = sorted(str(path) for path in SUITE_DIR.glob("*.test.js"))

node = shutil.which("node")
pytestmark = pytest.mark.skipif(
    node is None, reason="Node.js nije dostupan — DOM testovi se ne mogu izvršiti"
)


def test_frontend_dom_suite_passes():
    assert SUITES, "nema nijednog DOM testa u tests/frontend/"
    result = subprocess.run(
        [node, "--test", *SUITES],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, (
        "DOM testovi stranice su pali:\n" + (result.stdout or "") + (result.stderr or "")
    )
    assert "# fail 0" in (result.stdout or "")
