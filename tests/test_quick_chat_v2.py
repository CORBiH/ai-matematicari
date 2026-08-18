# -*- coding: utf-8 -*-
"""„Samo rezultat“ v2 — kratko po pravilu, fleksibilno po zahtjevu učenika.

ŽIVI BASELINE (2026-08-16, 10 turnova nad produkcijskim kodom) pokazao je da
tekstualni tok već radi (rezultat, „objasni“, „zašto“, „provjeri“), ali da
SLIKA nema nastavak: na stranici s pet zadataka Sol ispravno pita „koji da
riješim“, pa je na „Treći.“ stizalo „Pošalji sliku ili napiši tekst trećeg
zadatka.“, a na „Objasni postupak“ — „Nedostaje tekst ili slika trećeg
zadatka.“ Uz to je frontend prečicom „Objasni postupak“ MIJENJAO mod u
„Objasni mi“, gdje model više nije imao ni sliku ni zadatak.

Ovi testovi drže v2 ugovor: jedan poziv po turnu, kratko po defaultu, ali
objašnjenje/provjera/izbor zadatka kad ih učenik traži, uz aktivan zadatak koji
preživi turn i nestane kad počne nov zadatak.
"""
import re
from pathlib import Path

import pytest

from matbot import config, prompts, quick, quick_context
from matbot.schema import InvalidOutputError, validate_quick_output
from tests.conftest import (FakeLLM, make_detected_task, make_quick_image_output,
                            make_quick_output, make_visible_values)

INDEX = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(
    encoding="utf-8")


class _Image:
    data_url = "data:image/jpeg;base64,AAAA"
    image_format, width, height, normalized_bytes = "JPEG", 100, 100, 1024

    def log_metadata(self):
        return "test-image"


def run_turn(fake, message, store=None, image=None, grade=7, history=None):
    return quick.run_quick_turn(fake, {
        "session_id": "quick-v2", "grade": grade, "selected_topic": "",
        "selected_oblast": "", "student_message": message,
        "conversation_history": history or [], "interaction_phase": "",
    }, image=image, context_store=store)


# ---------------------------------------------------------------------------
# NAMJERA: podrazumijevano kratko, na zahtjev objašnjenje
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,expected", [
    ("2x + 5 = 13", quick.INTENT_RESULT),
    ("Koliko je $\\sqrt{48}$?", quick.INTENT_RESULT),
    ("Objasni kako si dobio.", quick.INTENT_EXPLAIN),
    ("Zašto si oduzeo 5?", quick.INTENT_EXPLAIN),
    ("Pokaži postupak korak po korak.", quick.INTENT_EXPLAIN),
    ("Riješi drugim načinom.", quick.INTENT_EXPLAIN),
    ("Provjeri je li moj odgovor tačan.", quick.INTENT_VERIFY),
    ("Je li tačno da je x = 4?", quick.INTENT_VERIFY),
])
def test_intent_classification(message, expected):
    assert quick.classify_quick_intent(message)[0] == expected


def test_subtask_intent_only_with_context_and_short_message():
    assert quick.classify_quick_intent("Treći.", has_context=True) == (quick.INTENT_SUBTASK, "3")
    assert quick.classify_quick_intent("pod b)", has_context=True) == (quick.INTENT_SUBTASK, "b")
    # Bez konteksta nema izbora zadatka; duga rečenica s brojem nije izbor.
    assert quick.classify_quick_intent("Treći.")[0] == quick.INTENT_RESULT
    assert quick.classify_quick_intent(
        "Koliko je tri puta pet?", has_context=True)[0] == quick.INTENT_RESULT


def test_default_prompt_contract_is_result_only():
    text = prompts.build_quick_instructions(7, intent="result")
    assert "SAMO REZULTAT" in text
    assert "Bez postupka" in text


def test_explain_contract_permits_steps_and_forbids_mode_handoff():
    text = prompts.build_quick_instructions(7, intent="explain")
    assert "UČENIK JE TRAŽIO OBJAŠNJENJE" in text
    assert "NE upućuj učenika u drugi mod" in text
    # Stara, kontradiktorna uputa da učenik pređe u „Objasni mi“ je uklonjena.
    assert "za detaljno učenje postoji mod" not in text


def test_verify_contract_asks_for_verdict_first():
    assert "Počni jasnom presudom" in prompts.build_quick_instructions(7, intent="verify")


