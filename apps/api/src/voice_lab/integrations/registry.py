"""Builds provider clients from a config. Unsupported combinations fail loudly."""

from functools import lru_cache

from openai import AsyncOpenAI

from voice_lab.core.config import get_settings
from voice_lab.integrations.llm.base import LLMClient
from voice_lab.integrations.llm.openai_llm import OpenAILLM
from voice_lab.integrations.stt.base import BatchSTT
from voice_lab.integrations.stt.openai_stt import OpenAISTT
from voice_lab.integrations.tts.base import TTS
from voice_lab.integrations.tts.openai_tts import OpenAITTS


class ProviderNotAvailable(RuntimeError):
    pass


@lru_cache
def _openai() -> AsyncOpenAI:
    key = get_settings().openai_api_key
    if not key:
        raise ProviderNotAvailable("OPENAI_API_KEY is not set")
    return AsyncOpenAI(api_key=key)


def llm_client(provider: str) -> LLMClient:
    if provider == "openai":
        return OpenAILLM(_openai())
    # Phase 3: "anthropic" -> AnthropicLLM implementing the same LLMClient protocol.
    raise ProviderNotAvailable(f"LLM provider '{provider}' is not implemented yet")


def stt_client(provider: str) -> BatchSTT:
    if provider == "openai":
        return OpenAISTT(_openai())
    raise ProviderNotAvailable(f"STT provider '{provider}' is not implemented yet")


def tts_client(provider: str) -> TTS:
    if provider == "openai":
        return OpenAITTS(_openai())
    raise ProviderNotAvailable(f"TTS provider '{provider}' is not implemented yet")


IMPLEMENTED = {"llm": {"openai"}, "stt": {"openai"}, "tts": {"openai"}, "engine": {"pipeline"}, "voice_provider": {"custom"}}
