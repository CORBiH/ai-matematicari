# -*- coding: utf-8 -*-
"""Regresioni testovi za batch bagova sa Farisovih screenshotova (2026-07-10).

BUG 1  — "da" na ponudu novog zadatka davalo meta-pitanje (extract na gradingu)
BUG 2  — poslije tačnog odgovora nije slijedio novi zadatak
BUG 4  — višestavčna ocjena numerisana "1. 1. 1."
BUG 5  — dupla labela "Tačno. Djelimično tačno."
BUG 6  — yes/no zadaci djeljivosti + "zadatak" potvrde pogrešno rutirane
BUG 7  — izbor slovom ("koji ugao veći, C ili D?" + "d") pogrešno ocijenjen
BUG 8  — "daj mi teži/lakši" re-rješavao stari zadatak
BUG 10 — meta red prikazivao pogrešan režim (session vs prompt mode)
BUG 11 — Rezultat mod nepozvano otkrivao rezultat pri pitanju "koji zadatak?"
BUG 12 — višestavčna ocjena gubila trag koju stavku učenik odgovara
BUG 13 — kontradikcija unutar stavke prolazila kroz guard (multi bypass)
BUG 14 — Rezultat→Objašnjenje drift na "da" (continue_image_test)

Svi OpenAI pozivi su mockirani — nikad stvarni API.
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot.answer_checker import check_practice_answer
from matbot.bosnian import to_ijekavica
from matbot.grading_guard import enforce_grading_consistency

FR_TOPIC = "6-04-031"


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    # Legacy exam path is exercised here; the v2 Exam Engine has its own test file.
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "off")
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _fake_chat(reply="U redu."):
    calls = {"messages": []}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["messages"].append(messages)
        msg = types.SimpleNamespace(content=reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    chat.calls = calls
    return chat


def _last_user_prompt(chat):
    return chat.calls["messages"][-1][-1]["content"]


# ===== BUG 1: grading potez ne smije riješeni izraz proglasiti novim zadatkom =====

GRADING_WITH_MATH = (
    "Djelimično tačno.\n"
    "Ti si izračunao 3/10 + 2/10 i dobio 5/10, što je tačno, ali možemo skratiti.\n"
    "Kad skratimo 5/10 dobijamo 1/2.\n"
    "Želiš li sličan zadatak za vježbu?"
)


def test_bug1_grading_turn_extracts_only_marked_task():
    # bez "Zadatak:" markera grading potez NE prepoznaje zadatak
    assert svc.extract_marked_task(GRADING_WITH_MATH) == ""
    marked = GRADING_WITH_MATH + "\nZadatak: Izračunaj \\(\\frac{2}{7} + \\frac{3}{7}\\)."
    assert "2/7" in svc.extract_marked_task(marked) or "frac{2}{7}" in svc.extract_marked_task(marked)


def test_bug4b_marker_task_not_trailing_meta_line():
    """Živi nalaz 2026-07-11: kad zadatak koristi glagole van action-regexa
    ('navedi/zapiši/koliki'), a poslije njega stoji meta-uputa u zasebnom
    paragrafu ('Riješi zadatak i napiši svoje odgovore.'), ekstrakcija je birala
    meta-liniju. Sad marker 'Zadatak:' ima prednost i uzima svoj paragraf."""
    ans = ("Zadatak: Čokolada je podijeljena na 12 jednakih kocki. Marija pojede "
           "5 kocki. Kako to zapišemo razlomkom? Navedi brojnik i nazivnik.\n\n"
           "Riješi zadatak i napiši svoje odgovore.")
    for fn in (lambda a: svc.extract_practice_task(a, mode="practice"),
               svc.extract_marked_task):
        got = fn(ans)
        assert "Čokolada" in got and "12" in got
        assert "napiši svoje odgovore" not in got
    # čista ponuda (bez markera i math-signala) i dalje nije zadatak
    assert svc.extract_practice_task("Odlično! Hoćeš da nastavimo dalje?", mode="practice") == ""


def test_bug1_offer_survives_grading_turn(master, tmap):
    """Ocjena sa računicom + ponuda → pending_action=generate_similar_task
    (ranije je izraz iz ocjene 'pojeo' ponudu)."""
    chat = _fake_chat(GRADING_WITH_MATH)
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Izračunaj: 3/10 + 2/10",
        "student_message": "5/10",
    }, chat, master, tmap, model="m", timeout=1)
    assert not out.get("last_tutor_task")
    ns = out["next_state"]
    assert ns["expected_user_action"] == "continue_confirmation"
    assert ns["pending_action"]["type"] == "generate_similar_task"


def test_bug1_da_without_pending_gives_new_task_not_meta_question(master, tmap):
    chat = _fake_chat("Zadatak: Izračunaj 2/9 + 4/9.")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Izračunaj: 3/10 + 2/10",
        "student_message": "da",
    }, chat, master, tmap, model="m", timeout=1)
    assert "Samo mi reci šta želiš dalje" not in out["answer"]
    assert "sličan novi zadatak" in _last_user_prompt(chat)


# ===== BUG 2: tačan odgovor završava trenutni zadatak; novi kreće eksplicitno =====

def test_bug2_marked_new_task_after_grading_is_not_auto_started(master, tmap):
    reply = (
        "Tačno. Lijepo si sabrao brojnike.\n"
        "Zadatak: Izračunaj 5/11 + 3/11."
    )
    chat = _fake_chat(reply)
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Izračunaj: 2/7 + 3/7",
        "student_message": "5/7",
    }, chat, master, tmap, model="m", timeout=1)
    assert out.get("last_tutor_task", "") == ""
    assert "5/11" not in out["answer"]
    assert out["next_state"]["expected_user_action"] == "none"
    assert out["next_state"]["task_status"] == "completed"


def test_bug2_correct_streak_travels_forward(master, tmap):
    chat = _fake_chat("Tačno. Zadatak: Izračunaj 1/8 + 2/8.")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Izračunaj: 2/7 + 3/7",
        "student_message": "5/7",
        "previous_next_state": {"correct_streak": 1},
    }, chat, master, tmap, model="m", timeout=1)
    assert out["next_state"]["correct_streak"] == 2


# ===== BUG 4: renumeracija "1. 1. 1." =====

def test_bug4_repeated_ones_renumbered():
    text = "Djelimično tačno.\n1. Prvi dio je tačan.\n1. Drugi dio nije.\n1. Treći čeka."
    fixed = svc.fix_repeated_item_numbering(text)
    assert "1. Prvi" in fixed and "2. Drugi" in fixed and "3. Treći" in fixed


def test_bug4_legit_numbering_untouched():
    text = "1. Tačno.\n2. Netačno.\n3. Čeka odgovor."
    assert svc.fix_repeated_item_numbering(text) == text


# ===== BUG 5: dupla labela =====

def _correct_check():
    return check_practice_answer("Izračunaj: 5/9 + 3/9 - 2/9", "6/9")


def test_bug5_no_double_label_on_correct():
    answer = "Djelimično tačno. Izračunajmo ovako: 5/9 + 3/9 = 8/9, pa 8/9 - 2/9 = 6/9."
    out = enforce_grading_consistency(answer, _correct_check())
    assert out.startswith("Tačno.")
    assert "Djelimično tačno" not in out
    assert "Tačno. Tačno." not in out


def test_bug5_make_partial_no_duplicate():
    check = check_practice_answer("Skrati razlomak 5/10.", "5/10")
    answer = "Tačno. Djelimično tačno. Vrijednost je ista, ali se može skratiti."
    out = enforce_grading_consistency(answer, check)
    assert out.count("Djelimično tačno.") == 1
    assert not out.startswith("Tačno. ")


# ===== BUG 6: da/ne djeljivost je deterministički provjerljiva =====

def test_bug6_yes_no_divisibility_correct_and_incorrect():
    task = "Provjeri da li je broj 48 djeljiv sa 6. Koristi pravilo djeljivosti sa 6."
    # 2026-07-20: the task explicitly demands the RULE, so a bare "da" is an
    # INCOMPLETE answer, not a full one (multi-condition grading policy, rule 2).
    ok = check_practice_answer(task, "da")
    assert ok.checkable and ok.items[0].verdict == "incomplete"
    # …with the rule stated it is fully correct.
    full = check_practice_answer(task, "da, 48 je djeljiv sa 6 jer je paran i zbir cifara je djeljiv sa 3")
    assert full.checkable and full.items[0].verdict == "correct"
    bad = check_practice_answer(task, "ne")
    assert bad.checkable and bad.items[0].verdict == "incorrect"
    # 75 JESTE djeljiv sa 3 → "ne" je netačno
    t2 = "Provjeri da li je broj 75 djeljiv sa 3."
    assert check_practice_answer(t2, "ne").items[0].verdict == "incorrect"


def test_bug6_zadatak_message_requests_new_task(master, tmap):
    """Poruka "zadatak" tokom vježbe → novi zadatak, ne ponovna ocjena starog."""
    chat = _fake_chat("Zadatak: Provjeri da li je 84 djeljiv sa 4.")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Provjeri da li je broj 75 djeljiv sa 3.",
        "student_message": "zadatak",
    }, chat, master, tmap, model="m", timeout=1)
    assert "answer_check" not in out            # ništa se ne ocjenjuje
    up = _last_user_prompt(chat)
    assert "novi zadatak" in up
    assert "MOD: VJEŽBAJ (practice)" in up


# ===== BUG 7: izbor slovom =====

def test_bug7_choice_letter_answer_graded_deterministically():
    task = ("U uglovima C i D važi da je m∠C = 45° i m∠D = 60°. "
            "Koji je ugao veći, ugao C ili ugao D?")
    ok = check_practice_answer(task, "d")
    assert ok.checkable and ok.items[0].verdict == "correct"
    bad = check_practice_answer(task, "c")
    assert bad.checkable and bad.items[0].verdict == "incorrect"


def test_bug7_wrong_letter_grading_reconciled(master, tmap):
    """Model kaže "Netačno" za tačan izbor → guard preokrene u pozitivno."""
    chat = _fake_chat("Netačno. Ugao D je veći od ugla C jer je 60° veće od 45°.")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": ("U uglovima C i D važi da je m∠C = 45° i m∠D = 60°. "
                            "Koji je ugao veći, ugao C ili ugao D?"),
        "student_message": "d",
    }, chat, master, tmap, model="m", timeout=1)
    assert out["answer"].startswith("Tačno.")
    assert "Netačno" not in out["answer"]


# ===== BUG 8: teži/lakši =====

def test_bug8_harder_request_routes_to_new_task(master, tmap):
    chat = _fake_chat("Zadatak: Izračunaj 7/12 + 5/12 - 1/12.")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Izračunaj: 2/7 + 3/7",
        "student_message": "daj mi tezi",
    }, chat, master, tmap, model="m", timeout=1)
    up = _last_user_prompt(chat)
    assert "TEŽI" in up
    assert "MOD: VJEŽBAJ (practice)" in up      # novi zadatak, ne help/re-solve
    assert "answer_check" not in out


def test_bug8_detect_new_task_request_variants():
    assert svc.detect_new_task_request("zadatak") == "same"
    assert svc.detect_new_task_request("novi zadatak") == "same"
    assert svc.detect_new_task_request("daj mi tezi") == "harder"
    assert svc.detect_new_task_request("može lakši zadatak") == "easier"
    # odgovori/objašnjenja NISU zahtjev za novim zadatkom
    assert svc.detect_new_task_request("zadatak 3 je 5") is None
    assert svc.detect_new_task_request("objasni zadatak") is None
    assert svc.detect_new_task_request("5/10") is None
    # poruka koja NOSI TEMU ne smije biti prepisana sintetičkom (tema bi se izgubila)
    assert svc.detect_new_task_request("daj mi zadatke sa razlomcima") is None
    assert svc.detect_new_task_request("zadatak iz uglova") is None


# ===== BUG 10/14: session mode + bez drifta =====

def test_bug10_session_mode_echoed(master, tmap):
    chat = _fake_chat("Tačno.")
    out = svc.handle_chat({
        "grade": 6, "mode": "exam", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "1. Izračunaj 1/2 + 1/4.\n2. Skrati 5/10.",
        "student_message": "1) 3/4 2) 1/2",
    }, chat, master, tmap, model="m", timeout=1)
    # prompt-mod je practice (followup), ali sesija ostaje exam
    assert out["session_mode"] == "exam"


def test_bug14_quick_session_stays_quick_on_image_continue():
    payload = {"mode": "quick"}
    svc._rewrite_confirmation_payload(payload, {
        "type": "continue_image_test", "source": "image_context", "next_item": 2,
    })
    assert payload["mode"] == "quick"           # nema drifta u explain
    payload2 = {"mode": "explain"}
    svc._rewrite_confirmation_payload(payload2, {
        "type": "continue_image_test", "source": "image_context", "next_item": 2,
    })
    assert payload2["mode"] == "explain"


# ===== BUG 11: bez nepozvanog otkrivanja rezultata =====

def test_bug11_multi_task_ask_reveals_no_result():
    items = [
        {"label": "1", "task": "Izračunaj 1/2 + 1/4."},
        {"label": "2", "task": "Izračunaj 3/5 - 1/5."},
    ]
    msg = svc._multi_task_ask_message(items)
    assert "rezultat:" not in msg.lower()
    assert "3/4" not in msg and "2/5" not in msg
    assert "broj zadatka" in msg


# ===== BUG 12: atribucija stavke preostaloj =====

THREE_ITEM_TASK = (
    "1. Saberi razlomke 3/8 + 1/4.\n"
    "2. Skrati razlomak 6/9.\n"
    "3. Izračunaj 2/5 · 10."
)


def test_bug12_single_answer_attributed_to_last_pending_item(master, tmap):
    chat = _fake_chat("Tačno. Zadatak 3 je riješen: 2/5 · 10 = 4.")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": THREE_ITEM_TASK,
        "student_message": "4",
        "previous_next_state": {
            "task_items": {"labels": [1, 2, 3], "graded": [1, 2]},
        },
    }, chat, master, tmap, model="m", timeout=1)
    check = out.get("answer_check")
    assert check and check["items"][0]["n"] == 3
    assert check["items"][0]["verdict"] == "correct"
    # stanje ide naprijed: sve tri ocijenjene
    assert out["next_state"]["task_items"] == {"labels": [1, 2, 3], "graded": [1, 2, 3]}
    up = _last_user_prompt(chat)
    assert "Stavke 1, 2 su VEĆ ocijenjene" in up
    assert "odgovor na stavku 3" in up


def test_bug12_new_multi_task_gets_fresh_task_items(master, tmap):
    chat = _fake_chat(
        "Evo zadataka:\n1. Izračunaj 1/2 + 1/4.\n2. Skrati 6/9.\n3. Izračunaj 2/5 · 10.\n"
        "Trik: pazi na nazivnike.\nUpozorenje: skrati do kraja."
    )
    out = svc.handle_chat({
        "grade": 6, "mode": "exam", "selected_topic": FR_TOPIC,
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    }, chat, master, tmap, model="m", timeout=1)
    ti = out["next_state"].get("task_items")
    assert ti == {"labels": [1, 2, 3], "graded": []}


# ===== BUG 13: kontradikcija unutar stavke u multi kontekstu =====

def test_bug13_same_value_negation_removed_in_multi():
    answer = (
        "Djelimično tačno.\n"
        "1. Najveći zajednički djelilac (NZD) brojeva 24 i 36 nije 12. "
        "Tačan rezultat je 12. To si dobro naveo.\n"
        "2. Tačno je da je broj 125 djeljiv sa 5.\n"
    )
    out = enforce_grading_consistency(answer, None)
    assert "nije 12" not in out
    assert "Tačan rezultat je 12" in out


def test_bug13_student_positive_with_negative_label_reconciled():
    answer = (
        "1. Prvi zadatak je tačan.\n"
        "2. Netačno. Množimo brojnik sa 10: 2 · 10 = 20, pa 20 : 5 = 4. "
        "Rezultat je 4. Tvoj odgovor je tačan!\n"
    )
    out = enforce_grading_consistency(answer, None)
    seg2 = out.split("2.", 1)[1] if "2." in out else out
    assert "Netačno" not in seg2
    assert "Tvoj odgovor je tačan" in out


def test_bug13_legit_mixed_grading_untouched():
    answer = (
        "1. Tačno. Lijepo si sabrao.\n"
        "2. Netačno. Tačan rezultat je 2/3, a ne 6/9 — evo računa: 6:3=2, 9:3=3.\n"
    )
    out = enforce_grading_consistency(answer, None)
    assert "1. Tačno." in out
    assert "2. Netačno." in out                 # legitimna miješana ocjena ostaje


# ===== NZD/NZS deterministički =====

def test_nzd_answer_checked():
    res = check_practice_answer("Koji je najveći zajednički djelilac brojeva 24 i 36?", "12")
    assert res.checkable and res.items[0].verdict == "correct"
    res2 = check_practice_answer("Odredi NZS brojeva 4 i 6.", "12")
    assert res2.checkable and res2.items[0].verdict == "correct"
    res3 = check_practice_answer("Koji je najveći zajednički djelilac brojeva 24 i 36?", "6")
    assert res3.items[0].verdict == "incorrect"


# ===== exam ekstrakcija: uzastopne stavke, bez rupe (live nalaz 2026-07-10) =====

def test_exam_extraction_keeps_verbally_phrased_item():
    """Stavka bez cifre i bez '?' (verbalno formulisana) NE smije ispasti —
    ranije su labele bile [1,3] pa je grading preskakao stavku 2."""
    ans = (
        "Evo tri zadatka:\n"
        "1. Izračunaj 3/8 + 1/4. Rezultat izrazi kao skraćeni razlomak.\n"
        "2. Predstavi dio kruga koji je obojen ako su obojana tri od osam dijela.\n"
        "3. Skrati 6/9.\n"
        "Trik: prvo zajednički nazivnik.\nUpozorenje: pazi na jedinice."
    )
    from matbot.answer_checker import split_numbered_items
    et = svc.extract_practice_task(ans, mode="exam")
    assert [n for n, _ in split_numbered_items(et)] == [1, 2, 3]


def test_exam_extraction_rejects_gap_and_plain_text():
    # rupa [1,3] (stavka 2 nedostaje) → nije validna numerisana lista
    gap = "1. Izračunaj 3/8 + 1/4.\n3. Skrati 6/9."
    assert svc._extract_numbered_tasks(gap, 600) == ""
    # slučajna inline numeracija u prozi nije spisak zadataka
    assert svc.extract_practice_task("Objasnicu ukratko šta je razlomak i kako se čita.", mode="exam") == ""


# ===== "ne znam gdje je zapelo" → hint, ne "Netačno" (live nalaz 2026-07-10) =====

def _stuck_payload(msg):
    return {
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Ivan je pojeo 3/8 pice, pa još 1/4. Koliko je ukupno pojeo?",
        "student_message": msg,
    }


@pytest.mark.parametrize("msg", [
    "ne znam gdje je zapelo", "ne znam", "ne razumijem ovo", "ne kontam", "nemam pojma",
])
def test_stuck_message_routes_to_help_not_grading(msg):
    p = _stuck_payload(msg)
    svc._apply_practice_help_contract(p)
    assert p.get("interaction_phase") == "practice_help"
    assert p.get("_skip_answer_check") is True
    assert p.get("_practice_help_intent") == "hint"


@pytest.mark.parametrize("msg", ["5/8", "3/8 + 1/4 = 5/8", "mislim da je 5/8"])
def test_numeric_answer_still_graded_not_help(msg):
    p = _stuck_payload(msg)
    svc._apply_practice_help_contract(p)
    # pravi odgovor ostaje za ocjenjivanje (nije preusmjeren u help)
    assert p.get("interaction_phase") == "answering_practice_task"
    assert not p.get("_skip_answer_check")


def test_stuck_message_not_labeled_incorrect(master, tmap):
    """End-to-end: 'ne znam gdje je zapelo' NE smije dobiti labelu 'Netačno.'."""
    chat = _fake_chat("Bez brige — koji ti korak nije jasan? Kako smo sveli 1/4 na 2/8?")
    out = svc.handle_chat(_stuck_payload("ne znam gdje je zapelo"),
                          chat, master, tmap, model="m", timeout=1)
    assert "answer_check" not in out            # ništa se ne ocjenjuje
    assert "netačno" not in out["answer"].lower()
    up = _last_user_prompt(chat)
    assert "POMOĆ ZA AKTIVNI ZADATAK" in up
    assert "NE ponavljaj cijelo rješenje" in up


# ===== živi nalazi simulacije 2026-07-11: routing frustracije/pitanja + solver =====

@pytest.mark.parametrize("msg", [
    "uh ovo mi je pretesko, mrzim razlomke",
    "glup sam za ovo",
    "ne volim razlomke, dosadno mi je",
])
def test_frustration_routes_to_help_not_grading(msg):
    """#1: frustracija bez odgovora → pomoć/empatija, NE ocjena 'Netačno'."""
    p = _stuck_payload(msg)
    svc._apply_practice_help_contract(p)
    assert p.get("interaction_phase") == "practice_help"
    assert p.get("_skip_answer_check") is True
    assert p.get("_stuck_help") is True
    assert p.get("_original_student_message") == msg  # empatija-detekcija ga koristi


