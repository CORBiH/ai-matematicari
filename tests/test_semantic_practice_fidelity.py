"""Semantička vjernost lekciji (Vježbajmo V1, F5K) — trajne regresije.

ŽIVI IZVOR: 150-turn real-state audit (scratchpad/real_state_audit/
20260808T114202Z) — 14 P1 nalaza na 7 lekcija: matematički ispravni zadaci
koji NE ispituju izabranu lekciju. Svaki uhvaćeni nevažeći paket ovdje je
DOSLOVNA fikstura koja mora pasti na novom semantičkom ugovoru, svaka
kategorija ima pozitivnu i near-miss fiksturu, a susjedne lekcije bez
ugovora ostaju netaknute.
"""
import json
import re
from pathlib import Path

import pytest

from matbot import semantic_practice
from matbot.semantic_practice import (FEATURE_CHECKS, contract_for,
                                      fidelity_failures,
                                      fake_visual_reference)
from matbot.tutor import package_preflight
from matbot.tutor.lesson_context import build

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1) SVIH 14 UHVAĆENIH P1 PAKETA — DOSLOVNO, MORAJU PASTI
# ---------------------------------------------------------------------------
# (scenario, lekcija, doslovni vidljivi tekst zadatka iz audita)

_P1_PACKAGES = [
    ("T107", "9-07-009",
     "Kvadratna piramida ima stranicu osnove $a=6\\,\\text{cm}$ i apotemu "
     "(visinu bočne strane) $h_a=5\\,\\text{cm}$. Izračunaj ukupnu površinu "
     "$P$ piramide."),
    ("T108", "9-07-009",
     "Kvadratna piramida ima stranicu osnove $a=12\\,\\text{cm}$ i apotemu "
     "(visinu bočne strane) $h_a=5\\,\\text{cm}$. Izračunaj ukupnu površinu "
     "$P$ piramide i daj rezultat u decimetarima kvadratnim."),
    ("T117", "8-02-007",
     "Za linearnu funkciju $y=2x-1$, izračunaj $y$ kada je $x=3$. Izaberi "
     "tačan odgovor."),
    ("T118", "8-02-007",
     "Za linearnu funkciju $y=2x-1$, nađi $x$ kada je $y=11$. Izaberi tačan "
     "odgovor."),
    ("T119", "9-03-004",
     "Za linearnu funkciju dana je jednačina $y=2x+1$. Kolika je vrijednost "
     "$y$ kada je $x=3$?"),
    ("T120", "9-03-004",
     "Za linearnu funkciju dana je jednačina $y=3x-5$. Koja je vrijednost "
     "$x$ kada je $y=7$?"),
    ("T123", "9-01-015",
     "Izračunaj vrijednost izraza $\\frac{2x+1}{x}$ za $x=2$."),
    ("T124", "9-01-015",
     "Pojednostavi izraz $\\frac{2x+1}{x}-1$ i zatim izračunaj njegovu "
     "vrijednost za $x=3$."),
    ("T139", "7-04-016",
     "U trouglu $ABC$ dati su $AB=5\\,\\text{cm}$, $BC=7\\,\\text{cm}$ i "
     "ugao $\\beta=60^\\circ$ (ugao u tjemenu $B$ između stranica $AB$ i "
     "$BC$). Koji od navedenih trouglova je sigurno podudaran sa trouglom "
     "$ABC$ prema SSU kriteriju?"),
    ("T140", "7-04-016",
     "U trouglu $ABC$ dati su $AB=5\\,\\text{cm}$, $BC=7\\,\\text{cm}$ i "
     "ugao $\\beta=60^\\circ$ (ugao u tjemenu $B$ između stranica $AB$ i "
     "$BC$). Koji od navedenih trouglova je sigurno podudaran sa trouglom "
     "$ABC$ prema SSU kriteriju?"),
    ("T141", "8-05-010",
     "Na mreži četverostrane prizme osnova je pravougaonik dimenzija "
     "$4\\,\\text{cm}$ i $3\\,\\text{cm}$, a visina prizme (bočna ivica) je "
     "$5\\,\\text{cm}$. Koliki je volumen prizme?"),
    ("T142", "8-05-010",
     "Na mreži četverostrane prizme osnova je pravougaonik dimenzija "
     "$4\\,\\text{cm}$ i $3\\,\\text{cm}$, a visina prizme (bočna ivica) je "
     "$5\\,\\text{cm}$. Koliki je volumen prizme?"),
    ("T147", "9-01-017",
     "Proširi razlomak $\\frac{3}{8}$ tako da ima nazivnik $24$. Koji je "
     "prošireni razlomak?"),
    ("T148", "9-01-017",
     "Proširi razlomak $\\frac{3}{8}$ tako da ima nazivnik $24$. Koji je "
     "prošireni razlomak?"),
]


