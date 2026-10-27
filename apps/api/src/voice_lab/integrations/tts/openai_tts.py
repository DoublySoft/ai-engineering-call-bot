from collections.abc import AsyncIterator

import numpy as np
from openai import AsyncOpenAI


class OpenAITTS:
    provider = "openai"
    output_sample_rate = 24_000  # response_format="pcm": 24 kHz, 16-bit LE, no header

    def __init__(self, client: AsyncOpenAI):
        self._client = client

    async def synthesize(self, text: str, model: str, voice: str) -> AsyncIterator[np.ndarray]:
        leftover = b""
        async with self._client.audio.speech.with_streaming_response.create(
            model=model, voice=voice, input=text, response_format="pcm"
        ) as response:
            async for chunk in response.iter_bytes(4800):
                data = leftover + chunk
                usable = len(data) - (len(data) % 2)
                leftover = data[usable:]
                if usable:
                    yield np.frombuffer(data[:usable], dtype="<i2").astype(np.int16)
