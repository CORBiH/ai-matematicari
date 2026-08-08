"""Zajednička infrastruktura determinističkih generatora (kapacitetni sloj).

ZAŠTO POSTOJI: Faza 4H je dokazala koncept na jednoj porodici i jednom modulu.
Širenje na više porodica NE SMIJE značiti 15 skoro identičnih kopija tog
modula: paket, dokaz težine, sastavljanje opcija i kanonski prikaz vrijednosti
su ISTI posao za svaku porodicu. Ovaj modul drži taj zajednički posao na
jednom mjestu; porodični moduli ostaju tanki adapteri koji nose isključivo
matematiku svoje vještine.

MATEMATIČKI AUTORITET: isključivo egzaktna aritmetika (`fractions.Fraction`,
cijeli brojevi). Binarni float se nigdje ne koristi kao izvor istine; decimalni
prikaz se izvodi iz razlomka čiji nazivnik dijeli dekadsku jedinicu, pa je
svaki prikaz egzaktan.

GRANICA: nijedan generator ovdje ne bira rutu i ne poznaje nijednu lekciju.
Ruta je isključivo stvar Practice orkestratora (matbot/tutor/pipeline.py), a
razlike među lekcijama nose parametri kompajliranog semantičkog ugovora.
"""
from dataclasses import dataclass, field
from fractions import Fraction

from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter, TaskPayload,
                                 TaskSignature, TutorOption)


class DeterministicGenerationError(RuntimeError):
    """Ni nakon ograničenog broja pokušaja nije nastao valjan paket."""


# ---------------------------------------------------------------------------
# DOKAZ TEŽINE — ista iskrena, fiksna struktura po nivou za sve porodice.
# Pragovi žive u matbot/tutor/schema.py (difficulty_evidence_errors) i OVDJE SE
# NE MIJENJAJU: generator proizvodi dokaz koji te iste pragove zadovoljava.
# ---------------------------------------------------------------------------

