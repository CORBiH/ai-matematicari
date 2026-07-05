"""Testovi za matbot.content_loader (Phase 1).

Koristi STVARNE commitovane Excel fajlove iz data/6_razred/ (offline, bez mreže),
plus par sintetičkih .xlsx fajlova napisanih u tmp_path za negativne slučajeve
(nedostaje sheet / kolona / fajl).
"""
import openpyxl
import pytest

from matbot import content_loader as cl


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


# --- fixtures za stvarne fajlove ------------------------------------------------

@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def master7():
    return cl.load_master_content(grade=7)


@pytest.fixture(scope="module")
def master8():
    return cl.load_master_content(grade=8)


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


@pytest.fixture(scope="module")
def tmap7():
    return cl.load_thinkific_map(grade="7")


@pytest.fixture(scope="module")
def tmap8():
    return cl.load_thinkific_map(grade="8")


# --- normalizacija --------------------------------------------------------------

def test_normalize_header():
    assert cl.normalize_header("  Lesson  Title ") == "lesson_title"
    assert cl.normalize_header("course-name") == "course_name"
    assert cl.normalize_header("TOPIC") == "topic"
    assert cl.normalize_header(None) == ""


def test_normalize_value_numbers_and_none():
    # cijeli broj zapisan kao float ne smije postati "1.0" (kompozitni ključ!)
    assert cl.normalize_value(1.0) == "1"
    assert cl.normalize_value(12) == "12"
    assert cl.normalize_value(None) == ""
    assert cl.normalize_value("  x  ") == "x"
    # bool ne smije proći kroz int granu
    assert cl.normalize_value(True) == "True"


# --- master: uspješno učitavanje stvarnog fajla ---------------------------------

def test_master_loads(master):
    assert master["topic_ids"], "TOPICS mora imati bar jedan topic"
    assert len(master["topics"]) == len(master["topic_ids"]), "topic id-evi jedinstveni"
    # nekoliko poznatih topic-a iz mastera (izvor istine)
    assert "skupovi_uvod" in master["topic_ids"]
    assert "razlomci_pojam_vrste" in master["topic_ids"]


def test_grade_7_master_loads(master7):
    assert master7["grade"] == 7
    assert master7["topic_ids"], "7. razred TOPICS mora imati bar jedan topic"
    assert "cijeli_sabiranje_oduzimanje" in master7["topic_ids"]


def test_grade_8_master_loads(master8):
    assert master8["grade"] == 8
    assert master8["topic_ids"], "8. razred TOPICS mora imati bar jedan topic"
    assert "stepeni_pravila_i_pojasnjenja_stepeni" in master8["topic_ids"]
    assert "tijela_valjak_osnove" in master8["topic_ids"]


def test_get_master_cache_is_per_grade(master, master7, master8):
    assert cl.get_master(grade=6)["grade"] == 6
    assert cl.get_master(grade="7")["grade"] == 7
    assert cl.get_master(grade="8")["grade"] == 8
    assert cl.get_master(grade=6)["source_path"] != cl.get_master(grade=7)["source_path"]
    assert cl.get_master(grade=7)["source_path"] != cl.get_master(grade=8)["source_path"]


def test_master_required_columns_present(master):
    for col in cl.REQUIRED_TOPIC_COLUMNS:
        assert col in master["columns"], f"nedostaje obavezna kolona {col}"


def test_master_optional_sheets_detected(master):
    # oba opciona sheet-a postoje u finalnom fajlu
    assert master["video_flow"] is not None
    assert master["parent_report"] is not None


def test_grade_7_optional_sheets_detected(master7):
    assert master7["parent_report"] is not None
    assert master7["video_flow"] is not None


def test_grade_8_optional_sheets_detected(master8):
    assert master8["parent_report"] is not None
    assert master8["video_flow"] is not None


def test_master_topics_by_id_maps_row(master):
    row = master["topics_by_id"]["skupovi_uvod"]
    assert row["topic"] == "skupovi_uvod"
    assert row["oblast"] == "Skupovi"


# --- master: negativni slučajevi ------------------------------------------------

