"""Uputa učeniku se ne pravi ISJEČKOM tuđe rečenice (predizdanje, ciljano).

ŽIVI NALAZ. Deterministički motor nejednačina je prozu gradio ovako:

    f"… $x$ je djeljenik, pa je granica "
    f"{_role_sentence('unknown_dividend').split('=')[1].strip()} — …"

`_role_sentence` vraća kurikularnu relaciju `„nepoznati djeljenik = količnik
puta djelilac“`. Isječak `.split("=")[1]` uzme PREDIKAT, obriše mu subjekat i
zakalemi ga na nov subjekat — internu riječ `granica` (ime promjenljive
`bound`). Učenik 6. razreda je dobijao „pa je granica količnik puta djelilac“:
imensku frazu bez subjekta, u rečenici o pojmu koji nigdje nije uveden. To
nije stvar ukusa nego pokvarena rečenična konstrukcija.

OPSEG OVE KAPIJE — namjerno uzak, i evo tačno šta NE tvrdi:

  • NE zabranjuje `.split()` u determinističkom kodu. Cijepanje MATEMATIČKOG
    lanca (`chain.split("=")[0]` → lijeva strana jednačine, koja ide unutar
    `$…$`) je legitimno i ostaje netaknuto: izraz je i sam po sebi ispravan
    matematički zapis, za razliku od komada rečenice.
  • NE mjeri prirodnost jezika i NE vodi spisak zabranjenih riječi. `granica`
    je savršeno ispravna riječ u drugim lekcijama; kvar je bio KONSTRUKCIJA.
  • NE dokazuje da su ostale determinističke rečenice dobre — samo da OVA
    klasa kvara (isječak relacione tabele u prozi) ne može da se vrati na
    imenovanim pristupnicima.

Provjerava se troje: parnost tabela, da svaka školska rečenica nosi VLASTITI
subjekat, i da nijedan poziv imenovanih pristupnika relacione tabele ne ide u
`.split(...)`.
"""
import ast
import random
from pathlib import Path

import pytest

from matbot.deterministic import equations
from matbot.practice_policy import (UNKNOWN_ROLE_EXPLANATIONS,
                                    UNKNOWN_ROLE_RELATIONS)

EQUATIONS_SOURCE = Path(equations.__file__)

# Imena kroz koja se PP-1 relaciona tabela čita u determinističkom motoru.
# Kapija sudi ISKLJUČIVO o njima — vidi opseg u docstringu.
_RELATION_ACCESSOR_FUNCTIONS = frozenset({"_role_sentence"})
_RELATION_ACCESSOR_TABLES = frozenset({"_ROLE_RELATIONS", "_ROLE_EXPLANATIONS"})


def test_explanation_table_covers_exactly_the_relation_roles():
    """Tabele moraju biti TOTALNE jedna prema drugoj.

    Djelimična tabela bi značila da neka uloga i dalje nema gotovu rečenicu,
    pa bi je pozivalac morao sklopiti — tačno ono što je proizvelo kvar."""
    assert set(UNKNOWN_ROLE_EXPLANATIONS) == set(UNKNOWN_ROLE_RELATIONS)


@pytest.mark.parametrize("role", sorted(UNKNOWN_ROLE_RELATIONS))
def test_explanation_carries_its_own_subject(role):
    """Svaka školska rečenica počinje VLASTITIM subjektom („nepoznati …“).

    To je upravo svojstvo koje je isječku nedostajalo: `.split("=")[1]` je
    vraćao „količnik puta djelilac“ — predikat bez subjekta, upotrebljiv samo
    uz tuđu rečenicu."""
    label = UNKNOWN_ROLE_RELATIONS[role][0]
    explanation = UNKNOWN_ROLE_EXPLANATIONS[role]
    assert explanation.startswith(label), (
        f"{role}: objašnjenje mora nositi svoj subjekat {label!r}, "
        f"a glasi {explanation!r}")
    # Gotova rečenica, ne relacija: `=` je zapis tabele, ne školskog teksta.
    assert "=" not in explanation


def test_no_relation_accessor_is_sliced_in_the_deterministic_engine():
    """AST kapija: `_role_sentence(...)` / `_ROLE_*[...]` ne smiju u `.split`.

    Uzak obuhvat: gleda se JEDAN modul i SAMO imenovani pristupnici relacione
    tabele. Svako drugo `.split()` u istom fajlu (cijepanje matematičkog
    lanca) ostaje dozvoljeno i ovdje se ne pominje."""
    tree = ast.parse(EQUATIONS_SOURCE.read_text(encoding="utf-8"))

    def reads_relation_table(node):
        if isinstance(node, ast.Call):
            target = node.func
            return (isinstance(target, ast.Name)
                    and target.id in _RELATION_ACCESSOR_FUNCTIONS)
        if isinstance(node, ast.Subscript):
            target = node.value
            return (isinstance(target, ast.Name)
                    and target.id in _RELATION_ACCESSOR_TABLES)
        return False

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != "split":
            continue
        if reads_relation_table(node.value):
            offenders.append(node.lineno)
    assert not offenders, (
        "kurikularna relacija se cijepa i kalemi u prozu učeniku "
        f"({EQUATIONS_SOURCE.name}, redovi {offenders}); "
        "napiši gotovu rečenicu u UNKNOWN_ROLE_EXPLANATIONS umjesto isječka")


@pytest.mark.parametrize("builder", [
    equations._role_inequality_additive_package,
    equations._role_inequality_multiplicative_package,
])
def test_inequality_hint_names_the_unknown_member_in_full(builder):
    """Ponašanje, ne samo oblik koda: uputa mora imenovati CIJELU ulogu.

    Pokvarena verzija je tu oznaku upravo brisala („$x$ je djeljenik, pa je
    granica …“ ne sadrži „nepoznati djeljenik“), pa ovaj assert razlikuje
    stari i novi tekst bez ijedne zabranjene riječi."""
    labels = {label for label, _relation in UNKNOWN_ROLE_RELATIONS.values()}
    seen = 0
    for seed in range(120):
        for level in (1, 2, 3):
            try:
                package = builder(random.Random(seed * 7 + level), level,
                                  "rational_nonneg", "6-07-005",
                                  "Nejednačine u Q+")
            except equations.DeterministicGenerationError:
                continue
            hint = package.hints[0]
            assert any(label in hint for label in labels), hint
            # Pridružena jednačina se STVARNO prikazuje — dva od četiri
            # oblika su je ranije samo najavljivala („Zamisli pridruženu
            # jednačinu:“) i nikad je nisu ispisala.
            head = hint.split(".", 1)[0]
            assert head.count("$") >= 2, hint
            seen += 1
    assert seen, "nijedan paket nije nastao — test ne bi ništa dokazao"
