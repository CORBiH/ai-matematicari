# -*- coding: utf-8 -*-
"""Regresije za nalaze iz dječijih simulacija 2026-07-12 (N1–N6).

Sve deterministički (mock model). Pokriva: preuzimanje učenikovog zadatka (N1),
image-practice ispravke (N3), meta-identitet (N5), routing dopune (N6),
'nemoj rješenje' (N2), refusal-nije-zadatak (N4 guard).
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot import prompt_builder as pb
from matbot.answer_checker import extract_task_expressions
from tests.helpers.conversation_client import ConversationClient


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content(grade=6)


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map(grade=6)


def _chat(reply="U redu."):
    calls = {"messages": []}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["messages"].append(messages)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))]
        )

    chat.calls = calls
    return chat


def _prompt(chat):
    up = chat.calls["messages"][-1][-1]["content"]
    if isinstance(up, list):
        up = next(p["text"] for p in up if p.get("type") == "text")
    return up


# ===================== N1 — učenikov vlastiti zadatak =====================

def test_extract_task_expressions_variants():
    assert extract_task_expressions("evo prvi zadatak iz knjige: 3/4 + 5/6") == ["3/4 + 5/6"]
    assert extract_task_expressions("4/9 podijeljeno sa 2/3") == ["4/9 : 2/3"]
    assert extract_task_expressions("3 puta 5 plus 2") == ["3 * 5 + 2"]
    assert len(extract_task_expressions(
        "1/2+1/4, 2/3+1/6, 3/8+1/8, 5/6-1/3, 7/10-2/5")) == 5
    # negatives — NE smiju biti prepoznati kao zadatak
    for msg in ("daj mi zadatak iz razlomaka",
                "moj drug rijesi ovakve zadatke za 10 sekundi",
                "imam ocjene 5,4,3,5,4 koji mi je prosjek",
                "ne kontam minus brojeve", "daj zadatak"):
        assert extract_task_expressions(msg) == [], msg


def test_student_task_adopted_and_graded(master, tmap):
    """N1: 'evo zadatak: 3/4 + 5/6' → TAJ zadatak aktivni; odgovor se ocjenjuje
    protiv NJEGA (ranije: bot izmišljao sličan → 'Netačno' na tačan odgovor)."""
    chat = _chat("Radimo tvoj zadatak!")
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-040",
         "student_message": "evo prvi zadatak iz knjige: 3/4 + 5/6"},
        chat, master, tmap, model="m", timeout=1)
    assert out["last_tutor_task"] == "Izračunaj: 3/4 + 5/6"
    up = _prompt(chat)
    assert "UČENIKOV VLASTITI ZADATAK" in up and "3/4 + 5/6" in up
    assert "MOD: VJEŽBAJ (practice)" not in up          # ne generiši svoj
    out2 = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-040",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": out["last_tutor_task"], "student_message": "19/12"},
        _chat("Tačno!"), master, tmap, model="m", timeout=1)
    assert [i["verdict"] for i in out2["answer_check"]["items"]] == ["correct"]


def test_student_task_list_becomes_multi_item(master, tmap):
    """N1 multi: lista od 5 izraza → numerisan zadatak + task_items; ordinalni
    odgovori se ocjenjuju po UČENIKOVIM zadacima."""
    chat = _chat("Redom!")
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-031",
         "student_message": "5 zadataka je: 1/2+1/4, 2/3+1/6, 3/8+1/8, 5/6-1/3, 7/10-2/5"},
        chat, master, tmap, model="m", timeout=1)
    assert out["next_state"]["task_items"] == {"labels": [1, 2, 3, 4, 5], "graded": []}
    out2 = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-031",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": out["last_tutor_task"],
         "previous_next_state": out["next_state"],
         "student_message": "za drugi sam dobio 5/6, treci 4/8, cetvrti 1/2, peti 3/10"},
        _chat("Bravo!"), master, tmap, model="m", timeout=1)
    by_n = {i["n"]: i["verdict"] for i in out2["answer_check"]["items"]}
    accepted = {"correct", "correct_equivalent_form"}
    assert {by_n[2], by_n[3], by_n[4], by_n[5]} <= accepted
    assert out2["next_state"]["task_items"]["graded"] == [2, 3, 4, 5]


def test_plain_task_request_not_adopted(master, tmap):
    """Guard: 'daj mi zadatak iz razlomaka' i dalje generiše bot-ov zadatak."""
    chat = _chat("Zadatak: Izračunaj 2/5+1/5.")
    svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-031",
         "student_message": "daj mi zadatak iz razlomaka"},
        chat, master, tmap, model="m", timeout=1)
    assert "UČENIKOV VLASTITI" not in _prompt(chat)


def test_answer_phase_never_adopts(master, tmap):
    """Odgovor '19/12' u answer fazi se NE tumači kao novi učenikov zadatak."""
    p = {"grade": 6, "mode": "practice",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": "Izračunaj: 3/4 + 5/6",
         "student_message": "9/12 + 10/12 = 19/12"}
    svc._apply_student_task_contract(p)
    assert "_student_task" not in p


# ===================== N3 — image-practice ispravke =====================

OCR3 = ("1. Izračunaj: 2/5 + 1/10\n2. Izračunaj: 3 1/2 - 1 3/4\n"
        "3. U razredu je 28 učenika. 3/7 su dječaci. Koliko je djevojčica?")


def test_image_practice_correction_does_not_eat_next_item(master, tmap):
    """N3: ispravka poslije netačnog NE smije 'pojesti' stavku 3; persist
    last_tutor_task drži tok; eager odgovor na najavljenu stavku radi."""
    c = ConversationClient(master, tmap, mode="practice")
    c.send("evo domaca", "Zadatak 1 sa slike.", image_ocr=OCR3)
    o2 = c.send("1/2", "Tačno! Na 2?", phase="answer")
    assert "3 1/2 - 1 3/4" in o2["last_tutor_task"]      # persist: sljedeća stavka
    c.send("da", "Zadatak 2.")
    o4 = c.send("2 1/4", "Netačno, tačno je 1 3/4. Idemo na 3?", phase="answer")
    assert o4["next_state"]["image_test"]["solved"] == ["1", "2"]
    assert "28 učenika" in o4["last_tutor_task"]          # persist: stavka 3
    # ISPRAVKA — prepoznata kao stavka 2, stavka 3 OSTAJE pending
    o5 = c.send("aha da, 1 3/4", "Tako je!", phase="answer")
    assert [i["verdict"] for i in o5["answer_check"]["items"]] == ["correct_equivalent_form"]
    assert o5["next_state"]["image_test"]["solved"] == ["1", "2"]
    assert o5["next_state"]["pending_action"]["next_item"] == 3
    # EAGER odgovor na stavku 3 bez 'da' → zatvara tok čisto
    o6 = c.send("16", "Bravo, sve si riješio!", phase="answer")
    assert o6["next_state"].get("image_test") is None      # image tok završen


# ===================== N5 — meta identitet =====================

@pytest.mark.parametrize("msg", [
    "jesi li ti pravi covjek ili robot",
    "ko te napravio",
    "jel me spijuniras? vidis li sta radim na mobitelu",
    "kako se zoveš",
])
def test_meta_identity_direct_answer(master, tmap, msg):
    chat = _chat("NE SMIJE biti pozvan.")
    out = svc.handle_chat(
        {"grade": 6, "mode": "explain", "student_message": msg},
        chat, master, tmap, model="m", timeout=1)
    assert chat.calls["messages"] == []                    # deterministički, bez modela
    assert "AI tutor za matematiku" in out["answer"]
    assert not out.get("last_tutor_task")


def test_meta_identity_skipped_mid_task():
    p = {"grade": 6, "mode": "practice",
         "interaction_phase": "answering_practice_task",
         "student_message": "jesi li ti robot"}
    svc._apply_meta_identity_contract(p)
    assert p.get("_direct_answer") is None                 # usred zadatka → model


# ===================== N6 — routing dopune =====================

def _answer_payload(msg, task="Izračunaj: 2/5 + 1/5"):
    return {"grade": 6, "mode": "practice", "selected_topic": "6-04-031",
            "interaction_phase": "answering_practice_task",
            "last_tutor_task": task, "student_message": msg}


def test_zasto_question_routes_to_help():
    p = _answer_payload("a zasto kod sabiranja minus i minus daje minus??")
    svc._apply_practice_help_contract(p)
    assert p.get("_skip_answer_check") is True
    assert p.get("interaction_phase") == "practice_help"


def test_score_question_routes_to_meta():
    p = _answer_payload("koliko bi to bilo bodova od 100, jesam prosao")
    svc._apply_practice_help_contract(p)
    assert p.get("_skip_answer_check") is True
    assert p.get("_score_question") is True
    assert "bodova" in p["student_message"] or "ocjenu" in p["student_message"]


def test_jos_jedan_isti_takav_is_new_task_request():
    assert svc.detect_new_task_request("daj jos jedan isti takav") == "same"
    assert svc.detect_new_task_request("mozes mi ponoviti isti zadatak") is None


def test_difficulty_request_payload_sets_hard_hint():
    p = _answer_payload("Daj mi teži zadatak.")
    p["difficulty_request"] = "harder"
    svc._apply_new_task_intent(p)
    assert p.get("intent") == "new_task_request"
    assert p.get("_difficulty_hint") == "harder"
    assert p.get("_skip_answer_check") is True


def test_explicit_hint_request_routes_to_practice_help():
    p = _answer_payload("Ne znam.")
    p["intent"] = "hint_request"
    svc._apply_hint_request_contract(p)
    assert p.get("_skip_answer_check") is True
    assert p.get("_practice_help_intent") == "hint"
    assert p.get("_explicit_hint_request") is True
    assert p.get("interaction_phase") == "practice_help"
    assert "Ne otkrivaj konačan rezultat" in p["student_message"]


# ===================== N2 — 'nemoj rješenje' =====================

def test_no_solution_request_flag_and_directive():
    p = _answer_payload("daj mi hint za drugi ali nemoj rjesenje",
                        task="1. Izračunaj: 1/2+1/4\n2. Izračunaj: 2/3+1/6")
    svc._apply_practice_help_contract(p)
    assert p.get("_no_solution_requested") is True
    assert p.get("_practice_help_intent") == "hint"
    block = pb.build_practice_help_instructions(p, {})
    assert block.startswith("‼️ UČENIK JE IZRIČITO TRAŽIO")


# ===================== N4 guard — refusal nije zadatak =====================

def test_refusal_redirect_line_is_not_a_task():
    assert svc._looks_like_practice_task_text(
        "Postavi mi pitanje ili zadatak iz matematike.") is False
    assert svc.extract_practice_task(
        "Postavi mi pitanje ili zadatak iz matematike.") == ""


# ============ Nalazi 2026-07-12 (screenshotovi): marker/re-explain/scroll ============

def test_new_task_marker_lead_in_variants_extracted():
    """Bot koji napiše 'Evo novi zadatak za tebe: ...' umjesto 'Zadatak: ...'
    NE smije izgubiti novi zadatak (inače se sljedeći odgovor ocijeni protiv
    PRETHODNOG zadatka — jagode/kruške bug sa screenshota)."""
    got = svc.extract_marked_task(
        "Djelimično tačno. Dobro si postavio razlomak.\n"
        "Evo novi zadatak za tebe: Napiši razlomak koji predstavlja odnos "
        "između 5 jagoda i 10 jagoda.")
    assert "5 jagoda" in got and "10 jagoda" in got
    assert "Evo novi" not in got and "za tebe" not in got
    # čisti 'Zadatak:' i dalje radi
    assert "2/5 + 1/5" in svc.extract_marked_task(
        "Tačno.\nZadatak: Izračunaj 2/5 + 1/5.")
    # 'Sljedeći zadatak:' varijanta
    assert "3/4 - 1/4" in svc.extract_marked_task(
        "Bravo.\nSljedeći zadatak: Izračunaj 3/4 - 1/4.")


def test_reexplain_simpler_reexplains_current_not_next():
    """'Objasni jednostavnije' usred koračanja sa slike PONAVLJA zadnji
    objašnjeni zadatak — NE prelazi na sljedeći (screenshot: skočio na zadatak 4)."""
    payload = {
        "grade": 6, "mode": "explain",
        "student_message": "objasni mi to jednostavnije",
        "image_ocr_text": OCR3,
        "previous_next_state": {
            "active_task_kind": "image_test",
            "image_test": {"item_labels": ["1", "2", "3"],
                           "solved": ["1", "2"], "next_item": "3"},
        },
    }
    st = svc._resolve_image_test_state(payload)
    assert st is not None
    assert st["current"] == "2"                 # zadnji objašnjeni, NE "3"
    assert payload.get("_reexplain_simpler") is True


def test_reexplain_directive_present_in_image_prompt():
    block = pb.build_image_test_instructions({
        "_image_test": {"labels": ["1", "2", "3"], "solved": ["1", "2"],
                        "current": "2", "current_task": "Izračunaj 3 1/2 - 1 3/4",
                        "style": "step_by_step"},
        "_reexplain_simpler": True,
    })
    assert "JEDNOSTAVNIJE" in block and "NE prelazi na sljedeći" in block


def test_no_nisi_glup_phrasing_in_prompts():
    """Bot NIKAD ne smije reći 'nisi glup' (učenik to nije izrekao)."""
    assert "nisi glup" not in pb._EMPATHY_DIRECTIVE.lower()
    from matbot import tutor_prompts as tp
    joined = " ".join(v for v in vars(tp).values() if isinstance(v, str)).lower()
    assert "nisi glup" not in joined
    # help blok za distres takođe čist
    distressed = pb.build_practice_help_instructions(
        {"grade": 6, "mode": "practice",
         "interaction_phase": "practice_help",
         "student_message": "ne mogu ovo, glup sam"}, {})
    assert "nisi glup" not in distressed.lower()


# ============ CLASS 1 (2026-07-12): hint pod-korak se ne ocjenjuje kao finalni ==========

def test_hint_turn_sets_just_hinted(master, tmap):
    """'ne znam' -> hint mora obilježiti next_state.just_hinted da sljedeći
    učenikov odgovor tretiramo kao mogući međukorak."""
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-040",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": "Izračunaj 1/2 + 1/3", "student_message": "ne znam"},
        _chat("Svedi na nazivnik 6: koliko je 1/2 = ?/6?"), master, tmap, model="m", timeout=1)
    assert out["next_state"]["just_hinted"] is True


def test_explicit_hint_request_never_grades_or_consumes_task(master, tmap):
    task = "Izračunaj 1/2 + 1/3"
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-040",
         "interaction_phase": "practice_help", "intent": "hint_request",
         "last_tutor_task": task, "student_message": "Ne znam."},
        _chat("Netačno. Svedi na nazivnik 6: koliko je 1/2 = ?/6?"),
        master, tmap, model="m", timeout=1,
    )
    assert "answer_check" not in out
    assert "Netačno" not in out["answer"]
    assert out["last_tutor_task"] == task
    assert out["next_state"]["expected_user_action"] == "answer_task"
    assert out["next_state"]["just_hinted"] is True


def test_just_hinted_survives_state_normalization():
    ns = svc._normalize_next_state({"just_hinted": True})
    assert ns["just_hinted"] is True
    assert svc._empty_next_state()["just_hinted"] is False


def test_post_hint_reply_softened_when_not_final(master, tmap):
    """Poslije hinta: međukorak '3/6' NE ide u ocjenjivanje (bez 'Netačno')."""
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-040",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": "Ana je pojela 1/2, Marko 1/3. Koliko zajedno?",
         "previous_next_state": {"just_hinted": True},
         "student_message": "3/6"},
        _chat("Tako je, bravo! Sad svedi i 1/3 na nazivnik 6."),
        master, tmap, model="m", timeout=1)
    # deterministička presuda povučena; zadatak persistira (nije prepisan prozom)
    assert out.get("answer_check") is None
    assert out["last_tutor_task"] == ""      # server ne mijenja zadatak; klijent drži original


def test_post_hint_final_correct_still_graded(master, tmap):
    """Poslije hinta: TAČAN FINALNI odgovor na aritmetički zadatak ostaje 'Tačno'
    (deterministička provjera se ne povlači kad je sve tačno)."""
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-039",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": "Izračunaj 2/9 + 5/9",
         "previous_next_state": {"just_hinted": True},
         "student_message": "7/9"},
        _chat("Tačno! Bravo."), master, tmap, model="m", timeout=1)
    assert out["answer_check"] is not None
    assert [i["verdict"] for i in out["answer_check"]["items"]] == ["correct"]


def test_post_hint_reply_prompt_block_has_no_verdict_forcing():
    """Post-hint blok ne smije forsirati ocjensku labelu (uzrok 'Netačno' na korak)."""
    block = pb.build_practice_followup_instructions(
        {"_post_hint_reply": True, "last_tutor_task": "Izračunaj 1/2 + 1/3"}, {})
    assert "VOĐENJE KROZ KORAK" in block
    assert "MEĐUKORAK" in block
    assert "PROVJERA ODGOVORA" not in block           # standardni grading blok isključen
    assert "PRVA REČENICA mora sadržavati" not in block


# ============ CLASS 2 (2026-07-12): težina u prirodnoj rečenici ==========

@pytest.mark.parametrize("msg,expected", [
    ("daj mi tezi", "harder"),
    ("to je previse lagano daj mi teze", "harder"),
    ("ovo je pretesko daj nesto laganije", "easier"),
    ("ovaj je bio lagan, daj tezi", "harder"),
    ("moze jos jedan ali tezi", "harder"),
    ("dosadno mi je daj nesto teze", "harder"),
    ("hocu izazov", "harder"),
    ("daj mi nesto teze", "harder"),
    ("ovo mi je prelagano", "harder"),
    ("uh ovo je pretesko daj nesto laganije", "easier"),
    # tema ima prednost — NE preusmjeravaj kao "iz iste teme"
    ("daj mi tezi zadatak iz procenata", None),
    ("daj mi zadatak sa razlomcima", None),
    # nije zahtjev za novim zadatkom
    ("koliko je 2+2", None),
    ("objasni mi ovaj tezi postupak", None),
    # čist novi zadatak i dalje "same"
    ("daj mi jos jedan", "same"),
    ("novi zadatak", "same"),
])
def test_detect_new_task_request_natural_phrasings(msg, expected):
    assert svc.detect_new_task_request(msg) == expected


def test_difficulty_adjustment_ordering():
    # "prelagano" sadrži "lagan" ali znači TEŽE; "pretesko" sadrži "tesk" ali LAKŠE
    assert svc._detect_difficulty_adjustment("prelagano") == "harder"
    assert svc._detect_difficulty_adjustment("pretesko") == "easier"
    assert svc._detect_difficulty_adjustment("previse lagano daj teze") == "harder"


def test_natural_harder_reroutes_in_exam_prep():
    """Exam-prep + 'to je previse lagano daj mi teze' → NOVI (teži) zadatak,
    NE ocjenjivanje i NE rješavanje botovog zadatka."""
    p = {"grade": 6, "mode": "exam", "selected_topic": "6-09-088",
         "student_message": "to je previse lagano daj mi teze"}
    svc._apply_new_task_intent(p)
    assert p.get("intent") == "new_task_request"
    assert p.get("_difficulty_hint") == "harder"
    assert p.get("_skip_answer_check") is True
    assert p.get("mode") == "exam"                    # kontrolni set ostaje exam
    assert "TEŽI" in p["student_message"]


# ============ CLASS 3 (2026-07-12): slika sa dva lista / dupla numeracija ==========

from matbot.image_result_verifier import extract_image_tasks

_TWO_SHEETS = (
    "1. Iz skupa C={1,3,7,9} izdvoj proste brojeve\n"
    "2. Ispitaj tačnost: 2|357\n3. Dopiši uglove\n"
    "4. Odredi suplementan ugao\n5. Jedan ugao veći za 54\n"
    "1. Iz skupa F={124,702} izdvoj djeljive sa 2\n"
    "2. Odredi NZD(72,96)\n3. Umjesto * stavi cifru\n"
    "4. Da li su suplementni\n5. Koliko iznosi alfa")
_ONE_SHEET = "1. Izračunaj 2+2\n2. Izračunaj 3+3\n3. Izračunaj 4+4"


def test_duplicate_task_numbering_detected():
    assert svc._duplicate_task_numbering(extract_image_tasks(_TWO_SHEETS)) is True
    assert svc._duplicate_task_numbering(extract_image_tasks(_ONE_SHEET)) is False


def test_two_sheets_single_result_asks_which_set():
    p = {"grade": 6, "mode": "quick", "image_ocr_text": _TWO_SHEETS,
         "student_message": "daj mi rezultat zadatka sa slike"}
    r = svc._resolve_result_selection(p)
    assert r and r["action"] == "ask"
    assert "dva" in r["message"].lower() and "set" in r["message"].lower()


def test_two_sheets_ambiguous_number_asks_which_set():
    """'prvi' se pojavljuje u OBA seta → ne pogađaj, pitaj s kojeg lista."""
    p = {"grade": 6, "mode": "quick", "image_ocr_text": _TWO_SHEETS,
         "student_message": "prvi"}
    r = svc._resolve_result_selection(p)
    assert r and r["action"] == "ask"
    assert "seta" in r["message"].lower() or "list" in r["message"].lower()


def test_single_sheet_number_still_solves():
    """Regres: jedan list + 'prvi' → normalno riješi (ne pitaj)."""
    p = {"grade": 6, "mode": "quick", "image_ocr_text": _ONE_SHEET,
         "student_message": "prvi"}
    r = svc._resolve_result_selection(p)
    assert r and r["action"] == "solve" and r["item"] == 1


def test_sheets_grouped_at_numbering_restart():
    sheets = svc._group_task_sheets(extract_image_tasks(_TWO_SHEETS))
    assert len(sheets) == 2
    assert [it["label"] for it in sheets[0]] == ["1", "2", "3", "4", "5"]
    assert [it["label"] for it in sheets[1]] == ["1", "2", "3", "4", "5"]


@pytest.mark.parametrize("msg,expect_in_task", [
    ("prvi zadatak s prvog lista", "skupa C"),
    ("prvi zadatak s drugog lista", "skupa F"),
    ("drugi zadatak sa drugog lista", "NZD"),
])
def test_sheet_plus_number_resolves_to_exact_task(msg, expect_in_task):
    """Nakon razjašnjenja bot mora RIJEŠITI tačan zadatak (bez dead-end petlje),
    i modelu ide TAČAN TEKST zadatka — ne samo broj (inače bi listove pomiješao)."""
    p = {"grade": 6, "mode": "quick", "image_ocr_text": _TWO_SHEETS,
         "student_message": msg}
    r = svc._resolve_result_selection(p)
    assert r and r["action"] == "solve"
    assert expect_in_task in p["_result_solve_task"]


def test_result_prompt_uses_exact_task_text_for_duplicate_sheets():
    out = pb.build_result_mode_prompt(
        {"_result_solve_item": 1, "_result_solve_task": "Iz skupa F={124,702} izdvoj"})
    assert "Iz skupa F={124,702} izdvoj" in out["user_prompt"]
    assert "dva lista" in out["user_prompt"]


def test_image_test_does_not_step_through_duplicate_sheets():
    """Koračanje kroz sliku mora ODUSTATI kad su dva lista (inače tasks_by_label
    kolabira na zadnji list i 'prvi' vrati zadatak s pogrešnog lista)."""
    st = svc._resolve_image_test_state({
        "grade": 6, "mode": "quick", "image_ocr_text": _TWO_SHEETS,
        "student_message": "prvi"})
    assert st is None


@pytest.mark.parametrize("mode", ["quick", "practice", "exam"])
def test_duplicate_sheets_ask_in_every_mode(master, tmap, mode):
    """Svježa slika sa dva lista → razjašnjenje u SVIM modovima, bez poziva modela."""
    chat = _chat("MODEL NE SMIJE BITI POZVAN")
    out = svc.handle_chat(
        {"grade": 6, "mode": mode, "student_message": "rijesi mi zadatak sa slike"},
        chat, master, tmap, model="m", timeout=1,
        image_bytes=b"x", image_data_url="data:image/png;base64,AAA=",
        ocr_image=lambda b: (_TWO_SHEETS, 0.97), vision_model="v")
    assert chat.calls["messages"] == []                  # deterministički
    assert "dva" in out["answer"].lower()
    assert "list" in out["answer"].lower()


def test_ambiguous_number_asks_then_sheet_ref_resolves(master, tmap):
    """'prvi' → pitaj s kojeg lista; 'prvi s drugog lista' → riješi TAJ zadatak."""
    ask = svc.handle_chat(
        {"grade": 6, "mode": "quick", "image_ocr_text": _TWO_SHEETS,
         "student_message": "prvi"},
        _chat("x"), master, tmap, model="m", timeout=1)
    assert "lista" in ask["answer"].lower()
    # sa navedenim listom više NE pita — ide u rješavanje
    p = {"grade": 6, "mode": "quick", "image_ocr_text": _TWO_SHEETS,
         "student_message": "prvi zadatak s drugog lista"}
    assert svc._duplicate_sheets_clarification(p) == ""
    r = svc._resolve_result_selection(p)
    assert r["action"] == "solve" and "skupa F" in p["_result_solve_task"]


# ============ Batch 2026-07-13: preostali AUD/N nalazi ==========

def test_b2_new_task_marker_ignored_while_items_pending(master, tmap):
    """AUD-04: model doda 'Zadatak:' iako stavke čekaju → server gate ga ignoriše
    (aktivni multi-zadatak persistira)."""
    exam = "1. Izračunaj 2/9 + 4/9\n2. Izračunaj 5/8 - 1/8\n3. Izračunaj 1/2 + 1/3"
    out = svc.handle_chat(
        {"grade": 6, "mode": "exam", "selected_topic": "6-04-039",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": exam,
         "previous_next_state": {"task_items": {"labels": [1, 2, 3], "graded": []}},
         "student_message": "1) 6/9"},
        _chat("1. Tačno.\nZadatak: Izračunaj 3/4 + 1/4."),
        master, tmap, model="m", timeout=1)
    assert out["last_tutor_task"] == exam[:600]          # marker ignorisan, original persistira
    assert out["next_state"]["task_items"]["graded"] == [1]


def test_b3_quick_fresh_multi_image_generic_message_asks(master, tmap):
    """AUD-07: quick + svježa multi-slika + generička poruka → deterministički
    pitaj koji broj (model se NE poziva)."""
    chat = _chat("NE SMIJE BITI POZVAN")
    out = svc.handle_chat(
        {"grade": 6, "mode": "quick", "student_message": "evo slika"},
        chat, master, tmap, model="m", timeout=1,
        image_bytes=b"x", image_data_url="data:image/png;base64,AAA=",
        ocr_image=lambda b: ("1. Izračunaj 2/5+1/5\n2. Izračunaj 1/2-1/3\n3. Izračunaj 3*4", 0.97),
        vision_model="v")
    assert chat.calls["messages"] == []
    assert "broj zadatka" in out["answer"].lower()


def test_a3_new_solvers():
    """AUD-05/11: procenti, obrnuti procenat, zagrade, stepeni, jedinice."""
    from matbot.answer_checker import derive_expected, check_practice_answer
    def val(task):
        e = derive_expected(task)
        if e is None:
            return None
        v = e.value
        return str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"
    assert val("Koliko je 20% od 50?") == "10"
    assert val("15% broja je 30. Koji je to broj?") == "200"
    assert val("Izračunaj: (-3) + 5") == "2"
    assert val("Koliko je 3^2?") == "9"
    assert val("Koliko je 5 na kvadrat?") == "25"
    assert val("Kvadrat broja 4 je?") == "16"
    assert val("Pretvori 3 m u cm.") == "300"
    assert val("Pretvori 2,5 kg u g") == "2500"
    assert val("Koliko je 2 h u minutama?") == "120"
    # kombinovani stepen se NE presuđuje (bolje 'ne znam' nego pogrešno 3+1=4)
    assert derive_expected("Izračunaj 2^3 + 1") is None
    r = check_practice_answer("Koliko je 20% od 50?", "10")
    assert r.items[0].verdict == "correct"


def test_c2_bosnian_croatian_terms():
    """AUD-09: zbroj/okomito/kut/decimalna tačka/Pithagora → bosanski oblici."""
    from matbot.bosnian import to_ijekavica
    assert to_ijekavica("Zbroj brojeva je 12.") == "Zbir brojeva je 12."
    assert "pod pravim uglom na" in to_ijekavica("prava okomita na AB")
    assert to_ijekavica("Izmjeri kut od 45 stepeni.") == "Izmjeri ugao od 45 stepeni."
    assert "kutija" in to_ijekavica("kutija sa 5 olovaka")        # 'kut' se ne dira u 'kutija'
    assert "decimalni zarez" in to_ijekavica("pomjeri decimalnu tačku")
    assert "Pitagorina" in to_ijekavica("Pithagorina teorema")


def test_n12_samo_jos_jedan_pa_idem_is_new_task():
    assert svc.detect_new_task_request("samo jos jedan pa idem") == "same"
    assert svc.detect_new_task_request("daj mi samo jos jedan zadatak pa idem spavati") == "same"
    assert svc.detect_new_task_request("moram ici, zadnji zadatak") == "same"


def test_n8_explain_request_in_practice_does_not_track_prose(master, tmap):
    """N8: 'objasni mi X' u Vježbi (bez answer-faze) → explain potez; proza
    objašnjenja NE postaje last_tutor_task."""
    prose = "Da nacrtaš brojevni pravac: Nacrtaj pravu, označi tačku 0, nanesi jednake dužine."
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-034",
         "student_message": "objasni mi kako se crta brojevni pravac"},
        _chat(prose), master, tmap, model="m", timeout=1)
    assert out["last_tutor_task"] == ""
    assert out["session_mode"] == "practice"             # UI labela ostaje Vježba
    # regres: 'objasni mi 3/4 + 5/6' nosi SVOJ zadatak → N1 kontrakt ima prednost
    out2 = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-040",
         "student_message": "objasni mi 3/4 + 5/6"},
        _chat("Radimo tvoj zadatak."), master, tmap, model="m", timeout=1)
    assert out2["last_tutor_task"] == "Izračunaj: 3/4 + 5/6"


def test_d1_continuation_block_forbids_task_marker():
    block = pb.build_continuation_instructions({"last_tutor_message": "Evo objašnjenja..."})
    assert "NE piši red \"Zadatak:\"" in block


# ============ N9 (2026-07-14): mikro-zadatak u Objašnjenju ("Probaj ti: …") ==========
# Produkt-odluka: Objašnjenje SMIJE provjeriti razumijevanje, ali NE postaje mod
# koji prati zadatke — mikro-zadatak živi u next_state.micro_task, nikad u
# last_tutor_task, i ocjena se saopštava toplo (bez labela "Tačno."/"Netačno.").

_EXPL = ("Kod istih nazivnika sabereš samo brojnike, nazivnik ostaje isti.\n"
         "Probaj ti: koliko je 3/8 + 2/8?")


def test_micro_task_extracted_only_with_marker():
    assert svc.extract_micro_task(_EXPL) == "koliko je 3/8 + 2/8?"
    # bez markera → ništa (proza se ne pogađa)
    assert svc.extract_micro_task("Hoćeš da probamo jedan zadatak?") == ""
    # marker bez matematičkog signala nije zadatak
    assert svc.extract_micro_task("Probaj ti: razmisli malo o tome") == ""


def test_micro_task_lives_outside_last_tutor_task(master, tmap):
    """Objašnjenje NE smije postati mod koji prati zadatke (BUG 3/9, N8)."""
    out = svc.handle_chat(
        {"grade": 6, "mode": "explain", "selected_topic": "6-04-039",
         "student_message": "objasni sabiranje razlomaka istih nazivnika"},
        _chat(_EXPL), master, tmap, model="m", timeout=1)
    assert out["last_tutor_task"] == ""                       # i dalje prazno
    assert out["next_state"]["micro_task"] == "koliko je 3/8 + 2/8?"


def test_micro_task_survives_state_normalization():
    ns = svc._normalize_next_state({"micro_task": "koliko je 3/8 + 2/8?"})
    assert ns["micro_task"] == "koliko je 3/8 + 2/8?"
    assert svc._empty_next_state()["micro_task"] == ""


def test_micro_task_reply_is_checked_and_consumed(master, tmap):
    """Odgovor na mikro-zadatak se deterministički provjeri; zadatak se troši."""
    prev = {"micro_task": "koliko je 3/8 + 2/8?"}
    out = svc.handle_chat(
        {"grade": 6, "mode": "explain", "selected_topic": "6-04-039",
         "previous_next_state": prev, "student_message": "5/8"},
        _chat("Tako je, bravo!"), master, tmap, model="m", timeout=1)
    chk = out["answer_check"] or {}
    assert [i["verdict"] for i in chk.get("items", [])] == ["correct"]
    assert out["next_state"]["micro_task"] == ""              # potrošen
    assert out["last_tutor_task"] == ""                       # nikad ne curi


def test_micro_task_reply_prompt_block_forbids_labels(master, tmap):
    chat = _chat("Tako je!")
    svc.handle_chat(
        {"grade": 6, "mode": "explain", "selected_topic": "6-04-039",
         "previous_next_state": {"micro_task": "koliko je 3/8 + 2/8?"},
         "student_message": "5/8"},
        chat, master, tmap, model="m", timeout=1)
    up = _prompt(chat)
    assert "PROVJERA MIKRO-ZADATKA" in up
    assert "NIKAD ne piši ocjenske labele" in up


def test_micro_task_hard_label_is_stripped_and_softened():
    """Model povremeno ipak napiše 'Netačno.' — deterministički enforcement."""
    from matbot.answer_checker import check_practice_answer
    chk = check_practice_answer("koliko je 3/8 + 2/8?", "5/16")
    out = svc._soften_micro_task_answer("Netačno. Saberi brojnike: 3+2=5.", chk)
    assert not out.lower().startswith("netačno")
    assert out.startswith("Nije baš —")
    # tačan odgovor → topla potvrda
    chk2 = check_practice_answer("koliko je 3/8 + 2/8?", "5/8")
    out2 = svc._soften_micro_task_answer("Tačno. Nazivnik ostaje 8.", chk2)
    assert out2.startswith("Tako je!")
    # ne dupliraj uvod kad tekst već ima meki sud
    out3 = svc._soften_micro_task_answer("Netačno. Nije baš tačno, probaj opet.", chk)
    assert out3.startswith("Nije baš tačno")


def test_micro_task_question_does_not_hijack(master, tmap):
    """Pitanje (ne odgovor) poslije mikro-zadatka ide normalnim tokom."""
    p = {"grade": 6, "mode": "explain",
         "previous_next_state": {"micro_task": "koliko je 3/8 + 2/8?"},
         "student_message": "a sta ako su nazivnici razliciti?"}
    svc._apply_micro_task_contract(p)
    assert not p.get("_micro_task_reply")
