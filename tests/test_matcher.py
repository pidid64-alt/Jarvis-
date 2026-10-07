"""Тесты сопоставления фраз: точность, неоднозначность, аргументы, опасное."""

from __future__ import annotations

import unittest

from tests.helpers import isolated_home  # noqa: F401  (нужен корень проекта в sys.path)

from jarvis.core.matcher import (
    SpeechBuffer,
    find_exact,
    find_fuzzy,
    has_negation,
    normalize_text,
    plan_fuzzy_multi,
    strip_fillers,
)
from jarvis.core.permissions import PermissionPolicy
from jarvis.core.types import Action, Permissions, Skill


def make_skill(skill_id: str, actions: list[Action]) -> Skill:
    policy = PermissionPolicy()
    for action in actions:
        policy.mark_dangerous(action)
    return Skill(id=skill_id, name=skill_id, description="", actions=actions)


class NormalizeTests(unittest.TestCase):
    def test_case_punctuation_and_yo(self):
        self.assertEqual(normalize_text("  ДЖАРВИС, ещё—раз! "), "джарвис еще раз")

    def test_fillers_stripped_at_edges_only(self):
        self.assertEqual(strip_fillers("Джарвис, покажи версию ядра, пожалуйста"), "версию ядра")
        self.assertEqual(strip_fillers("потом скажи время"), "время")

    def test_negation_detected(self):
        self.assertTrue(has_negation("не выключай компьютер"))
        self.assertFalse(has_negation("выключи компьютер"))


class ExactMatchTests(unittest.TestCase):
    def setUp(self):
        self.skills = [
            make_skill("time_date", [Action(id="time", phrases=["сколько времени", "который час"])]),
            make_skill("power", [Action(id="reboot", phrases=["перезагрузи компьютер"],
                                        confirm=True, tags=["dangerous"])]),
            make_skill("search", [Action(id="search", phrases=["найди"], capture=True)]),
        ]

    def test_phrase_found(self):
        intents = find_exact("сколько времени", self.skills, "linux")
        self.assertEqual([intent.full_id for intent in intents], ["time_date.time"])

    def test_word_boundaries(self):
        skills = [make_skill("x", [Action(id="p", phrases=["пароль"])])]
        self.assertEqual(find_exact("беспарольный вход", skills, "linux"), [])

    def test_cut_phrase_not_matched(self):
        self.assertEqual(find_exact("перезагрузи", self.skills, "linux"), [])

    def test_extra_meaningful_words_rejected(self):
        self.assertEqual(find_exact("сколько времени в токио", self.skills, "linux"), [])

    def test_negation_rejected(self):
        self.assertEqual(find_exact("не перезагружай, сколько времени", self.skills, "linux"), [])

    def test_dangerous_found_but_marked(self):
        intents = find_exact("перезагрузи компьютер", self.skills, "linux")
        self.assertEqual(len(intents), 1)
        self.assertTrue(intents[0].action.is_dangerous)

    def test_ambiguous_phrase_belongs_to_nobody(self):
        skills = [
            make_skill("a", [Action(id="one", phrases=["открой порты"])]),
            make_skill("b", [Action(id="two", phrases=["открой порты"])]),
        ]
        self.assertEqual(find_exact("открой порты", skills, "linux"), [])

    def test_capture_gets_argument(self):
        intents = find_exact("найди погоду в алматы", self.skills, "linux")
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].args.get("query"), "погоду в алматы")

    def test_platform_filter(self):
        skills = [make_skill("win", [Action(id="cmd", phrases=["открой проводник"],
                                            platforms=["windows"])])]
        self.assertEqual(find_exact("открой проводник", skills, "linux"), [])
        self.assertEqual(len(find_exact("открой проводник", skills, "windows")), 1)

    def test_multi_command_order(self):
        skills = [
            make_skill("vol", [Action(id="up", phrases=["громче"])]),
            make_skill("time_date", [Action(id="time", phrases=["сколько времени"])]),
        ]
        intents = find_exact("громче и сколько времени", skills, "linux")
        self.assertEqual([intent.full_id for intent in intents], ["vol.up", "time_date.time"])


class FuzzyMatchTests(unittest.TestCase):
    def setUp(self):
        self.skills = [
            make_skill("time_date", [Action(id="time", phrases=["сколько времени", "который час"])]),
            make_skill("power", [Action(id="reboot", phrases=["перезагрузи компьютер"], confirm=True)]),
        ]

    def test_close_phrase_matched(self):
        intent = find_fuzzy("скока времени", self.skills, "linux")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.full_id, "time_date.time")

    def test_dangerous_never_fuzzy(self):
        self.assertIsNone(find_fuzzy("перезагрузи комп", self.skills, "linux"))

    def test_ambiguous_pair_refused(self):
        skills = [
            make_skill("a", [Action(id="one", phrases=["какая погода сегодня"])]),
            make_skill("b", [Action(id="two", phrases=["какая погода завтра"])]),
        ]
        self.assertIsNone(find_fuzzy("какая погода", skills, "linux"))

    def test_multi_plan_splits_clauses(self):
        skills = [
            make_skill("vol", [Action(id="up", phrases=["сделай громче"])]),
            make_skill("time_date", [Action(id="time", phrases=["который час"])]),
        ]
        plan = plan_fuzzy_multi("сделай громче и патом который час", skills, "linux")
        self.assertEqual(len(plan), 2)


class SpeechBufferTests(unittest.TestCase):
    def test_requires_sustained_speech(self):
        buffer = SpeechBuffer(start_frames=3, min_frames=6, silence_frames=2)
        frame = b"\x00\x00" * 480
        self.assertFalse(buffer.feed(frame, True))   # 1-й кадр речи
        self.assertFalse(buffer.feed(frame, True))   # 2-й
        self.assertFalse(buffer.feed(frame, True))   # 3-й — только теперь «началась»
        self.assertFalse(buffer.feed(frame, True))
        self.assertFalse(buffer.feed(frame, True))
        self.assertFalse(buffer.feed(frame, True))
        self.assertFalse(buffer.feed(frame, False))   # первая тишина
        self.assertTrue(buffer.feed(frame, False))    # вторая — конец фразы
        self.assertTrue(buffer.valid())

    def test_short_noise_rejected(self):
        buffer = SpeechBuffer(start_frames=3, min_frames=6, silence_frames=2)
        frame = b"\x00\x00" * 480
        buffer.feed(frame, True)
        buffer.feed(frame, False)
        self.assertFalse(buffer.valid())


if __name__ == "__main__":
    unittest.main()
