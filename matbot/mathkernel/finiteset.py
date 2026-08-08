"""Egzaktna algebra KONAČNIH skupova (Batch #4, Prioritet 3).

Kanonski oblik: skup je ``frozenset`` cijelih brojeva — poredak ne postoji,
duplikati se sažimaju, pa su {1,2,3} i {3,2,1} JEDAN objekat, a jednakost
opcija je skupovna, nikad tekstualna. Modul ne poznaje lekciju ni Practice;
budući „Daj mi rezultat" mod je drugi predviđeni potrošač.
"""
from __future__ import annotations


class FiniteSetError(ValueError):
    """Nedozvoljena skupovna operacija (npr. komplement bez univerzuma)."""


def canonical(elements) -> frozenset:
    return frozenset(int(value) for value in elements)


def union(first, second) -> frozenset:
    return canonical(first) | canonical(second)


def intersection(first, second) -> frozenset:
    return canonical(first) & canonical(second)


def difference(first, second) -> frozenset:
    return canonical(first) - canonical(second)


def complement(subset, universe) -> frozenset:
    subset, universe = canonical(subset), canonical(universe)
    if not subset <= universe:
        raise FiniteSetError("komplement traži da skup bude podskup univerzuma")
    return universe - subset


def is_subset(first, second) -> bool:
    return canonical(first) <= canonical(second)


def sets_equal(first, second) -> bool:
    return canonical(first) == canonical(second)


def cardinality(elements) -> int:
    return len(canonical(elements))


def cartesian_product(first, second) -> frozenset:
    return frozenset((a, b) for a in canonical(first)
                     for b in canonical(second))


def display(elements) -> str:
    """Kanonski prozni prikaz: elementi sortirani, zarez-razmak, {} za prazan."""
    values = sorted(canonical(elements))
    if not values:
        return "∅"
    return "{" + ", ".join(str(value) for value in values) + "}"


def display_pairs(pairs) -> str:
    values = sorted(pairs)
    return "{" + ", ".join(f"({a}, {b})" for a, b in values) + "}"
