"""Real Hazard Veto Agent vision client -- one Claude vision call per tactical tick.

The heavyweight ``anthropic`` SDK is imported only inside :meth:`__init__`, when
this class is actually instantiated without an explicit ``client=``, mirroring how
:mod:`navila_orca.backends.mjlab_go2` defers its simulator import. The rest of the
``veto`` package -- parsing, :class:`~navila_orca.veto.scenario_injector.ScenarioInjector`,
the :class:`~navila_orca.veto.veto_agent.VetoVisionClient` Protocol -- stays usable
with no ``anthropic`` install and no API key, which is what lets
:class:`~navila_orca.veto.veto_agent.HazardVetoAgent` be developed and tested against
static images first, and wired to a real Claude call only once that's solid.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image


VETO_SYSTEM_PROMPT = (
    "You are the hazard-veto layer for a guide-dog robot. You are shown exactly one "
    "camera frame and the action the robot's driver is about to take. Decide only "
    "whether taking that action RIGHT NOW, given what is visible in this frame, is "
    "unsafe (for example: a red pedestrian signal, a person or obstacle directly in "
    "the path). Respond with exactly one line: either \"CLEAR\" or "
    "\"VETO: <one short sentence reason>\". Never respond with anything else."
)


class AnthropicVetoVisionClient:
    """Calls the Claude Messages API with vision, once per :meth:`query`.

    Pass an already-configured ``client`` (e.g. a test double, or an
    ``anthropic.Anthropic()`` instance with custom options) to avoid the lazy
    ``anthropic`` import and its API-key requirement entirely -- this is how
    ``tests/test_claude_vision_client.py`` exercises the encode/prompt/parse path
    without the real SDK installed.
    """

    def __init__(self, model: str = "claude-haiku-4-5", *, client: Any = None) -> None:
        self.model = model
        if client is not None:
            self._client = client
        else:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "AnthropicVetoVisionClient needs the 'anthropic' package "
                    "(pip install anthropic) and an API key configured for it. "
                    "Pass client= explicitly to use an already-configured client, "
                    "or use a stub VetoVisionClient for offline development."
                ) from exc
            self._client = anthropic.Anthropic()

    # Downscale + JPEG before sending, not the raw sim render. A full-res PNG of a
    # 2467x1219 OrcaLab frame smoke-tested at ~2.9MB / 3.95M base64 chars per call --
    # the API downscales anything past ~1568px on the long edge anyway, so sending
    # it at full size buys nothing and is a real latency/cost problem at a ~1Hz
    # veto cadence, not a cosmetic one. 1024px is comfortably under that threshold.
    _MAX_EDGE_PX = 1024
    _JPEG_QUALITY = 85

    @classmethod
    def _encode_jpeg(cls, image: Image.Image) -> str:
        rgb = image.convert("RGB")
        w, h = rgb.size
        scale = min(1.0, cls._MAX_EDGE_PX / max(w, h))
        if scale < 1.0:
            rgb = rgb.resize(
                (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS
            )
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=cls._JPEG_QUALITY)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def query(self, image: Image.Image, instruction: str, proposed_action: str) -> str:
        image_b64 = self._encode_jpeg(image)
        message = self._client.messages.create(
            model=self.model,
            max_tokens=64,
            system=VETO_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Navigation instruction: {instruction}\n"
                                f"Proposed next action: {proposed_action}"
                            ),
                        },
                    ],
                }
            ],
        )
        return "".join(
            block.text
            for block in message.content
            if getattr(block, "type", None) == "text"
        )