def evidence_for_level(level, comparison=False):
    """Dokaz težine za nivo 1–3.

    `comparison=True` je za porodice čija JE vještina poređenje: rubrika nivoa 1
    dozvoljava poređenje samo dok su sve ostale dimenzije minimalne, i upravo
    takav dokaz ovdje nastaje — nikad „snižen“ dokaz koji bi prošao rubriku a
    lagao o zadatku."""
    if level == 1:
        return DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=comparison, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    if level == 2:
        return DifficultyEvidence(
            reasoning_steps=2, condition_count=1, operation_count=2,
            representation_change_count=1, requires_explanation=False,
            requires_comparison=comparison, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    return DifficultyEvidence(
        reasoning_steps=3, condition_count=1, operation_count=3,
        representation_change_count=1, requires_explanation=False,
        requires_comparison=comparison, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


# ---------------------------------------------------------------------------
# KANONSKI PRIKAZ VRIJEDNOSTI
# ---------------------------------------------------------------------------

def fraction_display(value: Fraction) -> str:
    """Kanonski prikaz POZITIVNE ILI NEGATIVNE vrijednosti razlomka.

    Mješovit zapis `K\\frac{p}{q}` koristi se SAMO za pozitivne vrijednosti:
    mathcheck čita `K\\frac{p}{q}` kao K + p/q, pa bi `-2\\frac{1}{3}` značilo
    -2 + 1/3 = -5/3, a ne -7/3 — negativna vrijednost zato uvijek ostaje u
    obliku `-\\frac{7}{3}` (ili cio broj)."""
    if value.denominator == 1:
        return str(value.numerator)
    if value < 0:
        positive = -value
        return f"-\\frac{{{positive.numerator}}}{{{positive.denominator}}}"
    whole, remainder = divmod(value.numerator, value.denominator)
    if whole >= 1:
        return f"{whole}\\frac{{{remainder}}}{{{value.denominator}}}"
    return f"\\frac{{{value.numerator}}}{{{value.denominator}}}"


def plain_fraction_display(value: Fraction) -> str:
    """Nesvedeni „školski“ zapis a/b bez mješovitog oblika (za operande)."""
    if value.denominator == 1:
        return str(value.numerator)
    if value < 0:
        positive = -value
        return f"-\\frac{{{positive.numerator}}}{{{positive.denominator}}}"
    return f"\\frac{{{value.numerator}}}{{{value.denominator}}}"


def is_terminating_decimal(value: Fraction) -> bool:
    """Da li vrijednost ima KONAČAN decimalni zapis (nazivnik oblika 2^a·5^b)."""
    denominator = value.denominator
    for factor in (2, 5):
        while denominator % factor == 0:
            denominator //= factor
    return denominator == 1


def decimal_display(value: Fraction) -> str:
    """Egzaktan decimalni zapis s bosanskim zarezom, bez binarnog float-a.

    Poziva se SAMO za vrijednosti s konačnim decimalnim zapisom — sve drugo je
    programska greška generatora i pada odmah, nikad tiho zaokruženje."""
    if not is_terminating_decimal(value):
        raise DeterministicGenerationError("vrijednost nema konačan decimalni zapis")
    sign = "-" if value < 0 else ""
    value = abs(value)
    denominator = value.denominator
    places = 0
    while denominator % 2 == 0:
        denominator //= 2
        places += 1
    fives = 0
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    places = max(places, fives)
    scaled = value * (10 ** places)
    digits = str(scaled.numerator)
    if places == 0:
        return sign + digits
    digits = digits.rjust(places + 1, "0")
    whole, decimals = digits[:-places], digits[-places:]
    return f"{sign}{whole},{decimals}"


def decimal_places(value: Fraction) -> int:
    """Broj decimalnih mjesta kanonskog konačnog zapisa (za nivoe težine)."""
    if not is_terminating_decimal(value):
        raise DeterministicGenerationError("vrijednost nema konačan decimalni zapis")
    value = abs(value)
    places = 0
    while value.denominator > 1:
        value = value * 10
        places += 1
    return places


def parenthesized(display: str) -> str:
    """Negativan operand u izrazu ide u zagrade: `5\\cdot(-3)`, nikad `5\\cdot-3`."""
    return f"({display})" if display.startswith("-") else display


# ---------------------------------------------------------------------------
# SASTAVLJANJE MCQ OPCIJA — tačno jedna tačna, sve četiri različite
# ---------------------------------------------------------------------------

def assemble_option_texts(answer_value, answer_display, candidates,
                          display_of=None, wrap="$"):
    """Vrati torku od 4 teksta opcija (indeks 0 tačan) ili baci grešku.

    `candidates` je redoslijed PEDAGOŠKE vjerovatnoće pogrešnih VRIJEDNOSTI —
    svaka se egzaktno poredi s tačnim odgovorom i s već izabranima, pa
    „ekvivalentan zapis iste vrijednosti“ nikad ne može proći kao distraktor
    (isti ugovor koji objava provjerava kroz option_equivalence)."""
    display_of = display_of or fraction_display
    chosen_values, chosen_displays = [], []
    answer_text = f"{wrap}{answer_display}{wrap}" if wrap else answer_display
    for candidate in candidates:
        if candidate == answer_value or candidate in chosen_values:
            continue
        display = display_of(candidate)
        text = f"{wrap}{display}{wrap}" if wrap else display
        if display == answer_display or text in chosen_displays:
            continue
        chosen_values.append(candidate)
        chosen_displays.append(text)
        if len(chosen_values) == 3:
            break
    if len(chosen_values) != 3:
        raise DeterministicGenerationError("nedovoljno različitih distraktora")
    option_texts = (answer_text, *chosen_displays)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    return option_texts


# ---------------------------------------------------------------------------
# ZAJEDNIČKI PAKET — isti oblik za svaku porodicu, ista objava kao model-paket
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeterministicPackage:
    """Server-vlasnički strukturisan paket jednog zadatka bilo koje porodice."""

    lesson_id: str
    lesson_title: str
    family_id: str
    operation: str                 # operacija/koncept vidljivog zadatka
    level: int
    question: str
    option_texts: tuple            # 4 prikaza, indeks 0 tačan (server miješa)
    correct_index: int
    hints: tuple                   # tri nivoa nagovještaja
    solution: str
    display_answer: str            # kanonski prikaz bez $...$
    accepted_answers: tuple
    signature_parameters: tuple    # ((name, value), ...) — identitet zadatka
    required_conditions: tuple
    relevant_objects: tuple
    generator_version: str
    evidence: DifficultyEvidence = None
    comparison_evidence: bool = field(default=False)
    # PP-1 metodska provenijencija: KOJOM metodom su pisani hintovi/rješenje
    # ("unknown_member", "transposition", "" = metodski neutralno). Server je
    # poredi sa zabranjenim metodama razriješene politike — zabranjena metoda
    # pada strukturno, i kad joj proza ne oda nijednu riječ.
    method_id: str = ""

    def task_payload(self) -> TaskPayload:
        """Isti oblik paketa kao model-nacrt — objava i validatori ostaju
        DOSLOVNO isti kod za sve strategije izvršenja."""
        evidence = self.evidence or evidence_for_level(
            self.level, comparison=self.comparison_evidence)
        return TaskPayload(
            selected_lesson_id=self.lesson_id,
            selected_lesson_title=self.lesson_title,
            target_difficulty_level=self.level,
            text=self.question,
            task_type="multiple_choice",
            options=[TutorOption(id="abcd"[index], text=text)
                     for index, text in enumerate(self.option_texts)],
            correct_option_index=self.correct_index,
            correct_option_id="abcd"[self.correct_index],
            expected_answer=self.option_texts[self.correct_index],
            solution=self.solution,
            difficulty={1: "easy", 2: "standard", 3: "hard"}[self.level],
            difficulty_evidence=evidence,
            task_signature=TaskSignature(
                task_family=self.family_id,
                operation_or_relation=self.operation,
                normalized_parameters=[
                    SignatureParameter(name=name, value=str(value))
                    for name, value in self.signature_parameters
                ] + [SignatureParameter(name="level", value=str(self.level))],
                required_conditions=list(self.required_conditions),
                relevant_objects=list(self.relevant_objects),
                answer_type="multiple_choice",
            ),
        )


def clamp_level(level) -> int:
    return min(max(int(level), 1), 3)


def build_package(*, lesson_id, lesson_title, family_id, operation, level,
                  question, answer_value, answer_display, distractor_values,
                  hints, solution, signature_parameters, required_conditions,
                  relevant_objects, generator_version, display_of=None,
                  accepted_answers=(), wrap="$", comparison_evidence=False,
                  option_texts=None, evidence=None, method_id=""):
    """Jedina tačka sklapanja paketa — svaka porodica prolazi kroz ISTE provjere.

    `option_texts` smije doći gotov samo kad opcije nisu numeričke vrijednosti
    (npr. parovi brojeva); i tada moraju biti četiri različita teksta."""
    if option_texts is None:
        option_texts = assemble_option_texts(
            answer_value, answer_display, distractor_values,
            display_of=display_of, wrap=wrap)
    else:
        option_texts = tuple(option_texts)
        if len(option_texts) != 4 or len(set(option_texts)) != 4:
            raise DeterministicGenerationError("opcije nisu jedinstvene")
    if len(tuple(hints)) != 3:
        raise DeterministicGenerationError("nagovještaji nisu tri nivoa")
    accepted = tuple(dict.fromkeys((answer_display, *accepted_answers)))
    return DeterministicPackage(
        lesson_id=lesson_id, lesson_title=lesson_title, family_id=family_id,
        operation=operation, level=clamp_level(level), question=question,
        option_texts=tuple(option_texts), correct_index=0, hints=tuple(hints),
        solution=solution, display_answer=answer_display,
        accepted_answers=accepted,
        signature_parameters=tuple(signature_parameters),
        required_conditions=tuple(required_conditions),
        relevant_objects=tuple(relevant_objects),
        generator_version=generator_version,
        evidence=evidence, comparison_evidence=comparison_evidence,
        method_id=method_id,
    )
