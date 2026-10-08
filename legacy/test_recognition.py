"""Offline regression tests; no microphone, desktop, LLM or STT server needed."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis
from recognition import SpeechBuffer, normalize_text


CMDS = [
    {"id": "open", "phrases": ["открой дискорд"]},
    {"id": "close", "phrases": ["закрой дискорд"]},
    {"id": "weather", "phrases": ["какая погода", "погода"]},
    {"id": "reboot", "phrases": ["перезагрузи компьютер"], "tags": ["dangerous"]},
]


class RecognitionTests(unittest.TestCase):
    def test_normalize_unicode_punctuation(self):
        self.assertEqual(normalize_text("  ДЖАРВИС, ещё—раз! "), "джарвис еще раз")

    def test_word_boundaries_not_substrings(self):
        commands = [{"id": "x", "phrases": ["пароль"]}]
        self.assertEqual(jarvis.match_commands_exact("беспарольный", commands, 6), [])
        self.assertIsNone(jarvis.match_command("беспарольный", commands, .8)[0])

    def test_punctuation_in_phrase(self):
        got = jarvis.match_commands_exact("Джарвис, открой — дискорд, пожалуйста!", CMDS, 6)
        self.assertEqual([c["id"] for c in got], ["open"])

    def test_yo_normalized(self):
        got = jarvis.match_commands_exact("ещё раз", [{"id": "x", "phrases": ["еще раз"]}], 0)
        self.assertEqual(got[0]["id"], "x")

    def test_fillers_do_not_lower_fuzzy_score(self):
        cmd, score = jarvis.match_command("Джарвис открой дискор пожалуйста", CMDS, .72)
        self.assertEqual(cmd["id"], "open")
        self.assertGreater(score, .9)

    def test_close_candidates_do_not_execute(self):
        commands = [{"id": "a", "phrases": ["открой кот"]},
                    {"id": "b", "phrases": ["открой кит"]}]
        self.assertIsNone(jarvis.match_command("открой кут", commands, .6)[0])

    def test_same_phrase_different_ids_ambiguous(self):
        commands = [{"id": "a", "phrases": ["привет"]},
                    {"id": "b", "phrases": ["привет"]}]
        self.assertEqual(jarvis.match_commands_exact("привет", commands, 6), [])
        self.assertIsNone(jarvis.match_command("привет", commands, .6)[0])

    def test_negation_never_matches_locally(self):
        for text in ("не открой дискорд", "открой дискорд нет отмена", "не перезагрузи компьютер"):
            with self.subTest(text=text):
                self.assertEqual(jarvis.match_commands_exact(text, CMDS, 6), [])
                self.assertIsNone(jarvis.match_command(text, CMDS, .6)[0])
                self.assertEqual(jarvis.match_commands_local(text, CMDS, .6), [])

    def test_dangerous_fuzzy_disabled(self):
        self.assertIsNone(jarvis.match_command("перезагрузи компютер", CMDS, .6)[0])
        self.assertEqual(jarvis.match_command("перезагрузи компьютер", CMDS, .6)[0]["id"], "reboot")

    def test_incomplete_command_not_guessed(self):
        self.assertIsNone(jarvis.match_command("открой", CMDS, .6)[0])

    def test_fuzzy_multi_plan_in_order(self):
        got = jarvis.match_commands_local("открой дискор и какая пагода", CMDS, .72)
        self.assertEqual([c["id"] for c, _ in got], ["open", "weather"])

    def test_no_partial_plan_on_unknown_clause(self):
        text = "открой дискорд и сделай неизвестное действие"
        self.assertEqual(jarvis.match_commands_exact(text, CMDS, 6), [])
        self.assertEqual(jarvis.match_commands_local(text, CMDS, .72), [])

    def test_plan_limit_and_empty_clause(self):
        for text in ("открой дискорд и", " и ".join(["какая пагода"] * 5)):
            self.assertEqual(jarvis.match_commands_local(text, CMDS, .72), [])

    def test_real_catalog_fuzzy(self):
        got = jarvis.match_commands_local("открой дискор и какая пагода", jarvis.load_commands(), .72)
        self.assertEqual([c["id"] for c, _ in got], ["open_discord", "weather"])

    def test_confirmation_is_not_prefix(self):
        with patch.object(jarvis, "speak"), patch.object(jarvis, "record_audio", return_value=True):
            for answer, expected in [("да", True), ("Да!", True), ("да нет", False),
                                     ("давай не надо", False), ("даже не думай", False)]:
                with self.subTest(answer=answer), patch.object(jarvis, "transcribe", return_value=answer):
                    self.assertEqual(jarvis.ask_confirmation({}, {}), expected)

    def test_dangerous_tag_confirmed_on_every_route(self):
        with patch.object(jarvis, "ask_confirmation", return_value=False) as confirm, \
             patch.object(jarvis, "execute_foreground") as execute, patch.object(jarvis, "notify"):
            jarvis.handle_command({}, CMDS[-1], 1)
        confirm.assert_called_once()
        execute.assert_not_called()

    def test_whisper_parameters_and_caption_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.wav"
            path.write_bytes(b"fake audio")
            response = Mock()
            response.json.return_value = {"text": "[Музыка]"}
            cfg = {**jarvis.DEFAULT_CONFIG, "stt_prompt": "Discord, Джарвис"}
            with patch.object(jarvis.requests, "post", return_value=response) as post:
                self.assertEqual(jarvis.transcribe(cfg, path), "")
            data = post.call_args.kwargs["data"]
            self.assertEqual(data["temperature"], "0.0")
            self.assertEqual(data["prompt"], "Discord, Джарвис")


class SpeechBufferTests(unittest.TestCase):
    def test_silence_not_valid(self):
        buffer = SpeechBuffer()
        for _ in range(100):
            buffer.feed(b"0", False)
        self.assertFalse(buffer.valid)

    def test_isolated_click_not_speech(self):
        buffer = SpeechBuffer()
        buffer.feed(b"x", True)
        for _ in range(30):
            buffer.feed(b"0", False)
        self.assertFalse(buffer.started)
        self.assertFalse(buffer.valid)

    def test_short_noise_rejected(self):
        buffer = SpeechBuffer()
        for _ in range(3):
            buffer.feed(b"x", True)
        for _ in range(30):
            buffer.feed(b"0", False)
        self.assertTrue(buffer.started)
        self.assertFalse(buffer.valid)

    def test_preroll_and_end_of_speech(self):
        buffer = SpeechBuffer(silence_frames=3)
        buffer.feed(b"p", False)
        for _ in range(6):
            self.assertFalse(buffer.feed(b"x", True))
        self.assertFalse(buffer.feed(b"0", False))
        self.assertFalse(buffer.feed(b"0", False))
        self.assertTrue(buffer.feed(b"0", False))
        self.assertTrue(buffer.valid)
        self.assertEqual(bytes(buffer.audio), b"pxxxxxx000")


class AudioRecordingTests(unittest.TestCase):
    def run_recording(self, chunks, ready=True):
        cfg = {**jarvis.DEFAULT_CONFIG, "vad_silence_seconds": 0.06}
        proc = Mock()
        selector = Mock()
        selector.select.return_value = [object()] if ready else []
        vad_module = Mock()
        vad_module.Vad.return_value.is_speech.side_effect = lambda frame, rate: frame[:1] == b"x"
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(jarvis, "webrtcvad", vad_module), \
             patch.object(jarvis.subprocess, "Popen", return_value=proc), \
             patch.object(jarvis.selectors, "DefaultSelector", return_value=selector), \
             patch.object(jarvis.os, "read", side_effect=chunks), \
             patch.object(jarvis, "write_wav") as write:
            result = jarvis.record_audio_vad(cfg, Path(tmp) / "audio.wav")
        proc.terminate.assert_called_once()
        proc.stdout.close.assert_called_once()
        selector.close.assert_called_once()
        return result, write

    def test_partial_pipe_reads_reassembled(self):
        # 16000Hz * 30ms * 2 bytes = 960 bytes/frame; two reads per frame.
        result, write = self.run_recording([b"x" * 400, b"x" * 560] * 6 + [b"0" * 960] * 2)
        self.assertTrue(result)
        self.assertEqual(len(write.call_args.args[1]), 8 * 960)

    def test_stalled_microphone_returns_without_wav(self):
        result, write = self.run_recording([], ready=False)
        self.assertFalse(result)
        write.assert_not_called()

    def test_early_eof_without_speech(self):
        result, write = self.run_recording([b""])
        self.assertFalse(result)
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
