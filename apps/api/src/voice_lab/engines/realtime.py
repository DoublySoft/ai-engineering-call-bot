"""Speech-to-speech engine on the OpenAI Realtime API (WebSocket, server side).

Kept separate from the STT -> LLM -> TTS pipeline on purpose: the provider does
turn detection, transcription, generation and voice in one session. Anthropic
can't be plugged in here. The engine speaks the same AudioTransport contract,
so it runs over the browser transport and over Twilio unchanged.
"""

import asyncio
import base64
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

import numpy as np
from openai import AsyncOpenAI

from voice_lab.audio.codec import resample
from voice_lab.catalog.pricing import calculate_cost
from voice_lab.catalog.registry import get_model
from voice_lab.catalog.schemas import BillingDimension as D
from voice_lab.core.telemetry import tracer
from voice_lab.engines.base import EngineCapabilities, SessionContext
from voice_lab.engines.transcript import TranscriptStore

logger = logging.getLogger(__name__)

REALTIME_RATE = 24_000  # audio/pcm in and out
REALTIME_VOICES = {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"}
DEFAULT_VOICE = "marin"


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        if obj is None:
            return default
    return obj


def usage_quantities(usage: Any) -> dict[D, int]:
    """Realtime usage -> billable dimensions. Input text/audio counts include their
    cached part, so the uncached share is total minus cached (as in chat models)."""
    text_in = _get(usage, "input_token_details", "text_tokens", default=0)
    audio_in = _get(usage, "input_token_details", "audio_tokens", default=0)
    cached_text = _get(usage, "input_token_details", "cached_tokens_details", "text_tokens", default=0)
    cached_audio = _get(usage, "input_token_details", "cached_tokens_details", "audio_tokens", default=0)
    return {
        D.INPUT_TEXT_TOKENS: max(0, text_in - cached_text),
        D.CACHED_INPUT_TEXT_TOKENS: cached_text,
        D.INPUT_AUDIO_TOKENS: max(0, audio_in - cached_audio),
        D.CACHED_INPUT_AUDIO_TOKENS: cached_audio,
        D.OUTPUT_TEXT_TOKENS: _get(usage, "output_token_details", "text_tokens", default=0),
        D.OUTPUT_AUDIO_TOKENS: _get(usage, "output_token_details", "audio_tokens", default=0),
    }


class RealtimeEngine:
    capabilities = EngineCapabilities(streaming_stt=True, barge_in=True, turn_detection="provider")

    def __init__(self, ctx: SessionContext, client: AsyncOpenAI | None = None, connect: Callable[[str], Any] | None = None):
        self.ctx = ctx
        self.cfg = ctx.config
        self._connect = connect or (lambda model: client.realtime.connect(model=model))  # type: ignore[union-attr]
        self._manager: Any = None
        self.conn: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._transcript = TranscriptStore(ctx.sessionmaker, ctx.session_id)
        self._closed = False
        self._pending_marks: set[str] = set()
        # current turn / response state
        self._cid: str | None = None
        self._speech_stopped_at: float | None = None
        self._response_started_at: float | None = None
        self._item_id: str | None = None
        self._first_audio_at: float | None = None
        self._audio_sent_ms = 0
        self._agent_text = ""
        self._interrupted = False
        self._response_active = False
        self._otel_span: Any = None
        self._turns: list[dict[str, Any]] = []
        self._turn: dict[str, Any] = {}

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        model = self.cfg["s2s_model"]
        voice = self.cfg.get("tts_voice") if self.cfg.get("tts_voice") in REALTIME_VOICES else DEFAULT_VOICE
        self._manager = self._connect(model)
        self.conn = await self._manager.__aenter__()
        await self.conn.session.update(
            session={
                "type": "realtime",
                "instructions": self.cfg["system_prompt"],
                "output_modalities": ["audio"],
                "max_output_tokens": self.cfg.get("max_output_tokens", 300) * 4,  # audio responses need headroom
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": REALTIME_RATE},
                        "transcription": {"model": self.cfg["stt_model"], "language": self.cfg.get("language")},
                        "turn_detection": {
                            "type": "server_vad",
                            "silence_duration_ms": 500,
                            "create_response": True,
                            "interrupt_response": True,
                        },
                    },
                    "output": {"format": {"type": "audio/pcm", "rate": REALTIME_RATE}, "voice": voice},
                },
            }
        )
        self._reader = asyncio.create_task(self._read_loop())
        await self.ctx.events.emit(
            "session.started", engine="realtime", transport=self.ctx.transport.name, provider="openai", model=model, voice=voice
        )
        greeting = self.cfg.get("greeting")
        if greeting:
            self._new_turn(greeting=True)
            await self.conn.response.create(response={"instructions": f"Saluda diciendo exactamente lo siguiente y nada más: {greeting}"})

    async def end(self, reason: str) -> dict[str, Any]:
        self._closed = True
        if self._reader and not self._reader.done():
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self._manager is not None:
            try:
                await self._manager.__aexit__(None, None, None)
            except Exception:  # connection may already be gone
                logger.debug("realtime close failed", exc_info=True)
        metrics = self._summarize()
        await self.ctx.events.emit("session.completed", reason=reason, **metrics)
        return metrics

    async def handle_audio(self, samples: np.ndarray) -> None:
        if self._closed or self.conn is None:
            return
        pcm = resample(samples, self.ctx.transport.input_sample_rate, REALTIME_RATE).astype("<i2").tobytes()
        await self.conn.input_audio_buffer.append(audio=base64.b64encode(pcm).decode())

    async def on_playback_done(self, label: str) -> None:
        if label in self._pending_marks:
            self._pending_marks.discard(label)
            await self.ctx.events.emit("audio.playback.completed", correlation_id=label)

    # ------------------------------------------------------------ events
    def _new_turn(self, **extra: Any) -> None:
        self._cid = uuid.uuid4().hex[:16]
        self._turn = {"correlation_id": self._cid, **extra}

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                event = await self.conn.recv()
                await self._handle(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                logger.exception("realtime connection failed")
                await self.ctx.events.emit("s2s.failed", provider="openai", error=type(exc).__name__, message=str(exc)[:300])
                await self.ctx.transport.hangup("realtime_error")

    async def _handle(self, event: Any) -> None:
        kind = _get(event, "type")
        if kind == "input_audio_buffer.speech_started":
            await self.ctx.events.emit("speech.started", provider="openai")
            if self._first_audio_at is not None and self._item_id:
                await self._barge_in()
        elif kind == "input_audio_buffer.speech_stopped":
            self._speech_stopped_at = time.perf_counter()
            self._new_turn()
            await self.ctx.events.emit("speech.ended", correlation_id=self._cid, provider="openai")
        elif kind == "conversation.item.input_audio_transcription.completed":
            text = (_get(event, "transcript") or "").strip()
            if text:
                await self._transcript.add("user", text)
                self.ctx.events.push({"type": "transcript", "role": "user", "text": text, "final": True, "correlation_id": self._cid})
            await self._record_transcription(_get(event, "usage"))
        elif kind == "response.created":
            self._response_started_at = time.perf_counter()
            self._response_active = True
            self._otel_span = tracer.start_span(
                "voice.s2s",
                attributes={
                    "voice.call_id": str(self.ctx.call_id),
                    "voice.session_id": str(self.ctx.session_id),
                    "gen_ai.system": "openai",
                    "gen_ai.request.model": self.cfg["s2s_model"],
                },
            )
            self._first_audio_at, self._audio_sent_ms, self._agent_text, self._interrupted, self._item_id = None, 0, "", False, None
            if self._cid is None:
                self._new_turn()
            await self.ctx.events.emit("s2s.started", correlation_id=self._cid, provider="openai", model=self.cfg["s2s_model"])
        elif kind == "response.output_audio.delta":
            pcm = np.frombuffer(base64.b64decode(_get(event, "delta") or ""), dtype="<i2").astype(np.int16)
            self._item_id = _get(event, "item_id") or self._item_id
            if self._first_audio_at is None:
                self._first_audio_at = time.perf_counter()
                if self._speech_stopped_at is not None and not self._turn.get("greeting"):
                    self._turn["time_to_first_audio_ms"] = int((self._first_audio_at - self._speech_stopped_at) * 1000)
                await self.ctx.events.emit(
                    "audio.playback.started", correlation_id=self._cid, time_to_first_audio_ms=self._turn.get("time_to_first_audio_ms")
                )
            self._audio_sent_ms += int(pcm.size * 1000 / REALTIME_RATE)
            await self.ctx.transport.send_audio(pcm, REALTIME_RATE)
        elif kind == "response.output_audio_transcript.delta":
            self._agent_text += _get(event, "delta") or ""
            self.ctx.events.push(
                {"type": "transcript", "role": "agent", "text": self._agent_text, "final": False, "correlation_id": self._cid}
            )
        elif kind == "response.output_audio_transcript.done":
            self._agent_text = _get(event, "transcript") or self._agent_text
        elif kind == "response.done":
            await self._response_done(_get(event, "response"))
        elif kind == "error":
            code = _get(event, "error", "code")
            if code != "response_cancel_not_active":  # benign race when the user interrupts
                await self.ctx.events.emit("s2s.error", provider="openai", code=code, message=str(_get(event, "error", "message"))[:300])

    async def _barge_in(self) -> None:
        played_ms = min(self._audio_sent_ms, int((time.perf_counter() - (self._first_audio_at or 0)) * 1000))
        await self.ctx.transport.clear_audio()
        self._pending_marks.clear()
        try:  # the server already cancelled the response; drop the unplayed audio from context
            await self.conn.conversation.item.truncate(item_id=self._item_id, content_index=0, audio_end_ms=played_ms)
        except Exception:
            logger.debug("truncate failed", exc_info=True)
        self._interrupted = True
        self._first_audio_at = None
        await self.ctx.events.emit("agent.interrupted", correlation_id=self._cid, played_ms=played_ms)

    async def _response_done(self, response: Any) -> None:
        if not self._response_active:
            # A response.done we never saw start (e.g. the server cancelling on speech start):
            # nothing was said for it here, but any usage it reports was still billed.
            if _get(response, "usage") is not None:
                await self._record_s2s(_get(response, "usage"), 0)
            return
        self._response_active = False
        status = _get(response, "status")
        if self._otel_span is not None:
            self._otel_span.set_attributes({"voice.status": status or "", "voice.correlation_id": self._cid or ""})
            self._otel_span.end()
            self._otel_span = None
        duration = int((time.perf_counter() - (self._response_started_at or time.perf_counter())) * 1000)
        self._turn["response_ms"] = duration
        text = self._agent_text.strip().strip("«»\"'“”").strip()
        self._agent_text = ""
        interrupted = self._interrupted or status == "cancelled"
        if text:
            await self._transcript.add("agent", text, interrupted=interrupted)
            self.ctx.events.push(
                {
                    "type": "transcript",
                    "role": "agent",
                    "text": text,
                    "final": True,
                    "interrupted": interrupted,
                    "correlation_id": self._cid,
                }
            )
        await self.ctx.events.emit(
            "s2s.completed", correlation_id=self._cid, provider="openai", model=self.cfg["s2s_model"], duration_ms=duration, status=status
        )
        await self._record_s2s(_get(response, "usage"), duration)
        if self._first_audio_at is not None and self._cid:
            self._pending_marks.add(self._cid)
            await self.ctx.transport.flush_marker(self._cid)
        self._turns.append(self._turn)
        self._first_audio_at, self._item_id, self._cid = None, None, None
        await self._check_spend_limit()

    # ------------------------------------------------------------ usage
    async def _record_s2s(self, usage: Any, latency_ms: int) -> None:
        model = self.cfg["s2s_model"]
        definition = get_model("openai", model)
        if usage is None:
            cost = calculate_cost(definition.current_pricing() if definition else None, {})
            await self.ctx.usage.record(
                provider="openai",
                model=model,
                operation_type="s2s",
                cost=cost,
                usage_source="estimated",
                latency_ms=latency_ms,
                correlation_id=self._cid,
            )
            return
        quantities = usage_quantities(usage)
        cost = calculate_cost(definition.current_pricing() if definition else None, quantities)
        await self.ctx.usage.record(
            provider="openai",
            model=model,
            operation_type="s2s",
            cost=cost,
            usage_source="provider_reported",
            input_tokens=_get(usage, "input_tokens"),
            output_tokens=_get(usage, "output_tokens"),
            cached_tokens=_get(usage, "input_token_details", "cached_tokens"),
            latency_ms=latency_ms,
            correlation_id=self._cid,
            details={k.value: v for k, v in quantities.items()},
        )

    async def _record_transcription(self, usage: Any) -> None:
        """Input transcription is billed apart from the speech-to-speech model."""
        model = self.cfg["stt_model"]
        definition = get_model("openai", model)
        seconds = _get(usage, "seconds") if _get(usage, "type") == "duration" else None
        quantities = {D.AUDIO_INPUT_SECONDS: seconds} if seconds is not None else {}
        cost = calculate_cost(definition.current_pricing() if definition else None, quantities)
        await self.ctx.usage.record(
            provider="openai",
            model=model,
            operation_type="stt",
            cost=cost,
            usage_source="provider_reported" if usage is not None else "estimated",
            audio_duration_ms=int(seconds * 1000) if seconds is not None else None,
            correlation_id=self._cid,
            details={"realtime_input_transcription": True},
        )

    async def _check_spend_limit(self) -> None:
        limit = self.ctx.max_cost_usd
        if limit is not None and self.ctx.usage.total_cost_usd >= limit and not self._closed:
            await self.ctx.events.emit("call.spend_limit_reached", limit_usd=str(limit), total_cost_usd=str(self.ctx.usage.total_cost_usd))
            await self.ctx.transport.hangup("spend_limit")

    def _summarize(self) -> dict[str, Any]:
        def avg(key: str) -> int | None:
            values = [t[key] for t in self._turns if t.get(key) is not None]
            return int(sum(values) / len(values)) if values else None

        return {
            "engine": "realtime",
            "turns": len([t for t in self._turns if not t.get("greeting")]),
            "avg_time_to_first_audio_ms": avg("time_to_first_audio_ms"),
            "avg_response_ms": avg("response_ms"),
            "measurement_note": "Measured server-side from the provider's speech_stopped event to the first audio delta.",
            "total_cost_usd": str(self.ctx.usage.total_cost_usd),
        }