@pytest.mark.parametrize("scenario,lesson_id,task_text", _P1_PACKAGES,
                         ids=[row[0] for row in _P1_PACKAGES])
def test_every_captured_p1_package_fails_the_semantic_contract(
        scenario, lesson_id, task_text):
    contract = contract_for(lesson_id)
    assert contract is not None, lesson_id
    assert contract.enforcement == "blocking"
    failures = fidelity_failures(contract, task_text)
    assert failures, (scenario, lesson_id)


@pytest.mark.parametrize("scenario,lesson_id,task_text", _P1_PACKAGES[:1],
                         ids=["preflight"])
def test_preflight_carries_the_semantic_fidelity_issue(scenario, lesson_id,
                                                       task_text):
    from tests.conftest import make_task_payload

    contract = contract_for(lesson_id)
    payload = make_task_payload(text=task_text)
    codes = [issue.code for issue in package_preflight.collect_package_issues(
        payload, practice_contract=contract)]
    assert package_preflight.SEMANTIC_FIDELITY_CODE in codes


# ---------------------------------------------------------------------------
# 2) POZITIVNE FIKSTURE — vjeran zadatak svake kategorije PROLAZI
# ---------------------------------------------------------------------------

_POSITIVES = [
    ("graph-membership", "8-02-007",
     "Data je linearna funkcija $y=2x-1$. Koja od ponuđenih tačaka pripada "
     "grafiku ove funkcije?", "$(2, 3)$ $(1, 4)$ $(0, 2)$ $(3, 2)$"),
    ("graph-intercept", "9-03-004",
     "U kojoj tački grafik funkcije $y=2x-4$ siječe $x$-osu?",
     "$(2, 0)$ $(0, -4)$ $(4, 0)$ $(-2, 0)$"),
    ("graph-table", "6-10-007",
     "Funkcija je zadana tabelom: za $x$: 1, 2, 3 vrijednosti $y$ su 2, 4, "
     "6. Koja tačka pripada grafiku ove funkcije?",
     "$(2, 4)$ $(4, 2)$ $(2, 5)$ $(3, 5)$"),
    ("net-surface-from-faces", "8-05-010",
     "Mreža četverostrane prizme sastoji se od šest pravougaonika: dvije "
     "osnove dimenzija $4\\,\\text{cm}$ i $3\\,\\text{cm}$ i četiri bočne "
     "strane visine $5\\,\\text{cm}$. Kolika je ukupna površina mreže?",
     ""),
    ("net-face-count", "9-07-009",
     "Od kojih se figura sastoji mreža pravilne četverostrane piramide?",
     "kvadrat i četiri trougla"),
    ("word-problem-rational", "9-01-015",
     "Biciklista prelazi put od $60$ km stalnom brzinom od $v$ km/h. Izraz "
     "$\\frac{60}{v}$ daje trajanje vožnje u satima. Koliko sati traje "
     "vožnja ako je $v = 20$?", ""),
    ("identity-proof", "9-01-017",
     "Dokaži da za svako $x \\neq 0$ vrijedi identitet "
     "$\\frac{x^{2}+x}{x} = x+1$.", ""),
    ("congruence-ssu", "7-04-016",
     "Trouglovi $ABC$ i $DEF$ imaju $AB = DE = 7$, $BC = EF = 5$ i jednak "
     "ugao naspram veće od datih stranica. Da li su trouglovi sigurno "
     "podudarni po SSU kriteriju?", ""),
    ("congruence-proof", "7-04-017",
     "U jednakokrakom trouglu $ABC$ s krakom $AC = BC$ dokaži, koristeći "
     "podudarnost trouglova, da su uglovi na osnovici jednaki.", ""),
]


@pytest.mark.parametrize("label,lesson_id,task_text,options", _POSITIVES,
                         ids=[row[0] for row in _POSITIVES])
def test_faithful_tasks_pass_their_contract(label, lesson_id, task_text,
                                            options):
    contract = contract_for(lesson_id)
    assert contract is not None
    assert fidelity_failures(contract, task_text, options) == (), label


