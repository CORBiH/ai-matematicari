"""matbot — Stage 2 application package (SKELETON / Phase 2A).

This package is the target home for the Flask backend that currently lives in the
top-level ``app.py``. It is intentionally a *skeleton* in Phase 2A:

  * nothing here is imported by the running app yet, and
  * ``app.py`` remains the sole entrypoint (``python app.py`` / gunicorn ``app:app``).

Creating this empty package changes **no behavior** and is not wired into anything,
so the existing 104-test suite is unaffected.

Migration roadmap and the (critical) test-coupling rule live in ``matbot/README.md``.

Phase status
------------
  2A (this commit)  package skeleton only; ``app.py`` untouched; no behavior change.
  2B (next)         ``config.py`` + ``create_app()`` factory. NOT done in 2A because a
                    real factory must own the ``limiter``/config setup that routes and
                    tests reference as globals on ``app`` — moving it requires updating
                    the ``monkeypatch`` targets in ``tests/conftest.py`` (see README).
  2C+               ``clients/`` · ``services/`` · ``integrations/`` · ``security/`` ·
                    ``routes/`` blueprints, each move paired with its test-seam update.

NOTE: the test alias ``import app as matbot`` is a private alias of the legacy
entrypoint module and is unrelated to this package's name.
"""

__all__: list[str] = []
