"""G.711 mu-law <-> PCM16 and resampling with numpy (audioop is gone in 3.13)."""

import io
import wave

import numpy as np

_BIAS = 0x84
_CLIP = 32635


def _build_decode_table() -> np.ndarray:
    table = np.zeros(256, dtype=np.int16)
    for i in range(256):
        u = ~i & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa << 3) + _BIAS) << exponent
        sample -= _BIAS
        table[i] = -sample if sign else sample
    return table


_DECODE = _build_decode_table()


def mulaw_to_pcm16(data: bytes) -> np.ndarray:
    return _DECODE[np.frombuffer(data, dtype=np.uint8)]


def pcm16_to_mulaw(samples: np.ndarray) -> bytes:
    s = samples.astype(np.int32)
    sign = np.where(s < 0, 0x80, 0)
    s = np.minimum(np.abs(s), _CLIP) + _BIAS
    exponent = np.floor(np.log2(s)).astype(np.int32) - 7
    exponent = np.clip(exponent, 0, 7)
    mantissa = (s >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resampler with a crude low-pass when downsampling.
    Good enough for telephone-band speech; swap for soxr if quality matters."""
    if src_rate == dst_rate or samples.size == 0:
        return samples
    x = samples.astype(np.float32)
    if src_rate % dst_rate == 0:
        # Integer decimation (e.g. TTS 24 kHz -> Twilio 8 kHz): box filter + pick.
        # Chunk-boundary safe: output length is exactly len/ratio.
        ratio = src_rate // dst_rate
        usable = x.size - x.size % ratio
        out = x[:usable].reshape(-1, ratio).mean(axis=1)
        return np.clip(out, -32768, 32767).astype(np.int16)
    if dst_rate < src_rate:
        width = max(1, round(src_rate / dst_rate))
        x = np.convolve(x, np.ones(width, dtype=np.float32) / width, mode="same")
    n_out = int(round(x.size * dst_rate / src_rate))
    positions = np.linspace(0, x.size - 1, n_out)
    out = np.interp(positions, np.arange(x.size), x)
    return np.clip(out, -32768, 32767).astype(np.int16)


def pcm16_to_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())
    return buf.getvalue()
