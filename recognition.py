"""Local recognition helpers. Text selects whitelist entries, never shell code."""
import re
import unicodedata
from collections import deque


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def phrase_spans(text: str, phrase: str):
    return ((m.start(), m.end()) for m in re.finditer(
        r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text))


def has_negation(text: str) -> bool:
    # Conservative: let the semantic parser handle the whole request instead.
    return bool(set(normalize_text(text).split()) & {"не", "нет", "нельзя", "отмена", "отмени"})


def command_text(text: str) -> str:
    """Strip only boundary fillers; keep objects, verbs and internal word order."""
    words = normalize_text(text).split()
    fillers = {"джарвис", "jarvis", "пожалуйста"}
    while words and words[0] in fillers:
        words.pop(0)
    while words and words[-1] in fillers:
        words.pop()
    return " ".join(words)


class SpeechBuffer:
    """30ms VAD frames: require sustained speech and retain the onset pre-roll."""
    def __init__(self, start_frames=3, min_frames=6, silence_frames=30, preroll_frames=10):
        self.start_frames = max(1, start_frames)
        self.min_frames = max(self.start_frames, min_frames)
        self.silence_frames = max(1, silence_frames)
        self.preroll = deque(maxlen=max(preroll_frames, self.start_frames))
        self.started = False
        self.speech_frames = 0
        self.silence_run = 0
        self.audio = bytearray()

    def feed(self, frame: bytes, is_speech: bool) -> bool:
        """Return True when the end-of-speech silence has elapsed."""
        if not self.started:
            self.preroll.append((frame, is_speech))
            if sum(speech for _, speech in self.preroll) >= self.start_frames:
                self.started = True
                self.speech_frames = sum(speech for _, speech in self.preroll)
                self.audio.extend(b"".join(f for f, _ in self.preroll))
        else:
            self.audio.extend(frame)
            self.speech_frames += bool(is_speech)
            self.silence_run = 0 if is_speech else self.silence_run + 1
        return self.started and self.silence_run >= self.silence_frames

    @property
    def valid(self) -> bool:
        return self.started and self.speech_frames >= self.min_frames
