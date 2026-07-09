"""Testovi za matbot.topic_lookup (Phase 1).

Miješa stvarne podatke (iz commitovanih .xlsx) i sintetičke in-memory strukture.
Sintetički dio testira prioritet ključeva i dvosmislenost deterministički, pa
ostaje stabilan i ako se sadržaj tabele kasnije izmijeni.
"""
import pytest

from matbot import content_loader as cl
from matbot import topic_lookup as tl


# --- stvarni podaci -------------------------------------------------------------

@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


@pytest.fixture(scope="module")
def master8():
    return cl.load_master_content(grade=8)


@pytest.fixture(scope="module")
def tmap8():
    return cl.load_thinkific_map(grade=8)


# --- sintetički graditelji ------------------------------------------------------

def _mk_master(topic_ids):
    ids = set(topic_ids)
    return {
        "topics": [{"topic": t} for t in ids],
        "topics_by_id": {t: {"topic": t} for t in ids},
        "topic_ids": ids,
        "video_flow": None,
        "parent_report": None,
        "columns": [],
        "source_path": "<synthetic>",
    }


def _lesson(topic, *, title="", section="", course="", order="",
            url="", lid="", status="mapped"):
    return {
        "thinkific_lesson_id": lid,
        "lesson_url": url,
        "course_name": course,
        "section_name": section,
        "lesson_order": order,
        "lesson_title": title,
        "topic": topic,
        "status": status,
    }


def _mk_tmap(lessons, topic_reference_ids=()):
    return {
        "lessons": lessons,
        "mapped_topics": {l["topic"] for l in lessons if l.get("topic")},
        "topic_reference": None,
        "topic_reference_ids": set(topic_reference_ids),
        "columns": [],
        "source_path": "<synthetic>",
    }


# ================================================================================
# topic_exists / validate_topic / validate_detected_topic (stvarni podaci)
# ================================================================================

def test_topic_exists(master):
    assert tl.topic_exists("6-01-001", master) is True
    assert tl.topic_exists("ne_postoji_xyz", master) is False
    assert tl.topic_exists("", master) is False
    assert tl.topic_exists(None, master) is False


def test_validate_topic(master):
    assert tl.validate_topic("6-01-001", master)["status"] == "found"
    assert tl.validate_topic("ne_postoji_xyz", master)["status"] == "invalid"
    assert tl.validate_topic("", master)["status"] == "invalid"


def test_validate_detected_topic(master, tmap):
    assert tl.validate_detected_topic("6-01-001", master, tmap)["status"] == "found"
    # "unknown" je dozvoljeno
    assert tl.validate_detected_topic("unknown", master, tmap)["status"] == "unknown"
    assert tl.validate_detected_topic("", master, tmap)["status"] == "unknown"
    # izmišljena tema NIJE dozvoljena
    r = tl.validate_detected_topic("izmisljeno_123", master, tmap)
    assert r["status"] == "invalid"
    assert r["topic"] == "unknown"


def test_validate_detected_topic_accepts_topic_reference(master, tmap):
    # tema koja je u TOPIC_REFERENCE mora biti prihvaćena (handoff §5)
    some_tr = next(iter(tmap["topic_reference_ids"]))
    assert tl.validate_detected_topic(some_tr, master, tmap)["status"] == "found"


# ================================================================================
# find_lesson — stvarni podaci: kompozitni lookup + realna dvosmislenost
# ================================================================================

def test_npp_tmap_has_no_lesson_map(tmap, tmap8):
    """NPP model drži sve u jednom workbooku — nema zasebne Thinkific lesson mape,
    pa je ``lessons`` prazan, a ``topic_reference_ids`` = svi npp_topic_id-evi.
    (Prioritet ključeva find_lesson-a i dalje se testira sintetički niže.)"""
    for tm in (tmap, tmap8):
        assert tm["lessons"] == []
        assert tm["mapped_topics"] == set()
        assert tm["topic_reference_ids"]


def test_lesson_context_falls_through_when_no_lessons(master, tmap):
    """Bez lesson mape, thinkific_lesson kontekst se ne razriješi → pada na
    selected_topic (Objasni mi) ili detected_topic (Vježbajmo)."""
    res = tl.get_final_topic(
        {"entry_source": "thinkific_lesson", "lesson_title": "Bilo šta",
         "selected_topic": "6-01-001"},
        master, tmap,
    )
    assert res["status"] == "found"
    assert res["source"] == "selected_topic"
    assert res["final_topic"] == "6-01-001"