@pytest.mark.parametrize("msg", ["kolko je to", "koliko je to", "koji je rezultat"])
def test_vague_question_routes_to_help_not_grading(msg):
    """#3: nejasno pitanje bez odgovora → pomoć, NE ocjena 'Tačno'."""
    p = _stuck_payload(msg)
    svc._apply_practice_help_contract(p)
    assert p.get("_skip_answer_check") is True
    assert p.get("interaction_phase") == "practice_help"


def test_real_answer_still_graded_not_rerouted():
    """Regres-čuvar: stvaran numerički odgovor SE i dalje ocjenjuje."""
    p = _stuck_payload("5/8")
    svc._apply_practice_help_contract(p)
    assert p.get("_skip_answer_check") is not True


def test_fraction_times_integer_solver():
    """#2: 'Pomnoži ... 7/3 · 2' daje 14/3 (ranije 7/3 — konverzija presretala)."""
    from matbot.answer_checker import derive_expected
    e = derive_expected(r"Pomnoži i napiši kao mješoviti broj: \(\frac{7}{3}\cdot 2\).")
    assert e is not None and e.value == __import__("fractions").Fraction(14, 3)
    # čista konverzija i dalje radi
    e2 = derive_expected(r"Pretvori \(\frac{7}{3}\) u mješoviti broj.")
    assert e2 is not None and e2.value == __import__("fractions").Fraction(7, 3)


