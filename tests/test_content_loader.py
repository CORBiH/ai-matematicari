"""Testovi za matbot.content_loader (NPP schema, razredi 6–9).

Koristi STVARNE commitovane NPP workbook-ove iz data/{g}_razred/ (offline, bez
mreže), plus par sintetičkih .xlsx fajlova u tmp_path za negativne slučajeve
(nedostaje sheet / kolona / fajl). NPP_TOPICS je strukturno identičan kroz sve
razrede; sekundarni sheetovi (VIDEO_LINKS…) se razlikuju g6 vs g7/8/9 pa se
učitavanje mora osloniti samo na zajedničke kolone.
"""
import openpyxl
import pytest

from matbot import content_loader as cl

ALL_GRADES = (6, 7, 8, 9)


# --- helperi za sintetičke workbook-ove -----------------------------------------

def _write_xlsx(path, sheets):
    """sheets = {sheet_name: [ [red0...], [red1...] ]}"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


# --- normalizacija --------------------------------------------------------------

def test_normalize_header():
    assert cl.normalize_header("  Lesson  Title ") == "lesson_title"
    assert cl.normalize_header("course-name") == "course_name"
    assert cl.normalize_header("NPP_TOPIC_ID") == "npp_topic_id"
    assert cl.normalize_header(None) == ""


def test_normalize_value_numbers_and_none():
    # cijeli broj zapisan kao float ne smije postati "1.0"
    assert cl.normalize_value(1.0) == "1"
    assert cl.normalize_value(12) == "12"
    assert cl.normalize_value(None) == ""
    assert cl.normalize_value("  x  ") == "x"
    assert cl.normalize_value(True) == "True"


def test_normalize_grade_accepts_6_to_9():
    assert cl.normalize_grade("6. razred") == 6
    assert cl.normalize_grade(9) == 9
    for g in ALL_GRADES:
        assert cl.normalize_grade(g) == g


def test_unsupported_grade_clear_error():
    with pytest.raises(cl.ContentLoadError, match="Nepodrzan razred"):
        cl.normalize_grade(5)
    with pytest.raises(cl.ContentLoadError, match="Nepodrzan razred"):
        cl.load_master_content(grade=10)


# --- master: uspješno učitavanje stvarnih NPP fajlova ---------------------------

@pytest.mark.parametrize("grade", ALL_GRADES)
def test_master_loads_for_every_grade(grade):
    m = cl.load_master_content(grade=grade)
    assert m["grade"] == grade
    assert m["topic_ids"], f"g{grade}: NPP_TOPICS mora imati bar jedan topic"
    assert len(m["topics"]) == len(m["topic_ids"]), "npp_topic_id-evi jedinstveni"
    # npp_topic_id oblik "<grade>-NN-NNN"
    assert all(t["topic"].startswith(f"{grade}-") for t in m["topics"])


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_master_row_has_downstream_aliases(grade):
    m = cl.load_master_content(grade=grade)
    row = m["topics"][0]
    # aliasi na koje se oslanja ostatak pipeline-a
    assert row["topic"] == row["npp_topic_id"]
    assert row["oblast"] == row["oblast_ui"]
    assert row["display_name"] == row["tema_ui"]
    assert row["topic"] in m["topics_by_id"]


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_required_columns_present(grade):
    m = cl.load_master_content(grade=grade)
    for col in cl.REQUIRED_TOPIC_COLUMNS:
        assert col in m["columns"], f"g{grade}: nedostaje obavezna kolona {col}"


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_areas_derived_from_topics(grade):
    m = cl.load_master_content(grade=grade)
    areas = m["areas"]
    assert areas, f"g{grade}: mora imati bar jednu oblast"
    # oblasti su jedinstvene i sortirane po area_order
    names = [a["oblast"] for a in areas]
    assert len(names) == len(set(names)), "oblasti bez duplikata"
    orders = [a["area_order"] for a in areas]
    assert orders == sorted(orders), "oblasti sortirane po area_order"
    # zbir topic_count po oblastima == ukupan broj tema
    assert sum(a["topic_count"] for a in areas) == len(m["topics"])
    # svaka oblast dolazi iz stvarnih tema
    topic_oblasti = {t["oblast"] for t in m["topics"]}
    assert set(names) == topic_oblasti


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_videos_by_topic_shape(grade):
    m = cl.load_master_content(grade=grade)
    vids = m["videos_by_topic"]
    assert vids, f"g{grade}: bar jedna tema mora imati video"
    # svi ključevi su validni npp_topic_id-evi
    assert set(vids).issubset(m["topic_ids"])
    # svi redovi su lesson_type == video
    for rows in vids.values():
        assert rows
        for r in rows:
            assert r["lesson_type"].lower() == "video"
    # helper vraća iste redove
    tid = next(iter(vids))
    assert cl.videos_for_topic(tid, m) == vids[tid]
    assert cl.videos_for_topic("nema-takve-teme", m) == []


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_backwards_compatible_keys(grade):
    m = cl.load_master_content(grade=grade)
    # stari kod čita ove ključeve; NPP ih nema pa su None (ne smiju faliti)
    assert m["video_flow"] is None
    assert m["parent_report"] is None


def test_get_master_cache_is_per_grade():
    paths = {g: cl.get_master(grade=g)["source_path"] for g in ALL_GRADES}
    assert len(set(paths.values())) == len(ALL_GRADES), "svaki razred svoj fajl"
    for g in ALL_GRADES:
        assert cl.get_master(grade=g)["grade"] == g


def test_master_path_points_to_npp_file():
    p = cl.master_path_for_grade(8)
    assert p.name == "AI_MATH_8_NPP_BASE_UNIFIED_SIMPLE.xlsx"
    assert p.exists()


# --- thinkific map shim (NPP: jedan workbook, nema zasebne mape) -----------------

@pytest.mark.parametrize("grade", ALL_GRADES)
def test_thinkific_map_shim(grade):
    tm = cl.load_thinkific_map(grade=grade)
    assert tm["grade"] == grade
    # topic_reference_ids = svi npp_topic_id-evi (validacija detektovane teme)
    assert tm["topic_reference_ids"] == cl.get_master(grade=grade)["topic_ids"]
    # nema zasebne lesson mape u NPP modelu
    assert tm["lessons"] == []
    assert tm["mapped_topics"] == set()
    assert cl.validate_mapped_topics(grade=grade) == []


# --- master: negativni slučajevi ------------------------------------------------

def test_master_missing_file_raises(tmp_path):
    with pytest.raises(cl.ContentLoadError):
        cl.load_master_content(tmp_path / "nema.xlsx")


def test_master_missing_topics_sheet_raises(tmp_path):
    p = _write_xlsx(tmp_path / "m.xlsx", {"NESTO": [["a", "b"], [1, 2]]})
    with pytest.raises(cl.ContentLoadError):
        cl.load_master_content(p)


def test_master_missing_required_column_raises(tmp_path):
    # ima NPP_TOPICS ali bez oblast_ui/tema_ui
    p = _write_xlsx(
        tmp_path / "m.xlsx",
        {"NPP_TOPICS": [["grade", "npp_topic_id"], [6, "6-01-001"]]},
    )
    with pytest.raises(cl.ContentLoadError):
        cl.load_master_content(p)


def test_master_custom_valid_file_without_video_sheet(tmp_path):
    """VIDEO_LINKS je opcion — loader mora raditi i bez njega."""
    p = _write_xlsx(
        tmp_path / "m.xlsx",
        {
            "NPP_TOPICS": [
                ["grade", "npp_topic_id", "oblast_ui", "tema_ui", "area_order",
                 "has_thinkific_video"],
                [6, "6-01-001", "Skupovi", "Pojam skupa", 1, "DA"],
                [6, "6-01-002", "Skupovi", "Podskup", 1, "NE"],
                [6, "6-02-001", "Djeljivost", "Djelitelji", 2, "NE"],
                [None, None, None, None, None, None],  # prazan red se preskače
            ]
        },
    )
    m = cl.load_master_content(p)
    assert m["topic_ids"] == {"6-01-001", "6-01-002", "6-02-001"}
    assert m["videos_by_topic"] == {}
    # dvije oblasti, sortirane po area_order, topics_with_video ispravno
    assert [a["oblast"] for a in m["areas"]] == ["Skupovi", "Djeljivost"]
    assert m["areas"][0]["topic_count"] == 2
    assert m["areas"][0]["topics_with_video"] == 1


def test_video_links_variant_columns_tolerated(tmp_path):
    """g7/8/9 VIDEO_LINKS nema lesson_url; g6 ga ima — oba moraju proći."""
    p = _write_xlsx(
        tmp_path / "m.xlsx",
        {
            "NPP_TOPICS": [
                ["grade", "npp_topic_id", "oblast_ui", "tema_ui"],
                [7, "7-01-001", "Cijeli brojevi", "Pozitivni i negativni"],
            ],
            # nova varijanta: recommendation_rule umjesto lesson_url
            "VIDEO_LINKS": [
                ["grade", "npp_topic_id", "lesson_title", "lesson_type",
                 "has_url", "recommendation_rule", "lesson_order"],
                [7, "7-01-001", "Video A", "video", "NE", "kad zapne", 2],
                [7, "7-01-001", "Podsjetnik", "podsjetnik", "NE", "", 1],
                [7, "7-01-001", "Video B", "video", "NE", "kad zapne", 1],
            ],
        },
    )
    m = cl.load_master_content(p)
    vids = m["videos_by_topic"]["7-01-001"]
    # podsjetnik je izbačen; ostala 2 videa sortirana po lesson_order (1 pa 2)
    assert [v["lesson_title"] for v in vids] == ["Video B", "Video A"]
