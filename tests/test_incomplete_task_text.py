"""Zadatak koji traži matematički objekat, a ne pokaže ga — mora pasti zatvoreno.

ŽIVI NALAZ (kampanja Talas A + Talas B, universal_two_call, gpt-5-mini):

    A25  8-01-014  „Izračunaj vrijednost izraza:“        recenzent: approve
    B30  8-01-014  „Izračunaj vrijednost izraza:“        recenzent: approve
    B02  9-04-003  „Riješi jednačinu:“                   recenzent: correct
    B42  9-05-005  „Riješi sistem linearnih jednačina:“  recenzent: correct

Sva četiri su OBJAVLJENA učeniku: četiri numeričke opcije, označena tačna
opcija, i nijedan izraz, jednačina ni sistem koje bi učenik mogao riješiti.
Nijedan postojeći sloj to nije mogao vidjeti — `mathsafe` nema šta da
sanitizuje, `mathcheck` nema jednakost, `option_equivalence` vidi četiri
različite vrijednosti, `mcq_integrity` nije primjenjiv, a recenzent je vratio
`task_solvable_and_unambiguous=true`.

ZAŠTO NE „svaki tekst koji završava dvotačkom“: legitiman MCQ smije završiti
dvotačkom kad ponuđene opcije SAME dopunjuju pitanje („Odaberi tačnu tvrdnju:“).
Negativne kontrole ispod to zaključavaju.
"""
import pytest

from tests.conftest import (make_reviewer_final, make_task_payload, make_tutor_draft,
                            queue_two_call)
from matbot.tutor import package_preflight
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor.schema import UnifiedOutputError, validate_task

# Doslovni tekstovi iz produkcije. Ne mijenjati ih — oni su dokaz.
LIVE_INCOMPLETE = (
    "Izračunaj vrijednost izraza:",              # A25, B30
    "Riješi jednačinu:",                          # B02
    "Riješi sistem linearnih jednačina:",         # B42
)

# Isti defekt, samo drugačije razmaknut. Model varira formatiranje, ne suštinu.
WHITESPACE_VARIANTS = (
    "Izračunaj vrijednost izraza:   ",
    "  Riješi jednačinu:",
    "Riješi jednačinu:\n",
    "Riješi sistem linearnih jednačina:\n\n",
    "Riješi\tjednačinu:",
    "Izračunaj vrijednost izraza :",
    "Izračunaj vrijednost izraza.",               # tačka umjesto dvotačke
    "Riješi jednačinu",                           # bez interpunkcije
    "Izracunaj vrijednost izraza:",               # bez dijakritika
    "Riješite jednačinu:",
    "Riješi sistem:",
    "Pojednostavite izraz:",
    "Izračunaj vrijednost izraza :",         # nerazdvojni razmak (NFKC)
    "Riješi jednačinu：",                     # široka dvotačka (NFKC)
)

# ---------------------------------------------------------------------------
# FALSE-NEGATIVE GRANICA — namjerno NEDIRNUTO ovim uskim pravilom.
#
# Ovi tekstovi JESU semantički sumnjivi: cifra, razred ili nevezana varijabla
# nisu dokaz da je traženi objekat prikazan. Ali dokazati da duži tekst ne
# sadrži potpunu jednačinu traži širu analizu koja nije predmet ove izmjene.
# Zato ih ovo pravilo NE smije blokirati — niti smije tvrditi da su potpuni.
# Test zaključava tačno tu granicu: ne smiju nositi `incomplete_task_text`.
# ---------------------------------------------------------------------------
UNPROVEN_BY_THIS_RULE = (
    "Zadatak 2. Riješi jednačinu:",
    "Za 7. razred riješi jednačinu:",
    "Dat je broj 5. Riješi sistem linearnih jednačina:",
    "Posmatraj $x$. Riješi jednačinu:",
    "Koristi $a$. Izračunaj vrijednost izraza:",
)

# MCQ promptovi kod kojih OPCIJE nose sadržaj pitanja. Moraju ostati validni.
LEGITIMATE_COLON_PROMPTS = (
    "Odaberi tačnu tvrdnju:",
    "Koji je od ponuđenih odgovora tačan:",
    "Označi ispravan zapis:",
    "Odaberi tačnu jednačinu:",
    "Koja od ponuđenih formula je tačna:",
)

