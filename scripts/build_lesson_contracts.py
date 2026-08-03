"""Provjera i izvještaj o ugovorima lekcija (Faza A: bez masovnog generisanja).

    python scripts/build_lesson_contracts.py --report     # pokrivenost + defekti
    python scripts/build_lesson_contracts.py --dry-run    # samo validacija

FAZA A NAMJERNO NE MATERIJALIZUJE 534 UGOVORA. Alat postoji da bi validacija
bila ISTA na dva mjesta (build i start aplikacije) i da bi se pokrivenost mogla
mjeriti. Masovna triaža je posao Faze B.

Kad se u Fazi B doda predlaganje ugovora, važi pravilo: nesiguran red se NIKAD
ne označava kao `enabled`. Ide na `needs_review` (legacy put, ali zasebno
prebrojan) ili `unsupported` (Practice nedostupan). Tihi default ne postoji.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matbot.contracts import archetypes, registry, schema   # noqa: E402
from matbot.topics import lesson_info                        # noqa: E402

DATA_PATH = ROOT / "data" / "topics.json"


def _all_topic_ids():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return [
        (grade, lesson["id"])
        for grade, grade_data in data["grades"].items()
        for lesson in grade_data["lessons"]
    ]


def validate():
    """Ista validacija koja se izvodi i pri startu aplikacije."""
    problems = []
    try:
        contracts = registry.load_all()
    except schema.ContractSchemaError as error:
        return None, [f"učitavanje palo: {error}"]

    for topic_id, contract in contracts.items():
        if not lesson_info(contract.grade, topic_id):
            problems.append(
                f"{topic_id}: ugovor postoji, ali lekcija nije u data/topics.json"
            )
        try:
            archetypes.assert_supported(contract)
        except schema.ContractSchemaError as error:
            problems.append(str(error))
        if contract.status == "legacy_pinned" and not contract.pinned_reason:
            problems.append(f"{topic_id}: legacy_pinned bez pinned_reason")
    return contracts, problems


def report(contracts):
    topics = _all_topic_ids()
    statuses = Counter(registry.report_status(topic_id) for _, topic_id in topics)

    print(f"Lekcija u kurikulumu: {len(topics)}")
    print(f"Ugovora učitano:      {len(contracts)}")
    print("\nPokrivenost:")
    for status in ("enabled", "needs_review", "unsupported", "legacy_pinned",
                   registry.REPORT_LEGACY_UNCONTRACTED):
        count = statuses.get(status, 0)
        if count:
            print(f"  {status:22} {count:4}")

    print("\nUključeni ugovori:")
    for topic_id, contract in sorted(contracts.items()):
        if contract.status != "enabled":
            continue
        print(f"  {topic_id}  {contract.skill:34} "
              f"ops={','.join(contract.allowed_operations) or '-':18} "
              f"arch={','.join(contract.effective_archetypes)}")

    pinned = [c for c in contracts.values() if c.status == "legacy_pinned"]
    if pinned:
        print("\nAUDITIRAN POVRATAK NA LEGACY (mora imati unos u docs/LESSON_CONTRACTS.md):")
        for contract in pinned:
            print(f"  {contract.canonical_topic_id}: {contract.pinned_reason} "
                  f"({contract.pinned_at})")

    review = [c for c in contracts.values() if c.status == "needs_review"]
    if review:
        print("\nČEKA PREGLED (legacy put, ali NIJE 'još nije počelo'):")
        for contract in review:
            print(f"  {contract.canonical_topic_id}: {contract.skill}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="ispiši pokrivenost")
    parser.add_argument("--check", "--dry-run", dest="check", action="store_true",
                        help="samo validiraj; ništa se ne upisuje (izlaz 1 na defekt)")
    parser.add_argument("--materialize", action="store_true",
                        help="(Faza B) upiši razriješene ugovore — još nije podržano")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    if args.materialize:
        print("Materijalizacija je posao Faze B — Faza A ne generiše ugovore.",
              file=sys.stderr)
        return 2

    contracts, problems = validate()
    if problems:
        print("DEFEKTI UGOVORA:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("Validacija: svi ugovori ispravni.")
    if args.report:
        print()
        report(contracts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
