#!/usr/bin/env python
"""Engine V2 — Phase 7 evaluation harness CLI.

Runs the multi-turn corpus across feature-flag configurations fully OFFLINE
(scripted model) and prints a machine-readable JSON report plus a short summary.

    python scripts/eval_engine_v2.py                 # all configs, human summary
    python scripts/eval_engine_v2.py --json out.json # machine-readable report
    python scripts/eval_engine_v2.py --configs legacy all_v2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("LOCAL_MODE", "1")
os.environ.setdefault("OPENAI_API_KEY", "eval-not-real")
os.environ.setdefault("GSHEET_ID", "")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matbot.eval_v2 import CONFIGS, run_all  # noqa: E402


def _summary(report: dict) -> str:
    s = report["summary"]
    lines = [
        f"=== Engine V2 evaluation — {s['scenarios']} scenarios × "
        f"{len(s['configs'])} configs = {s['total_cases']} cases ===",
        f"passed {s['passed']}/{s['total_cases']}  ({s['pass_rate']}%)",
        "",
        "pass rate by category:",
    ]
    for cat, c in sorted(report["pass_rate_by_category"].items()):
        lines.append(f"  {cat:28} {c['passed']:>3}/{c['total']:<3} {c['pass_rate']:>5}%")
    lines += ["", "pass rate by config:"]
    for cfg, c in report["by_config"].items():
        lines.append(f"  {cfg:12} {c['passed']:>3}/{c['total']:<3} {c['pass_rate']:>5}%")
    lines += ["", f"legacy vs V2 divergences: {len(report['legacy_vs_v2_divergences'])}"]
    for d in report["legacy_vs_v2_divergences"][:10]:
        lines.append(f"  {d['config']:10} {d['id']:34} legacy={d['legacy']} v2={d['v2']}")
    lines += [
        "",
        f"grader sources : {report['grader_source_distribution']}",
        f"shadow conflicts: {report['shadow_conflicts']}",
        f"template validation failures: {len(report['template_validation_failures'])}",
        f"language failures: {len(report['language_failures'])}",
        f"consistency failures: {len(report['consistency_failures'])}",
        f"telemetry failures: {len(report['telemetry_failures'])}",
        f"sheets failures: {len(report['sheets_failures'])}",
        f"state-machine failures: {len(report['state_machine_failures'])}",
        "",
        "latency (mean ms/turn):",
    ]
    for cfg, l in report["latency"]["by_config"].items():
        lines.append(f"  {cfg:12} mean={l['mean_ms']:>7} max={l['max_ms']:>7}")
    lines.append(f"  all_v2 vs legacy: {report['latency']['all_v2_vs_legacy_pct']}%")
    lines += ["", "RELEASE GATES:"]
    for gate, ok in report["release_gates"].items():
        lines.append(f"  [{'PASS' if ok else 'FAIL'}] {gate}")
    lines.append("")
    lines.append(f"ALL GATES: {'PASS' if report['release_gates_passed'] else 'FAIL'}")
    if report["failures"]:
        lines += ["", f"failures ({len(report['failures'])}):"]
        for f in report["failures"][:25]:
            lines.append(f"  {f['config']}/{f['id']}: {f['failures']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Engine V2 evaluation harness (offline)")
    ap.add_argument("--configs", nargs="*", choices=list(CONFIGS), default=None)
    ap.add_argument("--json", dest="json_path", default=None,
                    help="write the machine-readable report here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    report = run_all(configs=args.configs)
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print(_summary(report))
    return 0 if report["release_gates_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
