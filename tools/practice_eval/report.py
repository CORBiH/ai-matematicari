"""Mašinski čitljiv i ljudski čitljiv izvještaj jedne kampanje.

Izvještaj NIKAD ne izmišlja brojku. Pokrivenost lekcija se računa iz
`data/topics.json`, ne procjenjuje. Reprezentativni primjeri se čuvaju uz
zbirne brojeve, jer zbir bez primjera ne omogućava popravku.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from tools.practice_eval import classify as classify_lib

STATUSES = ("PASS", "FAIL", "REVIEW", "INFRA_ERROR", "RATE_LIMITED", "TIMEOUT")
_EXAMPLE_CHARS = 400
_MAX_EXAMPLES_PER_CAUSE = 3


def load_records(results_path: Path) -> list:
    if not results_path.exists():
        return []
    records = []
    for line in results_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def _rate(part, whole):
    return f"{part}/{whole} ({100.0 * part / whole:.1f}%)" if whole else "0/0 (—)"


def _group(records, key):
    grouped = defaultdict(list)
    for record in records:
        grouped[record.get(key)].append(record)
    return grouped


def _pass_line(records):
    """Dvije BROJKE, ne jedna.

    `PASS` je strog: nijedna provjera nije pala, nijedna nije preskočena i
    scenario nema nerazriješenu kvalitativnu rubriku. Zato je `PASS` sam po sebi
    zavaravajuće nizak dok god model-sudija nije odobren — svaki scenario s
    rubrikom je REVIEW po konstrukciji.

    `det` je ono što ova faza stvarno mjeri: nijedna DETERMINISTIČKA provjera
    nije pala (PASS + REVIEW). REVIEW i dalje znači „nedokazano“, nikad „dobro“."""
    counts = Counter(record["status"] for record in records)
    deterministic = counts["PASS"] + counts["REVIEW"]
    return _rate(counts["PASS"], len(records)), _rate(deterministic, len(records)), counts


def _interaction_types(record):
    """Izvedeno iz stvarnih koraka, ne iz oznaka — oznaka može lagati."""
    kinds = set()
    for turn in record.get("turns", []):
        if turn["kind"] in ("choice", "repeat_choice"):
            kinds.add(turn["kind"])
        else:
            names = {entry["name"] for entry in turn.get("check_results", [])}
            if "task_published" in names:
                kinds.add("task_generation")
            elif "hint_no_leak" in names or "hint_differs" in names:
                kinds.add("hint")
            elif "reveal_present" in names:
                kinds.add("full_solution")
            else:
                kinds.add("free_text")
    return sorted(kinds) or ["unknown"]


def build_summary(meta, records, total_lessons) -> dict:
    counts = Counter(record["status"] for record in records)
    lessons = {record["topic_id"] for record in records}
    oblasti = {(record["grade"], record["oblast"]) for record in records}

    cause_counter = Counter()
    cause_scenarios = defaultdict(list)
    check_counter = Counter()
    for record in records:
        for entry in record.get("failed_checks", []):
            cause = _root_cause(entry["check"])
            cause_counter[cause] += 1
            cause_scenarios[cause].append(record["id"])
            check_counter[entry["check"]] += 1

    repeatable = {cause: sorted(set(ids)) for cause, ids in cause_scenarios.items()
                  if len(set(ids)) >= 2}
    isolated = {cause: sorted(set(ids)) for cause, ids in cause_scenarios.items()
                if len(set(ids)) == 1}

    by_grade = {}
    for grade, group in sorted(_group(records, "grade").items()):
        rate, det, group_counts = _pass_line(group)
        by_grade[str(grade)] = {"pass": rate, "det": det, "n": len(group),
                                **{status: group_counts[status] for status in STATUSES}}

    by_oblast = {}
    for oblast, group in sorted(_group(records, "oblast").items(), key=lambda item: str(item[0])):
        rate, det, group_counts = _pass_line(group)
        by_oblast[str(oblast)] = {"pass": rate, "det": det, "n": len(group),
                                  **{status: group_counts[status] for status in STATUSES}}

    by_importance = {}
    for importance, group in sorted(_group(records, "importance").items(), key=lambda item: str(item[0])):
        rate, det, group_counts = _pass_line(group)
        by_importance[str(importance)] = {"pass": rate, "det": det, "n": len(group),
                                          **{status: group_counts[status] for status in STATUSES}}

    by_interaction = defaultdict(lambda: Counter())
    for record in records:
        for kind in _interaction_types(record):
            by_interaction[kind][record["status"]] += 1
            by_interaction[kind]["_n"] += 1

    # RC11 taksonomija: sirovi PASS/FAIL ne razlikuje pogrešan objavljen
    # sadržaj od sigurnog odbijanja, nevaljanog scenarija i posljedice ranijeg
    # pada. Ove agregacije su JEDINI način da izvještaj odgovori na pitanje
    # „koliko je od ovoga zaista kvar proizvoda?“ (vidi classify.py).
    outcome_counter = Counter(record.get("outcome_class") or "UNCLASSIFIED"
                              for record in records)
    outcome_scenarios = defaultdict(list)
    for record in records:
        outcome_scenarios[record.get("outcome_class") or "UNCLASSIFIED"].append(
            record["id"])
    route_counter = Counter()
    for record in records:
        for route in record.get("routes") or ():
            route_counter[route] += 1
    # Recompute from raw turns instead of trusting persisted classification.
    # This keeps interrupted artifacts honest when `package_captured=false`.
    product_evidence = sorted(
        record["id"] for record in records if classify_lib.package_evidence(record))
    invalid_scenarios = sorted(
        record["id"] for record in records if record.get("coherence_problems"))
    third_call = sorted(
        record["id"] for record in records if record.get("third_call_violations"))
    cascade_only = sorted(
        record["id"] for record in records
        if record.get("cascade_failures") and not record.get("root_failures"))

    return {
        "runtime": meta,
        "totals": {status: counts[status] for status in STATUSES},
        "outcome_classes": dict(outcome_counter.most_common()),
        "outcome_scenarios": {key: sorted(value)
                              for key, value in outcome_scenarios.items()},
        "routes": dict(route_counter.most_common()),
        # Dokaz na nivou PAKETA preživljava nevaljan scenario (živi B012) —
        # ova lista se NIKAD ne prazni zbog nekoherentne fiksture.
        "package_level_product_evidence": product_evidence,
        "invalid_scenarios": invalid_scenarios,
        "third_call_violations": third_call,
        "cascade_only_scenarios": cascade_only,
        "scenarios": len(records),
        "sdk_calls": sum(record.get("sdk_calls", 0) for record in records),
        "deterministic_pass": _pass_line(records)[1],
        "critical_pass": _pass_line([r for r in records if r["importance"] == "critical"])[0],
        "critical_deterministic_pass":
            _pass_line([r for r in records if r["importance"] == "critical"])[1],
        "lesson_coverage": {
            "unique_lessons_tested": len(lessons),
            "curriculum_lessons_total": total_lessons,
            "percent": round(100.0 * len(lessons) / total_lessons, 2) if total_lessons else 0.0,
            "unique_oblasti_tested": len(oblasti),
        },
        "by_grade": by_grade,
        "by_oblast": by_oblast,
        "by_importance": by_importance,
        "by_interaction_type": {kind: dict(counter) for kind, counter in by_interaction.items()},
        "root_causes": dict(cause_counter.most_common()),
        "failed_checks": dict(check_counter.most_common()),
        "repeatable_causes": repeatable,
        "isolated_causes": isolated,
    }


def _root_cause(check_name):
    from tools.practice_eval.checks import root_cause
    return root_cause(check_name)


def _examples(records, cause):
    out = []
    for record in records:
        for entry in record.get("failed_checks", []):
            if _root_cause(entry["check"]) != cause:
                continue
            turn = next((t for t in record.get("turns", [])
                         if t["step_index"] == entry["step"]), None)
            out.append({
                "scenario": record["id"],
                "topic_id": record["topic_id"],
                "grade": record["grade"],
                "check": entry["check"],
                "detail": entry["detail"][:_EXAMPLE_CHARS],
                "student_message": (turn or {}).get("request", {}).get("student_message", "")[:200],
                "answer_excerpt": ((turn or {}).get("response", {}).get("answer") or "")[:_EXAMPLE_CHARS],
                "failure_category": (turn or {}).get("failure_category", ""),
                "reviewer_decision": (turn or {}).get("reviewer_decision", ""),
                "tutor_draft_issues": (turn or {}).get("tutor_draft_issues", "")[:200],
                "log_lines": list((turn or {}).get("log_lines", []))[:3],
            })
            if len(out) >= _MAX_EXAMPLES_PER_CAUSE:
                return out
    return out


def write_reports(output_dir: Path, meta, records, total_lessons):
    summary = build_summary(meta, records, total_lessons)
    summary["examples"] = {cause: _examples(records, cause)
                           for cause in summary["root_causes"]}
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(
        render_markdown(summary), encoding="utf-8")
    return summary


def render_markdown(summary) -> str:
    runtime = summary["runtime"]
    lines = [
        "# MAT-BOT — live dijagnostika moda „Vježbajmo“",
        "",
        f"- commit: `{runtime.get('git_commit', '')[:12]}` "
        f"(dirty: {runtime.get('git_dirty')})",
        f"- pipeline: `{runtime.get('practice_pipeline')}` · "
        f"difficulty levels: `{runtime.get('difficulty_levels_enabled')}`",
        f"- model: tutor `{runtime.get('tutor_model')}` / reviewer "
        f"`{runtime.get('reviewer_model')}` · effort `{runtime.get('reasoning_effort')}` · "
        f"timeout `{runtime.get('timeout_seconds')}` s",
        f"- započeto: {runtime.get('started_at')} · završeno: {runtime.get('finished_at')}",
        f"- stvarnih SDK poziva: **{runtime.get('actual_sdk_calls', summary['sdk_calls'])}** "
        f"(plafon {runtime.get('max_model_calls')})",
        "",
        "> Rate limiteri su za kampanju podignuti, pa 429/409 ponašanje NIJE mjereno.",
        "",
        "## Ukupno",
        "",
        "| Status | Broj |",
        "|---|---|",
    ]
    for status in STATUSES:
        lines.append(f"| {status} | {summary['totals'][status]} |")
    lines += [
        f"| **ukupno scenarija** | **{summary['scenarios']}** |",
        "",
        f"- **bez ijednog determinističkog pada: {summary['deterministic_pass']}** "
        "(PASS + REVIEW) — ovo je brojka koju ova faza stvarno mjeri;",
        f"- strogi PASS (bez ijedne preskočene provjere i bez nerazriješene rubrike): "
        f"{_rate(summary['totals']['PASS'], summary['scenarios'])};",
        f"- kritični scenariji: deterministički {summary['critical_deterministic_pass']}, "
        f"strogi PASS {summary['critical_pass']}.",
        "",
        "REVIEW znači **nedokazano**, nikad „dobro“.",
        "",
        "## Klasifikacija ishoda (RC11)",
        "",
        "Sirovi PASS/FAIL ne razlikuje pogrešan objavljen sadržaj od sigurnog "
        "odbijanja objave, nevaljanog scenarija i posljedice ranijeg pada.",
        "",
        "| Klasa ishoda | Broj |",
        "|---|---|",
    ]
    for outcome, count in (summary.get("outcome_classes") or {}).items():
        lines.append(f"| {outcome} | {count} |")
    lines += [
        "",
        "- dokaz o paketu (preživljava nevaljan scenario): "
        f"**{', '.join(summary.get('package_level_product_evidence') or []) or '—'}**",
        "- nevaljani scenariji (poruka protiv lekcije): "
        f"{', '.join(summary.get('invalid_scenarios') or []) or '—'}",
        "- samo posljedica ranijeg sigurnog odbijanja: "
        f"{', '.join(summary.get('cascade_only_scenarios') or []) or '—'}",
        "- prekoračena granica poziva: "
        f"**{', '.join(summary.get('third_call_violations') or []) or '—'}**",
        "- rute izvršavanja: "
        + (", ".join(f"{route}={count}"
                     for route, count in (summary.get("routes") or {}).items())
           or "—"),
        "",
        "## Pokrivenost kurikuluma (izračunato, ne procijenjeno)",
        "",
        f"- jedinstvenih lekcija testirano: **{summary['lesson_coverage']['unique_lessons_tested']}** "
        f"od **{summary['lesson_coverage']['curriculum_lessons_total']}** "
        f"= **{summary['lesson_coverage']['percent']} %**",
        f"- jedinstvenih oblasti: **{summary['lesson_coverage']['unique_oblasti_tested']}**",
        "",
        "## Po razredu",
        "",
        "| Razred | n | bez det. pada | FAIL | REVIEW | INFRA | RATE | TIMEOUT |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for grade, row in summary["by_grade"].items():
        lines.append(f"| {grade} | {row['n']} | {row['det']} | {row['FAIL']} | {row['REVIEW']} | "
                     f"{row['INFRA_ERROR']} | {row['RATE_LIMITED']} | {row['TIMEOUT']} |")

    lines += ["", "## Po oblasti", "",
              "| Oblast | n | bez det. pada | FAIL | REVIEW |", "|---|---|---|---|---|"]
    for oblast, row in summary["by_oblast"].items():
        lines.append(f"| {oblast} | {row['n']} | {row['det']} | {row['FAIL']} | {row['REVIEW']} |")

    lines += ["", "## Po važnosti", "",
              "| Važnost | n | bez det. pada | strogi PASS | FAIL | REVIEW |",
              "|---|---|---|---|---|---|"]
    for importance, row in summary["by_importance"].items():
        lines.append(f"| {importance} | {row['n']} | {row['det']} | {row['pass']} | "
                     f"{row['FAIL']} | {row['REVIEW']} |")

    lines += ["", "## Po tipu interakcije", "", "| Tip | n | PASS | FAIL | REVIEW |", "|---|---|---|---|---|"]
    for kind, row in sorted(summary["by_interaction_type"].items()):
        lines.append(f"| {kind} | {row.get('_n', 0)} | {row.get('PASS', 0)} | "
                     f"{row.get('FAIL', 0)} | {row.get('REVIEW', 0)} |")

    lines += ["", "## Kategorije padova (root cause)", "",
              "| Kategorija | padova | ponovljiv? | scenariji |", "|---|---|---|---|"]
    for cause, count in summary["root_causes"].items():
        repeatable = cause in summary["repeatable_causes"]
        ids = summary["repeatable_causes"].get(cause) or summary["isolated_causes"].get(cause, [])
        lines.append(f"| `{cause}` | {count} | {'DA' if repeatable else 'ne (izolovan)'} | "
                     f"{', '.join(ids[:12])} |")

    repeatable_total = sum(len(ids) for ids in summary["repeatable_causes"].values())
    isolated_total = sum(len(ids) for ids in summary["isolated_causes"].values())
    total_affected = repeatable_total + isolated_total
    lines += [
        "",
        f"Ponovljivi problemi: **{repeatable_total}** pogođenih scenarija · "
        f"izolovani: **{isolated_total}** · udio ponovljivih: "
        f"**{(100.0 * repeatable_total / total_affected):.1f} %**" if total_affected else
        "Nijedan deterministički pad.",
        "",
        "## Najčešće pale provjere",
        "",
        "| Provjera | broj |",
        "|---|---|",
    ]
    for name, count in summary["failed_checks"].items():
        lines.append(f"| `{name}` | {count} |")

    lines += ["", "## Reprezentativni primjeri", ""]
    for cause, examples in summary["examples"].items():
        if not examples:
            continue
        lines.append(f"### `{cause}`")
        lines.append("")
        for example in examples:
            lines += [
                f"- **{example['scenario']}** · {example['topic_id']} · {example['grade']}. razred "
                f"· provjera `{example['check']}`",
                f"  - detalj: `{example['detail']}`",
                f"  - poruka učenika: „{example['student_message']}“",
                f"  - odgovor (isječak): {example['answer_excerpt']!r}",
            ]
            if example["failure_category"]:
                lines.append(f"  - kategorija neuspjeha: `{example['failure_category']}`")
            if example["reviewer_decision"]:
                lines.append(f"  - odluka recenzenta: `{example['reviewer_decision']}`")
            if example["tutor_draft_issues"]:
                lines.append(f"  - serverski nalaz o nacrtu: `{example['tutor_draft_issues']}`")
            for line in example["log_lines"]:
                lines.append(f"  - log: `{line}`")
        lines.append("")

    lines += [
        "## Šta ovaj izvještaj NE dokazuje",
        "",
        "- Kvalitativne rubrike nisu automatski ocijenjene (model-sudija nije odobren);",
        "  svaki scenario s rubrikom je REVIEW, ne PASS.",
        "- `numeric_consistent` po dizajnu preskače izraze s promjenljivom, procentima,",
        "  stepenima, nejednačinama i korijenima. Preskočeno nije dokaz ispravnosti.",
        "- `geometry_ok` provjerava samo notaciju i samo u geometrijskim lekcijama.",
        "- Recenzentove `checks.*` su modelove tvrdnje i nikad se ne broje kao kvalitet.",
        "- Rate limit, 409 i auth ponašanje nisu mjereni (limiteri podignuti za kampanju).",
        "",
    ]
    return "\n".join(lines)
