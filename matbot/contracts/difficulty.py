"""Integritet PODATAKA o težini u ugovoru lekcije.

Do povlačenja starog motora ovdje je živio i relativni kontroler težine
(derive/target_levels/check_within_bounds/capability_for…) koji je K1/K3
generator koristio da konstruiše lakši ili teži zadatak. Aktivni put težinu
vodi kroz `matbot/difficulty_profiles.py` i `matbot/difficulty_level.py`, pa
je kontroler uklonjen zajedno s generatorom.

Ostaje jedna provjera koja se tiče SAMO podataka i zato i dalje vrijedi.
"""


def invariant_conflicts(contract):
    """Ograničenje koje je lekcija proglasila nepromjenjivim ne smije se
    pojaviti i kao dimenzija težine — inače bi „teže“ smjelo da ga pomjeri."""
    return sorted(set(contract.invariant_constraints) & set(contract.difficulty_dimensions))