def test_reply_limits_differ_by_intent():
    assert quick._reply_limit_for(quick.INTENT_RESULT) == config.MAX_QUICK_REPLY_CHARS
    assert quick._reply_limit_for(quick.INTENT_EXPLAIN) == config.MAX_QUICK_EXPLANATION_CHARS
    assert config.MAX_QUICK_EXPLANATION_CHARS > config.MAX_QUICK_REPLY_CHARS
    # Granica i dalje POSTOJI — objašnjenje nije esej.
    assert config.MAX_QUICK_EXPLANATION_CHARS <= 3000


def test_long_explanation_passes_but_result_stays_capped():
    long_reply = "Korak. " * 250                      # ~1750 znakova
    out = make_quick_output(reply=long_reply)
    validate_quick_output(out, max_reply_chars=config.MAX_QUICK_EXPLANATION_CHARS)
    with pytest.raises(InvalidOutputError):
        validate_quick_output(out, max_reply_chars=config.MAX_QUICK_REPLY_CHARS)


def test_grade_method_only_for_explanations():
    assert "VEZU MEĐU ČLANOVIMA" in prompts.build_quick_instructions(6, intent="explain")
    assert "VEZU MEĐU ČLANOVIMA" not in prompts.build_quick_instructions(6, intent="result")
    assert "VEZU MEĐU ČLANOVIMA" not in prompts.build_quick_instructions(9, intent="explain")


# ---------------------------------------------------------------------------
# AKTIVAN ZADATAK
# ---------------------------------------------------------------------------

def test_text_task_context_is_remembered_and_used():
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$x=4$"))
    run_turn(fake, "2x + 5 = 13", store=store)
    context = store.get("quick-v2")
    assert context["source_type"] == "text"
    assert "2x + 5 = 13" in context["task_text"]
    assert context["last_result"] == "$x=4$"

    fake.queue(make_quick_output(reply="$2x=8$, pa je $x=4$."))
    run_turn(fake, "Objasni kako si dobio.", store=store)
    sent = fake.quick_calls[-1][1]
    assert "AKTIVAN ZADATAK" in sent and "2x + 5 = 13" in sent
    assert fake.call_count == 2                       # jedan poziv po turnu


def test_a_fresh_problem_never_sees_the_previous_task():
    """ŽIVI NALAZ KAMPANJE (turn 03): poslije „3456 + 2891“ je na „2x + 5 = 13“
    stigao odgovor „$3456+2891=6347$“ — stari zadatak je i dalje bio u promptu.
    Poruka koja SAMA nosi zadatak ne smije vidjeti aktivan kontekst."""
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$6347$"))
    run_turn(fake, "3456 + 2891", store=store)
    fake.queue(make_quick_output(reply="$x=4$"))
    run_turn(fake, "2x + 5 = 13", store=store)
    sent = fake.quick_calls[-1][1]
    assert "AKTIVAN ZADATAK" not in sent
    assert "3456" not in sent
    assert store.get("quick-v2")["task_text"] == "2x + 5 = 13"


def test_number_inside_math_is_not_read_as_a_task_choice():
    """ŽIVI NALAZ KAMPANJE (turn 18): „Zašto si dijelio sa 5/9?“ je čitano kao
    izbor zadatka 5 i vraćalo poruku o nečitljivoj slici, bez ijednog poziva."""
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_image_output(
        reply="Više zadataka.", readability="multiple_tasks",
        detected_tasks=[make_detected_task("3", "Zadatak tri."),
                        make_detected_task("5", "Nejasan peti.", fully_readable=False)]))
    run_turn(fake, "", store=store, image=_Image())
    fake.queue(make_quick_output(reply="Zato što je $\\frac{5}{9}$ dio cjeline."))
    response = run_turn(fake, "Zašto si dijelio sa 5/9?", store=store)
    assert response["answer"] != quick.IMAGE_UNREADABLE_MESSAGE
    assert fake.call_count == 2


def test_new_task_replaces_stale_context():
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$x=4$"))
    run_turn(fake, "2x + 5 = 13", store=store)
    fake.queue(make_quick_output(reply="$12$"))
    run_turn(fake, "Novi zadatak: 84 : 7", store=store)
    context = store.get("quick-v2")
    assert "2x + 5" not in context["task_text"]
    assert "84 : 7" in context["task_text"]


