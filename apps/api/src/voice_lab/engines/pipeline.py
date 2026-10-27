"""STT -> LLM -> TTS pipeline with local turn detection and barge-in."""

import asyncio
import logging
import re
import time
import uuid
from typing import Any

import numpy as np

from voice_lab.audio.vad import EnergyVad, VadEvent
from voice_lab.catalog.pricing import calculate_cost
from voice_lab.catalog.registry import get_model
from voice_lab.catalog.schemas import BillingDimension as D
from voice_lab.engines.base import EngineCapabilities, SessionContext
from voice_lab.engines.transcript import TranscriptStore
from voice_lab.integrations.llm.base import ChatMessage, LLMClient, LLMUsage
from voice_lab.integrations.stt.base import BatchSTT
from voice_lab.integrations.tts.base import TTS

logger = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?¿¡…:;])\s+")
PREROLL_MS = 300
MIN_INTERRUPT_MS = 1500


def split_ready_sentences(buffer: str) -> tuple[list[str], str]:
    """Return complete sentences ready for TTS and the unfinished remainder."""
    parts = _SENTENCE_END.split(buffer)
    if len(parts) == 1:
        return [], buffer
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


class PipelineEngine:
    capabilities = EngineCapabilities()

    def __init__(self, ctx: SessionContext, stt: BatchSTT, llm: LLMClient, tts: TTS):
        self.ctx = ctx
        self.cfg = ctx.config
        self.stt, self.llm, self.tts = stt, llm, tts
        rate = ctx.transport.input_sample_rate
        self.vad = EnergyVad(sample_rate=rate, start_frames=5)
        self._rate = rate
        self._preroll: list[np.ndarray] = []
        self._utterance: list[np.ndarray] = []
        self._history: list[ChatMessage] = [ChatMessage("system", self.cfg["system_prompt"])]
        self._turn_task: asyncio.Task[None] | None = None
        self._pending_marks: set[str] = set()
        self._transcript = TranscriptStore(ctx.sessionmaker, ctx.session_id)
        self._turns: list[dict[str, Any]] = []
        self._closed = False
        self._uncommitted_audio: np.ndarray | None = None
        self._user_turn_committed = False
        self._audio_out_active = False

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self.ctx.events.emit("session.started", engine="pipeline", transport=self.ctx.transport.name)
        greeting = self.cfg.get("greeting")
        if greeting:
            self._turn_task = asyncio.create_task(self._speak_only(greeting))

    async def end(self, reason: str) -> dict[str, Any]:
        self._closed = True
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
            try:
                await self._turn_task
            except (asyncio.CancelledError, Exception):
                pass
        metrics = self._summarize()
        await self.ctx.events.emit("session.completed", reason=reason, **metrics)
        return metrics

    @property
    def agent_speaking(self) -> bool:
        """True when the agent is (or is about to be) audible — not during STT."""
        turn_busy = bool(self._turn_task and not self._turn_task.done())
        return self._audio_out_active or bool(self._pending_marks) or (turn_busy and self._user_turn_committed)

    # ------------------------------------------------------------ audio in
    async def handle_audio(self, samples: np.ndarray) -> None:
        if self._closed:
            return
        for event, frame in self.vad.process(samples):
            if self.vad.in_speech or event == VadEvent.SPEECH_ENDED:
                self._utterance.append(frame)
            else:
                self._preroll.append(frame)
                max_frames = PREROLL_MS // self.vad.frame_ms
                self._preroll = self._preroll[-max_frames:]

            if event == VadEvent.SPEECH_STARTED:
                self._utterance = self._preroll + self._utterance
                self._preroll = []
                turn_running = bool(self._turn_task and not self._turn_task.done())
                can_merge = turn_running and self._uncommitted_audio is not None and not self._user_turn_committed
                await self.ctx.events.emit("speech.started")
                if can_merge:
                    prev = self._uncommitted_audio
                    assert prev is not None
                    if self._turn_task and not self._turn_task.done():
                        self._turn_task.cancel()
                    self._utterance = [prev] + self._utterance
                    self._uncommitted_audio = None
                # If the agent is mid-reply, do not cancel yet: short echo/noise often
                # triggers SPEECH_STARTED; SPEECH_ENDED decides whether to yield.
            elif event == VadEvent.SPEECH_ENDED:
                audio = np.concatenate(self._utterance) if self._utterance else np.zeros(0, np.int16)
                self._utterance = []
                audio_ms = int(audio.size * 1000 / self._rate)
                prev_running = bool(self._turn_task and not self._turn_task.done())
                agent_mid_reply = prev_running and self.agent_speaking
                await self.ctx.events.emit("speech.ended", audio_ms=audio_ms)
                if agent_mid_reply and audio_ms < MIN_INTERRUPT_MS:
                    continue
                if prev_running:
                    if agent_mid_reply and self.capabilities.barge_in:
                        await self._barge_in()
                    elif self._turn_task and not self._turn_task.done():
                        self._turn_task.cancel()
                self._uncommitted_audio = audio
                self._user_turn_committed = False
                self._turn_task = asyncio.create_task(self._run_turn(audio, time.perf_counter()))

    async def on_playback_done(self, label: str) -> None:
        if label in self._pending_marks:
            self._pending_marks.discard(label)
            await self.ctx.events.emit("audio.playback.completed", correlation_id=label)

    async def _barge_in(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self._pending_marks.clear()
        self._audio_out_active = False
        self._uncommitted_audio = None
        self._user_turn_committed = False
        await self.ctx.transport.clear_audio()
        await self.ctx.events.emit("agent.interrupted")

    # ------------------------------------------------------------ turns
    async def _run_turn(self, audio: np.ndarray, speech_ended_at: float) -> None:
        cid = uuid.uuid4().hex[:16]
        turn: dict[str, Any] = {"correlation_id": cid}
        stt_model, llm_model = self.cfg["stt_model"], self.cfg["llm_model"]
        try:
            # ---- STT
            async with self.ctx.events.span("stt", correlation_id=cid, provider=self.stt.provider, model=stt_model) as res:
                t0 = time.perf_counter()
                transcript = await self.stt.transcribe(audio, self._rate, stt_model, self.cfg.get("language"))
                turn["stt_ms"] = int((time.perf_counter() - t0) * 1000)
                res["characters"] = len(transcript.text)
            await self._record_stt(transcript, turn["stt_ms"], cid)
            if not transcript.text:
                if self._uncommitted_audio is audio:
                    self._uncommitted_audio = None
                return
            await self._transcript.add("user", transcript.text)
            self.ctx.events.push({"type": "transcript", "role": "user", "text": transcript.text, "final": True, "correlation_id": cid})
            self._history.append(ChatMessage("user", transcript.text))
            self._user_turn_committed = True
            if self._uncommitted_audio is audio:
                self._uncommitted_audio = None

            # ---- LLM (streamed) -> TTS per sentence.
            # The LLM stream is consumed in its own task so llm.* timings measure
            # only the model, not the speech synthesis running alongside it.
            sentences: asyncio.Queue[str | None] = asyncio.Queue()
            llm_state: dict[str, Any] = {"reply": "", "usage": None, "complete": False}
            producer = asyncio.create_task(self._produce_reply(llm_model, cid, turn, sentences, llm_state))
            spoken_all = False
            try:
                while (sentence := await sentences.get()) is not None:
                    await self._speak(sentence, cid, turn, speech_ended_at)
                await producer  # re-raises LLM errors
                spoken_all = True
            finally:
                if not producer.done():
                    producer.cancel()
                    await asyncio.gather(producer, return_exceptions=True)
                interrupted = not (spoken_all and llm_state["complete"])
                reply = llm_state["reply"].strip()
                if reply:
                    self._history.append(ChatMessage("assistant", reply))
                    await self._transcript.add("agent", reply, interrupted=interrupted)
                    self.ctx.events.push(
                        {
                            "type": "transcript",
                            "role": "agent",
                            "text": reply,
                            "final": True,
                            "interrupted": interrupted,
                            "correlation_id": cid,
                        }
                    )
                await self._record_llm(llm_state["usage"], llm_model, turn.get("llm_ms", 0), cid)
            await self._mark(cid)
        except asyncio.CancelledError:
            turn["interrupted"] = True
            raise
        except Exception as exc:  # keep the call alive; surface the failure
            logger.exception("turn failed")
            await self.ctx.events.emit("turn.failed", correlation_id=cid, error=type(exc).__name__, message=str(exc)[:300])
        finally:
            self._audio_out_active = False
            self._turns.append(turn)
            await self._check_spend_limit()

    async def _produce_reply(
        self, model: str, cid: str, turn: dict[str, Any], sentences: "asyncio.Queue[str | None]", state: dict[str, Any]
    ) -> None:
        buffer = ""
        try:
            async with self.ctx.events.span("llm", correlation_id=cid, provider=self.llm.provider, model=model) as res:
                t0 = time.perf_counter()
                try:
                    async for chunk in self.llm.stream(model, self._history, self.cfg.get("max_output_tokens", 300)):
                        if chunk.usage:
                            state["usage"] = chunk.usage
                        if not chunk.text:
                            continue
                        turn.setdefault("llm_ttft_ms", int((time.perf_counter() - t0) * 1000))
                        state["reply"] += chunk.text
                        buffer += chunk.text
                        self.ctx.events.push(
                            {"type": "transcript", "role": "agent", "text": state["reply"], "final": False, "correlation_id": cid}
                        )
                        ready, buffer = split_ready_sentences(buffer)
                        for sentence in ready:
                            sentences.put_nowait(sentence)
                    if buffer.strip():
                        sentences.put_nowait(buffer.strip())
                    state["complete"] = True
                finally:
                    turn["llm_ms"] = int((time.perf_counter() - t0) * 1000)
                    res["output_chars"] = len(state["reply"])
        finally:
            sentences.put_nowait(None)  # always unblock the speaker loop

    async def _speak_only(self, text: str) -> None:
        cid = uuid.uuid4().hex[:16]
        turn: dict[str, Any] = {"correlation_id": cid, "greeting": True}
        try:
            await self._speak(text, cid, turn, None)
            await self._transcript.add("agent", text)
            self._history.append(ChatMessage("assistant", text))
            self.ctx.events.push({"type": "transcript", "role": "agent", "text": text, "final": True, "correlation_id": cid})
            await self._mark(cid)
        finally:
            self._audio_out_active = False
            self._turns.append(turn)

    async def _speak(self, text: str, cid: str, turn: dict[str, Any], speech_ended_at: float | None) -> None:
        tts_model, voice = self.cfg["tts_model"], self.cfg["tts_voice"]
        self._audio_out_active = True
        async with self.ctx.events.span(
            "tts", correlation_id=cid, provider=self.tts.provider, model=tts_model, characters=len(text)
        ) as res:
            t0 = time.perf_counter()
            first = True
            audio_ms = 0
            try:
                async for pcm in self.tts.synthesize(text, tts_model, voice):
                    if first:
                        first = False
                        turn.setdefault("tts_first_byte_ms", int((time.perf_counter() - t0) * 1000))
                        if speech_ended_at is not None and "time_to_first_audio_ms" not in turn:
                            turn["time_to_first_audio_ms"] = int((time.perf_counter() - speech_ended_at) * 1000)
                            await self.ctx.events.emit(
                                "audio.playback.started", correlation_id=cid, time_to_first_audio_ms=turn["time_to_first_audio_ms"]
                            )
                    audio_ms += int(pcm.size * 1000 / self.tts.output_sample_rate)
                    await self.ctx.transport.send_audio(pcm, self.tts.output_sample_rate)
            finally:
                res["audio_ms"] = audio_ms
                await self._record_tts(text, tts_model, int((time.perf_counter() - t0) * 1000), cid)

    async def _mark(self, cid: str) -> None:
        self._pending_marks.add(cid)
        await self.ctx.transport.flush_marker(cid)

    # ------------------------------------------------------------ usage
    def _pricing(self, provider: str, model: str):  # type: ignore[no-untyped-def]
        definition = get_model(provider, model)
        return definition.current_pricing() if definition else None

    async def _record_stt(self, transcript: Any, latency_ms: int, cid: str) -> None:
        provider, model = self.stt.provider, self.cfg["stt_model"]
        # Audio seconds are measured by us; OpenAI bills the file it receives.
        # calculate_cost marks the result `estimated` when the catalog price is only
        # an approximation of the real billing (e.g. token-billed transcription).
        reported = transcript.provider_usage or {}
        # Prefer the duration the provider says it billed (gpt-transcribe rounds up).
        billed_seconds = reported.get("seconds") if reported.get("type") == "duration" else None
        seconds = billed_seconds if billed_seconds is not None else transcript.audio_duration_ms / 1000
        cost = calculate_cost(self._pricing(provider, model), {D.AUDIO_INPUT_SECONDS: seconds})
        await self.ctx.usage.record(
            provider=provider,
            model=model,
            operation_type="stt",
            cost=cost,
            usage_source="provider_reported" if billed_seconds is not None else "measured",
            audio_duration_ms=int(seconds * 1000),
            input_tokens=reported.get("input_tokens"),
            output_tokens=reported.get("output_tokens"),
            latency_ms=latency_ms,
            correlation_id=cid,
            details={"measured_audio_ms": transcript.audio_duration_ms, **({"provider_usage": reported} if reported else {})},
        )

    async def _record_llm(self, usage: LLMUsage | None, model: str, latency_ms: int, cid: str) -> None:
        provider = self.llm.provider
        details: dict[str, Any] = {}
        quantities = {}
        billed_model = model
        if usage:
            quantities = {
                D.INPUT_TEXT_TOKENS: usage.input_tokens or 0,
                D.CACHED_INPUT_TEXT_TOKENS: usage.cached_tokens or 0,
                D.OUTPUT_TEXT_TOKENS: usage.output_tokens or 0,
            }
            if usage.cache_write_tokens is not None:
                quantities[D.CACHE_WRITE_INPUT_TEXT_TOKENS] = usage.cache_write_tokens
                details["cache_write_tokens"] = usage.cache_write_tokens
            if usage.served_by_model:
                # A provider-side fallback answered: bill at the model that served it.
                billed_model = usage.served_by_model
                details["requested_model"] = model
        cost = calculate_cost(self._pricing(provider, billed_model), quantities)
        await self.ctx.usage.record(
            provider=provider,
            model=billed_model,
            operation_type="llm",
            cost=cost,
            usage_source="provider_reported" if usage else "estimated",
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_tokens=usage.cached_tokens if usage else None,
            latency_ms=latency_ms,
            correlation_id=cid,
            details=details,
        )

    async def _record_tts(self, text: str, model: str, latency_ms: int, cid: str) -> None:
        provider = self.tts.provider
        cost = calculate_cost(self._pricing(provider, model), {D.INPUT_CHARACTERS: len(text)})
        await self.ctx.usage.record(
            provider=provider,
            model=model,
            operation_type="tts",
            cost=cost,
            usage_source="measured",
            characters=len(text),
            latency_ms=latency_ms,
            correlation_id=cid,
        )

    async def _check_spend_limit(self) -> None:
        limit = self.ctx.max_cost_usd
        if limit is not None and self.ctx.usage.total_cost_usd >= limit and not self._closed:
            await self.ctx.events.emit("call.spend_limit_reached", limit_usd=str(limit), total_cost_usd=str(self.ctx.usage.total_cost_usd))
            await self.ctx.transport.hangup("spend_limit")

    def _summarize(self) -> dict[str, Any]:
        def avg(key: str) -> int | None:
            values = [t[key] for t in self._turns if key in t]
            return int(sum(values) / len(values)) if values else None

        return {
            "turns": len([t for t in self._turns if not t.get("greeting")]),
            "avg_stt_ms": avg("stt_ms"),
            "avg_llm_ttft_ms": avg("llm_ttft_ms"),
            "avg_llm_ms": avg("llm_ms"),
            "avg_tts_first_byte_ms": avg("tts_first_byte_ms"),
            "avg_time_to_first_audio_ms": avg("time_to_first_audio_ms"),
            "measurement_note": "Latencies measured server-side; network time to the phone is not included.",
            "total_cost_usd": str(self.ctx.usage.total_cost_usd),
        }
