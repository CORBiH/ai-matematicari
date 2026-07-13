# -*- coding: utf-8 -*-
"""Deterministički višepotezni klijent-simulator za service-level testove.

Replicira SAMO browser state-carry (templates/index.html): nosi
``previous_next_state``, ``last_tutor_task``, ``conversation_history``,
``recent_tasks`` i ``last_image_context`` između poziva ``handle_chat``.

NE duplira aplikativnu logiku — model se uvijek mockira (bez mreže/API-ja).
Koristi se za regresije koje jednopotezni testovi ne mogu uhvatiti (AUD-01/02).
"""
from __future__ import annotations

import types
from typing import Any, Callable

from matbot import ai_tutor_service as svc


def scripted_chat(replies: list[str]) -> Callable:
    """Mock model koji redom vraća zadane odgovore; snima primljene poruke."""
    state = {"i": 0}
    calls: list[list[dict]] = []

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls.append(messages)
        reply = replies[min(state["i"], len(replies) - 1)]
        state["i"] += 1
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))]
        )

    chat.calls = calls
    return chat


def last_user_prompt(chat: Callable) -> str:
    """Tekst zadnjeg user prompta poslanog modelu (podržava multimodalni list)."""
    content = chat.calls[-1][-1]["content"]
    if isinstance(content, list):
        return next(p["text"] for p in content if p.get("type") == "text")
    return content


class ConversationClient:
    """Vodi jednu konverzaciju kroz više poziva ``handle_chat``, noseći stanje
    kao pravi frontend. Svaki ``send`` vraća pun response dict."""

    def __init__(self, master, tmap, grade: int = 6, mode: str = "practice",
                 topic: str = "", oblast: str = ""):
        self.master = master
        self.tmap = tmap
        self.grade = grade
        self.mode = mode
        self.topic = topic
        self.oblast = oblast
        self.prev_state: dict | None = None
        self.saved_task: str = ""
        self.image_context: str = ""
        self.last_tutor_message: str = ""
        self.history: list[dict] = []
        self.recent_tasks: list[str] = []

    def send(self, message: str, reply: str, *, phase: str | None = None,
             mode: str | None = None, seed_task: str | None = None,
             image_ocr: str | None = None, extra: dict | None = None,
             expect_model: bool = True) -> dict:
        """Jedan potez. ``phase``: None|'answer'|'continue'|'confirm'.
        ``image_ocr``: ako je dato, simulira upload slike (OCR sloj mockiran).
        ``expect_model=False``: potez je deterministički (model se NE poziva)."""
        if seed_task is not None:
            self.saved_task = seed_task
        use_mode = mode or self.mode
        payload: dict[str, Any] = {
            "grade": self.grade,
            "mode": use_mode,
            "selected_topic": self.topic,
            "selected_oblast": self.oblast,
            "student_message": message,
            "entry_source": "manual_topic_choice" if self.topic else "free_chat",
            "conversation_history": list(self.history),
        }
        if self.prev_state:
            payload["previous_next_state"] = self.prev_state
        if self.recent_tasks:
            payload["recent_tasks"] = list(self.recent_tasks)
        # frontend: image_test aktivan ⇒ uz svaki potez ide sačuvani image kontekst
        image_test_active = bool(self.prev_state and self.prev_state.get("active_task_kind") == "image_test")
        if self.image_context and (image_test_active or phase in ("continue", "confirm")):
            payload["last_image_context"] = self.image_context
        if phase == "answer":
            payload["interaction_phase"] = "answering_practice_task"
            payload["mode"] = "practice"
            payload["last_tutor_task"] = (self.saved_task or "")[:600]
        elif phase == "continue":
            payload["interaction_phase"] = "continuing_explanation"
            payload["last_tutor_message"] = (self.last_tutor_message or "")[:600]
        if extra:
            payload.update(extra)

        chat = scripted_chat([reply])
        kwargs = {}
        if image_ocr is not None:
            kwargs = dict(
                image_bytes=b"x", image_data_url="data:image/png;base64,AAA=",
                ocr_image=lambda b, _o=image_ocr: (_o, 0.97), vision_model="v",
            )
        out = svc.handle_chat(payload, chat, self.master, self.tmap,
                              model="m", timeout=1, **kwargs)

        # --- state carry (kao frontend) ---
        self.prev_state = out.get("next_state") or self.prev_state
        if "last_tutor_task" in out:
            self.saved_task = (out.get("last_tutor_task") or "")[:600]
        if out.get("image_context"):
            self.image_context = out["image_context"]
        answer = out.get("answer") or ""
        self.last_tutor_message = answer[:600]
        self.history += [{"role": "user", "content": message},
                         {"role": "assistant", "content": answer}]
        if self.saved_task:
            self.recent_tasks = ([self.saved_task]
                                 + [t for t in self.recent_tasks if t != self.saved_task])[:8]
        if expect_model:
            out["_prompt"] = last_user_prompt(chat)
        else:
            assert not chat.calls, "očekivan deterministički odgovor, a model JE pozvan"
            out["_prompt"] = ""
        return out
