from __future__ import annotations

import os

import pytest

_PROVIDER_ENV_PREFIX = "AZURE_OPENAI_"


@pytest.fixture(autouse=True)
def isolate_provider_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove ambient Azure provider variables from the process environment.

    ``Settings`` reads the process environment even when ``_env_file=None``, so a
    developer shell or an imported module that calls ``load_dotenv`` (as
    ``prototype/app.py`` does at import time) can make a deployment look
    configured to tests that assert the opposite. Every test constructs the
    settings it needs explicitly, so clearing these keeps results identical in a
    bare shell, a populated shell, and a run that also collects the prototype
    suite.
    """

    for name in list(os.environ):
        if name.startswith(_PROVIDER_ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)