# ---------------------------------------------------------------------------
# 3) NEAR-MISS FIKSTURE
# ---------------------------------------------------------------------------

def test_graph_near_miss_with_point_word_but_no_graph_action_fails():
    contract = contract_for("8-02-007")
    text = "Izračunaj vrijednost funkcije $y=2x-1$ u tački $x=3$."
    assert fidelity_failures(contract, text)


def test_net_near_miss_decorative_net_with_volume_fails():
    contract = contract_for("8-05-010")
    # T141: „na mreži“ + dimenzije, ali pitanje je zapremina — zabranjena.
    text = ("Na mreži prizme osnova je pravougaonik dimenzija $4$ i $3$, "
            "visina $5$. Koliki je volumen?")
    failures = fidelity_failures(contract, text)
    assert any(f.startswith("semantic_forbidden:volume_request")
               for f in failures)


def test_identity_near_miss_numeric_equality_without_proof_fails():
    contract = contract_for("9-01-017")
    text = "Provjeri jednakost $\\frac{3}{8} = \\frac{9}{24}$."
    assert fidelity_failures(contract, text)


def test_word_problem_near_miss_instruction_only_prose_fails():
    contract = contract_for("9-01-015")
    text = ("Odredi vrijednost izraza $\\frac{x+3}{x}$ za $x = 4$ i izaberi "
            "tačan odgovor među ponuđenih.")
    assert fidelity_failures(contract, text)


def test_ssu_near_miss_included_angle_language_fails():
    contract = contract_for("7-04-016")
    text = ("Trouglovi imaju dvije jednake stranice i jednak ugao između "
            "tih stranica. Jesu li podudarni po SSU kriteriju?")
    failures = fidelity_failures(contract, text)
    assert any("included_angle_with_sides" in f for f in failures)


def test_ssu_live_notation_false_accept_is_now_rejected():
    """ŽIVI F5K NALAZ (K07): zadatak je zahvaćeni ugao iskazao NOTACIJOM
    ($\\angle BAC$ uz stranice $AB$ i $AC$ — zajedničko tjeme A), a označeni
    odgovor doslovno kaže „ugao između njih, pa su podudarni po SSU“.
    Fraza-regex nad samim tekstom to nije vidio. Sada se zahvaćenost
    prepoznaje i iz notacije (tjeme ugla = zajednička tačka datih stranica)
    i iz teksta OPCIJA."""
    contract = contract_for("7-04-016")
    live_task = ("U trouglovima $ABC$ i $DEF$ vrijedi: $AB=DE$, $AC=DF$, i "
                 "ugao $\\beta=\\angle BAC$ je jednak uglu "
                 "$\\delta=\\angle EDF$. Da li su trouglovi $ABC$ i $DEF$ "
                 "podudarni po SSU kriteriju?")
    live_option = ("Da — imaju dva para jednakih stranica i jednak ugao "
                   "između njih, pa su podudarni po SSU.")
    failures = fidelity_failures(contract, live_task, live_option)
    assert any("included_angle_with_sides" in f for f in failures)
    # I sama notacija (bez opcije) mora biti dovoljna.
    assert any("included_angle_with_sides" in f
               for f in fidelity_failures(contract, live_task))


def test_ssu_opposite_angle_notation_stays_legal():
    """Ugao NASPRAM stranice ($\\angle ACB$ uz date $AB$ i $AC$ — tjeme C
    nije zajednička tačka) je legitimna SSU konfiguracija i NE smije pasti."""
    contract = contract_for("7-04-016")
    text = ("U trouglovima $ABC$ i $DEF$ vrijedi: $AB=DE$, $AC=DF$, i ugao "
            "$\\angle ABC$ je jednak uglu $\\angle DEF$. Da li se po SSU "
            "kriteriju može zaključiti da su trouglovi podudarni?")
    assert fidelity_failures(contract, text) == ()


def test_sus_lesson_allows_included_angle_language():
    """SUS kriterij UPRAVO koristi zahvaćeni ugao — susjedna lekcija ga
    smije opisivati; zabrana važi samo na SSU ugovoru."""
    contract = contract_for("7-04-013")
    text = ("Trouglovi imaju dvije jednake stranice i jednak ugao između "
            "tih stranica (zahvaćeni ugao). Jesu li podudarni po SUS "
            "kriteriju?")
    assert fidelity_failures(contract, text) == ()


