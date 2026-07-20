# -*- coding: utf-8 -*-
"""Phase 1 — durable, server-authoritative TaskDefinition (Engine V2).

Verifies the object is built from the same validation that gates activation,
that it is emitted only behind the flag and never changes legacy behavior, that
question == last_tutor_task by construction, that rejected tasks are recorded as
rejected, and that the record round-trips through the client unchanged.
"""
import types

import pytest

from matbot import task_model
from matbot import engine_v2
from matbot import ai_tutor_service as svc
from matbot import content_loader as cl


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
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


def _scripted_chat(reply: str):
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))]
        )
    return chat


def _new_task_payload(mode="practice", topic="6-01-001"):
    # A fresh generation turn (no interaction_phase → not a grading turn).
    return {"grade": 6, "mode": mode, "selected_topic": topic,
            "student_message": "daj mi zadatak"}


# --------------------------------------------------------------------------- #
# Pure builder / schema tests                                                 #
# --------------------------------------------------------------------------- #
def test_build_answer_schema_from_validation():
    validation = svc._validate_task_activation("Izračunaj: 1/4 + 1/4.", mode="practice")
    schema = task_model.build_answer_schema(validation)
    assert schema["checkable"] is True
    assert schema["items"] and schema["items"][0]["answer_type"]


def test_build_task_definition_populates_fields():
    validation = svc._validate_task_activation("Rastavi 60 na proste faktore.", mode="practice")
    td = task_model.build_task_definition(
        task_id="task_x", grade=6, oblast_id="ob", tema_id="6-01-001",
        mode="practice", question="Rastavi 60 na proste faktore.",
        validation=validation,
    )
    assert td is not None
    d = td.to_dict()
    assert d["question"] == "Rastavi 60 na proste faktore."
    assert d["validation_status"] == "validated"
    assert d["skill_id"] == "prime_factorization"
    assert d["solution_plan"] is None            # Phase 3 not implemented
    assert d["source"] in ("gpt_generated", "gpt_rubric")


def test_build_task_definition_none_for_empty_question():
    assert task_model.build_task_definition(
        task_id="t", grade=6, oblast_id="", tema_id="", mode="practice",
        question="   ", validation={"validation_status": "validated", "items": []},
    ) is None


def test_rejected_validation_recorded_as_rejected():
    # A task that asks for an undefined tangent segment length is rejected.
    bad = "Izmjeri dužinu tangente t."
    validation = svc._validate_task_activation(bad, mode="practice")
    td = task_model.build_task_definition(
        task_id="t", grade=6, oblast_id="", tema_id="", mode="practice",
        question=bad, validation=validation,
    )
    # It is still *recorded*, but with the rejected status — never as validated.
    assert td.validation_status == "rejected"


def test_skill_id_divisibility():
    validation = svc._validate_task_activation(
        "Provjeri da li je broj 240 djeljiv sa 6. Obrazloži svoj odgovor.", mode="practice")
    schema = task_model.build_answer_schema(validation)
    assert task_model.derive_skill_id(schema) == "divisibility"


# --------------------------------------------------------------------------- #
# Round-trip normalization                                                     #
# --------------------------------------------------------------------------- #
def test_normalize_task_definition_roundtrip():
    validation = svc._validate_task_activation("Izračunaj: 1/4 + 1/4.", mode="practice")
    td = task_model.build_task_definition(
        task_id="task_x", grade=6, oblast_id="ob", tema_id="6-01-001",
        mode="practice", question="Izračunaj: 1/4 + 1/4.", validation=validation)
    norm = task_model.normalize_task_definition(td.to_dict())
    assert norm["question"] == "Izračunaj: 1/4 + 1/4."
    assert norm["skill_id"] == td.skill_id
    assert norm["validation_status"] == "validated"


def test_normalize_rejects_non_dict_and_empty():
    assert task_model.normalize_task_definition(None) is None
    assert task_model.normalize_task_definition("x") is None
    assert task_model.normalize_task_definition({"question": ""}) is None


def test_next_state_roundtrips_task(monkeypatch):
    # A previous_next_state carrying a task must survive _normalize_next_state.
    td = {"task_id": "t1", "question": "Izračunaj: 2 + 2.", "mode": "practice",
          "validation_status": "validated", "skill_id": "numeric_integer",
          "answer_schema": {"checkable": True, "multi_item": False, "items": []},
          "source": "gpt_generated"}
    norm = svc._normalize_next_state({"task": td})
    assert norm["task"] is not None
    assert norm["task"]["question"] == "Izračunaj: 2 + 2."


def test_empty_next_state_has_task_none():
    assert svc._empty_next_state()["task"] is None


# --------------------------------------------------------------------------- #
# Flag gating + legacy parity (emission)                                       #
# --------------------------------------------------------------------------- #
def test_flag_off_no_task_field(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "off")
    out = svc.handle_chat(_new_task_payload(), _scripted_chat("Zadatak: Izračunaj 1/4 + 1/4."),
                          master, tmap, model="m", timeout=1)
    assert "task" not in out or out.get("task") is None
    assert out["next_state"].get("task") is None


def test_shadow_emits_task_matching_last_tutor_task(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    out = svc.handle_chat(_new_task_payload(), _scripted_chat("Zadatak: Izračunaj 1/4 + 1/4."),
                          master, tmap, model="m", timeout=1)
    task = out.get("task") or out["next_state"].get("task")
    if out.get("last_tutor_task"):
        assert task is not None
        # question is exactly the authoritative active task — no divergence.
        assert task["question"] == out["last_tutor_task"]
        assert task["task_id"] == out["next_state"].get("task_id")
        assert task["validation_status"] in ("validated", "unvalidated", "rejected")


def test_shadow_task_field_does_not_change_visible_answer(monkeypatch, master, tmap):
    payload = _new_task_payload()
    reply = "Zadatak: Izračunaj 3/4 + 1/4."
    monkeypatch.setenv("MATBOT_ENGINE_V2", "off")
    off = svc.handle_chat(dict(payload), _scripted_chat(reply), master, tmap, model="m", timeout=1)
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    shadow = svc.handle_chat(dict(payload), _scripted_chat(reply), master, tmap, model="m", timeout=1)
    assert shadow["answer"] == off["answer"]
    assert shadow.get("last_tutor_task") == off.get("last_tutor_task")
    assert shadow["next_state"].get("task_status") == off["next_state"].get("task_status")


def test_no_active_task_emits_task_none(monkeypatch, master, tmap):
    # Explanation mode never tracks a task → task must be None even in shadow.
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    out = svc.handle_chat(
        {"grade": 6, "mode": "explain", "selected_topic": "6-01-001",
         "student_message": "objasni mi razlomke"},
        _scripted_chat("Razlomak je dio cjeline."),
        master, tmap, model="m", timeout=1)
    assert (out.get("task") or out["next_state"].get("task")) is None