# ================================================================================
# find_lesson — sintetički: prioritet ključeva (deterministički)
# ================================================================================

def test_priority_lesson_id_wins_over_url_and_composite():
    tmap = _mk_tmap([
        _lesson("t_by_id", lid="L1"),
        _lesson("t_by_url", url="http://x/lesson", ),
        _lesson("t_by_comp", course="C", section="S", order="1", title="T"),
    ])
    payload = {
        "thinkific_lesson_id": "L1",
        "lesson_url": "http://x/lesson",
        "course_name": "C", "section_name": "S", "lesson_order": "1", "lesson_title": "T",
    }
    res = tl.find_lesson(payload, tmap)
    assert res["source"] == "lesson_id"
    assert res["final_topic"] == "t_by_id"


def test_priority_url_used_when_no_id():
    tmap = _mk_tmap([
        _lesson("t_by_url", url="http://x/lesson"),
        _lesson("t_by_comp", course="C", section="S", order="1", title="T"),
    ])
    payload = {
        "lesson_url": "http://x/lesson",
        "course_name": "C", "section_name": "S", "lesson_order": "1", "lesson_title": "T",
    }
    res = tl.find_lesson(payload, tmap)
    assert res["source"] == "lesson_url"
    assert res["final_topic"] == "t_by_url"


def test_composite_int_order_matches_string_cell():
    # payload lesson_order kao int mora se poklopiti sa "1" iz normalizovane ćelije
    tmap = _mk_tmap([_lesson("t", course="C", section="S", order="1", title="T")])
    res = tl.find_lesson(
        {"course_name": "C", "section_name": "S", "lesson_order": 1, "lesson_title": "T"},
        tmap,
    )
    assert res["status"] == "found" and res["final_topic"] == "t"


def test_section_plus_title_unambiguous():
    # nema lesson_order → pada na section+title; jedan topic → found
    tmap = _mk_tmap([
        _lesson("t_ok", section="S", title="Zajednicki"),
        _lesson("t_ok", section="S", title="Zajednicki"),  # isti topic, 2 reda
        _lesson("t_other", section="DRUGA", title="Zajednicki"),
    ])
    res = tl.find_lesson({"section_name": "S", "lesson_title": "Zajednicki"}, tmap)
    assert res["status"] == "found"
    assert res["final_topic"] == "t_ok"


def test_title_only_single_topic_found():
    tmap = _mk_tmap([
        _lesson("t_one", title="Naziv"),
        _lesson("t_one", title="Naziv"),
    ])
    res = tl.find_lesson({"lesson_title": "Naziv"}, tmap)
    assert res["status"] == "found"
    assert res["source"] == "fallback"
    assert res["final_topic"] == "t_one"


def test_title_only_multiple_topics_ambiguous():
    tmap = _mk_tmap([
        _lesson("t_a", title="Isti Naziv"),
        _lesson("t_b", title="Isti Naziv"),
    ])
    res = tl.find_lesson({"lesson_title": "Isti Naziv"}, tmap)
    assert res["status"] == "ambiguous"
    assert res["final_topic"] == "unknown"


def test_lesson_not_found():
    tmap = _mk_tmap([_lesson("t", title="Naziv")])
    res = tl.find_lesson({"lesson_title": "Ne postoji"}, tmap)
    assert res["status"] == "unknown"
    assert res["source"] == "fallback"


def test_matched_row_without_topic_is_unmapped():
    tmap = _mk_tmap([_lesson("", lid="L9", status="mapped")])
    res = tl.find_lesson({"thinkific_lesson_id": "L9"}, tmap)
    assert res["status"] == "unknown"
    assert res["final_topic"] == "unknown"


# ================================================================================
# get_final_topic — selected_topic / detected_topic / fallback
# ================================================================================

def test_final_topic_selected_valid():
    master = _mk_master({"t_sel"})
    tmap = _mk_tmap([])
    res = tl.get_final_topic({"selected_topic": "t_sel"}, master, tmap)
    assert res["status"] == "found"
    assert res["source"] == "selected_topic"
    assert res["final_topic"] == "t_sel"


