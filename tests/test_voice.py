"""Тесты голосового модуля: очистка текста для озвучки, WAV-разбор."""

import io
import wave

from jarvis.voice import clean_for_speech


def test_clean_for_speech_strips_markdown():
    text = "**Привет!** Вот `код` и https://example.com ссылка."
    cleaned = clean_for_speech(text)
    assert "Привет!" in cleaned
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "https://" not in cleaned


def test_clean_for_speech_truncates():
    cleaned = clean_for_speech("слово " * 500, limit=100)
    assert len(cleaned) <= 105
    assert cleaned.endswith("…")


def test_clean_for_speech_removes_code_blocks():
    cleaned = clean_for_speech("Ответ:\n```python\nprint(1)\n``` готово")
    assert "print" not in cleaned
    assert "готово" in cleaned


def test_wav_bytes_roundtrip():
    """WAV-буфер корректно читается стандартным wave (как в transcribe)."""
    sample_rate = 16000
    frames = b"\x00\x00" * sample_rate  # 1 секунда тишины
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(frames)
    buf.seek(0)
    with wave.open(buf, "rb") as reader:
        assert reader.getframerate() == 16000
        assert reader.getnchannels() == 1
        assert reader.getnframes() == sample_rate