def test_master_missing_file_raises(tmp_path):
    with pytest.raises(cl.ContentLoadError):
        cl.load_master_content(tmp_path / "nema.xlsx")


def test_master_missing_topics_sheet_raises(tmp_path):
    p = _write_xlsx(tmp_path / "m.xlsx", {"NESTO": [["a", "b"], [1, 2]]})
    with pytest.raises(cl.ContentLoadError):
        cl.load_master_content(p)


def test_master_missing_required_column_raises(tmp_path):
    # ima TOPICS ali bez display_name/lesson_scope
    p = _write_xlsx(
        tmp_path / "m.xlsx",
        {"TOPICS": [["grade", "oblast", "topic"], [6, "Skupovi", "x"]]},
    )
    with pytest.raises(cl.ContentLoadError):
        cl.load_master_content(p)


def test_master_custom_valid_file(tmp_path):
    p = _write_xlsx(
        tmp_path / "m.xlsx",
        {
            "TOPICS": [
                ["grade", "oblast", "topic", "display_name", "lesson_scope"],
                [6, "Skupovi", "t_a", "A", "scope a"],
                [6, "Skupovi", "t_b", "B", "scope b"],
                # prazan red mora biti preskočen
                [None, None, None, None, None],
            ]
        },
    )
    m = cl.load_master_content(p)
    assert m["topic_ids"] == {"t_a", "t_b"}
    assert m["video_flow"] is None and m["parent_report"] is None


# --- Thinkific mapa -------------------------------------------------------------

def test_thinkific_map_loads(tmap):
    assert tmap["lessons"], "MAP mora imati redove"
    assert tmap["mapped_topics"], "MAP mora mapirati bar jedan topic"


def test_grade_7_thinkific_map_loads(tmap7):
    assert tmap7["grade"] == 7
    assert tmap7["lessons"], "7. razred MAP mora imati redove"
    assert tmap7["mapped_topics"], "7. razred MAP mora mapirati bar jedan topic"


def test_grade_8_thinkific_map_loads(tmap8):
    assert tmap8["grade"] == 8
    assert tmap8["lessons"], "8. razred MAP mora imati redove"
    assert tmap8["mapped_topics"], "8. razred MAP mora mapirati bar jedan topic"
    assert "tijela_valjak_osnove" in tmap8["mapped_topics"]


def test_map_required_columns_present(tmap):
    for col in cl.REQUIRED_MAP_COLUMNS:
        assert col in tmap["columns"], f"nedostaje obavezna kolona {col}"


def test_topic_reference_optional_detected(tmap):
    assert tmap["topic_reference"] is not None
    assert tmap["topic_reference_ids"]


def test_grade_7_topic_reference_detected(tmap7):
    assert tmap7["topic_reference"] is not None
    assert tmap7["topic_reference_ids"]


def test_grade_8_topic_reference_detected(tmap8):
    assert tmap8["topic_reference"] is not None
    assert tmap8["topic_reference_ids"]


def test_unsupported_grade_clear_error():
    with pytest.raises(cl.ContentLoadError, match="Nepodrzan razred"):
        cl.load_master_content(grade=9)


def test_map_missing_sheet_raises(tmp_path):
    p = _write_xlsx(tmp_path / "map.xlsx", {"PRIMJER": [["a"], [1]]})
    with pytest.raises(cl.ContentLoadError):
        cl.load_thinkific_map(p)


# --- cross-file validacija (ključni poslovni zahtjev) ---------------------------

def test_all_mapped_topics_exist_in_master(master, tmap):
    missing = cl.validate_mapped_topics(master, tmap)
    assert missing == [], f"MAP topic-i koji ne postoje u TOPICS: {missing}"


def test_all_mapped_topics_exist_in_grade_7_master(master7, tmap7):
    missing = cl.validate_mapped_topics(master7, tmap7, grade=7)
    assert missing == [], f"7. razred MAP topic-i koji ne postoje u TOPICS: {missing}"


def test_all_mapped_topics_exist_in_grade_8_master(master8, tmap8):
    missing = cl.validate_mapped_topics(master8, tmap8, grade=8)
    assert missing == [], f"8. razred MAP topic-i koji ne postoje u TOPICS: {missing}"
