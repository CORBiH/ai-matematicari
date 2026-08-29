"""Učitava data/topics.json (generisan iz Excela, vidi scripts/build_topics_json.py)
i servira ga u obliku koji frontend očekuje na GET /api/ai-tutor/topics."""
import json
import re
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "topics.json"

_cache = None
_TOPIC_ID_RE = re.compile(r"^(?P<oblast_id>\d+-\d{2})-\d{3}$")


def oblast_id_for_topic(topic_id):
    """Vrati stabilni kanonski ID oblasti izveden iz kanonskog ID-ja lekcije."""
    match = _TOPIC_ID_RE.fullmatch(str(topic_id or "").strip())
    return match.group("oblast_id") if match else ""


def _load():
    global _cache
    if _cache is None:
        with DATA_PATH.open("r", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def lesson_info(grade, topic_id):
    """Vrati kanonske identifikatore i nazive lekcije ili None."""
    grade_data = _load().get("grades", {}).get(str(grade).strip())
    if not grade_data or not topic_id:
        return None
    for lesson in grade_data.get("lessons", []):
        if lesson["id"] == topic_id:
            return {
                "id": lesson["id"],
                "title": lesson["title"],
                "oblast_id": oblast_id_for_topic(lesson["id"]),
                "oblast": lesson["oblast"],
                # Optional curriculum metadata: old title-only topic files remain valid.
                "lesson_scope": lesson.get("lesson_scope", lesson.get("scope", "")),
                "objectives": lesson.get("objectives", []),
                "exclusions": lesson.get("exclusions", []),
            }
    return None


def oblast_lessons(grade, oblast_id):
    """Kanonski spisak lekcija JEDNE oblasti: [{"id", "title"}, ...] u
    kurikularnom redoslijedu, ili prazna lista za nepoznat (grade, oblast_id).

    Postoji zbog moda „Sutra imam kontrolni“: klijent bira oblast, a server
    ODAVDE izvodi koje lekcije toj oblasti pripadaju — model nikad sam ne
    odlučuje šta je u oblasti, a proizvoljno ime oblasti iz browsera se ne
    prihvata (isti princip kao lesson_info za selected_topic)."""
    grade_data = _load().get("grades", {}).get(str(grade).strip())
    wanted = str(oblast_id or "").strip()
    if not grade_data or not wanted:
        return []
    return [
        {"id": lesson["id"], "title": lesson["title"], "oblast": lesson["oblast"]}
        for lesson in grade_data.get("lessons", [])
        if oblast_id_for_topic(lesson["id"]) == wanted
    ]


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
            {
                "topic": lesson["id"],
                "oblast_id": oblast_id_for_topic(lesson["id"]),
                "display_name": lesson["title"],
            }
        )

    return {"grouped": grouped, "oblast_order": grade_data.get("oblast_order", [])}


# --- FAZA 3D: kanonski izbor gradiva za evidenciju časa ---------------------
# EVIDENCIJA ČASA KORISTI ISTI KURIKULUM KAO I SVE OSTALO. Drugi spisak oblasti
# i lekcija bi značio drugu istinu o istom gradivu, a mjesečno grupisanje po
# oblastima u izvještaju roditelju oslanja se upravo na to da su nazivi isti
# svuda. Zato ovdje NEMA novih podataka — samo pogled na `data/topics.json`.
#
# PAMTE SE NAZIVI, NE ID-evi. Naziv oblasti i naslov lekcije su već kanonske
# vrijednosti kojima cijeli projekat barata (Practice, Kontrolni, izvještaj), pa
# bi uvođenje `topic_id` u `student_sessions` napravilo migracijski posao bez
# ijedne nove garancije.


def curriculum_areas(grade):
    """Nazivi oblasti jednog razreda, u KURIKULARNOM redoslijedu."""
    return list(topics_response(grade).get("oblast_order") or [])


def curriculum_lessons(grade, area_name):
    """Naslovi lekcija JEDNE oblasti, u kurikularnom redoslijedu.

    Nepoznata oblast vraća praznu listu — nikad izuzetak, jer ovo poslužuje i
    administratorski formular."""
    grouped = topics_response(grade).get("grouped") or {}
    return [entry["display_name"] for entry in grouped.get(str(area_name or "").strip(), [])]


def curriculum_choices(grade):
    """{oblast: [naslovi]} — sve što formular treba za dva povezana izbornika."""
    return {area: curriculum_lessons(grade, area) for area in curriculum_areas(grade)}


def curriculum_pair_valid(grade, area_name, lesson_name):
    """Da li (oblast, lekcija) STVARNO postoji u kurikulumu tog razreda.

    SERVER JE AUTORITET. Izbornici u pregledniku su pogodnost; ovo je jedina
    provjera koja odlučuje šta smije u bazu. Lekcija iz druge oblasti, iz drugog
    razreda ili izmišljena vrijednost padaju ovdje."""
    area = str(area_name or "").strip()
    lesson = str(lesson_name or "").strip()
    if not area or not lesson:
        return False
    return lesson in curriculum_lessons(grade, area)
