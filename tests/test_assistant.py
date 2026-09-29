"""The assistant's model call, with Ollama faked.

Pinned here: thinking mode is switched off for models that have one, left alone for
models that do not, and any reasoning that arrives anyway never reaches the answer.
"""

from __future__ import annotations

import pytest

from api import assistant


class FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {
            "message": {"role": "assistant", "content": self._content},
            "prompt_eval_count": 100,
            "eval_count": 10,
            "total_duration": 1_000_000_000,
            "load_duration": 0,
        }


@pytest.fixture
def sent(monkeypatch):
    """Capture the payload sent to Ollama; reply with a canned message."""
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured.update(json)
        return FakeResponse(captured.pop("_reply", "Demucs or UVR."))

    monkeypatch.setattr(assistant.httpx, "post", fake_post)
    return captured


def test_thinking_is_switched_off_for_qwen3(sent):
    assistant._chat([{"role": "user", "content": "q"}], model="qwen3:8b")

    assert sent["model"] == "qwen3:8b"
    assert sent["think"] is False


def test_models_without_thinking_are_not_sent_the_switch(sent):
    """An unknown option could be rejected; only send it where it means something."""
    assistant._chat([{"role": "user", "content": "q"}], model="llama3.1:8b")

    assert "think" not in sent


def test_reasoning_that_slips_through_is_stripped(monkeypatch):
    reply = "<think>\nThe user wants the tool. Result 3 says Demucs.\n</think>\n\nDemucs or UVR."
    monkeypatch.setattr(assistant.httpx, "post", lambda url, json, timeout: FakeResponse(reply))

    message, _ = assistant._chat([{"role": "user", "content": "q"}], model="qwen3:8b")

    assert message["content"] == "Demucs or UVR."
