import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure tests never accidentally pick up a real developer's env vars."""
    for key in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    yield
