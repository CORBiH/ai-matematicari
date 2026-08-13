"""EVALUACIJA-ONLY: statička revizija determinističkih porodica (0 modelskih poziva).

Pitanje nije „radi li generator“ nego da li nudi STVARNU raznolikost i STVARNU
progresiju težine. Nulti poziv i nula sekundi imaju veliku vrijednost, ali ne
ako učenik na tri nivoa dobija istu rečenicu s drugim brojevima.

    python audit_deterministic.py
"""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
os.environ.setdefault("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")

from matbot.practice import run_practice_turn                       # noqa: E402
from matbot.session_store import SessionStore                       # noqa: E402
from matbot.tutor import lesson_context                             # noqa: E402

SAMPLES = 6          # koliko puta se traži zadatak na istom nivou
LEVELS = (1, 2, 3)


class NoModel:
    """Svaki modelski poziv je greška: ovdje se mjeri SAMO deterministički put."""

    def __getattr__(self, name):
        def explode(*args, **kwargs):
            raise AssertionError(f"modelski poziv: {name}")
        return explode


def lessons():
    topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    for grade, payload in sorted(topics["grades"].items()):
        for lesson in payload["lessons"]:
            yield int(grade), lesson


def probe(grade, lesson_id):
    """Vrati {nivo: [tekstovi]} ili None ako lekcija nije deterministička."""
    store = SessionStore()
    out = defaultdict(list)
    for sample in range(SAMPLES):
        sid = f"audit-{lesson_id}-{sample}"
        steps = [("Daj mi zadatak.", "")] + [("Daj mi teži zadatak.", "harder")] * 2
        for message, request in steps:
            try:
                run_practice_turn(store, NoModel(), {
                    "session_id": sid, "grade": grade, "selected_topic": lesson_id,
                    "selected_oblast": "", "student_message": message, "intent": "",
                    "difficulty_request": request, "interaction_phase": "",
                    "last_tutor_task": "", "interaction_type": "student_question",
                    "selected_option_id": "", "client_turn_id": ""})
            except AssertionError:
                return None
            except Exception:                                       # noqa: BLE001
                return None
            state = store.peek(sid) or {}
            if state.get("current_task"):
                out[state.get("difficulty_level", 1)].append(state["current_task"])
    return out or None


def main():
    rows = []
    for grade, lesson in lessons():
        levels = probe(grade, lesson["id"])
        if not levels:
            continue
        texts_by_level = {lvl: Counter(t for t in seq) for lvl, seq in levels.items()}
        distinct_total = len({t for seq in levels.values() for t in seq})
        total = sum(len(seq) for seq in levels.values())
        # ista rečenica na više nivoa = težina se ne vidi u zadatku
        shared = set.intersection(*[set(seq) for seq in levels.values()]) \
            if len(levels) > 1 else set()
        rows.append({
            "lesson_id": lesson["id"], "grade": grade, "title": lesson["title"],
            "oblast": lesson["oblast"], "levels": sorted(levels),
            "samples": total, "distinct_texts": distinct_total,
            "variety_ratio": round(distinct_total / total, 3) if total else 0.0,
            "text_shared_across_levels": len(shared),
            "distinct_per_level": {str(k): len(v) for k, v in texts_by_level.items()},
        })
        print(f"  {lesson['id']} r{grade} nivoi={sorted(levels)} "
              f"razlicitih={distinct_total}/{total} "
              f"dijeli_nivoe={len(shared)}  {lesson['title'][:40]}")
    out = Path(__file__).resolve().parent / "deterministic_audit.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\ndeterministickih lekcija: {len(rows)}")
    poor = [r for r in rows if r["variety_ratio"] < 0.34]
    flat = [r for r in rows if r["text_shared_across_levels"] > 0]
    print(f"slaba raznolikost (<34% razlicitih): {len(poor)}")
    print(f"ista recenica na vise nivoa: {len(flat)}")
    for r in sorted(flat, key=lambda x: -x["text_shared_across_levels"])[:12]:
        print(f"   {r['lesson_id']} dijeli {r['text_shared_across_levels']} tekst(ova) "
              f"| razlicitih {r['distinct_texts']}/{r['samples']} | {r['title'][:38]}")
    print(f"\nzapisano {out.name}")


if __name__ == "__main__":
    main()
