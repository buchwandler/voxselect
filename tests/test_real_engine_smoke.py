from __future__ import annotations

import io
import wave

import pytest

from scripts.smoke_real_engine import validate_wav


def make_wav(samples: bytes, *, sample_width: int = 2) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(24000)
        wav_file.writeframes(samples)
    return output.getvalue()


def test_validate_wav_accepts_non_silent_pcm() -> None:
    result = validate_wav(make_wav(b"\x01\x00\x02\x00"))

    assert result == {
        "channels": 1,
        "frames": 2,
        "sample_rate": 24000,
        "sample_width": 2,
    }


def test_validate_wav_rejects_silent_pcm() -> None:
    with pytest.raises(RuntimeError, match="WAV is silent"):
        validate_wav(make_wav(b"\x00\x00" * 4))


def test_validate_wav_checks_unsigned_eight_bit_silence() -> None:
    with pytest.raises(RuntimeError, match="WAV is silent"):
        validate_wav(make_wav(b"\x80" * 4, sample_width=1))
