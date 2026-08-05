"""Kompajler semantičkih ugovora lekcija (Faza 4A).

    python scripts/build_lesson_semantics.py            # kompajliraj
    python scripts/build_lesson_semantics.py --report   # + sažetak
    python scripts/build_lesson_semantics.py --check    # samo provjeri artefakt

ULAZI (uređuje čovjek):
    data/semantic_families.json            — višekratni ugovori porodica
    data/lesson_semantic_assignments.json  — dodjele po lekciji (samo parametri)

IZLAZ (čita ga runtime):
    data/lesson_semantics.compiled.json

ZAŠTO KOMPILACIJA POSTOJI: runtime ne smije razrješavati nasljeđivanje ni
sintetizovati tekst prompta — inače bi Tutor i Recenzent mogli dobiti dvije
različito formulisane verzije istog ugovora. Ovdje se ugovor razriješi JEDNOM,
tekst prompta se generiše JEDNOM, i oba poziva dobijaju identičan artefakt.

Validacija je tvrda: nepoznata porodica, nepoznat parametar, nepoznata
vrijednost, nedostajući obavezan parametar, prazan dokaz ili nepoznat
`enforcement_mode` obaraju build (i CI), nikad se ne degradiraju tiho.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAMILIES_PATH = ROOT / "data" / "semantic_families.json"
ASSIGNMENTS_PATH = ROOT / "data" / "lesson_semantic_assignments.json"
COMPILED_PATH = ROOT / "data" / "lesson_semantics.compiled.json"
TOPICS_PATH = ROOT / "data" / "topics.json"

ENFORCEMENT_MODES = ("blocking", "advisory")
ACTIVATION_CLASSES = ("READY", "ADVISORY_ONLY", "BLOCKED")


class SemanticSchemaError(ValueError):
    """Ugovor je strukturno neispravan. Poruka je interna (CI/log)."""


def _strip_comments(mapping):
    return {key: value for key, value in mapping.items() if not key.startswith("_")}


def _read(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_families():
    payload = _read(FAMILIES_PATH)
    families = payload.get("families")
    if not isinstance(families, dict) or not families:
        raise SemanticSchemaError("nema definisanih porodica")
    return _strip_comments(families)


def load_assignments():
    payload = _read(ASSIGNMENTS_PATH)
    rows = payload.get("assignments")
    if not isinstance(rows, list) or not rows:
        raise SemanticSchemaError("nema dodjela lekcija")
    return rows, str(payload.get("contract_version") or "")


def _known_lesson_ids():
    payload = _read(TOPICS_PATH)
    return {lesson["id"]
            for grade in payload.get("grades", {}).values()
            for lesson in grade.get("lessons", [])}


def _validate_parameters(raw, schema, lesson_id):
    """Provjeri vrijednosti prema shemi porodice i vrati kanonski rječnik."""
    if not isinstance(raw, dict):
        raise SemanticSchemaError(f"{lesson_id}: parameters nije objekat")
    unknown = sorted(set(raw) - set(schema))
    if unknown:
        raise SemanticSchemaError(f"{lesson_id}: nepoznat parametar {unknown}")

    resolved = {}
    for name, spec in sorted(schema.items()):
        if name not in raw:
            if spec.get("required"):
                raise SemanticSchemaError(
                    f"{lesson_id}: nedostaje obavezan parametar '{name}'")
            continue
        value = raw[name]
        allowed = spec.get("values", ())
        if spec["kind"] == "enum":
            if value not in allowed:
                raise SemanticSchemaError(
                    f"{lesson_id}: '{name}' = {value!r} nije dozvoljena vrijednost")
            resolved[name] = value
        elif spec["kind"] == "enum_set":
            if not isinstance(value, list) or not value:
                raise SemanticSchemaError(
                    f"{lesson_id}: '{name}' mora biti neprazna lista")
            bad = sorted(set(value) - set(allowed))
            if bad:
                raise SemanticSchemaError(
                    f"{lesson_id}: '{name}' sadrži nedozvoljene vrijednosti {bad}")
            # Sortirano radi determinističkog artefakta.
            resolved[name] = sorted(set(value))
        else:
            raise SemanticSchemaError(
                f"{lesson_id}: nepoznata vrsta parametra '{spec['kind']}'")
    return resolved


def _prompt_lines(family, parameters):
    """Sintetiši KOMPAKTAN blok ugovora — isti tekst za Tutora i Recenzenta."""
    template = family["prompt_template"]
    op_labels = family["operation_labels"]
    directive_labels = family["directive_labels"]

    operations = ", ".join(op_labels[op] for op in parameters["allowed_operations"])
    lines = [template["header"], template["operations"].format(operations=operations)]

    relation = parameters.get("denominator_relation", "any")
    if relation == "equal":
        lines.append(template["denominator_equal"])
    elif relation == "unlike":
        lines.append(template["denominator_unlike"])

    forbidden = parameters.get("forbidden_directives") or ()
    if forbidden:
        lines.append(template["forbidden"].format(
            forbidden=", ".join(directive_labels[item] for item in forbidden)))
    lines.append(template["supporting"])
    lines.append(template["closing"])
    return lines


def build_assignment(row, families):
    """Razriješi jednu dodjelu u kompajliran ugovor. Baca SemanticSchemaError."""
    if not isinstance(row, dict):
        raise SemanticSchemaError("dodjela nije objekat")
    lesson_id = str(row.get("lesson_id") or "").strip()
    if not lesson_id:
        raise SemanticSchemaError("dodjela bez lesson_id")

    family_id = str(row.get("family_id") or "").strip()
    family = families.get(family_id)
    if family is None:
        raise SemanticSchemaError(
            f"{lesson_id}: nepoznata porodica '{family_id}'")

    mode = str(row.get("enforcement_mode") or "").strip()
    if mode not in ENFORCEMENT_MODES:
        raise SemanticSchemaError(
            f"{lesson_id}: nepoznat enforcement_mode '{mode}'")

    activation = str(row.get("activation_class") or "").strip()
    if activation and activation not in ACTIVATION_CLASSES:
        raise SemanticSchemaError(
            f"{lesson_id}: nepoznata activation_class '{activation}'")
    # Blokiranje smije nositi SAMO lekcija koju je pregled klasifikovao READY.
    if mode == "blocking" and activation != "READY":
        raise SemanticSchemaError(
            f"{lesson_id}: 'blocking' traži activation_class READY "
            f"(dobijeno {activation!r})")

    evidence = [str(item).strip() for item in (row.get("evidence_ids") or [])
                if str(item).strip()]
    if not evidence:
        raise SemanticSchemaError(f"{lesson_id}: nema zapisanog dokaza")

    parameters = _validate_parameters(
        row.get("parameters"), family["parameter_schema"], lesson_id)

    enforced = [name for name in family.get("enforced_parameters", ())
                if name in parameters]
    if not enforced:
        raise SemanticSchemaError(
            f"{lesson_id}: nijedan parametar nije deterministički provjerljiv")

    level_bounds = {str(key): str(value)
                    for key, value in sorted((row.get("level_bounds") or {}).items())}

    return {
        "lesson_id": lesson_id,
        "family_id": family_id,
        "family_version": int(family.get("family_version", 1)),
        "detector": str(family["detector"]),
        "enforcement_mode": mode,
        "activation_class": activation,
        "parameters": parameters,
        "enforced_parameters": sorted(enforced),
        "advisory_parameters": sorted(
            name for name in family.get("advisory_parameters", ())
            if name in parameters),
        "forbidden_neighbour_skills": sorted(
            str(item) for item in (row.get("forbidden_neighbour_skills") or [])),
        "level_bounds": level_bounds,
        "evidence_ids": sorted(evidence),
        "prompt_lines": _prompt_lines(family, parameters),
        "reviewer_note": str(row.get("reviewer_note") or ""),
    }


def compile_all():
    families = load_families()
    rows, contract_version = load_assignments()
    known = _known_lesson_ids()

    compiled, seen = {}, set()
    for row in rows:
        entry = build_assignment(row, families)
        lesson_id = entry["lesson_id"]
        if lesson_id in seen:
            raise SemanticSchemaError(f"dupla dodjela za {lesson_id}")
        if lesson_id not in known:
            raise SemanticSchemaError(
                f"{lesson_id}: lekcija ne postoji u kanonskom kurikulumu")
        seen.add(lesson_id)
        entry["contract_version"] = contract_version
        compiled[lesson_id] = entry

    return {
        "schema_version": "1",
        "contract_version": contract_version,
        "lessons": dict(sorted(compiled.items())),
    }


def write(path=COMPILED_PATH):
    payload = compile_all()
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys + fiksan separator = bajt-reproducibilan artefakt.
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kompajler semantičkih ugovora")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="samo provjeri da je artefakt u koraku s izvorima")
    parser.add_argument("--out", type=Path, default=COMPILED_PATH)
    args = parser.parse_args(argv)

    if args.check:
        expected = compile_all()
        actual = _read(COMPILED_PATH) if COMPILED_PATH.exists() else None
        if actual != expected:
            print("Kompajlirani artefakt NIJE u koraku s izvorima.", file=sys.stderr)
            return 1
        print("OK: artefakt je u koraku s izvorima.")
        return 0

    payload = write(args.out)
    if args.report:
        for lesson_id, entry in payload["lessons"].items():
            print(f"  {lesson_id}  {entry['family_id']}  "
                  f"{entry['enforcement_mode']}  "
                  f"ops={','.join(entry['parameters']['allowed_operations'])}  "
                  f"denom={entry['parameters'].get('denominator_relation')}")
    print(f"OK: {args.out} — {len(payload['lessons'])} lekcija")
    return 0


if __name__ == "__main__":
    sys.exit(main())
