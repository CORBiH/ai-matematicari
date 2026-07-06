"""Mala jezička zaštita: najčešći ekavski/srpski oblici → bosanska ijekavica.

Sistemska prompt pravila već traže ijekavicu, ali model povremeno "procuri"
("deo", "rešenje", "vežba"). Ovo je zadnja linija odbrane SAMO za vrlo česte i
jednoznačne oblike — namjerno kratka lista sa granicama riječi, da se ništa ne
prekoriguje (npr. "video" sadrži "deo" ali ga \\b štiti; matematički zapis nema
ovakve riječi pa je netaknut).
"""
from __future__ import annotations

import re

__all__ = ["to_ijekavica"]

# (ekavski obrazac, ijekavska zamjena) — poredak nebitan, obrasci disjunktni.
# Samo oblici koji su NEDVOSMISLENO ekavski; dvosmislene riječi se ne diraju.
_REPLACEMENTS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(rf"\b{pat}\b"), repl)
    for pat, repl in (
        (r"deo", "dio"),
        (r"dela", "dijela"),
        (r"delu", "dijelu"),
        (r"delovi", "dijelovi"),
        (r"delova", "dijelova"),
        (r"delovima", "dijelovima"),
        (r"rešenj(\w*)", r"rješenj\1"),
        (r"rešiti", "riješiti"),
        (r"reši", "riješi"),
        (r"vežb(\w*)", r"vježb\1"),
        (r"deljenj(\w*)", r"dijeljenj\1"),
        (r"deljiv(\w*)", r"djeljiv\1"),
        (r"deliti", "dijeliti"),
        (r"podeli", "podijeli"),
        (r"podeliti", "podijeliti"),
        (r"celi", "cijeli"),
        (r"cela", "cijela"),
        (r"celo", "cijelo"),
        (r"celu", "cijelu"),
        (r"celih", "cijelih"),
        (r"ceo", "cio"),
        (r"celin(\w*)", r"cjelin\1"),
        (r"primer(\w*)", r"primjer\1"),
        (r"sledeć(\w*)", r"sljedeć\1"),
        (r"sledec(\w*)", r"sljedec\1"),
        (r"uvek", "uvijek"),
        (r"posle", "poslije"),
        (r"ovde", "ovdje"),
        (r"gde", "gdje"),
        (r"dve", "dvije"),
        (r"lepo", "lijepo"),
        (r"uspeh", "uspjeh"),
        (r"vrednost(\w*)", r"vrijednost\1"),
        (r"promenljiv(\w*)", r"promjenljiv\1"),
    )
)


def _preserve_case(source: str, replacement: str) -> str:
    """Zadrži veliko početno slovo ("Deo" → "Dio")."""
    if source and source[0].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def to_ijekavica(text: str) -> str:
    """Zamijeni vrlo česte ekavske oblike ijekavskim; sve ostalo netaknuto."""
    if not text:
        return text
    out = text
    for pattern, repl in _REPLACEMENTS:
        # case-insensitive prolaz sa čuvanjem velikog početnog slova
        ci = re.compile(pattern.pattern, re.IGNORECASE)

        def _sub(m: re.Match, _repl=repl) -> str:
            return _preserve_case(m.group(0), m.expand(_repl))

        out = ci.sub(_sub, out)
    return out
