"""Minimalna strict šema AI odgovora za jedan Practice turn + server validacija.

Model smije reći SAMO ovo — svaka promjena stanja izvodi se serverski iz ovih
polja. Model nikad ne postavlja ID-jeve, brojače, verzije ni state patcheve.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from matbot import config


class NewTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    expected_answer: str
    difficulty: Literal["easy", "standard", "hard"]


class PracticeTurnOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    evaluation: Optional[Literal["correct", "partially_correct", "incorrect"]]
    gave_hint: bool
    new_task: Optional[NewTask]


class ExplainTurnOutput(BaseModel):
    """Explain mod: namjerno NAJMANJA moguća šema — samo vidljivi tekst.
    Bez evaluation/gave_hint/new_task: Explain ništa ne ocjenjuje, ne daje
    hintove po nivou i ne mijenja nikakav aktivni zadatak, pa svako dodatno
    polje samo otvara prostor za kontradikciju."""

    model_config = ConfigDict(extra="forbid")

    reply: str


class InvalidOutputError(ValueError):
    """AI odgovor je strukturno validan JSON, ali sadržajno neupotrebljiv."""


def validate_output(out: PracticeTurnOutput) -> None:
    """Server-side provjere povrh strict JSON šeme. Baca InvalidOutputError.

    NAPOMENA O OBIMU: server ovdje provjerava SAMO strukturu (neprazna polja,
    dužinska ograničenja, dozvoljene enum vrijednosti). NE postoji nikakva
    provjera da li 'new_task' stvarno pripada istoj lekciji/oblasti — nema
    lesson ID-ja u šemi niti matematičkog/verifier motora koji bi to mogao
    deterministički dokazati bez dodatnog AI poziva (što je van dizajna Faze 1).
    Pripadnost lekciji je ISKLJUČIVO prompt instrukcija (matbot/prompts.py:
    "Novi zadatak ostaje u ISTOJ lekciji...") — fake testovi mogu dokazati samo
    da se prompt instrukcija ŠALJE modelu, ne da je model poštuje. Da li model
    stvarno ostaje u temi je pitanje za live eval set, ne za ovaj validator.
    """
    if not (out.reply or "").strip():
        raise InvalidOutputError("prazan reply")
    if len(out.reply) > config.MAX_REPLY_CHARS:
        raise InvalidOutputError("predug reply")
    if out.new_task is not None:
        if not (out.new_task.text or "").strip():
            raise InvalidOutputError("novi zadatak bez teksta")
        if len(out.new_task.text) > config.MAX_TASK_CHARS:
            raise InvalidOutputError("predug tekst zadatka")
        if not (out.new_task.expected_answer or "").strip():
            raise InvalidOutputError("novi zadatak bez očekivanog odgovora")
        if len(out.new_task.expected_answer) > config.MAX_EXPECTED_ANSWER_CHARS:
            raise InvalidOutputError("predug očekivani odgovor")


def validate_explain_output(out: ExplainTurnOutput) -> None:
    """Server-side provjere Explain outputa povrh strict šeme."""
    if not (out.reply or "").strip():
        raise InvalidOutputError("prazan reply")
    if len(out.reply) > config.MAX_EXPLAIN_REPLY_CHARS:
        raise InvalidOutputError("predug reply")
