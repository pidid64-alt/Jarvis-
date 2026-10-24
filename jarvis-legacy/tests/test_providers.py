"""Тесты провайдеров: сетевые сбои, повторы, предохранитель и защита ключа."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests.helpers import isolated_home  # noqa: F401

from jarvis.core.errors import ProviderError, ProviderTimeoutError, SecretMissingError
from jarvis.providers.http import CircuitBreaker, HttpClient
from jarvis.providers.llm import LLMProvider


class _Handler(BaseHTTPRequestHandler):
    """Крошечный сервер: отвечает так, как скажет очередь сценария."""

    responses: list[tuple[int, dict]] = []
    seen_bodies: list[dict] = []
    fail_times = 0
    delay = 0.0

    def do_POST(self):  # noqa: N802 - так требует http.server
        # Сначала читаем и записываем тело, и только потом «думаем»: иначе
        # незавершённый запрос не попадёт в счёт и тест не отличит повтор от
        # одного запроса. Пауза нужна, чтобы вызвать таймаут у клиента.
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            _Handler.seen_bodies.append(json.loads(body) if body else {})
        except ValueError:
            _Handler.seen_bodies.append({"raw": body})
        if _Handler.delay:
            import time
            time.sleep(_Handler.delay)

        if _Handler.fail_times > 0:
            _Handler.fail_times -= 1
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "внутренняя ошибка"}).encode("utf-8"))
            return

        status, payload = _Handler.responses.pop(0) if _Handler.responses else (200, {})
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, *args):  # тишина в тестах
        pass


def start_server() -> tuple[HTTPServer, str]:
    from http.server import ThreadingHTTPServer

    # потоки: тесты про повторы шлют второй запрос, пока первый ещё «думает»
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def chat_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 7}}


class LLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = start_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.responses = []
        _Handler.seen_bodies = []
        _Handler.fail_times = 0

    def provider(self, **kwargs) -> LLMProvider:
        options = dict(base_url=self.url, api_key="sk-test-abcdef123456", model="test-model",
                       timeout=3.0, retries=1)
        options.update(kwargs)
        return LLMProvider(**options)

    # --------------------------------------------------------------- проверки
    def test_no_key_means_not_ready_and_clear_error(self):
        llm = self.provider(api_key="")
        ready, reason = llm.available()
        self.assertFalse(ready)
        with self.assertRaises(SecretMissingError):
            llm.chat([{"role": "user", "content": "привет"}])

    def test_no_model_means_not_ready(self):
        ready, reason = self.provider(model="").available()
        self.assertFalse(ready)
        self.assertIn("модель", reason)

    def test_state_hides_key(self):
        state = self.provider().state()
        self.assertNotIn("sk-test-abcdef123456", json.dumps(state, ensure_ascii=False))
        self.assertTrue(state["key_masked"].startswith("sk-t"))
        self.assertNotIn("abcdef123456", state["key_masked"])

    def test_json_mode_and_text_mode(self):
        _Handler.responses = [(200, chat_response('{"actions": []}'))]
        llm = self.provider()
        self.assertEqual(llm.chat([{"role": "user", "content": "привет"}]), '{"actions": []}')
        self.assertIn("response_format", _Handler.seen_bodies[-1])

        _Handler.responses = [(200, chat_response("обычный текст"))]
        self.assertEqual(llm.chat_text([{"role": "user", "content": "привет"}]), "обычный текст")
        self.assertNotIn("response_format", _Handler.seen_bodies[-1])

    def test_retry_after_server_error(self):
        _Handler.fail_times = 1
        _Handler.responses = [(200, chat_response("получилось"))]
        llm = self.provider(retries=2)
        self.assertEqual(llm.chat_text([{"role": "user", "content": "привет"}]), "получилось")
        self.assertEqual(len(_Handler.seen_bodies), 2)

    def test_timeout_is_reported_as_timeout(self):
        llm = self.provider(timeout=0.05, retries=0)
        _Handler.delay = 0.3
        _Handler.responses = [(200, chat_response("поздно"))]
        try:
            with self.assertRaises((ProviderTimeoutError, ProviderError)):
                llm.chat_text([{"role": "user", "content": "привет"}])
        finally:
            _Handler.delay = 0.0

    def test_breaker_opens_after_repeated_failures(self):
        llm = self.provider(retries=0)
        llm.client.breaker = CircuitBreaker("test", threshold=2, cooldown=60)
        _Handler.fail_times = 2
        for _ in range(2):
            with self.assertRaises(ProviderError):
                llm.chat_text([{"role": "user", "content": "привет"}])
        ready, reason = llm.available()
        self.assertFalse(ready)
        self.assertIn("временно", reason)
        # пока предохранитель открыт, запросы даже не уходят
        before = len(_Handler.seen_bodies)
        with self.assertRaises(ProviderError):
            llm.chat_text([{"role": "user", "content": "привет"}])
        self.assertEqual(len(_Handler.seen_bodies), before)

    def test_http_client_returns_json(self):
        _Handler.responses = [(200, {"ok": True})]
        client = HttpClient(timeout=3.0, retries=0)
        result = client.post_json(f"{self.url}/x", {"a": 1})
        self.assertEqual(result.json(), {"ok": True})
        self.assertEqual(_Handler.seen_bodies[-1], {"a": 1})


class CircuitBreakerTests(unittest.TestCase):
    def test_threshold_and_cooldown(self):
        breaker = CircuitBreaker("x", threshold=2, cooldown=0.05)
        self.assertTrue(breaker.allow())
        breaker.record_failure("раз")
        self.assertTrue(breaker.allow())
        breaker.record_failure("два")
        self.assertFalse(breaker.allow())
        import time
        time.sleep(0.06)
        self.assertTrue(breaker.allow())
        self.assertEqual(breaker.failures, 0)

    def test_success_resets(self):
        breaker = CircuitBreaker("x", threshold=1)
        breaker.record_failure("раз")
        breaker.record_success()
        self.assertTrue(breaker.allow())


class SearchProviderTests(unittest.TestCase):
    def test_empty_query_rejected(self):
        from jarvis.providers.search import WebSearch

        search = WebSearch(max_results=3)
        with self.assertRaises(ProviderError):
            search.search("   ")

    def test_state_has_no_secrets(self):
        from jarvis.providers.search import WebSearch

        state = json.dumps(WebSearch(max_results=3).state(), ensure_ascii=False)
        self.assertNotIn("key", state.lower())


if __name__ == "__main__":
    unittest.main()

class RealProvidersSurfaceTests(unittest.TestCase):
    """Сторож за «удобствами для навыков» в настоящих провайдерах.

    Именно здесь пряталась причина всех «Сервис не ответил»: метод
    ``search_web`` случайно оказался объявлен свойством, и любой поиск падал с
    TypeError ещё до сети. Тесты со заглушками этого не видели — у заглушки
    метод самый обычный.
    """

    SHORTCUTS = ("search_web", "read_page", "llm_available", "ask_model", "speak", "notify",
                 "record", "transcribe", "state", "maintain", "stop")

    def test_no_method_is_a_property(self):
        import ast
        import pathlib as pathlib_module

        root = pathlib_module.Path(__file__).resolve().parents[1] / "jarvis"
        found: list[str] = []
        for file in root.rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                if any(ast.unparse(item) == "property" for item in node.decorator_list):
                    # свойство с аргументами (кроме self) — ошибка: вызывать его нельзя
                    if len(node.args.args) > 1 or node.args.kwonlyargs:
                        found.append(f"{file.relative_to(root.parent)}:{node.lineno} {node.name}")
        self.assertEqual(found, [], "методы объявлены свойствами и сломают навыки: " + ", ".join(found))

    @staticmethod
    def real_providers():
        """Настоящие провайдеры (не заглушки) в отдельном каталоге настроек."""
        from jarvis.core.assistant import create_assistant

        return create_assistant().providers

    def test_shortcuts_are_callable(self):
        import inspect

        from jarvis.providers import Providers

        with isolated_home():
            providers = self.real_providers()
            self.assertIsInstance(providers, Providers)
            for name in self.SHORTCUTS:
                attribute = inspect.getattr_static(Providers, name)
                self.assertNotIsInstance(attribute, property, f"{name} объявлен свойством")
                self.assertTrue(callable(getattr(providers, name)), f"{name} нельзя вызвать")

    def test_search_web_reaches_the_search_provider(self):
        from tests.helpers import FakeSearch

        with isolated_home():
            providers = self.real_providers()
            providers._search = FakeSearch()
            found = providers.search_web("тихоходки", limit=2)
            self.assertTrue(found)
            self.assertLessEqual(len(found), 2)
            self.assertIn("тихоходки", providers.search.queries)

    def test_read_page_uses_the_page_reader(self):
        from tests.helpers import FakePages

        with isolated_home():
            providers = self.real_providers()
            pages = FakePages()
            pages.add("https://example.com", title="Пример", text="текст страницы")
            providers._pages = pages
            page = providers.read_page("example.com")
            self.assertEqual(page["title"], "Пример")
            self.assertEqual(providers.read_page("example.com", max_chars=4)["text"], "текс")
            with self.assertRaises(ProviderError):
                providers.read_page("нет-такого-сайта.example")


class SearxSearchTests(unittest.TestCase):
    """Свой поисковик: там, где DuckDuckGo недоступен, выручает SearxNG."""

    @staticmethod
    def make(**kwargs):
        from jarvis.providers.search import WebSearch

        return WebSearch(**kwargs)

    def test_results_come_from_instance(self):
        from unittest import mock

        search = self.make(instance="http://localhost:8888/", engine="auto")
        payload = {"results": [{"title": "Квантовая запутанность", "content": "Связь частиц",
                                "url": "https://example.org/quantum"}]}

        class Response:
            body = json.dumps(payload).encode("utf-8")

        with mock.patch.object(search, "client") as client:
            client.get.return_value = Response()
            results = search.search("квантовая запутанность")
        self.assertEqual(results[0]["title"], "Квантовая запутанность")
        self.assertEqual(results[0]["href"], "https://example.org/quantum")
        called = client.get.call_args[0][0]
        self.assertTrue(called.startswith("http://localhost:8888/search?"))
        self.assertIn("format=json", called)

    def test_unreachable_instance_is_named(self):
        from unittest import mock

        search = self.make(instance="http://localhost:8888", engine="searx")
        with mock.patch.object(search, "client") as client:
            client.get.side_effect = OSError("Connection refused")
            with self.assertRaises(ProviderError) as caught:
                search.search("что-нибудь")
        self.assertIn("не ответил", str(caught.exception))
        self.assertIn("Connection refused", str(caught.exception))

    def test_searx_without_address_asks_for_it(self):
        search = self.make(engine="searx")
        ready, reason = search.available()
        self.assertFalse(ready)
        self.assertIn("search.instance", reason)
        with self.assertRaises(ProviderError):
            search.search("что-нибудь")

    def test_broken_json_is_explained(self):
        from unittest import mock

        search = self.make(instance="http://localhost:8888", engine="searx")

        class Response:
            body = "<html>это не json</html>".encode("utf-8")

        with mock.patch.object(search, "client") as client:
            client.get.return_value = Response()
            with self.assertRaises(ProviderError) as caught:
                search.search("что-нибудь")
        self.assertIn("json", str(caught.exception))


class TimeoutRetryTests(unittest.TestCase):
    """Таймаут не повторяем: локальная модель в этот момент ещё считает ответ.

    Если повторить запрос, её сервер отменит текущую задачу и начнёт заново —
    именно это видно в журнале llama.cpp как отмена задачи.
    """

    def setUp(self):
        _Handler.responses = []
        _Handler.seen_bodies = []
        _Handler.fail_times = 0
        _Handler.delay = 0.0
        self.server, self.url = start_server()
        self.addCleanup(self.server.shutdown)

    def test_timeout_is_not_retried_when_disabled(self):
        _Handler.delay = 1.5
        client = HttpClient(timeout=0.4, retries=2, retry_timeouts=False)
        with self.assertRaises(ProviderTimeoutError):
            client.post_json(f"{self.url}/slow", {"x": 1})
        self.assertEqual(len(_Handler.seen_bodies), 1, "запрос ушёл повторно")

    def test_timeout_is_retried_by_default(self):
        _Handler.delay = 1.5
        client = HttpClient(timeout=0.4, retries=1)
        with self.assertRaises(ProviderTimeoutError):
            client.post_json(f"{self.url}/slow", {"x": 1})
        self.assertEqual(len(_Handler.seen_bodies), 2, "обычный сервис должен получить повтор")

    def test_llm_timeout_message_explains_what_to_do(self):
        _Handler.delay = 1.5
        provider = LLMProvider(base_url=self.url, api_key="k", model="m", timeout=0.4, retries=2)
        with self.assertRaises(ProviderTimeoutError) as caught:
            provider.chat([{"role": "user", "content": "привет"}])
        self.assertIn("не ответила", str(caught.exception))
        self.assertIn("Ожидание ответа модели", getattr(caught.exception, "details", ""))
        self.assertEqual(len(_Handler.seen_bodies), 1, "LLM не должен повторять по таймауту")


class MicrophoneTests(unittest.TestCase):
    """Микрофон: сбой звуковой системы должен объясняться словами, а не «internal»."""

    @staticmethod
    def _recorder():
        from jarvis.providers.audio import Recorder

        return Recorder(beep=False)

    def test_stream_failure_names_reason(self):
        import contextlib
        from unittest import mock

        @contextlib.contextmanager
        def broken(sample_rate):
            raise OSError("No Default Input Device Available")
            yield  # pragma: no cover

        with mock.patch("jarvis.providers.audio.get_platform") as platform, \
                mock.patch.object(self._recorder(), "available", return_value=(True, "готов")):
            platform.return_value.mic_stream = broken
            recorder = self._recorder()
            with mock.patch.object(recorder, "available", return_value=(True, "готов")):
                with self.assertRaises(ProviderError) as caught:
                    recorder.record()
        text = str(caught.exception)
        self.assertIn("No Default Input Device", text)
        self.assertIn("микрофон", text)
        self.assertIn("разрешён", text)

    def test_windows_without_microphone_explains(self):
        import sys
        import types
        from unittest import mock

        fake_sd = types.ModuleType("sounddevice")
        fake_sd.query_devices = lambda: [{"name": "Мониторы", "max_input_channels": 0}]
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}), \
                mock.patch("jarvis.providers.audio.get_platform") as platform:
            platform.return_value.name = "windows"
            from jarvis.providers.audio import Recorder

            ready, reason = Recorder.available(self._recorder())
        self.assertFalse(ready)
        self.assertIn("не видит ни одного микрофона", reason)

    def test_windows_with_microphone_is_ready(self):
        import sys
        import types
        from unittest import mock

        fake_sd = types.ModuleType("sounddevice")
        fake_sd.query_devices = lambda: [
            {"name": "Микрофон (Realtek)", "max_input_channels": 2}]
        with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}), \
                mock.patch("jarvis.providers.audio.get_platform") as platform:
            platform.return_value.name = "windows"
            from jarvis.providers.audio import Recorder

            ready, reason = Recorder.available(self._recorder())
        self.assertTrue(ready)
        self.assertIn("Realtek", reason)


class VoiceFlagTests(unittest.TestCase):
    def test_voice_enabled_is_a_plain_flag(self):
        from tests.helpers import make_assistant

        with isolated_home():
            assistant = make_assistant()
            providers = assistant.providers
            self.assertIsInstance(providers.voice_enabled, bool)
        self.assertIn(providers.voice_enabled, (True, False))