def test_image_context_survives_the_turn_and_avoids_a_second_call():
    """Jezgro v2: slika je pročitana JEDNOM (Sol), nastavak ide Lunom iz
    zapamćenog konteksta — bez ponovnog slanja slike i bez drugog poziva."""
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", task_type="rectangle_area",
        requested_quantity="area",
        visible_values=make_visible_values(("a", "8", "cm"), ("b", "5", "cm"))))
    run_turn(fake, "", store=store, image=_Image())
    context = store.get("quick-v2")
    assert context["source_type"] == "image"
    assert {g["symbol"] for g in context["givens"]} == {"a", "b"}
    assert context["last_result"] == "$P=40\\,\\text{cm}^2$"

    fake.queue(make_quick_output(reply="$P=a\\cdot b=8\\cdot 5=40$"))
    run_turn(fake, "Objasni mi postupak korak po korak.", store=store)
    sent = fake.quick_calls[-1][1]
    assert "AKTIVAN ZADATAK" in sent and "a = 8 cm" in sent
    assert fake.quick_images[-1] is None              # slika NIJE ponovo poslana
    assert fake.call_count == 2                       # tačno jedan poziv po turnu


def test_multi_task_page_stores_inventory_and_solves_the_chosen_task():
    """Živi nalaz: „Treći.“ poslije stranice s više zadataka."""
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_image_output(
        reply="Na slici je više zadataka.", readability="multiple_tasks",
        detected_tasks=[
            make_detected_task("1", "Imenuj brojnike u razlomcima."),
            make_detected_task("3", "U 6. razredu je 25 djevojčica, što je 5/9 ukupnog broja. Koliko je učenika?"),
        ]))
    first = run_turn(fake, "", store=store, image=_Image())
    assert first["answer"] == quick.IMAGE_MULTIPLE_TASKS_MESSAGE
    assert len(store.get("quick-v2")["detected_tasks"]) == 2

    fake.queue(make_quick_output(reply="$45$"))
    run_turn(fake, "Treći.", store=store)
    sent = fake.quick_calls[-1][1]
    assert "25 djevojčica" in sent
    assert "izabran zadatak sa stranice: 3" in sent
    assert fake.quick_images[-1] is None
    assert fake.call_count == 2


def test_unreadable_chosen_task_is_refused_without_a_model_call():
    """Zadatak koji model NIJE pouzdano pročitao se ne rješava iz sjećanja."""
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_image_output(
        reply="Više zadataka.", readability="multiple_tasks",
        detected_tasks=[make_detected_task("2", "…nejasno…", fully_readable=False)]))
    run_turn(fake, "", store=store, image=_Image())
    calls_before = fake.call_count
    response = run_turn(fake, "Drugi.", store=store)
    assert response["answer"] == quick.IMAGE_UNREADABLE_MESSAGE
    assert fake.call_count == calls_before            # nijedan novi poziv


def test_unknown_label_does_not_invent_a_task():
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_image_output(
        reply="Više zadataka.", readability="multiple_tasks",
        detected_tasks=[make_detected_task("1", "Prvi zadatak.")]))
    run_turn(fake, "", store=store, image=_Image())
    fake.queue(make_quick_output(reply="Napiši koji zadatak."))
    run_turn(fake, "Peti.", store=store)
    sent = fake.quick_calls[-1][1]
    # Aktivan zadatak ostaje inventar; ništa se ne izmišlja o zadatku 5.
    assert "izabran zadatak sa stranice: 5" not in sent


def test_new_image_replaces_previous_task_context():
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_image_output(reply="$40$", visible_math="8*5"))
    run_turn(fake, "", store=store, image=_Image())
    fake.queue(make_quick_image_output(reply="$12$", visible_math="84:7"))
    run_turn(fake, "", store=store, image=_Image())
    context = store.get("quick-v2")
    assert "84:7" in context["task_text"] and "8*5" not in context["task_text"]


def test_missing_store_keeps_the_old_stateless_behaviour():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$x=4$"))
    response = run_turn(fake, "2x + 5 = 13", store=None)
    assert response["status"] == "ready" and fake.call_count == 1


def test_context_is_isolated_per_session():
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$x=4$"))
    run_turn(fake, "2x+5=13", store=store)
    assert store.get("neka-druga-sesija") is None


# ---------------------------------------------------------------------------
# HISTORIJA
# ---------------------------------------------------------------------------

def test_history_keeps_exactly_six_messages():
    assert quick.MAX_HISTORY_MESSAGES == 6
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"poruka {i}"}
               for i in range(20)]
    cleaned = quick._clean_history(history)
    assert len(cleaned) == 6
    assert cleaned[-1]["content"] == "poruka 19"      # zadnje 3 razmjene
    assert config.MAX_HISTORY_ITEMS == 6


