from __future__ import annotations

import os

import pytest

from api.config import Settings

_PROVIDER_ENV_PREFIX = "AZURE_OPENAI_"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_env_file: test asserts the production .env wiring; do not neutralise it",
    )


@pytest.fixture(autouse=True)
def isolate_provider_environment(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolate settings tests from whatever this machine happens to be holding.

    ``Settings`` draws from two ambient sources, and both have broken this suite:

    * the process environment, which ``Settings`` reads even when
      ``_env_file=None`` — a developer shell, or an imported module calling
      ``load_dotenv`` (as ``prototype/app.py`` does), can make a deployment look
      configured to a test asserting the opposite;
    * the ``.env`` / ``.env.local`` files named by ``model_config``, which exist
      on any machine actually set up to run the app.

    Every test builds the settings it needs explicitly, so neutralising both
    keeps results identical whether or not the checkout is configured to run.
    """

    for name in list(os.environ):
        if name.startswith(_PROVIDER_ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)
    if "real_env_file" not in request.keywords:
        monkeypatch.setitem(Settings.model_config, "env_file", None)
