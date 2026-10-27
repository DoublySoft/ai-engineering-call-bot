import numpy as np

from tests.fakes import silence, speech
from voice_lab.audio.codec import mulaw_to_pcm16, pcm16_to_mulaw, pcm16_to_wav, resample
from voice_lab.audio.vad import EnergyVad, VadEvent
from voice_lab.engines.pipeline import split_ready_sentences
from voice_lab.integrations.telephony.twilio.client import stream_twiml


def test_mulaw_roundtrip_is_close() -> None:
    pcm = speech(100)
    back = mulaw_to_pcm16(pcm16_to_mulaw(pcm))
    assert back.shape == pcm.shape
    assert np.max(np.abs(back.astype(int) - pcm.astype(int))) < 300  # G.711 quantization


def test_mulaw_silence_byte() -> None:
    assert pcm16_to_mulaw(np.zeros(4, np.int16)) == b"\xff" * 4


def test_resample_24k_to_8k_exact_length() -> None:
    assert resample(np.ones(2400, np.int16), 24000, 8000).size == 800


def test_wav_header() -> None:
    assert pcm16_to_wav(np.zeros(80, np.int16), 8000)[:4] == b"RIFF"


def test_vad_detects_turn() -> None:
    vad = EnergyVad()
    events = [e for e, _ in vad.process(np.concatenate([silence(300), speech(500), silence(900)])) if e != VadEvent.NONE]
    assert events == [VadEvent.SPEECH_STARTED, VadEvent.SPEECH_ENDED]


def test_vad_ignores_short_click() -> None:
    vad = EnergyVad()
    events = [e for e, _ in vad.process(np.concatenate([silence(200), speech(20), silence(900)])) if e != VadEvent.NONE]
    assert events == []


def test_sentence_splitting_keeps_remainder() -> None:
    ready, rest = split_ready_sentences("Hola. ¿Qué tal estás? Yo bien")
    assert ready == ["Hola.", "¿Qué tal estás?"] and rest == "Yo bien"


def test_twiml_escapes_parameters() -> None:
    xml = stream_twiml("wss://x/twilio/media", {"call_id": "a&b", "token": 't"k'})
    assert '<Connect><Stream url="wss://x/twilio/media">' in xml
    import xml.etree.ElementTree as ET

    params = {p.get("name"): p.get("value") for p in ET.fromstring(xml).iter("Parameter")}
    assert params == {"call_id": "a&b", "token": 't"k'}
