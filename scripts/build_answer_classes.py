"""Izvedi mapu DEKLARISANI POJAM LEKCIJE → KLASA TRAŽENOG ODGOVORA.

Dvije klase, i ništa između:

    value       odgovor je VRIJEDNOST/REZULTAT (broj, mjera, izraz s brojem)
    prose       odgovor je PREPOZNAVANJE (naziv vrste, tvrdnja, klasifikacija)

ZAŠTO JE TO SEMANTIKA, A NE KOZMETIKA: „Vrste uglova“ traži da učenik ugao
IMENUJE, a ne da izračuna njegovu mjeru; „Obrat Pitagorine teoreme“ traži
ODLUKU je li trougao pravougli, a ne dužinu hipotenuze. Kad model na takvoj
lekciji vrati broj, zadatak je matematički uredan a ispituje drugu vještinu —
tačno onaj drift zbog kojeg semantički ugovori postoje.

Klasu NE izmišljamo: čita je `hint_policy.value_shaped`, isti provjereni
klasifikator kojim ljestvica pomoći već razlikuje računski od tvrdnjskog
zadatka. Nema drugog parsera.

Mapa se IZVODI mjerenjem nad determinističkim generatorima istih porodica.
Pojam kod kojeg klasa nije jednoglasna kroz cio uzorak se NE upisuje i time se
nikad ne blokira.

Pokretanje:
    python scripts/build_answer_classes.py
Rezultat:
    data/semantic_answer_classes.json
"""
import collections
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")

from matbot import deterministic as registry                          # noqa: E402
from matbot import hint_policy                                        # noqa: E402
from matbot.deterministic.core import DeterministicGenerationError     # noqa: E402
from matbot.semantics import contracts as semantic_contracts          # noqa: E402

COMPILED = ROOT / "data" / "lesson_semantics.compiled.json"
OUTPUT = ROOT / "data" / "semantic_answer_classes.json"
SAMPLES_PER_LEVEL = int(os.environ.get("ANSWER_SAMPLES", "12"))

# Polja ugovora koja imenuju VRSTU zadatka. Jedno te isto pitanje u različitim
# porodicama nosi različito ime polja — mapa je zato po TOKENU, ne po polju.
TOKEN_FIELDS = ("kinds", "concepts", "shapes", "problem_types")

CLASS_VALUE = "value"
CLASS_PROSE = "prose"


def declared_tokens(contract):
    tokens = []
    for field in TOKEN_FIELDS:
        for item in contract.parameters.get(field) or ():
            tokens.append(str(item))
    return tuple(tokens)


def main():
    lessons = json.loads(COMPILED.read_text(encoding="utf-8"))["lessons"]
    observed = collections.defaultdict(collections.Counter)
    sampled_lessons = collections.Counter()

    for lesson_id in sorted(lessons):
        contract = semantic_contracts.contract_for(lesson_id)
        if contract is None or not contract.blocking:
            continue
        tokens = declared_tokens(contract)
        if len(tokens) != 1:
            # Lekcija koja deklariše više pojmova ne može pripisati klasu
            # pojedinom pojmu — takva ne doprinosi izvođenju mape.
            continue
        module = registry.GENERATORS.get(contract.family_id)
        if module is None or not module.supports(dict(contract.parameters)):
            continue
        for level in (1, 2, 3):
            for seed in range(SAMPLES_PER_LEVEL):
                try:
                    package = module.generate_package(
                        lesson_id, "", dict(contract.parameters), level,
                        rng=random.Random(f"{lesson_id}|{level}|{seed}"))
                except DeterministicGenerationError:
                    continue
                marked = package.option_texts[package.correct_index]
                label = (CLASS_VALUE if hint_policy.value_shaped(marked)
                         else CLASS_PROSE)
                observed[tokens[0]][label] += 1
                sampled_lessons[tokens[0]] += 1

    classes, rejected = {}, {}
    for token, counter in sorted(observed.items()):
        if len(counter) == 1 and sum(counter.values()) >= 6:
            classes[token] = next(iter(counter))
        else:
            rejected[token] = dict(counter)

    payload = {
        "_readme": [
            "DEKLARISANI POJAM LEKCIJE -> KLASA TRAZENOG ODGOVORA.",
            "  value = rezultat/vrijednost,  prose = prepoznavanje/tvrdnja",
            "IZVEDENO MJERENJEM (scripts/build_answer_classes.py) nad",
            "determinstickim generatorima; klasu cita hint_policy.value_shaped,",
            "isti klasifikator koji koristi ljestvica pomoci. Pojam bez",
            "jednoglasne klase stoji u `rejected_tokens` i NIKAD se ne blokira.",
        ],
        "schema_version": "1",
        "samples_per_level": SAMPLES_PER_LEVEL,
        "class_by_token": classes,
        "rejected_tokens": rejected,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    split = collections.Counter(classes.values())
    print(f"tokens with a proven class : {len(classes)}  {dict(split)}")
    print(f"rejected (not unanimous)   : {len(rejected)}")
    print(f"written: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
