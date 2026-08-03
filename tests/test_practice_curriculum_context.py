"""Kanonski ID-jevi i grade-6 pokrivenost porodica za Practice."""

import json
from pathlib import Path

from matbot import task_families as tf
from matbot.contracts import registry
from matbot.topics import lesson_info, oblast_id_for_topic


ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))

# Šest pilot lekcija koje su prešle na univerzalni motor ugovora. Identitet
# lekcije više ne nosi „primarna porodica“ iz ručne mape po ID-ju, nego UGOVOR
# (data/lesson_contracts.json) — zato su ovdje očekivane VJEŠTINE, ne porodice.
EXPECTED = {
    "6-04-005": ("Proširivanje razlomaka", "expand_fraction"),
    "6-04-006": ("Skraćivanje i nesvodivi razlomak", "reduce_fraction"),
    "6-04-009": ("Sabiranje i oduzimanje razlomaka jednakih imenilaca",
                 "add_subtract_like_denominators"),
    "6-04-010": ("Sabiranje i oduzimanje razlomaka različitih imenilaca",
                 "add_subtract_unlike_denominators"),
    "6-04-011": ("Množenje razlomka prirodnim brojem i razlomkom", "multiply_fractions"),
    "6-04-012": ("Dijeljenje razlomka prirodnim brojem i razlomkom", "divide_fractions"),
}


def test_all_grade6_topic_ids_are_unique_and_have_canonical_oblast_ids():
    lessons = DATA["grades"]["6"]["lessons"]
    ids = [lesson["id"] for lesson in lessons]
    assert len(ids) == len(set(ids))
    assert all(oblast_id_for_topic(topic_id) for topic_id in ids)

    oblast_ids_by_name = {}
    for lesson in lessons:
        oblast_ids_by_name.setdefault(lesson["oblast"], set()).add(
            oblast_id_for_topic(lesson["id"])
        )
    assert all(len(ids_for_oblast) == 1 for ids_for_oblast in oblast_ids_by_name.values())


def test_relevant_fraction_ids_resolve_to_the_expected_distinct_records():
    records = []
    for topic_id, (title, _) in EXPECTED.items():
        info = lesson_info(6, topic_id)
        assert info["id"] == topic_id
        assert info["title"] == title
        assert info["oblast_id"] == "6-04"
        records.append((info["id"], info["title"]))
    assert len(records) == len(set(records))


def test_each_pilot_lesson_is_distinguished_by_contract_data_not_by_python():
    """Identitet lekcije nosi UGOVOR, i to razlikom u VRIJEDNOSTIMA.

    Ranije je ovo bila mapa `topic_id → lista porodica` u Pythonu (15 ručno
    nabrojanih lekcija). Sada svaka pilot lekcija ima svoju vještinu, a parovi
    koji dijele isti predložak razlikuju se u TAČNO jednoj vrijednosti."""
    contracts = registry.load_all()
    skills = {}
    for topic_id, (title, expected_skill) in EXPECTED.items():
        info = lesson_info(6, topic_id)
        contract = contracts[topic_id]
        assert info["title"] == title
        assert contract.skill == expected_skill
        assert contract.status == "enabled"
        skills[topic_id] = contract.skill
    assert len(set(skills.values())) == len(EXPECTED), "vještine moraju biti različite"

    # Jednaki vs različiti imenioci: isti predložak, ista šifra, JEDNA vrijednost.
    equal, unlike = contracts["6-04-009"], contracts["6-04-010"]
    assert equal.allowed_operations == unlike.allowed_operations
    assert equal.effective_archetypes == unlike.effective_archetypes
    assert equal.constraint("denominator_relation") == "equal"
    assert unlike.constraint("denominator_relation") == "different"

    # Množenje vs dijeljenje: isto, razlika je samo dozvoljena operacija.
    multiply, divide = contracts["6-04-011"], contracts["6-04-012"]
    assert multiply.effective_archetypes == divide.effective_archetypes
    assert multiply.allowed_operations == ("multiply",)
    assert divide.allowed_operations == ("divide",)

    # Proširivanje vs skraćivanje: razlika je smjer skaliranja.
    assert contracts["6-04-005"].constraint("scaling_direction") == "expand"
    assert contracts["6-04-006"].constraint("scaling_direction") == "reduce"


def test_pilot_lessons_never_use_the_legacy_family_path():
    """Uključen ugovor ide ISKLJUČIVO kroz motor — nikad na legacy porodice."""
    for topic_id in EXPECTED:
        assert registry.state_for_topic(topic_id) == registry.STATE_ENGINE


def test_uncontracted_grade6_fraction_lessons_keep_their_own_routing():
    """Nemigrirane lekcije razlomaka ZADRŽAVAJU svoj zatečeni redoslijed.

    Ranija verzija ovog testa je blagoslovila regresiju: tvrdila je da te
    lekcije prelaze na zajednički domenski routing (proširivanje prvo) zato što
    je mapa po ID-ju lekcije bila uklonjena. Mapa je vraćena u izolovanu legacy
    granicu, pa ponašanje mora biti isto kao prije Faze A.

    Puna provjera nad svih 528 nemigriranih lekcija je u
    tests/test_legacy_routing_parity.py; ovdje ostaje uzorak koji je regresiju
    i otkrio."""
    expected_first = {
        "6-04-001": "recognize_correct_statement",
        "6-04-002": "fraction_word_problem",
        "6-04-003": "recognize_correct_statement",
        "6-04-004": "compare_fractions",
        "6-04-007": "expand_to_given_denominator",
        "6-04-008": "compare_fractions",
        "6-04-013": "fraction_operation",
        "6-04-014": "fraction_expression",
        "6-04-015": "fraction_word_problem",
    }
    seen = []
    for lesson in DATA["grades"]["6"]["lessons"]:
        if not lesson["id"].startswith("6-04") or lesson["id"] in EXPECTED:
            continue
        info = lesson_info(6, lesson["id"])
        families = tf.applicable_families(6, info["oblast"], info["title"],
                                          lesson_id=info["id"])
        assert registry.state_for_topic(lesson["id"]) == registry.STATE_LEGACY
        assert families[0] == expected_first[lesson["id"]], lesson["id"]
        seen.append(lesson["id"])
    assert seen == sorted(expected_first)


def test_practice_api_rejects_missing_topic_without_model_call(client, fake_llm):
    response = client.post("/api/ai-tutor/chat", json={
        "session_id": "missing-practice-topic",
        "client_turn_id": "missing-practice-turn",
        "grade": 6,
        "mode": "practice",
        "selected_topic": "",
        "selected_oblast": "",
        "student_message": "Daj zadatak.",
        "conversation_history": [],
    })
    assert response.status_code == 400
    assert response.get_json()["error"] == "MISSING_TOPIC"
    assert fake_llm.call_count == 0