def test_continuation_block_forbids_rezultat_bold():
    """#4: 'Rezultat:' bold ne curi ni u nastavku objašnjenja."""
    from matbot import prompt_builder as pb
    block = pb.build_continuation_instructions({"last_tutor_message": "Ideja: ..."})
    assert "**Rezultat:**" in block  # zabrana izražena


# ===== #5: exam višestavkovni tok — "ne znam" ostaje pending, task persistira =====

def test_numbered_ne_znam_not_counted_as_answered():
    """#5: '3) ne znam' NIJE pokušaj — stavka 3 ostaje nepocijenjena."""
    from matbot.answer_checker import _numbered_nonanswer_items, check_practice_answer
    assert _numbered_nonanswer_items("1) 3/5 2) 1/2 3) ne znam") == {3}
    task = ("1. Saberi: \\(\\frac{3}{4}+\\frac{5}{6}\\).\n"
            "2. Pomnoži: \\(\\frac{7}{3}\\cdot 2\\).\n"
            "3. Podijeli \\(\\frac{5}{6}\\) na 3 dijela.")
    res = check_practice_answer(task, "1) 3/5 2) 1/2 3) ne znam")
    by_n = {it.n: it.verdict for it in res.items}
    assert by_n.get(1) == "incorrect" and by_n.get(2) in ("incorrect", "unverified")
    # stavka 3 nije "odgovorena": missing/not_attempted, NIKAD unverified/incorrect
    assert by_n.get(3) in ("missing", "not_attempted")


