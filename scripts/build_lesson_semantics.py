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
        elif spec["kind"] == "enum_set_by_level":
            # Rječnik NIVO → dozvoljene vrijednosti. Postoji da se razdvoji
            # „šta lekcija uopšte podržava“ od „šta smije nastati na datom
            # nivou težine“: prvo je pitanje kurikuluma, drugo je pitanje
            # ugovora o težini, i jedno ne smije tiho odlučivati o drugom.
            if not isinstance(value, dict) or not value:
                raise SemanticSchemaError(
                    f"{lesson_id}: '{name}' mora biti neprazan rječnik po nivou")
            by_level = {}
            for level, values in value.items():
                if str(level) not in ("1", "2", "3"):
                    raise SemanticSchemaError(
                        f"{lesson_id}: '{name}' ima nepoznat nivo {level!r}")
                if not isinstance(values, list) or not values:
                    raise SemanticSchemaError(
                        f"{lesson_id}: '{name}' nivo {level} mora biti "
                        f"neprazna lista")
                bad = sorted(set(values) - set(allowed))
                if bad:
                    raise SemanticSchemaError(
                        f"{lesson_id}: '{name}' nivo {level} sadrži "
                        f"nedozvoljene vrijednosti {bad}")
                by_level[str(level)] = sorted(set(values))
            resolved[name] = dict(sorted(by_level.items()))
        else:
            raise SemanticSchemaError(
                f"{lesson_id}: nepoznata vrsta parametra '{spec['kind']}'")

    # PODSKUP UKUPNOG ENUMA. Nivo-pool i kreativni pool su SUŽENJA onoga što
    # lekcija podržava — nikad bočni ulaz za tip koji ugovor ne nosi.
    supported = set(resolved.get("problem_types") or ())
    if supported:
        for name in ("creative_problem_types",):
            extra = sorted(set(resolved.get(name) or ()) - supported)
            if extra:
                raise SemanticSchemaError(
                    f"{lesson_id}: '{name}' nosi tipove van problem_types {extra}")
        for level, values in (resolved.get("problem_types_by_level") or {}).items():
            extra = sorted(set(values) - supported)
            if extra:
                raise SemanticSchemaError(
                    f"{lesson_id}: 'problem_types_by_level' nivo {level} nosi "
                    f"tipove van problem_types {extra}")
    return resolved


def _prompt_lines(family, parameters):
    """Sintetiši KOMPAKTAN blok ugovora — isti tekst za Tutora i Recenzenta.

    GENERIČKO RENDEROVANJE (kapacitetna ekspanzija): porodica opisuje svoje
    linije PODACIMA, nikad granom u ovom kompajleru:
      • "header"          — obavezna prva linija;
      • "operations"      — linija s {operations}; renderuje se kad dodjela ima
                            `allowed_operations` (etikete iz operation_labels);
      • "main_line"       — {"parameter": ime, "text": "- ...: {values}"}:
                            glavna radnja porodice iz BILO KOJEG enum/enum_set
                            parametra (etikete iz value_labels; bez etikete
                            ostaje sirova vrijednost — npr. djelioci);
      • "fixed_lines"     — fiksne linije porodice, uvijek prisutne;
      • "parameter_lines" — {ime_parametra: {vrijednost: linija}}: linija se
                            dodaje kad dodjela ima tačno tu vrijednost enum
                            parametra (redoslijed = redoslijed u šablonu;
                            enum_set liste se preskaču — nisu jedna vrijednost);
      • "forbidden"       — linija s {forbidden} kad dodjela zabranjuje
                            direktive (etikete iz directive_labels);
      • "supporting"      — opciona fiksna linija;
      • "closing"         — obavezna posljednja linija.
    Nepoznata vrijednost u parameter_lines se NE renderuje tiho — provjeru
    vrijednosti već garantuje _validate_parameters nad shemom porodice."""
    template = family["prompt_template"]

    lines = [template["header"]]
    if "operations" in template and parameters.get("allowed_operations"):
        op_labels = family["operation_labels"]
        operations = ", ".join(op_labels[op]
                               for op in parameters["allowed_operations"])
        lines.append(template["operations"].format(operations=operations))

    main = template.get("main_line")
    if main:
        labels = family.get("value_labels", {})
        raw_values = parameters.get(main["parameter"])
        if raw_values is not None:
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            rendered = ", ".join(labels.get(str(item), str(item))
                                 for item in values)
            lines.append(main["text"].format(values=rendered))

    lines.extend(template.get("fixed_lines") or ())

    for parameter_name, value_lines in (template.get("parameter_lines") or {}).items():
        value = parameters.get(parameter_name)
        # enum_set s TAČNO JEDNOM vrijednošću JESTE jedna vrijednost. Lekcija
        # koja deklariše jedan jedini pojam (npr. `concepts: ["scientific_
        # notation"]`) nema šta drugo raditi, pa smije dobiti liniju vezanu za
        # tu vrijednost. Lista s više vrijednosti se i dalje preskače — tamo se
        # ne zna koja bi linija važila.
        if isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        if isinstance(value, (str, int)) and value in value_lines:
            rendered = value_lines[value]
            # Linija smije biti i VIŠE linija: neki ugovori traže nekoliko
            # rečenica, a spajanje u jednu dugu liniju bi ih učinilo nečitkim.
            lines.extend(rendered if isinstance(rendered, list) else [rendered])

    forbidden = parameters.get("forbidden_directives") or ()
    if forbidden and "forbidden" in template:
        directive_labels = family["directive_labels"]
        lines.append(template["forbidden"].format(
            forbidden=", ".join(directive_labels[item] for item in forbidden)))
    if "supporting" in template:
        lines.append(template["supporting"])
    lines.append(template["closing"])
    return lines


def _archetype_definitions(family, parameters):
    """Definicije SAMO za vrijednosti koje ova lekcija dozvoljava."""
    definitions = family.get("archetype_definitions") or {}
    used = parameters.get("problem_types") or ()
    return {name: definitions[name] for name in sorted(used)
            if name in definitions}


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

    entry = {
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
        # DEFINICIJE ARHETIPA koje lekcija stvarno koristi. Identifikator sam
        # po sebi modelu ne kazuje ništa, a eskalacijski put mora imenovati
        # CILJ mašinski provjerljivo — pa se uz identifikator nosi i njegovo
        # značenje. Podatak porodice, nikad tekst u kodu.
        "archetype_definitions": _archetype_definitions(family, parameters),
        "reviewer_note": str(row.get("reviewer_note") or ""),
    }
    # Prazan rječnik se NE upisuje: lekcija bez arhetipskog rječnika mora
    # ostati bajt-identična ranijem artefaktu, da se u pregledu razlika vidi
    # samo lekcija koja se stvarno mijenja. Čitač već ima podrazumijevanu
    # praznu vrijednost (matbot/semantics/contracts.py).
    if not entry["archetype_definitions"]:
        del entry["archetype_definitions"]
    return entry


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
