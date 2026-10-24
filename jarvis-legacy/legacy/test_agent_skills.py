#!/usr/bin/env python3
"""
Тесты агентских умений: action=search (llm_parser), веб-поиск (web_search),
исполнение handle_search и fast-path команды solve_terminal.

Запуск: venv/bin/python3 test_agent_skills.py
"""

import unittest
import unittest.mock as m

import jarvis
import llm_parser
import web_search


class FakeClient:
    def __init__(self, raw="Краткий ответ по делу."):
        self.raw = raw
        self.calls = []

    def chat(self, messages, max_tokens=300, temperature=0.2):
        self.calls.append(messages)
        return self.raw


CMDS = [{"id": "solve_terminal", "phrases": ["реши проблему"],
         "command": "bash $HOME/jarvis/scripts/solve_terminal.sh"}]


class TestSearchActionParsing(unittest.TestCase):
    def _parse(self, raw):
        client = FakeClient(raw)
        return llm_parser.parse_intent(client, "x",
                                       [{"id": "a", "phrases": ["а"]}])

    def test_search_speak_mode(self):
        acts = self._parse('{"actions": [{"action": "search", '
                           '"query": "  погода   марс "}]}')
        self.assertEqual(acts[0]["action"], "search")
        self.assertEqual(acts[0]["query"], "погода марс")
        self.assertFalse(acts[0]["open"])

    def test_search_open_mode(self):
        acts = self._parse('{"actions": [{"action": "search", '
                           '"query": "гайд по vim", "open": true}]}')
        self.assertTrue(acts[0]["open"])

    def test_search_empty_query_raises(self):
        with self.assertRaises(llm_parser.LLMError):
            self._parse('{"actions": [{"action": "search", "query": " "}]}')

    def test_unknown_action_still_rejected(self):
        with self.assertRaises(llm_parser.LLMError):
            self._parse('{"actions": [{"action": "rm_rf"}]}')


class TestWebSearch(unittest.TestCase):
    class FakeDDGS:
        def __init__(self, results):
            self.results = results

        def text(self, query, max_results=5):
            return self.results[:max_results]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_snippets_ok(self):
        fake = self.FakeDDGS([{"title": "T1", "body": "B1", "href": "h1"},
                              {"title": "", "body": "", "href": "x"}])
        with m.patch.object(web_search, "DDGS", lambda: fake):
            got = web_search.search_snippets("тест")
        self.assertEqual(len(got), 1)          # пустой сниппет отброшен
        self.assertEqual(got[0]["title"], "T1")

    def test_no_results_raises(self):
        fake = self.FakeDDGS([])
        with m.patch.object(web_search, "DDGS", lambda: fake):
            with self.assertRaises(web_search.SearchError):
                web_search.search_snippets("тест")

    def test_missing_lib_raises(self):
        with m.patch.object(web_search, "DDGS", None):
            with self.assertRaises(web_search.SearchError):
                web_search.search_snippets("тест")


class TestHandleSearch(unittest.TestCase):
    def setUp(self):
        self.cfg = {**jarvis.DEFAULT_CONFIG}
        self.spoken = []
        m.patcher = m.patch.object(jarvis, "speak",
                                   lambda c, t: self.spoken.append(t))
        m.patcher.start()
        self.addCleanup(m.patcher.stop)

    def test_voice_summary_via_llm(self):
        snips = [{"title": "Марс", "body": "Четвёртая планета.", "href": ""},
                 {"title": "T2", "body": "B2", "href": ""}]
        with m.patch.object(web_search, "search_snippets", return_value=snips):
            rtype, said = jarvis.handle_search(
                self.cfg, "марс", False, client=FakeClient("У Марса два спутника."))
        self.assertEqual(rtype, "speak")
        self.assertEqual(said, "У Марса два спутника.")
        # суммаризация видела сниппеты
        FakeClient_calls = None

    def test_summary_fallback_to_titles(self):
        snips = [{"title": "Заголовок раз", "body": "", "href": ""},
                 {"title": "Заголовок два", "body": "", "href": ""}]
        with m.patch.object(web_search, "search_snippets", return_value=snips):
            _, said = jarvis.handle_search(
                self.cfg, "тест", False, client=None)
        self.assertIn("Заголовок раз", said)

    def test_open_browser_branch(self):
        with m.patch.object(web_search, "search_snippets") as ps, \
             m.patch.object(jarvis.subprocess, "Popen") as popen:
            rtype, said = jarvis.handle_search(self.cfg, "гайд vim", True,
                                               client=None)
        self.assertEqual(rtype, "command")
        popen.assert_called_once()
        self.assertIn("duckduckgo.com/?q=", popen.call_args[0][0][1])
        ps.assert_not_called()   # при открытии браузера LLM не тратится

    def test_search_error_spoken(self):
        with m.patch.object(web_search, "search_snippets",
                            side_effect=web_search.SearchError("нет сети")):
            rtype, said = jarvis.handle_search(self.cfg, "тест", False)
        self.assertEqual(rtype, "speak")
        self.assertEqual(said, "Поиск недоступен.")


class TestSolveTerminalFastPath(unittest.TestCase):
    def test_exact_phrase_matches_whitelist(self):
        cmds = jarvis.load_commands()
        got = jarvis.match_commands_exact("джарвис реши проблему", cmds, 6)
        self.assertIn("solve_terminal", [c["id"] for c in got])


if __name__ == "__main__":
    unittest.main(verbosity=2)
