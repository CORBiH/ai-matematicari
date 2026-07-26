"""Konfiguracija iz environment varijabli. Nikad ne loguje vrijednosti tajni."""
import os


def _float_env(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _int_env(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# Model i AI parametri (interaktivni Practice put)
OPENAI_MODEL_TEXT = os.environ.get("OPENAI_MODEL_TEXT", "gpt-5-mini")
REASONING_EFFORT = os.environ.get("MATBOT_REASONING_EFFORT", "low")
AI_TIMEOUT_S = _float_env("AI_TUTOR_TIMEOUT", 30.0)
MAX_OUTPUT_TOKENS = _int_env("MATBOT_MAX_OUTPUT_TOKENS", 1200)

# Ograničenja ulaza (server odbija prevelike poruke prije AI poziva)
MAX_MESSAGE_CHARS = 4000
MAX_TASK_CHARS = 600
MAX_REPLY_CHARS = 2500
MAX_EXPECTED_ANSWER_CHARS = 400

# Session store
MAX_RECENT_TASKS = 3
MAX_RECENT_TURNS = 3
MAX_HINT_LEVEL = 3
MAX_SESSIONS_IN_MEMORY = 2000
