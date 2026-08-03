"""Recenzent VJERNOSTI LEKCIJI — jedan dodatni poziv, samo za nove zadatke.

ZAŠTO POSTOJI (živi nalaz, 5 prijavljenih slučajeva): generisan zadatak je
često pogađao samo ŠIRU OBLAST, a ne tačno izabranu lekciju — „Upoređivanje
decimalnih brojeva“ je dobilo oduzimanje razlomaka, „Pravila djeljivosti“ obično
dijeljenje, „Upoređivanje uglova“ oduzimanje mjera, a „Tekstualni zadatak sa
sistemom“ gotov sistem bez priče.

Dio toga je bio defekt ROUTINGA i popravljen je u podacima
(`task_families._promote_declared_task_form`) — recenzent ne služi da sakrije
mapiranje. Ostatak je slobodna interpretacija modela i to hvata ovaj sloj.

GRANICE (namjerno uske):
  • poziva se ISKLJUČIVO kad turn pravi ili mijenja zadatak;
  • odgovori, klikovi, hintovi, „Ne znam“, objašnjenja i obična konverzacija
    NIKAD ne plaćaju ovaj poziv i zadržavaju zatečeno ponašanje;
  • vraća approve / correct(jedan kompletan zadatak) / fail_closed;
  • nema trećeg poziva, retryja, repair petlje ni prelaska na drugu lekciju.

Nijedan ID lekcije ne postoji u ovom modulu. Recenzent dobija TAČAN NASLOV
lekcije, oblast, razred i porodicu kao podatke — konkretni primjeri iz nalaza
žive u promptu (kao ilustracija principa) i u testovima.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from matbot.schema import NewTask

# Namjere turna koje SMIJU platiti recenzenta. Sve ostalo ide zatečenim putem.
TASK_GENERATING = frozenset({
    "generate_task", "next_task", "easier_task", "harder_task",
})

FAIL_REASON_CODES = (
    "math_incorrect",
    "wrong_lesson",
    "wrong_task_form",
    "not_grade_appropriate",
    "ambiguous_or_unsolvable",
    "wrong_marked_option",
    "duplicate_options",
    "difficulty_direction_wrong",
)


class FidelityChecks(BaseModel):
    """Svaka provjera je ODVOJENA — „djeluje u redu“ nije provjera.

    `tests_exact_lesson` je stroži od „ista oblast“: zadatak mora ispitivati
    baš vještinu iz naslova lekcije."""

    model_config = ConfigDict(extra="forbid")

    math_correct: bool
    tests_exact_lesson: bool
    required_task_form: bool
    grade_appropriate: bool
    solvable_and_unambiguous: bool
    answer_correct: bool
    marked_option_correct: bool
    options_unique: bool
    difficulty_direction_correct: bool
    # Kratko obrazloženje ZA LOG (nikad se ne prikazuje učeniku).
    lesson_skill_summary: str


class LessonFidelityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "correct", "fail_closed"]
    checks: FidelityChecks
    fail_reason_code: Optional[Literal[FAIL_REASON_CODES]] = None
    # Popunjen SAMO kod 'correct' — kompletan ispravljen zadatak koji se objavljuje.
    corrected_task: Optional[NewTask] = None


class FidelityRejected(ValueError):
    """Zadatak nije prošao recenziju vjernosti. Poruka je INTERNA (log)."""


def is_task_generating(new_task, difficulty_request=""):
    """Da li OVAJ turn pravi/mijenja zadatak (i time zaslužuje recenzenta).

    Odluka se donosi iz SERVERSKE činjenice (model je vratio `new_task`), ne iz
    modelove izjave o namjeri — server ionako mijenja zadatak samo tada."""
    return new_task is not None


_PRINCIPLE = """Ti si stroga kontrola VJERNOSTI LEKCIJI za zadatak iz matematike
(osnovna škola, Bosna i Hercegovina). Dobijaš NACRT zadatka koji je napisao drugi
nastavnik i moraš ga provjeriti prije nego što ga učenik vidi.

NAJVAŽNIJE PRAVILO: zadatak mora ispitivati TAČNO IZABRANU LEKCIJU, a ne samo
istu širu oblast. „Ista oblast“ NIJE dovoljno. Ako naslov lekcije imenuje
određenu radnju (upoređivanje, primjena pravila, prepoznavanje, tekstualni
zadatak…), zadatak MORA tražiti baš tu radnju.

Ilustracije principa (nisu spisak lekcija — princip vrijedi za svaku):
- lekcija o UPOREĐIVANJU brojeva traži da učenik poredi/uredi vrijednosti ili
  izabere veći/manji ($<$, $>$, $=$); puka računska operacija s tim brojevima
  NE ispituje tu lekciju;
- lekcija o PRAVILIMA DJELJIVOSTI traži da učenik primijeni, obrazloži ili
  prepozna djeljivost po imenovanim pravilima; obično dijeljenje NE ispituje tu
  lekciju;
