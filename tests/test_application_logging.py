"""Aplikacijski INFO logovi moraju stići do Gunicorn/Docker toka."""

import io
import logging

from matbot.application_logging import configure_application_logging


def _state(logger):
    return list(logger.handlers), logger.level, logger.propagate


def _restore(logger, state):
    handlers, level, propagate = state
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate


def test_matbot_info_and_error_use_gunicorn_stderr_handler_once():
    matbot_logger = logging.getLogger("matbot")
    gunicorn_logger = logging.getLogger("gunicorn.error")
    matbot_before = _state(matbot_logger)
    gunicorn_before = _state(gunicorn_logger)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    try:
        matbot_logger.handlers = []
        matbot_logger.setLevel(logging.WARNING)
        matbot_logger.propagate = True
        gunicorn_logger.handlers = [handler]

        configure_application_logging()
        configure_application_logging()
        logging.getLogger("matbot.admin_reports").info(
            "admin_report_generate_failed code=safe calls=1")
        logging.getLogger("matbot.admin_reports").error(
            "admin_report_generate_failed code=unexpected exception=SafeError")
        handler.flush()

        output = stream.getvalue()
        assert output.count("code=safe") == 1
        assert output.count("code=unexpected") == 1
        assert matbot_logger.level == logging.INFO
        assert matbot_logger.handlers == [handler]
        assert matbot_logger.propagate is False
    finally:
        _restore(matbot_logger, matbot_before)
        _restore(gunicorn_logger, gunicorn_before)


def test_configuring_matbot_logging_does_not_change_unrelated_logger():
    root_logger = logging.getLogger()
    matbot_logger = logging.getLogger("matbot")
    gunicorn_logger = logging.getLogger("gunicorn.error")
    unrelated = logging.getLogger("unrelated.library")
    root_before = _state(root_logger)
    matbot_before = _state(matbot_logger)
    gunicorn_before = _state(gunicorn_logger)
    unrelated_before = _state(unrelated)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    unrelated_handler = logging.NullHandler()
    try:
        matbot_logger.handlers = []
        gunicorn_logger.handlers = [handler]
        unrelated.handlers = [unrelated_handler]
        unrelated.setLevel(logging.ERROR)
        unrelated.propagate = True

        configure_application_logging()

        assert unrelated.handlers == [unrelated_handler]
        assert unrelated.level == logging.ERROR
        assert unrelated.propagate is True
        assert _state(root_logger) == root_before
        unrelated.error("not-a-matbot-record")
        handler.flush()
        assert "not-a-matbot-record" not in stream.getvalue()
    finally:
        _restore(root_logger, root_before)
        _restore(matbot_logger, matbot_before)
        _restore(gunicorn_logger, gunicorn_before)
        _restore(unrelated, unrelated_before)