# ---------------------------------------------------------------------------
# 3c) ŽIVI F5L NALAZI (produkcijski smoke 20260808T144302Z + lokalna forenzika)
# ---------------------------------------------------------------------------

def test_congruence_masculine_singular_podudaran_is_detected():
    """ŽIVI F5L NALAZ (S01): muški rod jednine „podudaran“ nosi nepostojano
    a, pa ga stariji stem „podudarn“ NIJE hvatao. Posljedica je bila fatalna
    za vjeran zadatak: nacrt lažno označen kao semantic_missing, recenzentova
    ispravka zadrži istu riječ, finalna kapija opet padne — zagarantovan
    fail-close (u produkciji viđen kao 65s timeout) na najprirodnijoj
    formulaciji svježeg SSU zadatka."""
    contract = contract_for("7-04-016")
    text = ("U trouglu $ABC$ date su stranice $AB=5\\,\\text{cm}$ i "
            "$AC=7\\,\\text{cm}$ i ugao $\\angle B=40^\\circ$. Koji od "
            "ponuđenih trouglova je sigurno podudaran trouglu $ABC$ po SSU "
            "kriteriju?")
    assert fidelity_failures(contract, text) == ()


def test_ssu_single_letter_included_angle_notation_is_rejected():
    """ŽIVI F5L NALAZ (S01 forenzika): stvarni nacrt modela dao je $AB=5$,
    $AC=7$ i ugao $\\angle A=60^\\circ$ pa raspored proglasio „po kriteriju
    SSU“. Tjeme A je zajednička tačka datih stranica, dakle $\\angle A$ je
    zahvaćeni ugao (SUS raspored) iskazan JEDNIM slovom — K07 detektor je
    tražio tačno tri slova i ova forma mu je promicala. Uz popravku
    „podudaran“ stema ovaj nacrt bi inače prošao čist."""
    contract = contract_for("7-04-016")
    live_draft = ("U trouglu $ABC$ važi $AB=5\\,\\text{cm}$, "
                  "$AC=7\\,\\text{cm}$ i ugao $\\angle A=60^\\circ$. Koji od "
                  "donjih trouglova je podudaran sa trouglom $ABC$ po "
                  "kriteriju SSU?")
    failures = fidelity_failures(contract, live_draft)
    assert any("included_angle_with_sides" in f for f in failures)
    # Lažni „missing“ na „podudaran“ ne smije se vratiti:
    assert not any("congruence_semantics_present" in f for f in failures)


def test_ssu_single_letter_angle_at_non_shared_vertex_stays_legal():
    """Kontra-fikstura uz F5L proširenje: $\\angle B$ uz stranice $AB$ i
    $AC$ (zajedničko tjeme A) NIJE zahvaćeni ugao i ne smije pasti — to je
    legitimna SSU konfiguracija jednim slovom."""
    contract = contract_for("7-04-016")
    text = ("U trouglu $ABC$ važi $AB=5\\,\\text{cm}$, $AC=7\\,\\text{cm}$ "
            "i ugao $\\angle B=40^\\circ$. Da li je svaki trougao s istim "
            "podacima podudaran trouglu $ABC$ po SSU kriteriju?")
    assert fidelity_failures(contract, text) == ()


def test_graph_g801_point_membership_choice_stays_valid():
    """ŽIVI F5L SMOKE (G801, PASS): izbor ponuđene tačke koja pripada
    grafiku — kanonski oblik pripadnosti, ostaje prihvaćen."""
    contract = contract_for("8-02-007")
    text = ("Koja od navedenih tačaka pripada grafiku linearne funkcije "
            "$y=2x+1$?")
    options = "Tačka $(0,0)$ Tačka $(1,4)$ Tačka $(2,5)$ Tačka $(2,4)$"
    assert fidelity_failures(contract, text, options) == ()


