"""GRANICA LEGACY KODA — privremeno, do Faze D.

Sve u ovom paketu opslužuje ISKLJUČIVO lekcije koje još nemaju uključen ugovor
(`legacy_uncontracted`, `needs_review`, `legacy_pinned`). Ovdje je DOZVOLJENO
granati po kanonskom ID-ju lekcije, jer je to zatečeno stanje koje se migrira,
a ne nova arhitektura.

Generički motor (`matbot/contracts/`) nema pristup ovom paketu i ne smije ga
uvoziti. Arhitektonska kapija zato provjerava `matbot/contracts/` i generički
dio `matbot/practice.py`, a NE ovaj paket — vidi
tests/test_contract_architecture_gate.py i docs/LESSON_CONTRACTS.md.

UKLANJANJE: kad sve lekcije dobiju ugovor (Faza D), cio paket nestaje zajedno s
`matbot/task_families.py` i `matbot/task_family_validation.py`.
"""
