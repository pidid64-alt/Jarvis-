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
        if _Handler.delay:
            import time
            time.sleep(_Handler.delay)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            _Handler.seen_bodies.append(json.loads(body) if body else {})
        except ValueError:
            _Handler.seen_bodies.append({"raw": body})

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
    server = HTTPServer(("127.0.0.1", 0), _Handler)
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
