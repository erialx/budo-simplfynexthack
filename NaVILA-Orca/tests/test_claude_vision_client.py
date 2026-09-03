import numpy as np
from PIL import Image
import pytest

from navila_orca.veto.claude_vision_client import AnthropicVetoVisionClient


def _blank_frame() -> Image.Image:
    return Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8))


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeMessagesResource:
    def __init__(self, response_text):
        self.response_text = response_text
        self.received_kwargs = None

    def create(self, **kwargs):
        self.received_kwargs = kwargs
        return _FakeMessage(self.response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text="CLEAR"):
        self.messages = _FakeMessagesResource(response_text)


def test_query_returns_the_response_text_via_injected_client():
    fake_client = _FakeAnthropicClient(response_text="VETO: person crossing")
    client = AnthropicVetoVisionClient(client=fake_client)
    result = client.query(_blank_frame(), "go to the door", "move forward 25 cm")
    assert result == "VETO: person crossing"


def test_query_sends_the_instruction_and_action_and_an_image():
    fake_client = _FakeAnthropicClient(response_text="CLEAR")
    client = AnthropicVetoVisionClient(client=fake_client)
    client.query(_blank_frame(), "go to the door", "move forward 25 cm")

    kwargs = fake_client.messages.received_kwargs
    content = kwargs["messages"][0]["content"]
    types = [block["type"] for block in content]
    assert "image" in types
    assert "text" in types
    text_block = next(block for block in content if block["type"] == "text")
    assert "go to the door" in text_block["text"]
    assert "move forward 25 cm" in text_block["text"]


def test_missing_anthropic_client_raises_helpful_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("no module named anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="anthropic"):
        AnthropicVetoVisionClient()