def test_exam_task_persists_when_items_pending(master, tmap):
    """#5: poslije grading poteza sa preostalom stavkom, last_tutor_task se
    ZADRŽAVA (ranije se brisao pa je sljedeći odgovor gubio kontekst)."""
    exam = ("1. Saberi: \\(\\frac{3}{4}+\\frac{5}{6}\\).\n"
            "2. Pomnoži: \\(\\frac{7}{3}\\cdot 2\\).\n"
            "3. Podijeli \\(\\frac{5}{6}\\) na 3 dijela.")
    chat = _fake_chat("1. Netačno...\n2. Netačno...\n3. Reci mi odgovor za stavku 3.")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": exam, "student_message": "1) 3/5 2) 1/2 3) ne znam",
        "previous_next_state": {"task_items": {"labels": [1, 2, 3], "graded": []}},
    }, chat, master, tmap, model="m", timeout=1)
    ti = out["next_state"].get("task_items")
    assert ti == {"labels": [1, 2, 3], "graded": [1, 2]}   # 3 ostaje pending
    assert out["last_tutor_task"].startswith("1.")          # exam zadržan


def test_multi_item_followup_forbids_top_label():
    """#5: kod više stavki labela ide UZ SVAKU, ne jedna zajednička na vrh."""
    from matbot import prompt_builder as pb
    fu = pb.build_practice_followup_instructions({"last_tutor_task": "1. ... 2. ..."}, {})
    assert "NE stavljaj jednu zajedničku labelu" in fu


# ===== jezik: novi oblici =====

def test_ijekavica_new_forms():
    assert to_ijekavica("Umesto toga, poslednja cifra.") == "Umjesto toga, posljednja cifra."
    assert to_ijekavica("Množimo brojitelj sa 10.") == "Množimo brojnik sa 10."
    assert to_ijekavica("To je točno.") == "To je tačno."
    assert "na primjer" in to_ijekavica("primjerice, kada mjeriš dužinu")
    # #6: provera→provjera (ne dira već ijekavski oblik)
    assert to_ijekavica("Provera: 25 · 0,5 = 12,5.") == "Provjera: 25 · 0,5 = 12,5."
    assert to_ijekavica("Provjera je tačna.") == "Provjera je tačna."


def test_dobro_pitanje_grammar():
    # KORAK 3 (2026-07-11): rod se ne slaže ("pitanje" je srednji rod).
    assert to_ijekavica("Dobar je pitanje!") == "Dobro pitanje!"
    assert to_ijekavica("dobar pitanje") == "dobro pitanje"
    # ne dira ispravno korišten muški rod ispred druge imenice
    assert to_ijekavica("Dobar zadatak.") == "Dobar zadatak."