# ---------------------------------------------------------------------------
# FRONTEND: prečice Quick-a NE MIJENJAJU mod
# ---------------------------------------------------------------------------

def test_quick_chips_do_not_switch_mode():
    """Poslije čišćenja akcija (2026-08-16) Quick ima TAČNO jednu prečicu.

    „Provjeri odgovor“ je uklonjena (učenik po pravilu nije ništa ponudio — to
    se traži porukom), a „Sličan zadatak“ je bila živi P0: slala je
    mode=practice sa selected_topic='' jer Quick nema lekciju → 400
    MISSING_TOPIC, a mod je do tada VEĆ bio promijenjen.
    """
    zone = INDEX[INDEX.index("return [   /* quick */"):]
    block = zone[:zone.index("]")]
    assert "Objasni postupak" in block
    assert "Provjeri odgovor" not in block
    assert "Sličan zadatak" not in block
    assert "mode:" not in block, block


def test_no_quick_action_can_enter_practice_without_a_lesson():
    """P0 regresija na nivou izvora; ponašanje dokazuje DOM svita.

    Cijeli frontend smije imati SAMO jednu prečicu koja mijenja mod, i to onu
    iz „Objasni mi“ (gdje lekcija postoji), a i nju čuva brana u renderChips.
    """
    zone = INDEX[INDEX.index("function chipDefs"):INDEX.index("function renderChips")]
    assert zone.count("mode: 'practice'") == 1
    assert "Pređi na vježbu" in zone
    render = INDEX[INDEX.index("function renderChips"):]
    render = render[:render.index("function showFallback")]
    assert "if (c.mode === 'practice' && !practiceHandoffTopic()) return;" in render


def test_image_gates_are_not_publishable_answers():
    """Nijedna slikovna kapija ne smije nositi `status` — to je JEDINO

    strukturno polje po kojem frontend zna da je odgovor stvarno objavljen, pa
    prečica „Objasni postupak“ ne smije stajati ispod poruke „ne mogu
    pročitati“ ili „na slici vidim više zadataka“.
    """
    for readability in ("multiple_tasks", "non_math", "unreadable"):
        fake = FakeLLM()
        fake.queue(make_quick_image_output(reply="…", readability=readability))
        response = run_turn(fake, "", image=_Image())
        assert "status" not in response, readability

    # Izbor zadatka koji model NIJE pouzdano pročitao — ista klasa odgovora.
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_image_output(
        reply="Više zadataka.", readability="multiple_tasks",
        detected_tasks=[make_detected_task("2", "…nejasno…", fully_readable=False)]))
    run_turn(fake, "", store=store, image=_Image())
    response = run_turn(fake, "Drugi.", store=store)
    assert response["answer"] == quick.IMAGE_UNREADABLE_MESSAGE
    assert "status" not in response

    # Kontrola: STVARNO riješen zadatak i dalje nosi status ready.
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$x=4$"))
    assert run_turn(fake, "2x + 5 = 13")["status"] == "ready"


def test_typed_task_with_math_does_not_switch_out_of_quick():
    """“Novi zadatak: 84:7” je zadatak koji učenik DAJE, ne zahtjev za vježbom."""
    zone = INDEX[INDEX.index("function wantsPracticeTask"):]
    body = zone[:zone.index("function isStoredQuickContext")]
    assert "if (/[0-9=]/.test(s)) return false;" in body


# ---------------------------------------------------------------------------
# IDENTITETI I GRANICE OSTAJU
# ---------------------------------------------------------------------------

def test_model_identities_unchanged():
    assert (config.QUICK_MODEL, config.QUICK_REASONING_EFFORT) == ("gpt-5.6-sol", "low")
    assert (config.QUICK_IMAGE_MODEL, config.QUICK_IMAGE_REASONING_EFFORT,
            config.QUICK_IMAGE_DETAIL) == ("gpt-5.6-sol", "low", "original")


def test_image_safety_gates_still_active():
    store = quick_context.QuickContextStore()
    fake = FakeLLM()
    fake.queue(make_quick_image_output(
        reply="$x=5$", math_content_uncertain=True,
        uncertainty_reason="Nazivnik može biti 3 ili 8."))
    response = run_turn(fake, "", store=store, image=_Image())
    assert response["answer"] == quick.IMAGE_UNREADABLE_MESSAGE
    # Nesiguran zadatak ne postaje aktivan kontekst.
    assert store.get("quick-v2") is None
