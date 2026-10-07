"""Тесты окна Jarvis: файлы интерфейса, раздача по HTTP и логика страницы.

Настоящий браузер в тестах не запускается, поэтому проверяем три вещи:

1. сервер отдаёт файлы интерфейса (и не отдаёт ничего лишнего);
2. страница не содержит секретов и не тянет ничего из интернета;
3. чистые функции страницы (app.js) ведут себя правильно — их прогоняет node,
   если он есть в системе.

Плюс проверяем запуск окна: поиск браузера и режим приложения.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.helpers import isolated_home, make_assistant

from jarvis.interfaces import webapp
from jarvis.interfaces.api import LocalApi

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEBUI = PROJECT_ROOT / "jarvis" / "interfaces" / "webui"


class FilesTests(unittest.TestCase):
    def test_interface_files_exist(self):
        for name in ("index.html", "app.css", "app.js", "favicon.svg"):
            self.assertTrue((WEBUI / name).is_file(), f"нет файла {name}")

    def test_page_stays_local(self):
        """Ни одного обращения в интернет: только свои файлы и свой API."""
        html = (WEBUI / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("http://", html.replace("http://127.0.0.1", ""))
        self.assertNotIn("https://", html)
        self.assertNotIn("cdn.", html)
        self.assertIn("app.css", html)
        self.assertIn("app.js", html)

    def test_every_element_used_by_script_exists(self):
        """Скрипт не обращается к элементам, которых нет на странице."""
        import re

        html = (WEBUI / "index.html").read_text(encoding="utf-8")
        js = (WEBUI / "app.js").read_text(encoding="utf-8")
        present = set(re.findall(r'id="([^"]+)"', html))
        used = set()
        for match in re.findall(r'(?:getElementById|el)\("([^"]+)"\)', js):
            used.add(match)
        self.assertEqual(sorted(used - present), [], "в app.js есть обращения к несуществующим элементам")

    def test_page_has_no_secrets_and_no_text_from_core(self):
        html = (WEBUI / "index.html").read_text(encoding="utf-8")
        js = (WEBUI / "app.js").read_text(encoding="utf-8")
        for text in (html, js):
            self.assertNotIn("JARVIS_LLM_KEY=", text)
            self.assertNotIn("api.token", text)
        # токен приходит в адресной строке и убирается из неё
        self.assertIn("searchParams.delete(\"token\")", js)
        self.assertIn("sessionStorage", js)

    def test_theme_variables_match_theme_module(self):
        """Цвета страницы совпадают с палитрой темы — иначе окно разъедется."""
        from jarvis.interfaces import theme

        css = (WEBUI / "app.css").read_text(encoding="utf-8")
        for name in ("dark", "light"):
            palette = theme.palette(name)
            for key in ("bg", "surface", "surface2", "border", "field_border", "text", "muted",
                        "accent", "on_accent", "input_bg", "user_bg", "user_text",
                        "assistant_bg", "assistant_text", "ok", "warn", "err", "chip_bg", "chip_text"):
                self.assertIn(palette[key], css, f"в app.css нет цвета {name}.{key} = {palette[key]}")

    def test_scales_match_theme_module(self):
        from jarvis.interfaces import theme

        css = (WEBUI / "app.css").read_text(encoding="utf-8")
        for value in theme.SPACE.values():
            self.assertIn(f"--space", css)
            self.assertRegex(css, rf"--space-[a-z]+: {value}px")
        for kind in ("title", "heading", "body", "caption", "mono"):
            self.assertIn(f"--font-{kind}: {theme.FONTS[kind]['size']}px", css)
        self.assertIn(f"--radius-card: {theme.RADIUS['card']}px", css)

    def test_hidden_wins_over_decorations(self):
        """`hidden` обязан побеждать оформление, иначе элементы не спрятать.

        У окна подтверждения и кнопки «Стоп» в CSS задан `display`, а правила
        страницы перебивают встроенное браузерное правило `[hidden]`. Если
        общее правило убрать, окно подтверждения повиснет на экране с самого
        запуска: кнопки в нём нажимаются, но окно не исчезает.
        """
        css = (WEBUI / "app.css").read_text(encoding="utf-8")
        rule = re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css)
        self.assertIsNotNone(rule, "в app.css нет правила [hidden] { display: none !important }")

    def test_elements_hidden_by_script_have_their_own_display_rules(self):
        """Каждый элемент, который скрипт прячет, действительно чем-то прячется."""
        html = (WEBUI / "index.html").read_text(encoding="utf-8")
        js = (WEBUI / "app.js").read_text(encoding="utf-8")
        css = (WEBUI / "app.css").read_text(encoding="utf-8")
        hidden_ids = set(re.findall(r'el\("([^"]+)"\)\.hidden\s*=', js))
        self.assertTrue(hidden_ids, "скрипт ничего не прячет — проверке нечего делать")
        global_rule = bool(re.search(r"\[hidden\]\s*\{[^}]*display:\s*none", css))
        for element_id in hidden_ids:
            tag = re.search(rf'<[^>]*id="{re.escape(element_id)}"[^>]*>', html)
            self.assertIsNotNone(tag, f"элемента #{element_id} нет в разметке")
            classes = re.search(r'class="([^"]+)"', tag.group(0))
            if not classes or not global_rule:
                continue
            for name in classes.group(1).split():
                # если у класса в CSS задан display — без общего правила элемент не спрятать
                for block in re.findall(rf"\.{re.escape(name)}\s*\{{([^}}]*)\}}", css):
                    if "display" in block:
                        self.assertTrue(
                            global_rule,
                            f"#{element_id} (.{name}) прячется атрибутом hidden, "
                            f"но в CSS задан display — нужно правило [hidden]")

    def test_interface_has_no_heavy_frameworks(self):
        """Никаких библиотек: страница работает на голом браузере."""
        js = (WEBUI / "app.js").read_text(encoding="utf-8")
        for forbidden in ("import ", "require(", "react", "vue", "jquery", "bootstrap"):
            self.assertNotIn(forbidden, js.lower().replace("module.exports", "").replace("export ", ""),
                             f"в app.js появилось «{forbidden}»")


class ServingTests(unittest.TestCase):
    """Раздача интерфейса через локальный API."""

    def setUp(self):
        self.home = isolated_home()
        self.home.__enter__()
        self.assistant = make_assistant()
        self.api = LocalApi(self.assistant, host="127.0.0.1", port=0)
        self.api.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.api.stop()
        self.assistant.shutdown()
        self.home.__exit__(None, None, None)

    def fetch(self, path: str, *, token: str | None = None):
        url = f"{self.api.url}{path}"
        if token:
            url += ("&" if "?" in path else "?") + f"token={token}"
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()

    def test_index_is_served(self):
        status, headers, body = self.fetch("/ui/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Jarvis", body)

    def test_assets_are_served_with_right_types(self):
        for path, kind in (("/ui/app.css", "text/css"), ("/ui/app.js", "javascript"),
                           ("/ui/favicon.svg", "image/svg+xml")):
            status, headers, body = self.fetch(path)
            self.assertEqual(status, 200, path)
            self.assertIn(kind, headers["Content-Type"], path)
            self.assertTrue(body, path)

    def test_ui_does_not_require_token_but_api_does(self):
        # файлы интерфейса открыты: в них нет ничего секретного
        self.assertEqual(self.fetch("/ui/")[0], 200)
        # а данные ядра — только с токеном
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/status")
        self.assertEqual(caught.exception.code, 401)
        status, _, body = self.fetch("/status", token=self.api.token)
        self.assertEqual(status, 200)
        self.assertIn("status", json.loads(body))

    def test_unknown_file_is_not_found(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/ui/no-such-file.html")
        self.assertEqual(caught.exception.code, 404)

    def test_подстановки_путей_не_ломают_раздачу(self):
        """Обращение с «..» не выводит за пределы каталога интерфейса."""
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/ui/..%2fapi.py")
        self.assertEqual(caught.exception.code, 404)

    def test_ui_url_contains_token(self):
        url = self.api.ui_url()
        self.assertTrue(url.endswith(f"/ui/?token={self.api.token}"))
        self.assertIn(self.api.url, url)

    def test_client_activity_is_noted(self):
        self.assertFalse(self.api.client_active())
        self.fetch("/status", token=self.api.token)
        self.assertTrue(self.api.client_active())


class LauncherTests(unittest.TestCase):
    def test_browsers_are_detected_without_errors(self):
        browsers = webapp.find_browsers()
        self.assertIsInstance(browsers, list)
        for name, path in browsers:
            self.assertTrue(name)
            self.assertTrue(Path(path).is_file(), path)

    def test_app_mode_command_is_built(self):
        original = webapp.find_browsers
        webapp.find_browsers = lambda: [("edge", "C:/fake/msedge.exe")]
        try:
            command = webapp.open_window("http://127.0.0.1:1/ui/?token=x", dry_run=True)
            self.assertIn("--app=http://127.0.0.1:1/ui/?token=x", command)
            self.assertIn("msedge.exe", command)
            command = webapp.open_window("http://127.0.0.1:1/ui/?token=x", app_mode=False, dry_run=True)
            self.assertNotIn("--app=", command)
        finally:
            webapp.find_browsers = original

    def test_missing_browser_is_not_a_crash(self):
        original = webapp.find_browsers
        webapp.find_browsers = lambda: []
        try:
            self.assertIsNone(webapp.pick_browser())
            self.assertEqual(webapp.open_window("http://127.0.0.1:1/", dry_run=True), "браузер не найден")
        finally:
            webapp.find_browsers = original

    def test_daemon_log_lives_in_state_dir(self):
        with isolated_home():
            from jarvis.core import paths

            self.assertEqual(webapp.daemon_log_path(), paths.state_dir() / "daemon.log")


class PageLogicTests(unittest.TestCase):
    """Чистые функции страницы: их считает node, если он установлен."""

    node = shutil.which("node") or shutil.which("nodejs")

    def run_node(self, script: str) -> str:
        result = subprocess.run([self.node, "-e", script], capture_output=True, text=True, timeout=30,
                                cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            self.fail(f"node вернул ошибку:\n{result.stderr[-800:]}")
        return result.stdout.strip()

    def test_syntax_is_valid(self):
        if not self.node:
            self.skipTest("node не установлен — проверка синтаксиса пропущена")
        result = subprocess.run([self.node, "--check", str(WEBUI / "app.js")],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])

    def test_pure_helpers(self):
        if not self.node:
            self.skipTest("node не установлен — проверки логики пропущены")
        script = (
            f"const P = require({str(WEBUI / 'app.js')!r});"
            "const out = [];"
            "out.push(P.themeName('system', true));"          # system + тёмная система
            "out.push(P.themeName('system', false));"
            "out.push(P.themeName('light', true));"
            "out.push(P.themeName('чепуха', true));"
            "out.push(P.levelOf('WARNING'));"
            "out.push(P.levelOf('ERROR'));"
            "out.push(P.levelOf(undefined));"
            "out.push(P.bubbleClass('user', true));"
            "out.push(P.bubbleClass('jarvis', false));"
            "out.push(P.bubbleClass('jarvis', true));"
            "out.push(P.providerChips({providers:{llm:{available:false},stt:{available:true}}}).map(c=>c.ok).join(','));"
            "out.push(P.journalRows([{ts:1700000000,level:'warning',source:'skill.power',message:'нужно подтверждение'}])"
            "  .map(r=>r.level+':'+r.source).join(','));"
            "const values={a:1,b:'x',c:true}, original={a:1,b:'y',c:true};"
            "out.push(JSON.stringify(P.changedSettings(values, original)));"
            "out.push(P.t('skills.total', {total: 5, enabled: 4}));"
            "out.push(P.secretName('${MY_KEY}', 'JARVIS_LLM_KEY'));"
            "out.push(P.confirmationText(''));"
            "out.push(P.confirmationText('   '));"
            "out.push(P.confirmationText('Выключить компьютер? Подтверждаете?'));"
            "out.push(P.secretName('просто текст', 'JARVIS_LLM_KEY'));"
            "console.log(out.join('|'));"
        )
        answer = self.run_node(script).split("|")
        self.assertEqual(answer[0], "dark")
        self.assertEqual(answer[1], "light")
        self.assertEqual(answer[2], "light")
        self.assertEqual(answer[3], "dark")
        self.assertEqual(answer[4:7], ["warning", "error", "info"])
        self.assertEqual(answer[7:10], ["bubble user", "bubble error", "bubble assistant"])
        self.assertEqual(answer[10], "false,true,false")
        self.assertEqual(answer[11], "warning:skill.power")
        self.assertEqual(answer[12], '[["b","x"]]')
        self.assertEqual(answer[13], "Навыков: 5 (включено 4)")
        self.assertEqual(answer[14], "MY_KEY")
        # окно подтверждения никогда не бывает пустым
        self.assertIn("Разрешите", answer[15])
        self.assertIn("Разрешите", answer[16])
        self.assertEqual(answer[17], "Выключить компьютер? Подтверждаете?")
        self.assertEqual(answer[18], "JARVIS_LLM_KEY")


class PageRunTests(unittest.TestCase):
    """Страница целиком: app.js исполняется в node поверх настоящего API.

    Браузера в песочнице нет, поэтому страницу запускает крошечный подставной
    DOM (`tests/js/dom.js`), а запросы уходят в настоящий LocalApi. Так
    проверяются все разделы окна: чат, навыки, настройки, журнал, «О программе»
    и окно подтверждения опасного действия.
    """

    node = shutil.which("node") or shutil.which("nodejs")

    def setUp(self):
        if not self.node:
            self.skipTest("node не установлен — прогон страницы пропущен")
        self.home = isolated_home()
        self.home.__enter__()
        self.assistant = make_assistant()
        self.stopped: list[str] = []
        self.api = LocalApi(self.assistant, host="127.0.0.1", port=0,
                            on_shutdown=lambda: self.stopped.append("stop"))
        self.api.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.api.stop()
        self.assistant.shutdown()
        self.home.__exit__(None, None, None)

    def test_all_sections_work(self):
        script = PROJECT_ROOT / "tests" / "js" / "smoke.js"
        result = subprocess.run(
            [self.node, str(script), self.api.url, self.api.token, str(WEBUI)],
            capture_output=True, text=True, timeout=180, cwd=str(PROJECT_ROOT),
        )
        text = result.stdout[result.stdout.find("{"):result.stdout.rfind("}") + 1]
        self.assertTrue(text, f"страница не ответила отчётом:\n{result.stdout[-600:]}\n{result.stderr[-600:]}")
        report = json.loads(text)
        self.assertNotIn("error", report, report.get("error", ""))
        failed = [item for item in report["checks"] if not item["ok"]]
        self.assertEqual(failed, [], f"не прошли проверки: {failed}")
        self.assertGreaterEqual(len(report["checks"]), 20, "проверок подозрительно мало")
        self.assertGreater(report.get("calls", 0), 10, "страница почти не обращалась к ядру")
        # кнопка «Остановить Jarvis» действительно дошла до ядра
        self.assertEqual(self.stopped, ["stop"], "ядро не получило команду остановки из окна")


if __name__ == "__main__":
    unittest.main()