def test_graph_g802_membership_with_unknown_coordinate_stays_valid():
    """ŽIVI F5L ADJUDIKACIJA (G802): „Za koju vrijednost $x$ tačka $(x,7)$
    pripada grafiku...“ JESTE grafička semantika po SPC-1 i kurikulumu:
    KS_2018-0342 („iz nacrtanog grafika čitati vrijednosti“) pokriva čitanje
    u OBA smjera (za dato $y$ naći $x$), a pripadnost tačke grafiku je i
    samostalna kurikularna lekcija (9-03-011 „Da li tačka pripada grafiku
    funkcije“). Algebarski postupak ($7=2x+1$) je METODA rješavanja, ne
    semantika zadatka — i G801 se rješava uvrštavanjem u sva četiri kandidata.
    Ručna smoke oznaka POTENTIAL_SEMANTIC_FALSE_ACCEPT je ODBIJENA; ova
    fikstura je trajni zapis da budući auditi G802 oblik ne broje kao P1."""
    contract = contract_for("8-02-007")
    text = ("Za koju vrijednost $x$ tačka $(x,7)$ pripada grafiku linearne "
            "funkcije $y=2x+1$?")
    options = "$x=3$ $x=4$ $x=5$ $x=2$"
    assert fidelity_failures(contract, text, options) == ()


def test_graph_naked_equation_solving_still_fails():
    """Kontra-fikstura uz G802 adjudikaciju: golo rješavanje jednačine —
    čak i „ukrašeno“ riječju grafik, bez ijedne tačke ili grafičke radnje —
    i dalje pada. Granica ugovora se adjudikacijom NIJE pomjerila."""
    contract = contract_for("8-02-007")
    assert fidelity_failures(contract, "Riješi jednačinu $2x+1=7$.")
    assert fidelity_failures(
        contract, "Riješi jednačinu $2x+1=7$ koristeći grafik.")


# ---------------------------------------------------------------------------
# 4) SUSJEDNE LEKCIJE OSTAJU NETAKNUTE (kontrakti su TAČNO nabrojani podaci)
# ---------------------------------------------------------------------------

def test_contract_registry_covers_exactly_the_reviewed_lessons():
    contracts = semantic_practice.all_contracts()
    assert len(contracts) == 27
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))
    for lesson_id, contract in contracts.items():
        # Nijedna deterministička lekcija ne nosi ugovor vježbe — model-put.
        assert lesson_id not in compiled["lessons"], lesson_id
        assert contract.evidence, lesson_id
        assert contract.contract_version == "SPC-1"
        assert contract.visual_policy in semantic_practice.VISUAL_POLICIES


def test_neighbouring_lessons_have_no_contract_and_keep_their_shapes():
    # Susjedne vještine za koje su „zabranjene zamjene“ LEGITIMNA lekcija:
    # uvrštavanje (9-01-003), zapremina (8-05-014...), proširivanje brojevnih
    # razlomaka (6-04-005) — nijedna NE SMIJE dobiti ugovor vježbe.
    for lesson_id in ("9-01-003", "6-04-005", "8-05-015", "9-03-005",
                      "8-02-008", "9-07-023"):
        assert contract_for(lesson_id) is None, lesson_id
    # Bez ugovora nema prekršaja — isti goli oblici su tamo dozvoljeni.
    assert fidelity_failures(None, "Izračunaj vrijednost izraza "
                                   "$\\frac{2x+1}{x}$ za $x=2$.") == ()


def test_deterministic_route_is_unaffected():
    context = build(9, "9-01-003")
    assert context.practice_contract is None
    context = build(8, "8-05-010")
    assert context.practice_contract is not None


# ---------------------------------------------------------------------------
# 5) LAŽNA SLIKA — globalna zabrana
# ---------------------------------------------------------------------------

def test_fake_visual_reference_is_flagged_globally():
    assert fake_visual_reference("Na slici je prikazan trougao $ABC$.")
    assert fake_visual_reference("Pogledaj graf funkcije i odredi $k$.")
    assert not fake_visual_reference(
        "Mreža se sastoji od dva kruga i pravougaonika.")
    from tests.conftest import make_task_payload

    payload = make_task_payload(text="Na slici je prikazan kvadrat $ABCD$. "
                                     "Kolika mu je površina?")
    codes = [issue.code for issue in
             package_preflight.collect_package_issues(payload)]
    assert package_preflight.FAKE_VISUAL_CODE in codes


# ---------------------------------------------------------------------------
# 6) PROMPTOVI — Tutor i Recenzent dobijaju ISTI ugovor; recenzentska
#    definicija inside_lesson
# ---------------------------------------------------------------------------

def test_both_prompts_carry_the_identical_contract_block():
    from matbot.tutor import prompts as tutor_prompts

    for grade, lesson_id in ((8, "8-02-007"), (9, "9-07-009"),
                             (9, "9-01-017"), (7, "7-04-016")):
        context = build(grade, lesson_id)
        block = context.practice_contract.prompt_block()
        assert "SEMANTIČKI UGOVOR IZABRANE LEKCIJE" in block
        for text in (tutor_prompts.build_tutor_instructions(context),
                     tutor_prompts.build_reviewer_instructions(context)):
            assert block in text, lesson_id