# Zadaci koji STVARNO nose objekat — prije ili poslije uvodne fraze.
# RAW stringovi: `\frac` mora ostati backslash + „frac“, nikad form-feed.
COMPLETE_TASKS = (
    r"Izračunaj vrijednost izraza: $2^{-2}$",
    r"Dat je izraz $2^{-2}$. Izračunaj vrijednost izraza:",
    r"Riješi jednačinu: $3x=12$",
    r"Riješi jednačinu $2(x)=8$ i izaberi tačan rezultat.",
    r"Riješi sistem linearnih jednačina: $x+y=5$ i $2x-y=1$",
    "Riješi sistem linearnih jednačina:\n$x+y=5$\n$2x-y=1$",
    r"Pojednostavi izraz $\frac{6x^2+9x}{3x}$.",
    r"Uprosti izraz $\dfrac{8x}{4x}$.",
    r"Riješi nejednačinu $-3x<12$ i izaberi tačan rezultat.",
    r"Koji od sljedećih brojeva je djeljiv sa 5?",
    r"Zbir unutrašnjih uglova trougla je poznata vrijednost. Kolika je?",
    r"Riješi jednačinu 3x = 12.",                  # bez $…$, ali s brojevima
)


def _task(text):
    return make_task_payload(text=text)


# ---------------------------------------------------------------------------
# 1. HARD GUARD — validate_task
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", LIVE_INCOMPLETE)
def test_live_incomplete_task_text_is_rejected(text):
    with pytest.raises(UnifiedOutputError) as error:
        validate_task(_task(text))
    assert package_preflight.INCOMPLETE_TASK_TEXT_CODE in str(error.value)


@pytest.mark.parametrize("text", WHITESPACE_VARIANTS)
def test_whitespace_and_newline_variants_are_rejected(text):
    with pytest.raises(UnifiedOutputError):
        validate_task(_task(text))


@pytest.mark.parametrize("text", LEGITIMATE_COLON_PROMPTS)
def test_legitimate_colon_ending_mcq_prompt_stays_valid(text):
    """Opcije nose sadržaj pitanja — ovo NIJE nepotpun zadatak."""
    validate_task(_task(text))


@pytest.mark.parametrize("text", COMPLETE_TASKS)
def test_task_that_actually_shows_its_object_stays_valid(text):
    validate_task(_task(text))


@pytest.mark.parametrize("text", UNPROVEN_BY_THIS_RULE)
def test_rule_does_not_claim_more_than_it_can_prove(text):
    """Sumnjivo, ali NEDOKAZANO ovim uskim pravilom — ne smije se blokirati.

    Cifra, oznaka razreda ili nevezana varijabla nisu dokaz da je traženi
    objekat prikazan; dokazati suprotno traži širu analizu izvan ove izmjene."""
    validate_task(_task(text))
    codes = [issue.code for issue in package_preflight.collect_package_issues(_task(text))]
    assert package_preflight.INCOMPLETE_TASK_TEXT_CODE not in codes


def test_fixtures_carry_a_real_backslash_frac_not_a_form_feed():
    """Zaštita od tihe greške u samom testu: `"\\f"` je form-feed, ne LaTeX.

    Da je fixture napisan bez raw stringa ili bez dvostrukog backslasha, test
    „potpun zadatak prolazi“ bi provjeravao pogrešan tekst i ništa ne bi značio."""
    fixture = next(text for text in COMPLETE_TASKS if "rac{6x^2" in text)
    assert "\\frac" in fixture
    assert "\x0c" not in fixture
    assert fixture.encode("utf-8").count(b"\\frac") == 1


# ---------------------------------------------------------------------------
# 2. PREFLIGHT — recenzent mora dobiti PRECIZAN nalaz, ne generički
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", LIVE_INCOMPLETE)
def test_preflight_reports_a_dedicated_code_for_the_reviewer(text):
    issues = package_preflight.collect_package_issues(_task(text))
    codes = [issue.code for issue in issues]
    assert package_preflight.INCOMPLETE_TASK_TEXT_CODE in codes
    # Generički kod se NE dodaje uz precizni — recenzent bi dobio istu stvar dvaput.
    assert "task_structure_invalid" not in codes


