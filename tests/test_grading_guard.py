# -*- coding: utf-8 -*-
"""Autoritativno pomirenje ocjene — jedan sud o tačnosti → konzistentan odgovor.

Cilj: bot NIKAD ne smije u istoj poruci i potvrditi i osporiti tačnost istog
odgovora, niti tačan odgovor proglasiti netačnim. Testovi pokrivaju:
- reprodukciju grešaka sa screenshotova (razlomci, direktan račun),
- najmanje tri NEPOVEZANE klase zadataka (nejednačine, jednačine, djeljivost),
- generički detektor kontradikcije koji radi NAD stvarno generisanim odgovorima
  (kroz handle_chat), ne samo nad izolovanim pomoćnim funkcijama.

Svi OpenAI pozivi su mockirani — nikad stvarni API.
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot.answer_checker import check_practice_answer
from matbot.grading_guard import (
    authoritative_verdict,
    enforce_grading_consistency,
    grade_contradiction_phrases,
    has_grade_contradiction,
)

FR_TOPIC = "razlomci_pojam_vrste"


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _fake_chat(reply):
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        msg = types.SimpleNamespace(content=reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    return chat


def _answer_payload(task, student, mode="practice", topic=FR_TOPIC):
    return {
        "grade": 6,
        "mode": mode,
        "selected_topic": topic,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": task,
        "student_message": student,
    }


# --- generički detektor kontradikcije (izolovano) -----------------------------------

@pytest.mark.parametrize("text", [
    "Nije tačno. Tačan rezultat je 5/8.",
    "netočno, ali je tačno na kraju",
    "This is wrong but actually correct.",
    "Nije ispravno — ipak, ispravno je rješenje 4.",
])
def test_detector_flags_positive_and_negative_together(text):
    assert has_grade_contradiction(text) is True


@pytest.mark.parametrize("text", [
    "Tačno! Bravo, odlično si to riješio.",
    "Nije tačno, tačan rezultat je 5/8.".replace("tačan rezultat", "rezultat"),
    "Rezultat je 5/8.",
    "Hajde da provjerimo zajedno korak po korak.",
])
def test_detector_does_not_flag_single_sided(text):
    assert has_grade_contradiction(text) is False


def test_detector_masks_negative_before_positive():
    # "tačno" unutar "nije tačno" ne smije proći kao pozitivna ocjena
    neg, pos = grade_contradiction_phrases("Nije tačno.")
    assert neg is True and pos is False


# --- reprodukcija screenshotova: tačan odgovor lažno proglašen netačnim -------------

def test_correct_fraction_complement_false_negative_becomes_positive():
    cr = check_practice_answer("Ako su obojane 3/8 kruga, koji dio nije obojen?", "5/8")
    assert authoritative_verdict(cr) == "correct"
    out = enforce_grading_consistency(
        "Nije tačno. Tačan rezultat je 5/8. Bravo!", cr
    )
    assert not has_grade_contradiction(out)
    assert "nije tačno" not in out.lower() and "netačno" not in out.lower()
    assert "5/8" in out


def test_correct_arithmetic_pure_false_negative_gets_positive_opener():
    cr = check_practice_answer("Izračunaj: 1/2 + 1/3", "5/6")
    assert authoritative_verdict(cr) == "correct"
    out = enforce_grading_consistency("Nije tačno, rezultat je 5/6.", cr)
    assert not has_grade_contradiction(out)
    assert out.lower().startswith("tačno")
    assert "5/6" in out


# --- nepovezane klase zadataka (kroz handle_chat, nad stvarnim odgovorom) -----------

CONTRADICTORY_REPLY = "Nije tačno. Ali zapravo je tačno, odgovor je ispravan."


@pytest.mark.parametrize("task,student", [
    # nejednačina — checker ne može presuditi (unknown), ali kontradikciju gasimo
    ("Riješi nejednačinu: x + 3 < 7.", "x < 4"),
    # jednačina
    ("Riješi jednačinu: x - 5 = 2.", "x = 7"),
    # djeljivost / prosti brojevi
    ("Da li je broj 7 prost broj? Objasni.", "da, 7 je prost broj"),
    # NZD
    ("Odredi NZD brojeva 12 i 18.", "6"),
])
def test_unverified_task_types_never_self_contradict(master, tmap, task, student):
    chat = _fake_chat(CONTRADICTORY_REPLY)
    out = svc.handle_chat(
        _answer_payload(task, student), chat, master, tmap, model="m", timeout=1
    )
    assert not has_grade_contradiction(out["answer"]), out["answer"]
    # nesiguran sud → ne smije ostati "Nije tačno" (izbjegni lažno negativno)
    assert "nije tačno" not in out["answer"].lower()


def test_verified_correct_answer_through_chat_is_positive(master, tmap):
    chat = _fake_chat("Nije tačno. Tačan rezultat je 5/12. Svaka čast na trudu!")
    out = svc.handle_chat(
        _answer_payload("Ako je pojedeno 7/12 pizze, koji dio je ostao?", "5/12"),
        chat, master, tmap, model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "correct"
    assert not has_grade_contradiction(out["answer"])
    assert "nije tačno" not in out["answer"].lower()
    assert "5/12" in out["answer"]


def test_verified_wrong_answer_through_chat_stays_negative(master, tmap):
    chat = _fake_chat("Nije tačno. Tačan rezultat je 5/8. Hajde da vidimo zajedno.")
    out = svc.handle_chat(
        _answer_payload("Ako su obojane 3/8 kruga, koji dio nije obojen?", "3/8"),
        chat, master, tmap, model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "incorrect"
    # netačan odgovor: negativna ocjena OSTAJE (i to nije kontradikcija — "tačan
    # rezultat je 5/8" je saopštavanje tačnog rezultata, ne potvrda učenika)
    assert "nije tačno" in out["answer"].lower()


# --- legitimna po-stavkovna ocjena se NE dira ---------------------------------------

def test_multi_item_mixed_grading_preserved(master, tmap):
    task = (
        "1. Ako je obojeno 5/12 pizze, koji dio nije obojen?\n"
        "2. Dječak je potrošio 3/10 novca. Koji dio novca mu je ostao?"
    )
    reply = "Prva stavka je tačna, 7/12. Druga nije tačna, tačno je 7/10."
    chat = _fake_chat(reply)
    out = svc.handle_chat(
        _answer_payload(task, "1) 7/12 2) 3/10"), chat, master, tmap,
        model="m", timeout=1,
    )
    # miješana ocjena je legitimna — per-stavka i tačno i netačno smiju stajati
    assert "tačna" in out["answer"].lower()
    assert "nije tačna" in out["answer"].lower()


# --- direktne jedinice pomirenja -----------------------------------------------------

def test_incorrect_verdict_false_positive_opener_neutralized():
    cr = check_practice_answer("Ako su obojane 3/8 kruga, koji dio nije obojen?", "3/8")
    assert authoritative_verdict(cr) == "incorrect"
    out = enforce_grading_consistency("Tačno! Bravo. Rezultat je 5/8.", cr)
    assert not out.lower().startswith("tačno")


def test_guard_is_noop_without_grading_language():
    cr = check_practice_answer("Izračunaj: 1/2 + 1/3", "5/6")
    text = "Rezultat je 5/6. Želiš li sličan zadatak za vježbu?"
    assert enforce_grading_consistency(text, cr) == text


def test_guard_never_raises_on_garbage():
    assert enforce_grading_consistency("", None) == ""
    assert enforce_grading_consistency(None, None) is None
    assert enforce_grading_consistency("neki tekst", object()) == "neki tekst"
