"""Phase 2 (audit) — offline eval harness za tutor odgovore.

DVA načina rada:

1. DRY (default, BEZ ijednog API poziva):
     python scripts/eval_tutor.py
   Provjerava RUTIRANJE svakog slučaja (status/mode/final_topic vs. expect_*),
   mjeri veličinu promptova i ispisuje user prompt — dovoljno da se uhvati
   regresija u prompt stacku/detekciji odmah poslije izmjene, bez troška.

2. LIVE (SVJESNO, zove stvarni OpenAI API — nikad iz pytest-a):
     set MATBOT_EVAL_LIVE=1  (+ OPENAI_API_KEY)
     python scripts/eval_tutor.py --live
   Puni odgovori modela ulaze u izvještaj; čovjek ih ocjenjuje po
   docs/eval/RUBRIC.md.

Izvještaj: markdown u --out (default storage/eval_report.md — gitignored).
Pytest koristi run_eval(..., live=False) direktno — bez mreže.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_CASES = _REPO_ROOT / "docs" / "eval" / "eval_cases.json"
DEFAULT_OUT = _REPO_ROOT / "storage" / "eval_report.md"

DRY_ANSWER = "[DRY RUN — model NIJE pozvan]"

RUBRIC_COLUMNS = (
    "Tačnost", "Ijekavica", "Uzrast", "Toplina",
    "Kratkoća", "Format", "Tema", "Sljedeći korak",
)


def load_cases(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else DEFAULT_CASES
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["cases"]


def make_dry_chat():
    """Fake openai_chat: vraća marker; broji pozive (uklj. LLM klasifikator)."""
    calls = {"n": 0}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["n"] += 1
        # klasifikator teme očekuje JSON — vrati unknown da ne izmišlja temu
        content = DRY_ANSWER
        if messages and "klasifikator" in str(messages[0].get("content", ""))[:200]:
            content = '{"detected_topic": "unknown"}'
        msg = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    chat.calls = calls
    return chat


def make_live_chat():
    """Stvarni OpenAI poziv kroz app._tutor_openai_chat — SAMO uz eksplicitan opt-in."""
    if os.getenv("MATBOT_EVAL_LIVE") != "1":
        raise SystemExit("LIVE eval traži MATBOT_EVAL_LIVE=1 (svjesna odluka).")
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        raise SystemExit("LIVE eval traži OPENAI_API_KEY.")
    import app as app_mod
    return app_mod._tutor_openai_chat, app_mod.MODEL_TEXT


def run_case(case: dict, chat, model: str, live: bool) -> dict:
    from matbot import ai_tutor_service as svc

    payload = dict(case["payload"])
    grade = payload.get("grade", 6)
    out = svc.handle_chat(payload, chat, model=model, timeout=60 if live else 1)

    checks = []
    for key, field in (("expect_status", "status"), ("expect_mode", "mode"),
                       ("expect_topic", "final_topic")):
        want = case.get(key)
        if want is not None:
            got = out.get(field)
            checks.append((field, want, got, want == got))

    # DRY uvid u prompt (bez odgovora): rekonstruiši prompt istim putem
    prompt_info = ""
    if not live:
        from matbot.content_loader import get_master, get_thinkific_map
        from matbot import prompt_builder as pb
        from matbot.topic_lookup import get_final_topic
        try:
            m = get_master(grade=grade)
            t = get_thinkific_map(grade=grade)
            pr = pb.build_tutor_prompt(payload, get_final_topic(payload, m, t), m, t)
            prompt_info = (
                f"system≈{len(pr['system_prompt']) // 4} tok, "
                f"user≈{len(pr['user_prompt']) // 4} tok, "
                f"history_msgs={len(pr.get('history_messages') or [])}"
            )
        except Exception as exc:  # eval nikad ne smije pasti zbog uvida
            prompt_info = f"(prompt uvid nedostupan: {exc})"

    return {
        "id": case["id"],
        "note": case.get("note", ""),
        "status": out.get("status"),
        "mode": out.get("mode"),
        "final_topic": out.get("final_topic"),
        "answer": out.get("answer", ""),
        "checks": checks,
        "prompt_info": prompt_info,
    }


def render_report(results: list[dict], live: bool) -> str:
    total = len(results)
    failed = [r for r in results if any(not ok for *_x, ok in r["checks"])]
    lines = [
        "# MAT-BOT eval izvještaj",
        "",
        f"- Način: {'LIVE (stvarni model)' if live else 'DRY (bez API poziva)'}",
        f"- Slučajeva: {total}; rutiranje palo: {len(failed)}",
        "",
        "Ocjenjivanje (LIVE): upiši 0/1/2 po koloni prema docs/eval/RUBRIC.md.",
        "",
    ]
    for r in results:
        lines.append(f"## {r['id']}")
        if r["note"]:
            lines.append(f"*{r['note']}*")
        lines.append(
            f"- status=`{r['status']}` mode=`{r['mode']}` topic=`{r['final_topic']}`"
        )
        if r["prompt_info"]:
            lines.append(f"- prompt: {r['prompt_info']}")
        for field, want, got, ok in r["checks"]:
            lines.append(f"- {'✅' if ok else '❌'} {field}: očekivano `{want}`, dobijeno `{got}`")
        if live:
            lines.append("")
            lines.append("**Odgovor:**")
            lines.append("")
            lines.append("> " + (r["answer"] or "(prazno)").replace("\n", "\n> "))
            lines.append("")
            lines.append("| " + " | ".join(RUBRIC_COLUMNS) + " |")
            lines.append("|" + "---|" * len(RUBRIC_COLUMNS))
            lines.append("| " + " | ".join("_" for _ in RUBRIC_COLUMNS) + " |")
        lines.append("")
    return "\n".join(lines)


def run_eval(cases_path=None, live: bool = False, chat=None, model: str = "eval-model") -> tuple[str, list[dict]]:
    """Programski ulaz (koristi ga i pytest — uvijek sa fake chat-om)."""
    cases = load_cases(cases_path)
    if live:
        chat, model = make_live_chat()
    elif chat is None:
        chat = make_dry_chat()
    results = [run_case(c, chat, model, live) for c in cases]
    return render_report(results, live), results


def main() -> int:
    ap = argparse.ArgumentParser(description="MAT-BOT tutor eval (DRY default; --live zove API)")
    ap.add_argument("--live", action="store_true", help="stvarni OpenAI pozivi (traži MATBOT_EVAL_LIVE=1)")
    ap.add_argument("--cases", default=None, help=f"putanja do cases JSON (default {DEFAULT_CASES})")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="izlazni markdown izvještaj")
    args = ap.parse_args()

    report, results = run_eval(args.cases, live=args.live)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    bad = sum(1 for r in results if any(not ok for *_x, ok in r["checks"]))
    print(f"Eval gotov: {len(results)} slučajeva, rutiranje palo: {bad}. Izvještaj: {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
