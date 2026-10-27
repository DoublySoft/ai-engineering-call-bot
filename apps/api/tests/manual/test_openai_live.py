"""Hits the real OpenAI API with OPENAI_API_KEY from .env. Costs a fraction of a cent.

Run with: uv run pytest -m manual tests/manual -s
"""

import numpy as np
import pytest

from voice_lab.integrations.llm.base import ChatMessage
from voice_lab.integrations.llm.openai_llm import OpenAILLM
from voice_lab.integrations.stt.openai_stt import OpenAISTT
from voice_lab.integrations.tts.openai_tts import OpenAITTS

pytestmark = pytest.mark.manual


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    from openai import AsyncOpenAI

    from tests.conftest import REAL_KEYS

    if not REAL_KEYS["openai"]:
        pytest.skip("OPENAI_API_KEY not configured in .env")
    return AsyncOpenAI(api_key=REAL_KEYS["openai"])


async def test_tts_then_stt_roundtrip(client) -> None:  # type: ignore[no-untyped-def]
    chunks = [c async for c in OpenAITTS(client).synthesize("Hola, esto es una prueba.", "tts-1", "alloy")]
    audio = np.concatenate(chunks)
    assert audio.size > 24_000 * 0.5
    transcript = await OpenAISTT(client).transcribe(audio, 24_000, "gpt-transcribe", "es")
    print("transcript:", transcript.text, transcript.provider_usage)
    assert "prueba" in transcript.text.lower()


async def test_llm_stream_reports_usage(client) -> None:  # type: ignore[no-untyped-def]
    chunks = [c async for c in OpenAILLM(client).stream("gpt-4.1-mini", [ChatMessage("user", "Di hola")], 20)]
    assert "".join(c.text for c in chunks)
    assert chunks[-1].usage and chunks[-1].usage.output_tokens
