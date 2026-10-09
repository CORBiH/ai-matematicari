"""Produkcijsko usmjeravanje MAT-BOT aplikacijskih logova.

Gunicorn podrazumijevano postavlja obrađivače samo na ``gunicorn.error`` i
``gunicorn.access``. Imenovani ``matbot.*`` loggeri zato nisu stizali do Docker
stderr-a iako je pristupni log radio. Ovaj modul dira samo ``matbot`` hijerarhiju;
loggeri biblioteka i korijenski logger ostaju netaknuti.
"""

import logging


LOGGER_NAME = "matbot"


def _unique_handlers(handlers):
    """Sačuvaj redoslijed bez dvostrukog pozivanja istog handler objekta."""
    result = []
    for handler in handlers:
        if not any(handler is existing for existing in result):
            result.append(handler)
    return result


def configure_application_logging():
    """Pošalji ``matbot.*`` INFO+ zapise u postojeći procesni izlaz.

    Gunicorn handler je prvi izbor jer već nosi produkcijski format i stderr.
    Pytest i drugi domaćini koji su postavili root handler nastavljaju ga
    koristiti propagacijom. Samostalni proces bez ijednog handlera dobija jedan
    stderr fallback. Ponovljen poziv ne dodaje drugi handler.
    """
    matbot_logger = logging.getLogger(LOGGER_NAME)
    matbot_logger.setLevel(logging.INFO)

    gunicorn_handlers = _unique_handlers(
        logging.getLogger("gunicorn.error").handlers)
    if gunicorn_handlers:
        matbot_logger.handlers = gunicorn_handlers
        matbot_logger.propagate = False
        return

    if matbot_logger.handlers:
        matbot_logger.handlers = _unique_handlers(matbot_logger.handlers)
        matbot_logger.propagate = False
        return

    if logging.getLogger().handlers:
        matbot_logger.propagate = True
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(process)d] [%(levelname)s] %(message)s"))
    matbot_logger.addHandler(handler)
    matbot_logger.propagate = False
