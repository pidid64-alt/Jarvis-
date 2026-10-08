"""Тесты MCP: настройки, транспорт, инструменты и путь через ядро.

Здесь запускается настоящий подставной сервер MCP (`tests/fake_mcp_server.py`)
отдельным процессом — так проверяется весь путь целиком: чтение mcp.toml,
рукопожатие, список инструментов, превращение их в действия, подтверждение и
вызов.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import FakeProviders, isolated_home, make_assistant

from jarvis.core import paths
from jarvis.core.errors import ConfigError, ProviderError
from jarvis.core.mcp import McpRegistry, McpTool, ensure_config_file, parse_servers, validate_args
from jarvis.providers.mcp import (
    HttpTransport,
    McpClient,
    McpError,
    StdioTransport,
    extract_text,
    resolve_env,
)

SERVER = str(Path(__file__).resolve().parent / "fake_mcp_server.py")


def stdio_server(*extra: str, name: str = "fake") -> StdioTransport:
    return StdioTransport(sys.executable, [SERVER, *extra], name=name)


def write_mcp_config(config_dir: Path, *, command: str = "", args: list[str] | None = None,
                     url: str = "", enabled: bool = True, confirm: bool = False,
                     extra: str = "") -> Path:
    """Кладёт mcp.toml в каталог настроек (обычно из paths.config_dir())."""
    path = Path(config_dir) / "mcp.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[mcp]", "enabled = true", "", "[[server]]", 'name = "fake"']
    if url:
        lines.append(f'url = "{url}"')
    else:
        lines.append(f'command = "{command or sys.executable}"')
        parts = ", ".join(f'"{part}"' for part in (args or [SERVER]))
        lines.append(f"args = [{parts}]")
    lines.append(f"enabled = {str(enabled).lower()}")
    lines.append(f"confirm = {str(confirm).lower()}")
    lines.append("timeout_seconds = 10")
    lines.append('description = "Подставной сервер"')
    if extra:
        lines.append(extra)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class ConfigTests(unittest.TestCase):
    """Разбор mcp.toml: понятные ошибки вместо тихих сюрпризов."""

    def test_stdio_and_http_servers(self):
        servers = parse_servers({"server": [
            {"name": "files", "command": "npx", "args": ["-y", "server-filesystem"]},
            {"name": "home", "url": "http://localhost:8080/mcp"},
        ]})
        self.assertEqual([item.kind for item in servers], ["stdio", "http"])
        self.assertEqual(servers[0].args, ["-y", "server-filesystem"])
        self.assertEqual(servers[1].url, "http://localhost:8080/mcp")

    def test_server_without_name_is_rejected(self):
        with self.assertRaises(ConfigError) as caught:
            parse_servers({"server": [{"command": "npx"}]})
        self.assertIn("name", str(caught.exception))

    def test_server_without_command_or_url_is_rejected(self):
        with self.assertRaises(ConfigError) as caught:
            parse_servers({"server": [{"name": "пусто"}]})
        self.assertIn("command", str(caught.exception))

    def test_token_must_be_a_reference(self):
        with self.assertRaises(ConfigError) as caught:
            parse_servers({"server": [{"name": "home", "url": "http://x/mcp",
                                       "headers": {"Authorization": "Bearer секрет-прямо-в-файле"}}]})
        self.assertIn("переменную окружения", str(caught.exception))

    def test_reference_in_headers_is_allowed(self):
        servers = parse_servers({"server": [{"name": "home", "url": "http://x/mcp",
                                             "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}}]})
        self.assertEqual(servers[0].headers["Authorization"], "Bearer ${MCP_TOKEN}")

    def test_example_file_is_created(self):
        with isolated_home():
            path = ensure_config_file()
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("[[server]]", text)
            self.assertEqual(Path(path), paths.mcp_config_path())


class EnvTests(unittest.TestCase):
    """Ключи серверов: только из окружения, и о пропаже сказано вслух."""

    def test_reference_is_resolved(self):
        with mock.patch.dict("os.environ", {"MCP_TEST_TOKEN": "секрет"}):
            self.assertEqual(resolve_env({"TOKEN": "${MCP_TEST_TOKEN}"}), {"TOKEN": "секрет"})

    def test_missing_variable_is_explained(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("MCP_TEST_MISSING", None)
            with self.assertRaises(McpError) as caught:
                resolve_env({"TOKEN": "${MCP_TEST_MISSING}"})
        self.assertIn("MCP_TEST_MISSING", str(caught.exception))

    def test_plain_value_is_kept(self):
        self.assertEqual(resolve_env({"LOG_LEVEL": "info"}), {"LOG_LEVEL": "info"})


class TransportTests(unittest.TestCase):
    """Живой stdio-сервер: рукопожатие, инструменты, вызовы, сбои, таймауты."""

    def setUp(self):
        self.transport = stdio_server()
        self.addCleanup(self.transport.close)
        self.client = McpClient("fake", self.transport, timeout=10)
        self.client.connect()

    def test_handshake_reports_server(self):
        self.assertEqual(self.client.server_info.get("name"), "fake-mcp")
        self.assertTrue(self.client.connected)

    def test_tools_are_listed(self):
        names = [item["name"] for item in self.client.list_tools()]
        self.assertIn("echo", names)
        self.assertIn("sum", names)

    def test_call_returns_text(self):
        self.assertEqual(self.client.call_tool("echo", {"text": "привет"}), "эхо: привет")
        self.assertEqual(self.client.call_tool("sum", {"a": 2, "b": 3}), "5.0")

    def test_tool_error_is_explained(self):
        with self.assertRaises(McpError) as caught:
            self.client.call_tool("boom")
        self.assertIn("всё сломалось", str(caught.exception))

    def test_unknown_method_is_an_error(self):
        with self.assertRaises(McpError):
            self.transport.request("такого/метода", {})

    def test_unknown_tool_is_an_error(self):
        with self.assertRaises(McpError) as caught:
            self.client.call_tool("нет-такого")
        self.assertIn("нет инструмента", str(caught.exception))

    def test_timeout_mentions_seconds(self):
        with self.assertRaises(McpError) as caught:
            self.transport.request("tools/call", {"name": "slow"}, timeout=0.5)
        self.assertIn("не ответил за", str(caught.exception))

    def test_broken_command_is_explained(self):
        transport = StdioTransport("/нет/такой/программы", name="bad")
        with self.assertRaises(McpError) as caught:
            transport.request("initialize", {})
        self.assertIn("не удалось запустить", str(caught.exception))

    def test_dead_process_is_reported(self):
        transport = stdio_server("--die", name="dead")
        with self.assertRaises(McpError):
            transport.request("initialize", {})
        transport.close()

    def test_close_stops_the_server(self):
        process = self.transport.process
        self.transport.close()
        self.assertFalse(self.transport.alive())
        self.assertTrue(process.poll() is not None)

    def test_secret_from_env_is_not_in_logs(self):
        transport = StdioTransport(sys.executable, [SERVER], name="fake",
                                   env={"FAKE_MCP_SECRET": "супер-секрет"})
        client = McpClient("fake", transport, timeout=10)
        self.addCleanup(transport.close)
        client.connect()
        self.assertIn("супер-секрет", client.call_tool("secret"))


class HttpTransportTests(unittest.TestCase):
    """HTTP-транспорт: и обычный JSON, и поток SSE."""

    def test_json_response(self):
        transport = HttpTransport("http://example.test/mcp")
        answer = {"jsonrpc": "2.0", "id": "1", "result": {"ok": True}}
        self.assertEqual(transport._pick_message(json.dumps(answer), "application/json", "1"),
                         answer)

    def test_sse_response(self):
        body = 'event: message\ndata: {"jsonrpc": "2.0", "id": "7", "result": {"tools": []}}\n\n'
        picked = HttpTransport._pick_message(body, "text/event-stream", "7")
        self.assertEqual(picked["result"], {"tools": []})

    def test_session_id_is_remembered(self):
        class Response:
            headers = {"Content-Type": "application/json", "Mcp-Session-Id": "abc"}
            body = b'{"jsonrpc": "2.0", "id": "1", "result": {}}'

        transport = HttpTransport("http://example.test/mcp")
        with mock.patch.object(transport, "client") as client:
            client.post_json.return_value = Response()
            transport.request("initialize", {})
        self.assertEqual(transport.session_id, "abc")

    def test_unreachable_server_is_explained(self):
        transport = HttpTransport("http://127.0.0.1:9/mcp", timeout=1)
        with self.assertRaises(McpError) as caught:
            transport.request("initialize", {})
        self.assertIn("недоступен", str(caught.exception))

    def test_garbage_body_is_reported(self):
        class Response:
            headers = {"Content-Type": "application/json"}
            body = "<html>не json</html>".encode("utf-8")

        transport = HttpTransport("http://example.test/mcp")
        with mock.patch.object(transport, "client") as client:
            client.post_json.return_value = Response()
            with self.assertRaises(McpError) as caught:
                transport.request("initialize", {})
        self.assertIn("JSON-RPC", str(caught.exception))


class ExtractTests(unittest.TestCase):
    """Результат вызова приводится к обычному тексту."""

    def test_text_parts_are_joined(self):
        result = {"content": [{"type": "text", "text": "раз"},
                              {"type": "text", "text": "два"}], "isError": False}
        self.assertEqual(extract_text(result), "раз\nдва")

    def test_error_flag_becomes_exception(self):
        with self.assertRaises(McpError):
            extract_text({"content": [{"type": "text", "text": "плохо"}], "isError": True})

    def test_structured_content_is_serialised(self):
        text = extract_text({"structuredContent": {"temp": 21}})
        self.assertIn("temp", text)

    def test_image_is_marked(self):
        self.assertIn("картинка", extract_text({"content": [{"type": "image",
                                                             "mimeType": "image/png"}]}))


class ArgsTests(unittest.TestCase):
    """Аргументы от модели: только короткие простые значения."""

    def test_simple_values_are_kept(self):
        self.assertEqual(validate_args({"a": 2, "b": "текст", "c": True}),
                         {"a": 2, "b": "текст", "c": True})

    def test_nested_objects_are_dropped(self):
        self.assertEqual(validate_args({"bad": {"вложено": 1}}), {})

    def test_long_values_are_cut(self):
        self.assertEqual(len(validate_args({"q": "я" * 1000})["q"]), 400)

    def test_many_keys_are_limited(self):
        self.assertLessEqual(len(validate_args({f"k{index}": index for index in range(50)})), 8)


class RegistryTests(unittest.TestCase):
    """Реестр MCP: настройки, подключение по требованию, бездействие."""

    def setUp(self):
        self.home = isolated_home()
        self.home.__enter__()
        self.addCleanup(self.home.__exit__, None, None, None)

    def test_without_file_mcp_is_off(self):
        mcp = McpRegistry(None)
        self.assertFalse(mcp.enabled)
        self.assertEqual(mcp.discover(), [])

    def test_tools_are_discovered(self):
        write_mcp_config(paths.config_dir())
        mcp = McpRegistry(None)
        self.addCleanup(mcp.close)
        tools = mcp.discover()
        ids = [tool.action_id for tool in tools]
        self.assertIn("fake.echo", ids)
        self.assertIn("fake.sum", ids)
        summary = next(tool for tool in tools if tool.name == "sum")
        self.assertEqual(summary.required, ["a", "b"])
        self.assertTrue(summary.read_only)
        self.assertTrue(next(tool for tool in tools if tool.name == "destroy").destructive)

    def test_disabled_server_is_not_touched(self):
        write_mcp_config(paths.config_dir(), enabled=False)
        mcp = McpRegistry(None)
        self.addCleanup(mcp.close)
        self.assertEqual(mcp.discover(), [])

    def test_broken_server_is_recorded_not_fatal(self):
        write_mcp_config(paths.config_dir(), command="/нет/такой/программы")
        mcp = McpRegistry(None)
        self.addCleanup(mcp.close)
        self.assertEqual(mcp.discover(), [])
        self.assertIn("fake", mcp.problems)
        self.assertIn("не удалось запустить", mcp.problems["fake"])

    def test_call_and_idle_shutdown(self):
        write_mcp_config(paths.config_dir())
        mcp = McpRegistry(None)
        self.addCleanup(mcp.close)
        self.assertEqual(mcp.call("fake", "echo", {"text": "раз"}), "эхо: раз")
        self.assertIn("fake", mcp.status()["servers"][0]["name"])
        stopped = mcp.stop_if_idle(now=__import__("time").monotonic() + 10_000)
        self.assertEqual(stopped, ["fake"])

    def test_call_to_unknown_server_is_explained(self):
        write_mcp_config(paths.config_dir())
        mcp = McpRegistry(None)
        self.addCleanup(mcp.close)
        with self.assertRaises(ConfigError) as caught:
            mcp.call("нет-такого", "echo", {})
        self.assertIn("нет-такого", str(caught.exception))

    def test_status_hides_nothing_but_shows_no_secrets(self):
        write_mcp_config(paths.config_dir())
        mcp = McpRegistry(None)
        self.addCleanup(mcp.close)
        mcp.discover()
        text = json.dumps(mcp.status(), ensure_ascii=False)
        self.assertIn("fake", text)
        self.assertNotIn("FAKE_MCP_SECRET", text)

    def test_missing_env_variable_is_reported(self):
        write_mcp_config(paths.config_dir(), extra='env = { TOKEN = "${MCP_NOPE_TOKEN}" }')
        mcp = McpRegistry(None)
        self.addCleanup(mcp.close)
        self.assertIn("MCP_NOPE_TOKEN", mcp.missing_env)


class AssistantTests(unittest.TestCase):
    """Путь через ядро: инструменты MCP становятся действиями для модели."""

    def setUp(self):
        self.home = isolated_home()
        self.home.__enter__()
        self.addCleanup(self.home.__exit__, None, None, None)
        write_mcp_config(paths.config_dir())
        self.providers = FakeProviders()
        self.assistant = make_assistant(providers=self.providers)
        self.addCleanup(self.assistant.shutdown)

    def say(self, text, *, confirm=True, answer=None):
        if answer:
            self.providers.llm.answer = answer
        return self.assistant.handle_text(text, confirm_callback=lambda question: confirm)

    def test_tools_appear_only_when_model_needed(self):
        before = self.assistant.registry.get("mcp")
        self.assertIsNone(before, "MCP не должен подключаться до обращения к модели")
        self.providers.llm.answer = '{"actions": [{"action": "speak", "text": "привет"}]}'
        self.say("расскажи что-нибудь о себе")
        self.assertIsNotNone(self.assistant.registry.get("mcp"))

    def test_action_ids_and_descriptions_go_to_model(self):
        self.providers.llm.answer = '{"actions": [{"action": "speak", "text": "ok"}]}'
        self.say("непонятная просьба")
        skill = self.assistant.registry.get("mcp")
        ids = [action.id for action in skill.actions]
        self.assertIn("fake.echo", ids)
        block = "\n".join(f"{action.id}: {action.description}" for action in skill.actions)
        self.assertIn("нужно указать: a, b", block)
        # Подтверждение спрашивает не политика прав, а сам обработчик: только так
        # в вопросе видно, что именно уйдёт серверу. Поэтому «опасных» действий
        # у навыка нет — иначе окно спрашивало бы дважды, а первый раз вслепую.
        by_id = {action.id: action for action in skill.actions}
        self.assertFalse(any(action.is_dangerous for action in by_id.values()))
        self.assertIn("меняет", next(tool.description for tool in self.assistant.mcp.tools()
                                     if tool.name == "destroy").lower())

    def test_model_call_runs_the_tool(self):
        reply = self.say("скажи эхо", answer='{"actions": [{"action": "run", "id": "mcp.fake.echo"}]}')
        self.assertTrue(reply.ok)
        self.assertEqual(reply.text, "эхо: привет")
        self.assertEqual(reply.skill, "mcp")

    def test_arguments_are_confirmed_before_sending(self):
        seen: list[str] = []

        def confirm(question: str) -> bool:
            seen.append(question)
            return True

        self.providers.llm.answer = json.dumps({"actions": [
            {"action": "run", "id": "mcp.fake.sum", "args": {"a": 2, "b": 3}}]})
        reply = self.assistant.handle_text("сложи два и три", confirm_callback=confirm)
        self.assertTrue(reply.ok)
        self.assertEqual(reply.text, "5.0")
        self.assertTrue(seen, "вопрос не задан")
        self.assertIn("a: 2", seen[0])
        self.assertIn("fake", seen[0])

    def test_refusal_stops_the_call(self):
        self.providers.llm.answer = json.dumps({"actions": [
            {"action": "run", "id": "mcp.fake.sum", "args": {"a": 1, "b": 1}}]})
        reply = self.assistant.handle_text("сложи", confirm_callback=lambda question: False)
        self.assertFalse(reply.ok)
        self.assertIn("Отменяю", reply.text)

    def test_destructive_tool_is_confirmed_even_without_arguments(self):
        seen: list[str] = []

        def confirm(question: str) -> bool:
            seen.append(question)
            return False

        self.providers.llm.answer = '{"actions": [{"action": "run", "id": "mcp.fake.destroy"}]}'
        reply = self.assistant.handle_text("испорти данные", confirm_callback=confirm)
        self.assertFalse(reply.ok)
        self.assertIn("Отменяю", reply.text)
        self.assertTrue(seen)
        self.assertIn("без аргументов", seen[0])

    def test_tool_without_arguments_needs_no_confirmation(self):
        def boom(question: str) -> bool:
            raise AssertionError("подтверждение не должно спрашиваться")

        self.providers.llm.answer = '{"actions": [{"action": "run", "id": "mcp.fake.echo"}]}'
        reply = self.assistant.handle_text("эхо", confirm_callback=boom)
        self.assertTrue(reply.ok)

    def test_missing_arguments_are_asked_back(self):
        self.providers.llm.answer = '{"actions": [{"action": "run", "id": "mcp.fake.sum"}]}'
        reply = self.say("сложи числа")
        self.assertFalse(reply.ok)
        self.assertIn("нужны данные", reply.text)
        self.assertIn("a, b", reply.text)

    def test_tool_failure_is_explained(self):
        reply = self.say("сломайся", answer='{"actions": [{"action": "run", "id": "mcp.fake.boom"}]}')
        self.assertFalse(reply.ok)
        self.assertIn("не сработал", reply.text)
        self.assertIn("всё сломалось", reply.text)

    def test_ordinary_actions_cannot_get_arguments_from_model(self):
        self.providers.llm.answer = json.dumps({"actions": [
            {"action": "run", "id": "time_date.now", "args": {"команда": "rm -rf /"}}]})
        reply = self.say("который час")
        # аргументы обычному действию не передаются: ядро просто выполняет его
        self.assertTrue(reply.ok)
        self.assertNotIn("rm -rf", reply.text)

    def test_tools_can_be_listed_without_model(self):
        # фразу разбирает навык: модель не спрашивают, но MCP нужен — значит,
        # инструменты подключаются и здесь
        reply = self.say("какие инструменты mcp")
        self.assertTrue(reply.ok)
        self.assertIn("fake.echo", reply.text)
        self.assertIn("нужны аргументы: a, b", reply.text)

    def test_tool_can_be_called_by_name_without_model(self):
        reply = self.say("вызови инструмент fake.echo")
        self.assertTrue(reply.ok)
        self.assertEqual(reply.text, "эхо: привет")

    def test_arguments_can_be_typed_directly(self):
        # аргументы человек набрал сам — переспрашивать не о чем
        thing = self.say("вызови инструмент fake.sum a=2 b=3")
        self.assertTrue(thing.ok)
        self.assertEqual(thing.text, "5.0")

    def test_direct_call_of_dangerous_tool_still_asks(self):
        def confirm(question: str) -> bool:
            raise AssertionError("подтверждение спрашивается только у опасных действий")

        reply = self.assistant.handle_text("вызови инструмент fake.echo",
                                           confirm_callback=confirm)
        self.assertTrue(reply.ok)

    def test_unknown_tool_name_is_explained(self):
        # имя без слов-отрицаний: «нет» внутри фразы ядро считает отрицанием и
        # такую команду не выполняет (защита от «не надо выключать компьютер»)
        reply = self.say("вызови инструмент xyzzy.tool")
        self.assertFalse(reply.ok)
        self.assertIn("Не знаю инструмент", reply.text)

    def test_broken_server_does_not_break_ordinary_work(self):
        self.assistant.shutdown()
        write_mcp_config(paths.config_dir(), command="/нет/такой/программы")
        self.providers = FakeProviders()
        self.assistant = make_assistant(providers=self.providers)
        # фраза без навыка: именно тут ядро идёт к модели и пробует поднять MCP
        reply = self.say("расскажи сказку на ночь",
                         answer='{"actions": [{"action": "speak", "text": "жили-были"}]}')
        self.assertTrue(reply.ok)
        self.assertIn("жили-были", reply.text)
        self.assertTrue(self.assistant.mcp.problems, "проблема сервера не записана")
        self.assertIsNone(self.assistant.registry.get("mcp"))

    def test_skill_can_be_switched_off_like_any_other(self):
        self.providers.llm.answer = '{"actions": [{"action": "speak", "text": "ок"}]}'
        self.say("непонятная просьба")
        self.assistant.set_skill_enabled("mcp", False)
        self.assertFalse(self.assistant.registry.get("mcp").enabled)
        self.assertNotIn("mcp", [item.id for item in self.assistant.registry.enabled()])
        import tomllib

        disabled = tomllib.loads(paths.config_path().read_text(encoding="utf-8"))["skills"]["disabled"]
        self.assertIn("mcp", disabled)


if __name__ == "__main__":
    unittest.main()
