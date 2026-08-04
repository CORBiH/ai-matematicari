"""Univerzalni LessonContext — isti oblik za SVIH 534 kanonskih lekcija.

ZAŠTO POSTOJI: raniji Practice je imao dva aktivna puta jer je „šta ova lekcija
uči“ živjelo na dva različita mjesta (ugovor za 6 lekcija, porodice za 528).
Ovdje se oba izvora čitaju kao **kontekst**, nikad kao zasebna izvršna grana:
rezultat je jedan objekat koji prompt uvijek popunjava na isti način.

NEMA GRANANJA PO ID-ju LEKCIJE. Sve što razlikuje jednu lekciju od druge dolazi
iz podataka: `data/topics.json` (kanonski identitet), `matbot/task_families.py`
(zatečeno mapiranje porodica, 528/528 zamrznuto) i — kad postoji — red ugovora
u `data/lesson_contracts.json`. Lekcija bez ugovora nije poseban slučaj; ona
samo ima manje popunjenih polja.
"""
from dataclasses import dataclass, field

from matbot import geometry_rules, task_families
from matbot.contracts import registry as contract_registry
from matbot.topics import lesson_info


@dataclass(frozen=True)
class LessonContext:
    """Sve što model smije znati o izabranoj lekciji, u jednom obliku.

    `has_contract` NIJE prekidač izvršnog puta — samo govori da za ovu lekciju
    postoji dodatni deklarativni opis matematike koji ide u prompt kao još
    jedno ograničenje. Put je isti za svih 534."""

    grade: int
    topic_id: str
    title: str
    oblast_id: str
    oblast: str
    families: tuple = ()
    primary_family: str = ""
    family_description: str = ""
    geometry_scope: str = ""
    geometry_figures: tuple = ()
    # Opcioni deklarativni opis matematike (postoji za migrirane lekcije).
    has_contract: bool = False
    skill: str = ""
    allowed_operations: tuple = ()
    operand_constraints: dict = field(default_factory=dict)
    lesson_scope: str = ""
    objectives: tuple = ()
    exclusions: tuple = ()

    @property
    def canonical_label(self):
        return f"{self.title} ({self.oblast}, {self.grade}. razred)"


def build(grade, topic_id):
    """LessonContext za kanonsku lekciju, ili None kad lekcija ne postoji.

    None znači „nepouzdan kurikularni kontekst“ i pozivalac tada odbija turn
    BEZ ijednog modela poziva — isto kao i ranije."""
    lesson = lesson_info(grade, topic_id)
    if lesson is None:
        return None

    families = tuple(task_families.applicable_families(
        grade, lesson["oblast"], lesson["title"], lesson_id=lesson["id"]
    ))
    primary = families[0] if families else ""
    scope, figures = geometry_rules.route_geometry_topic(
        lesson["oblast"], lesson["title"]
    )

    contract = contract_registry.contract_for(lesson["id"])
    return LessonContext(
        grade=grade,
        topic_id=lesson["id"],
        title=lesson["title"],
        oblast_id=lesson["oblast_id"],
        oblast=lesson["oblast"],
        families=families,
        primary_family=primary,
        family_description=task_families.describe(primary) if primary else "",
        geometry_scope=scope,
        geometry_figures=tuple(figures or ()),
        has_contract=contract is not None,
        skill=contract.skill if contract is not None else "",
        allowed_operations=tuple(contract.allowed_operations) if contract is not None else (),
        operand_constraints=dict(contract.operand_constraints) if contract is not None else {},
        lesson_scope=str(lesson.get("lesson_scope", "") or ""),
        objectives=tuple(lesson.get("objectives", []) or ()),
        exclusions=tuple(lesson.get("exclusions", []) or ()),
    )
