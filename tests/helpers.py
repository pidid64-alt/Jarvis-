"""Общие помощники тестов: изоляция каталогов и подставные провайдеры."""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@contextlib.contextmanager
def isolated_home():
    """Временный JARVIS_HOME: тесты не трогают настоящие настройки.

    Заодно запоминаем и возвращаем переменные ``JARVIS_*``: сохранение секрета
    в одном тесте не должно влиять на проверки секретов в другом.
    """
    previous = os.environ.get("JARVIS_HOME")
    saved_env = {key: value for key, value in os.environ.items() if key.startswith("JARVIS_")}
    with tempfile.TemporaryDirectory(prefix="jarvis-test-") as folder:
        for key in list(saved_env):
            os.environ.pop(key, None)
        os.environ["JARVIS_HOME"] = folder
        (Path(folder) / "config").mkdir(parents=True, exist_ok=True)
        (Path(folder) / "state").mkdir(parents=True, exist_ok=True)
        from jarvis.core import secrets
        from jarvis.platform import reset_platform

        reset_platform()
        secrets.reset_registry()
        try:
            yield Path(folder)
        finally:
            for key in [item for item in os.environ if item.startswith("JARVIS_")]:
                os.environ.pop(key, None)
            os.environ.update(saved_env)
            if previous is None:
                os.environ.pop("JARVIS_HOME", None)
            else:
                os.environ["JARVIS_HOME"] = previous
            reset_platform()


class FakeProviders:
    """Провайдеры-заглушки: никаких процессов, моделей и сети."""

    def __init__(self, *, voice_enabled: bool = False):
        self.voice_enabled = voice_enabled
        self.spoken: list[str] = []
        self.notifications: list[tuple[str, str]] = []
        self.llm = FakeLLM()
        self.search = FakeSearch()
        self.stt = FakeSTT()
        self.tts = FakeTTS()
        self.recorder = FakeRecorder()
        self.pages = FakePages()

    def speak(self, text: str, language: str | None = None) -> bool:
        self.spoken.append(text)
        return True

    def notify(self, title: str, body: str, urgency: str = "normal") -> bool:
        self.notifications.append((title, body))
        return True

    # -- тот же интерфейс, что у настоящих Providers (см. jarvis/providers) --
    def search_web(self, query: str, limit: int | None = None):
        results = self.search.search(query)
        return results[:limit] if limit else results

    def llm_available(self) -> bool:
        return bool(getattr(self.llm, "enabled", True))

    def ask_model(self, prompt: str, *, system: str | None = None) -> str | None:
        try:
            return self.llm.chat_text([{"role": "user", "content": prompt}])
        except Exception:  # noqa: BLE001
            return None

    def transcribe(self, wav_path) -> str:
        return self.stt.transcribe(wav_path)

    def record(self):
        return self.recorder.record()

    def read_page(self, target: str, max_chars: int | None = None) -> dict:
        page = self.pages.fetch(target)
        if max_chars:
            page = dict(page, text=page["text"][:max_chars])
        return page

    def maintain(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def state(self) -> dict:
        return {"voice_enabled": self.voice_enabled}


class FakePages:
    """Страницы сайтов: заранее заданные ответы, никакой сети."""

    def __init__(self):
        self.pages: dict[str, dict] = {}
        self.error: Exception | None = None
        self.requested: list[str] = []

    def add(self, url: str, *, title: str = "Тестовая страница", text: str = "",
            links: list[dict] | None = None) -> str:
        target = url.split("//")[-1].strip("/")
        self.pages[target] = {"url": url, "title": title, "text": text, "links": links or []}
        return target

    def fetch(self, target: str) -> dict:
        self.requested.append(target)
        if self.error is not None:
            raise self.error
        page = self.pages.get(target.strip().rstrip("/"))
        if page is None:
            from jarvis.core.errors import ProviderError

            raise ProviderError(f"страница {target} недоступна")
        return dict(page)


class FakeLLM:
    def __init__(self, answer: str = '{"actions":[{"action":"speak","text":"Ответ модели"}]}'):
        self.answer = answer
        self.calls: list[list[dict]] = []
        self.enabled = True

    def available(self):
        return (True, "готов") if self.enabled else (False, "выключен")

    def chat(self, messages, max_tokens=None, temperature=None):
        self.calls.append(messages)
        return self.answer

    def chat_text(self, messages, max_tokens=None, temperature=None):
        self.calls.append(messages)
        return self.answer if not self.answer.startswith("{") else "Текстовый ответ"

    def state(self):
        return {"available": self.enabled, "reason": "готов" if self.enabled else "выключен"}


class FakeSearch:
    def __init__(self, snippets=None):
        self.snippets = snippets if snippets is not None else [
            {"title": "Заголовок", "body": "Текст", "href": "https://example.com"}
        ]
        self.queries: list[str] = []

    def search(self, query: str):
        self.queries.append(query)
        if self.snippets is None:
            raise RuntimeError("поиск недоступен")
        return self.snippets

    def available(self):
        return True, "готов"

    def state(self):
        return {"available": True, "reason": "готов"}


class FakeSTT:
    def __init__(self, text: str = "привет"):
        self.text = text

    def transcribe(self, wav_path) -> str:
        return self.text

    def available(self):
        return True, "готов"

    def state(self):
        return {"available": True, "reason": "готов"}


class FakeTTS:
    def synthesize(self, text: str) -> bytes:
        return b"RIFF"

    def speak(self, text: str) -> bool:
        return True

    def available(self):
        return True, "готов"

    def state(self):
        return {"available": True, "reason": "готов"}


class FakeRecorder:
    def __init__(self, path=None):
        self.path = path

    def record(self):
        if self.path is None:
            return None
        self.path.write_bytes(b"RIFF")
        return self.path

    def available(self):
        return True, "готов"

    def state(self):
        return {"available": True, "reason": "готов"}


def make_assistant(*, providers=None, config=None):
    """Ассистент с подставными провайдерами — без моделей, сети и процессов."""
    from jarvis.core.assistant import Assistant
    from jarvis.core.config import Config

    return Assistant(config or Config.load(), providers=providers or FakeProviders())
