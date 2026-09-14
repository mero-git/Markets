from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marketbrief.config import Config, load_config  # noqa: E402


@pytest.fixture(scope="session")
def cfg() -> Config:
    return load_config()


class FakeResponse:
    def __init__(self, *, json_data=None, text: str = "", content: bytes | None = None,
                 status_code: int = 200):
        self._json = json_data
        self.text = text
        self.content = content if content is not None else text.encode()
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Stub of requests.Session: returns queued responses, records calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.headers: dict[str, str] = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {url}")
        return self._responses.pop(0)
