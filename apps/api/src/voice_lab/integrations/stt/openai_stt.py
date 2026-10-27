import numpy as np
from openai import AsyncOpenAI

from voice_lab.audio.codec import pcm16_to_wav
from voice_lab.integrations.stt.base import STTCapabilities, Transcript


class OpenAISTT:
    provider = "openai"
    capabilities = STTCapabilities()

    def __init__(self, client: AsyncOpenAI):
        self._client = client

    async def transcribe(self, samples: np.ndarray, sample_rate: int, model: str, language: str | None) -> Transcript:
        wav = pcm16_to_wav(samples, sample_rate)
        kwargs = {"language": language} if language else {}
        result = await self._client.audio.transcriptions.create(model=model, file=("utterance.wav", wav, "audio/wav"), **kwargs)
        usage = result.usage.model_dump() if getattr(result, "usage", None) else None
        return Transcript(
            text=result.text.strip(),
            audio_duration_ms=int(samples.size * 1000 / sample_rate),
            provider_usage=usage,
        )
