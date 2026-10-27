"""Muse Voice file transcription, billed in whole audio seconds."""
import io
import wave

from .. import models
from . import _wire


def transcribe(conn, audio_bytes, mime, language=None, *, purpose="voice") -> dict:
    models.check(conn, 0)
    try:
        if mime not in ("audio/wav", "audio/x-wav", "audio/wave"):
            raise ValueError("WAV required")
        with wave.open(io.BytesIO(audio_bytes), "rb") as audio:
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or
                    audio.getframerate() not in (16000, 24000) or audio.getcomptype() != "NONE"):
                raise ValueError("Unsupported WAV encoding")
            frames = audio.getnframes()
            if frames <= 0 or len(audio.readframes(frames)) != frames * 2:
                raise ValueError("Truncated or empty WAV")
            units = frames // audio.getframerate()
    except (ValueError, EOFError, wave.Error) as exc:
        raise _wire.stop("Convert audio to mono 16-bit PCM WAV at 16 or 24 kHz.",
                         "Преобразуйте звук в моно PCM WAV 16 бит, 16 или 24 кГц.") from exc
    data = models.request_units(conn, purpose, "muse-voice-transcribe-1.0", units,
                                "/asr/transcribe", fields=_wire.voice_fields(language),
                                files={"audio": ("recording.wav", audio_bytes, "audio/wav")},
                                usage_units=_wire.voice_units)
    if not isinstance(data.get("transcript"), str):
        raise _wire.stop("No transcript returned.", "Расшифровка не получена.")
    return data