- lekcija o UPOREĐIVANJU UGLOVA traži poređenje ili uređivanje mjera uglova;
  računanje njihove razlike samo po sebi NE ispituje tu lekciju;
- lekcija o TEKSTUALNOM ZADATKU traži stvarnu, uzrastu primjerenu priču iz koje
  se postavlja ili koristi model (npr. sistem); gotov zapis bez priče NE
  ispituje tu lekciju;
- lekcija o SABIRANJU CIJELIH BROJEVA RAZLIČITIH ZNAKOVA je ispravno ispitana
  izrazom poput $-7+12$ — kad naslov imenuje baš tu operaciju, direktan račun
  JESTE tražena radnja.

ODLUKA:
- `approve` — nacrt ispituje tačno tu lekciju i matematički je ispravan;
- `correct` — popravljivo: u `corrected_task` vrati JEDAN KOMPLETAN ispravljen
  zadatak (tekst, 4 opcije, tačan indeks, očekivani odgovor). To je konačna
  verzija koju učenik vidi;
- `fail_closed` — ne može se sigurno objaviti; navedi `fail_reason_code`.

Kad ispravljaš, NE MIJENJAJ izabranu lekciju, razred ni oblast — popravi zadatak
tako da odgovara TOJ lekciji. Ne postoji treći poziv: tvoja odluka je konačna.
Ako nisi siguran u matematiku ili je zadatak dvosmislen, biraj `fail_closed`."""


def build_instructions(grade):
    from matbot.prompts import _GRADE_STYLE

    style = _GRADE_STYLE.get(grade, _GRADE_STYLE[6])
    return f"{_PRINCIPLE}\n\nUZRAST: {style}"


def _option_lines(new_task):
    lines = []
    for index, option in enumerate(new_task.options):
        marker = "  <-- OZNAČENA KAO TAČNA" if index == new_task.correct_option_index else ""
        lines.append(f"  {index}) {option.text}{marker}")
    return lines


def build_input(context, new_task, student_message, family="",
                family_description="", prior_task="", difficulty_request=""):
    """`context` je matbot.tutor.lesson_context.LessonContext (dijeli se s
    univerzalnim putem — isti kanonski identitet, bez duplikata)."""
    lines = [
        "IZABRANA LEKCIJA (nepromjenjiva):",
        f"- razred: {context.grade}",
        f"- oblast: {context.oblast} ({context.oblast_id})",
        f"- kanonski ID lekcije: {context.topic_id}",
        f"- TAČAN NASLOV LEKCIJE: {context.title}",
    ]
    if family:
        label = f"{family} — {family_description}" if family_description else family
        lines.append(f"- dodijeljena porodica zadatka: {label}")
    lines.append("")
    lines.append(f"PORUKA UČENIKA: „{(student_message or '').strip()[:400]}“")
    if difficulty_request:
        lines.append(f"TRAŽENA PROMJENA TEŽINE: {difficulty_request}")
    if prior_task:
        lines.append(f"PRETHODNI ZADATAK (za poređenje težine): {prior_task}")
    lines.append("")
    lines.append("NACRT ZADATKA (provjeri ga, ne vjeruj mu):")
    lines.append(f"- tekst: {new_task.text}")
    lines.extend(_option_lines(new_task))
    lines.append(f"- očekivani odgovor: {new_task.expected_answer}")
    lines.append(f"- težina: {new_task.difficulty}")
    lines.append("")
    lines.append("Vrati strukturisanu odluku prema šemi.")
    return "\n".join(lines)


_MANDATORY_CHECKS = (
    "math_correct", "tests_exact_lesson", "required_task_form",
    "grade_appropriate", "solvable_and_unambiguous", "answer_correct",
    "marked_option_correct", "options_unique",
)


def resolve(review, requested_difficulty=""):
    """Vrati zadatak koji se objavljuje. Baca FidelityRejected — fail closed.

    Kontradiktoran payload (odobreno uz oborenu provjeru) je PAD, ne odobrenje:
    inače bi „approve“ postalo prazna riječ."""
    if review.decision == "fail_closed":
        raise FidelityRejected(f"fail_closed:{review.fail_reason_code or 'nepoznato'}")

    failed = [name for name in _MANDATORY_CHECKS if not getattr(review.checks, name)]
    if failed:
        raise FidelityRejected(f"odobreno uprkos oborenim provjerama: {failed}")

    if (requested_difficulty or "").strip().lower() in ("easier", "harder"):
        if not review.checks.difficulty_direction_correct:
            raise FidelityRejected("smjer promjene težine nije potvrđen")

    if review.decision == "correct":
        if review.corrected_task is None:
            raise FidelityRejected("odluka 'correct' bez ispravljenog zadatka")
        return review.corrected_task
    return None          # approve → objavljuje se originalni nacrt
