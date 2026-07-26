"""Učitava data/topics.json (generisan iz Excela, vidi scripts/build_topics_json.py)
i servira ga u obliku koji frontend očekuje na GET /api/ai-tutor/topics."""
import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "topics.json"

_cache = None


def _load():
    global _cache
    if _cache is None:
        with DATA_PATH.open("r", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def topics_response(grade):
    """Vraća {grouped, oblast_order} za dati razred (string ili int).

    Nepostojeći/nevažeći razred vraća prazan, ali validan odgovor —
    endpoint nikad ne smije baciti izuzetak zbog lošeg ulaza.
    """
    grades = _load().get("grades", {})
    grade_data = grades.get(str(grade).strip())
    if not grade_data:
        return {"grouped": {}, "oblast_order": []}

    grouped = {}
    for oblast in grade_data.get("oblast_order", []):
        grouped[oblast] = []
    for lesson in grade_data.get("lessons", []):
        grouped.setdefault(lesson["oblast"], []).append(
            {"topic": lesson["id"], "display_name": lesson["title"]}
        )

    return {"grouped": grouped, "oblast_order": grade_data.get("oblast_order", [])}