def test_reviewer_inside_lesson_meaning_is_explicit():
    from matbot.tutor import prompts as tutor_prompts

    text = tutor_prompts.build_reviewer_instructions(build(8, "8-02-007"))
    lowered = text.lower()
    assert "inside_lesson" in lowered
    assert "same broad topic" in lowered or "zvuči srodno" in lowered
    assert "`approve` forbidden" in lowered


def test_uncontracted_lesson_prompt_is_unchanged():
    from matbot.tutor import prompts as tutor_prompts

    context = build(6, "6-04-001")
    assert context.practice_contract is None
    for text in (tutor_prompts.build_tutor_instructions(context),
                 tutor_prompts.build_reviewer_instructions(context)):
        assert "SEMANTIČKI UGOVOR IZABRANE LEKCIJE" not in text


# ---------------------------------------------------------------------------
# 7) ARHITEKTONSKE KAPIJE
# ---------------------------------------------------------------------------

_TOPIC_ID_RE = re.compile(r"\b\d-\d{2}-\d{3}\b")


def test_semantic_practice_module_contains_no_lesson_identity():
    source = (ROOT / "matbot" / "semantic_practice.py").read_text(
        encoding="utf-8")
    assert not _TOPIC_ID_RE.search(source)
    assert "lesson_title ==" not in source


def test_contract_resolution_reads_only_the_server_context():
    import inspect

    assert list(inspect.signature(
        semantic_practice.contract_for).parameters) == ["lesson_id"]
    # Payload modela nema polje kojim bi birao/isključio ugovor.
    from matbot.tutor.schema import TaskPayload

    assert "practice_contract" not in TaskPayload.model_fields
    assert "semantic_contract" not in TaskPayload.model_fields


def test_every_feature_in_data_exists_in_the_generic_library():
    payload = json.loads(
        (ROOT / "data" / "semantic_practice_contracts.json").read_text(
            encoding="utf-8"))
    for type_id, spec in payload["requirement_types"].items():
        for feature in (*spec.get("required_features", ()),
                        *spec.get("forbidden_features", ())):
            assert feature in FEATURE_CHECKS, (type_id, feature)
    for row in payload["contracts"]:
        for feature in (*row.get("extra_required_features", ()),
                        *row.get("extra_forbidden_features", ())):
            assert feature in FEATURE_CHECKS, (row["lesson_id"], feature)


# ---------------------------------------------------------------------------
# 8) TEŽINA I SEMANTIKA SU NEZAVISNE DIMENZIJE
# ---------------------------------------------------------------------------

def test_semantics_and_difficulty_are_both_required(monkeypatch):
    """Vjeran grafički zadatak s dokazom težine van cilja i dalje pada na
    TEŽINSKOM nalazu; nevjeran zadatak s urednom težinom pada na
    SEMANTIČKOM — nijedna dimenzija ne pokriva drugu."""
    from matbot import difficulty_profiles
    from matbot.tutor.schema import DifficultyEvidence
    from tests.conftest import make_task_payload

    contract = contract_for("8-02-007")
    faithful = make_task_payload(
        text="Koja od ponuđenih tačaka pripada grafiku funkcije $y=2x-1$?",
        options=("$(2, 3)$", "$(1, 4)$", "$(0, 2)$", "$(3, 2)$"))
    too_hard = faithful.model_copy(update={
        "difficulty_evidence": DifficultyEvidence(
            reasoning_steps=4, condition_count=3, operation_count=6,
            representation_change_count=2, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)})
    codes = [issue.code for issue in package_preflight.collect_package_issues(
        too_hard, practice_contract=contract)]
    assert package_preflight.DIFFICULTY_OUTSIDE_TARGET_CODE in codes
    assert package_preflight.SEMANTIC_FIDELITY_CODE not in codes

    unfaithful = make_task_payload(
        text="Za linearnu funkciju $y=2x-1$, izračunaj $y$ kada je $x=3$.")
    codes = [issue.code for issue in package_preflight.collect_package_issues(
        unfaithful, practice_contract=contract)]
    assert package_preflight.SEMANTIC_FIDELITY_CODE in codes
    assert package_preflight.DIFFICULTY_OUTSIDE_TARGET_CODE not in codes
