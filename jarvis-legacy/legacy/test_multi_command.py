#!/usr/bin/env python3
"""
Тесты мультикоманд: массив действий от LLM (llm_parser.parse_intent)
и точный fast-path matcher (jarvis.match_commands_exact).

Запуск: venv/bin/python3 test_multi_command.py
"""

import unittest

from llm_client import LLMError
import llm_parser
import jarvis


CMDS = [
    {"id": "open_discord", "phrases": ["открой дискорд"], "response": "Открываю."},
    {"id": "weather", "phrases": ["какая погода", "погода"], "response": "Погода:"},
    {"id": "system_status", "phrases": ["статус системы"], "response": "Статус:"},
    {"id": "reboot", "phrases": ["перезагрузи компьютер"],
     "tags": ["dangerous"], "confirm": True},
]


class FakeClient:
    """Подмена LLMClient.chat — возвращает заранее заданный сырой ответ."""
    def __init__(self, raw):
        self.raw = raw

    def chat(self, messages, max_tokens=300, temperature=0.2):
        return self.raw


class TestParseIntentMulti(unittest.TestCase):
    def test_array_two_commands(self):
        raw = ('{"actions": [{"action": "command", "id": "open_discord"},'
               ' {"action": "command", "id": "weather"}]}')
        acts = llm_parser.parse_intent(FakeClient(raw), "x", CMDS)
        self.assertEqual([a["action"] for a in acts], ["command", "command"])
        self.assertEqual([a["id"] for a in acts], ["open_discord", "weather"])

    def test_legacy_single_object(self):
        raw = '{"action": "speak", "text": "привет"}'
        acts = llm_parser.parse_intent(FakeClient(raw), "x", CMDS)
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["action"], "speak")

    def test_bare_array(self):
        raw = '[{"action": "speak", "text": "а"}, {"action": "ask", "text": "б"}]'
        acts = llm_parser.parse_intent(FakeClient(raw), "x", CMDS)
        self.assertEqual([a["action"] for a in acts], ["speak", "ask"])

    def test_fenced_json_array_with_prose(self):
        raw = 'Вот ответ:\n```json\n{"actions": [{"action": "speak", "text": "ок"}]}\n```'
        acts = llm_parser.parse_intent(FakeClient(raw), "x", CMDS)
        self.assertEqual(len(acts), 1)

    def test_cap_max_actions(self):
        many = ",".join('{"action": "speak", "text": "т%d"}' % i
                        for i in range(llm_parser.MAX_ACTIONS + 2))
        acts = llm_parser.parse_intent(
            FakeClient('{"actions": [%s]}' % many), "x", CMDS)
        self.assertEqual(len(acts), llm_parser.MAX_ACTIONS)

    def test_unknown_id_raises(self):
        raw = '{"actions": [{"action": "command", "id": "hack_the_planet"}]}'
        with self.assertRaises(LLMError):
            llm_parser.parse_intent(FakeClient(raw), "x", CMDS)

    def test_empty_actions_raises(self):
        with self.assertRaises(LLMError):
            llm_parser.parse_intent(FakeClient('{"actions": []}'), "x", CMDS)

    def test_dangerous_forces_confirmation(self):
        raw = '{"actions": [{"action": "command", "id": "reboot"}]}'
        acts = llm_parser.parse_intent(FakeClient(raw), "x", CMDS)
        self.assertTrue(acts[0]["needs_confirmation"])

    def test_rambler_text_without_json_raises(self):
        raw = 'We need to parse user request. Likely they want to open Discord.'
        with self.assertRaises(LLMError):
            llm_parser.parse_intent(FakeClient(raw), "x", CMDS)


class TestMatchCommandsExact(unittest.TestCase):
    def test_two_non_overlapping_in_spoken_order(self):
        got = jarvis.match_commands_exact(
            "открой дискорд и какая погода", CMDS, 6)
        self.assertEqual([c["id"] for c in got],
                         ["open_discord", "weather"])

    def test_overlap_longest_phrase_wins(self):
        # "какая погода" длиннее "погода" — должна победить она (один запуск)
        got = jarvis.match_commands_exact("а какая погода будет", CMDS, 6)
        self.assertEqual([c["id"] for c in got], ["weather"])

    def test_dedup_same_command(self):
        got = jarvis.match_commands_exact("погода, скажи какая погода", CMDS, 6)
        self.assertEqual([c["id"] for c in got], ["weather"])

    def test_extra_words_over_limit_rejected(self):
        long_text = ("друг мой давай ты вот сначала покажешь мне статус "
                     "системы а потом мы уже решим что делать дальше с этим")
        got = jarvis.match_commands_exact(long_text, CMDS, 6)
        self.assertEqual(got, [])

    def test_extra_words_within_limit_accepted(self):
        got = jarvis.match_commands_exact(
            "джарвис, открой дискорд пожалуйста", CMDS, 6)
        self.assertEqual([c["id"] for c in got], ["open_discord"])

    def test_empty_text(self):
        self.assertEqual(jarvis.match_commands_exact("", CMDS, 6), [])
        self.assertEqual(jarvis.match_commands_exact("  ", CMDS, 6), [])

    def test_matcher_still_single_result(self):
        cmd, score = jarvis.match_command("открой дискорд и какая погода",
                                          CMDS, 0.6)
        self.assertIsNotNone(cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
