"""Голос Джарвиса: озвучка (TTS) и распознавание речи (STT) — локально и бесплатно.

TTS: Windows SAPI через pyttsx3 (русский голос Microsoft Irina, если
языковой пакет установлен). STT: Vosk — офлайн-модель ~45 МБ, работает
на слабом CPU. Модель скачивается автоматически при первом использовании.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import urllib.request
import wave
import zipfile
from pathlib import Path

log = logging.getLogger("jarvis.voice")

_VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
_VOSK_DIRNAME = "vosk-model-small-ru-0.22"

_model = None  # кеш Vosk-модели


# ---------- TTS: говорить ----------

def _pick_russian_voice(engine) -> str | None:
    for voice in engine.getProperty("voices"):
        name = (voice.name or "").lower()
        langs = str(getattr(voice, "languages", "") or "").lower()
        if "irina" in name or "russian" in name or "ru-ru" in langs:
            return voice.id
    return None


def speak_sync(text: str, rate: int = 185) -> bool:
    """Озвучивает текст через SAPI. False — TTS недоступен."""
    try:
        import pyttsx3

        engine = pyttsx3.init()
        voice_id = _pick_russian_voice(engine)
        if voice_id:
            engine.setProperty("voice", voice_id)
        engine.setProperty("rate", rate)
        engine.say(text)
        engine.runAndWait()
        return True
    except Exception as e:
        log.warning("TTS не сработал: %s", e)
        return False


async def speak(text: str, rate: int = 185) -> bool:
    return await asyncio.to_thread(speak_sync, text, rate)


def clean_for_speech(text: str, limit: int = 600) -> str:
    """Убирает markdown и мусор перед озвучкой."""
    cleaned = re.sub(r"<untrusted[^>]*>.*?</untrusted>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"```.*?```", " (код) ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", " (ссылка) ", cleaned)
    cleaned = re.sub(r"[#*_>|]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0] + "…"
    return cleaned


# ---------- STT: распознавать речь ----------

def _model_dir(data_dir: Path) -> Path:
    return Path(data_dir) / _VOSK_DIRNAME


def ensure_model(data_dir: Path) -> Path:
    """Скачивает и распаковывает модель Vosk при первом запуске."""
    target = _model_dir(Path(data_dir))
    if (target / "conf").exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    zip_path = target.parent / (_VOSK_DIRNAME + ".zip")
    if not zip_path.exists():
        log.info("Скачиваю модель распознавания речи (%s)…", _VOSK_URL)
        urllib.request.urlretrieve(_VOSK_URL, zip_path)
    log.info("Распаковываю модель Vosk…")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target.parent)
    zip_path.unlink(missing_ok=True)
    if not (target / "conf").exists():
        raise RuntimeError("Модель Vosk распакована некорректно")
    return target


def transcribe_wav_sync(wav_bytes: bytes, data_dir: Path) -> str:
    """WAV (16 кГц, моно, 16 бит) → текст. Русский, офлайн."""
    from vosk import KaldiRecognizer, Model

    global _model
    if _model is None:
        _model = Model(str(ensure_model(data_dir)))
    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        if reader.getframerate() != 16000 or reader.getnchannels() != 1:
            raise ValueError("Нужен WAV 16 кГц, моно")
        recognizer = KaldiRecognizer(_model, 16000)
        parts = []
        while True:
            chunk = reader.readframes(4000)
            if not chunk:
                break
            if recognizer.AcceptWaveform(chunk):
                parts.append(json.loads(recognizer.Result()).get("text", ""))
        parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
    return " ".join(p for p in parts if p).strip()


async def transcribe_wav(wav_bytes: bytes, data_dir: Path) -> str:
    return await asyncio.to_thread(transcribe_wav_sync, wav_bytes, data_dir)