def test_reviewer_input_block_names_the_incomplete_task(  ):
    issues = package_preflight.collect_package_issues(_task("Riješi jednačinu:"))
    block = package_preflight.format_for_reviewer(issues)
    assert package_preflight.INCOMPLETE_TASK_TEXT_CODE in block
    assert "approve" in block          # blok i dalje zabranjuje odobrenje


@pytest.mark.parametrize("text", LEGITIMATE_COLON_PROMPTS + COMPLETE_TASKS)
def test_preflight_stays_silent_for_valid_task_text(text):
    codes = [issue.code for issue in package_preflight.collect_package_issues(_task(text))]
    assert package_preflight.INCOMPLETE_TASK_TEXT_CODE not in codes


# ---------------------------------------------------------------------------
# 3. OBJAVA — nepotpun paket ne smije proći ni kad ga recenzent vrati
# ---------------------------------------------------------------------------

def test_publication_guard_rejects_the_incomplete_package(monkeypatch):
    from matbot.tutor import lesson_context as lesson_context_module

    context = lesson_context_module.build(9, "9-04-003")
    task = make_task_payload(text="Riješi jednačinu:")
    task = task.model_copy(update={"selected_lesson_id": context.topic_id,
                                   "selected_lesson_title": context.title})
    with pytest.raises(UnifiedOutputError):
        tutor_pipeline.validate_task_package(task, context)


def test_reviewer_may_repair_an_incomplete_draft(store, fake_llm, monkeypatch):
    """Nacrt s nalazom ide recenzentu; njegov KOMPLETAN zadatak se objavljuje."""
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    broken = make_task_payload(text="Riješi jednačinu:")
    # PP-1 LIVE-150 (F008): orakl rješavanja sada NEZAVISNO rješava „Riješi
    # jednačinu: $3x=12$“ (x=4), pa popravljeni paket mora biti i matematički
    # konzistentan — podrazumijevane fixture opcije (razlomci od 5/7) bile bi
    # ispravno odbijene kao `no_correct_option`.
    repaired = make_task_payload(text="Riješi jednačinu: $3x=12$",
                                 options=("$4$", "$3$", "$12$", "$36$"),
                                 expected="$4$")
    draft = make_tutor_draft(intent="generate_task", new_task=broken)
    fixed = make_tutor_draft(intent="generate_task", new_task=repaired)
    queue_two_call(fake_llm, draft=draft, reviewer=make_reviewer_final(decision="correct",
                                                                      final=fixed))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn())

    assert response.get("status") == "ready"
    assert "3x=12" in response["answer"]
    assert fake_llm.call_count == 2


def test_reviewer_returning_the_same_incomplete_task_is_never_published(store, fake_llm,
                                                                        monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    broken = make_task_payload(text="Izračunaj vrijednost izraza:")
    draft = make_tutor_draft(intent="generate_task", new_task=broken)
    queue_two_call(fake_llm, draft=draft,
                   reviewer=make_reviewer_final(decision="approve", final=draft))
    response = tutor_pipeline.run_turn(store, fake_llm, _turn())

    assert response.get("status") is None            # ugovor odbijanja
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2                  # bez trećeg poziva
    assert store.peek("inc-1") is None               # nijedna mutacija sesije


def _turn(message="Daj mi zadatak."):
    return {
        "session_id": "inc-1", "grade": 9, "selected_topic": "9-04-003",
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "inc-turn-1",
    }


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija: ovi testovi ispituju MODEL-strategiju (Tutor +
# Recenzent) i na lekcijama koje produkcija sada rutira deterministički
# (blocking ugovor + potpun generator). Izričito isključenje je ISTI mehanizam
# koji služi i kao produkcijski rollback (MATBOT_DETERMINISTIC_PRACTICE=
# disabled) — model-put time ostaje trajno testiran, bajt za bajt kakav je bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