def test_final_topic_selected_invalid():
    master = _mk_master({"t_sel"})
    tmap = _mk_tmap([])
    res = tl.get_final_topic({"selected_topic": "ne_postoji"}, master, tmap)
    assert res["status"] == "invalid"
    assert res["final_topic"] == "unknown"


def test_final_topic_detected_valid():
    master = _mk_master({"t_det"})
    tmap = _mk_tmap([])
    res = tl.get_final_topic({"detected_topic": "t_det"}, master, tmap)
    assert res["status"] == "found"
    assert res["source"] == "detected_topic"
    assert res["final_topic"] == "t_det"


def test_final_topic_detected_unknown():
    master = _mk_master({"t_det"})
    tmap = _mk_tmap([])
    res = tl.get_final_topic({"detected_topic": "unknown"}, master, tmap)
    assert res["status"] == "unknown"
    assert res["final_topic"] == "unknown"


def test_final_topic_detected_invalid():
    master = _mk_master({"t_det"})
    tmap = _mk_tmap([])
    res = tl.get_final_topic({"detected_topic": "izmisljeno"}, master, tmap)
    assert res["status"] == "invalid"
    assert res["final_topic"] == "unknown"


def test_final_topic_empty_payload_unknown_fallback():
    master = _mk_master({"t"})
    tmap = _mk_tmap([])
    res = tl.get_final_topic({}, master, tmap)
    assert res["status"] == "unknown"
    assert res["source"] == "fallback"
    assert res["final_topic"] == "unknown"


def test_selected_invalid_but_detected_valid_uses_detected():
    # handoff prioritet: nevalidan selected ne prekida — koristi se validan detected
    master = _mk_master({"t_det"})
    tmap = _mk_tmap([])
    res = tl.get_final_topic(
        {"selected_topic": "ne_postoji", "detected_topic": "t_det"}, master, tmap
    )
    assert res["status"] == "found"
    assert res["source"] == "detected_topic"
    assert res["final_topic"] == "t_det"


def test_lesson_context_beats_selected_and_detected():
    master = _mk_master({"t_lesson", "t_sel", "t_det"})
    tmap = _mk_tmap([_lesson("t_lesson", lid="L1", status="mapped")])
    res = tl.get_final_topic(
        {
            "entry_source": "thinkific_lesson",
            "thinkific_lesson_id": "L1",
            "selected_topic": "t_sel",
            "detected_topic": "t_det",
        },
        master,
        tmap,
    )
    assert res["status"] == "found"
    assert res["source"] == "lesson_id"
    assert res["final_topic"] == "t_lesson"


def test_unmapped_lesson_status_falls_through_to_selected():
    # lekcija nađena ali status != 'mapped' → ne prihvata se; koristi selected
    master = _mk_master({"t_sel", "t_lesson"})
    tmap = _mk_tmap([_lesson("t_lesson", lid="L1", status="needs_review")])
    res = tl.get_final_topic(
        {"thinkific_lesson_id": "L1", "selected_topic": "t_sel"}, master, tmap
    )
    assert res["status"] == "found"
    assert res["source"] == "selected_topic"
    assert res["final_topic"] == "t_sel"


def test_ambiguous_lesson_requires_manual_selection():
    master = _mk_master({"t_a", "t_b"})
    tmap = _mk_tmap([
        _lesson("t_a", title="Dupli"),
        _lesson("t_b", title="Dupli"),
    ])
    res = tl.get_final_topic({"lesson_title": "Dupli"}, master, tmap)
    assert res["status"] == "ambiguous"
    assert res["final_topic"] == "unknown"


def test_final_topic_guarantee_exists_in_master(master, tmap):
    # invariant (pravilo 10): svaki non-unknown final_topic postoji u masteru
    payloads = [
        {"selected_topic": "6-01-001"},
        {"detected_topic": "6-04-031"},
        {},
        {"selected_topic": "ne_postoji"},
    ]
    for p in payloads:
        res = tl.get_final_topic(p, master, tmap)
        if res["final_topic"] != "unknown":
            assert res["final_topic"] in master["topic_ids"]
