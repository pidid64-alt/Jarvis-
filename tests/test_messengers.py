"""Тесты сообщений: адресная книга, каналы, отправка, входящие, CLI.

Сети нет: у Telegram подменяем HTTP-клиент, у почты — ``imaplib``/``smtplib``,
а WhatsApp говорит с настоящим файлом-скриптом вместо wacli.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest

from tests.helpers import FakeProviders, isolated_home

from jarvis.core import paths
from jarvis.core.assistant import Assistant
from jarvis.core.config import Config
from jarvis.core.errors import ConfigError, ProviderError
from jarvis.core.messengers import (
    AddressBook,
    Contact,
    MessengerService,
    ensure_contacts_file,
    parse_contacts,
)
from jarvis.core.matcher import find_exact
from jarvis.providers.messengers import (
    Channels,
    IncomingMessage,
    MailChannel,
    TelegramChannel,
    WhatsAppChannel,
    _clean,
)

CONTACTS = """
[[contact]]
name = "Артём"
telegram = "123456789"
email = "artem@example.com"
whatsapp = "+77001234567"

[[contact]]
name = "Мама"
telegram = "987654321"
whatsapp = "+77007654321"

[[contact]]
name = "Бухгалтерия"
email = "buh@example.com"
"""


# ------------------------------------------------------------------ помощники
def write_contacts(text: str = CONTACTS) -> Path:
    path = paths.contacts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_config(**values) -> Config:
    """Настройки с включёнными сообщениями — правки идут в файл, как у человека."""
    from jarvis.core import toml_edit

    Config.load()                       # создаст config.toml из шаблона
    path = paths.config_path()
    text = path.read_text(encoding="utf-8")
    text = toml_edit.set_value(text, "messengers.enabled", True)
    for key, value in values.items():
        text = toml_edit.set_value(text, f"messengers.{key}", value)
    path.write_text(text, encoding="utf-8")
    return Config.load()


def config_without_messengers() -> Config:
    """Настройки, где сообщения выключены (как после чистой установки)."""
    from jarvis.core import toml_edit

    Config.load()
    path = paths.config_path()
    text = path.read_text(encoding="utf-8")
    path.write_text(toml_edit.set_value(text, "messengers.enabled", False), encoding="utf-8")
    return Config.load()


class IsolatedTestCase(unittest.TestCase):
    """Каждый тест работает в своём временном JARVIS_HOME.

    Иначе настройки и состояние сообщений попадали бы в настоящую домашнюю
    папку разработчика, а «уже виденные» сообщения переносились бы из теста
    в тест.
    """

    def setUp(self):
        self._home = isolated_home()
        self.home = self._home.__enter__()
        self.addCleanup(lambda: self._home.__exit__(None, None, None))


class FakeChannel:
    """Канал-заглушка: помнит, что отправили, и отдаёт заготовленные входящие."""

    def __init__(self, name: str, title: str, *, available: bool = True, reason: str = "готов"):
        self.name = name
        self.title = title
        self.mailbox = "INBOX"
        self._available = available
        self._reason = reason
        self.sent: list[tuple[str, str]] = []
        self.incoming: list[IncomingMessage] = []
        self.offers: dict[str, list[str]] = {}
        self.fail_with: str = ""

    def available(self):
        return self._available, self._reason

    def send(self, target: str, text: str, **kwargs) -> str:
        if self.fail_with:
            raise ProviderError(self.fail_with)
        self.sent.append((target, text))
        return "42"

    def poll(self, *args, **kwargs):
        if self.name == "telegram":
            return list(self.incoming), 100
        if self.name == "mail":
            return list(self.incoming), 0
        return list(self.incoming)

    def qr_hint(self) -> str:
        return f"{self.name}: отсканируйте QR в WhatsApp → Связанные устройства"

    def resolve(self, query: str):
        names = self.offers.get(query, [])
        if len(names) == 1:
            return {"jid": f"{names[0]}@s.whatsapp.net", "name": names[0]}, []
        return None, names

    def state(self, *, probe: bool = False):
        return {"available": self._available, "reason": self._reason}

    def doctor(self):
        return self._available, self._reason


def make_service(channels=None, **settings) -> MessengerService:
    service = MessengerService(write_config(**settings), channels=channels)
    return service


def fake_channels(**kwargs) -> Channels:
    return Channels(
        telegram=FakeChannel("telegram", "Telegram", **kwargs.get("telegram", {})),
        mail=FakeChannel("mail", "Почта", **kwargs.get("mail", {})),
        whatsapp=FakeChannel("whatsapp", "WhatsApp", **kwargs.get("whatsapp", {})),
    )


# --------------------------------------------------------------- адресная книга
class AddressBookTests(IsolatedTestCase):
    def test_example_file_parses(self):
        """Шаблон contacts.example.toml из пакета должен быть рабочим."""
        data = paths.bundled_contacts_example_path().read_text(encoding="utf-8")
        import tomllib

        contacts = parse_contacts(tomllib.loads(data))
        assert [contact.name for contact in contacts][:2] == ["Артём", "Мама"]
        assert contacts[0].telegram and contacts[0].email and contacts[0].whatsapp

    def test_ensure_creates_file(self):
        with isolated_home():
            path = ensure_contacts_file()
            assert path.exists()
            assert "[[contact]]" in path.read_text(encoding="utf-8")

    def test_parse_requires_name(self):
        with pytest.raises(ConfigError) as exc:
            parse_contacts({"contact": [{"telegram": "1"}]})
        assert "не указано имя" in str(exc.value)

    def test_parse_rejects_non_table(self):
        with pytest.raises(ConfigError):
            parse_contacts({"contact": ["просто строка"]})

    def test_find_exact_and_prefix(self):
        with isolated_home():
            write_contacts()
            book = AddressBook()
            assert book.find("Мама").telegram == "987654321"

    def test_find_ambiguous_prefix_returns_none(self):
        with isolated_home():
            write_contacts(CONTACTS + '\n[[contact]]\nname = "Мария"\ntelegram = "1"\n'
                           '[[contact]]\nname = "Марина"\ntelegram = "2"\n')
            assert AddressBook().find("Мар") is None

    def test_find_understands_cases(self):
        """«Артёму», «артему», «Артём» — один и тот же человек."""
        with isolated_home():
            write_contacts()
            book = AddressBook()
            for spoken in ("Артёму", "артему", "Артём", "артём"):
                assert book.find(spoken).telegram == "123456789"

    def test_find_does_not_guess_between_similar_names(self):
        with isolated_home():
            write_contacts(CONTACTS + '\n[[contact]]\nname = "Мария"\ntelegram = "1"\n')
            assert AddressBook().find("Маруся") is None
            assert AddressBook().find("Маме").name == "Мама"

    def test_similar_lists_candidates_for_question(self):
        with isolated_home():
            write_contacts(CONTACTS + '\n[[contact]]\nname = "Мария"\ntelegram = "1"\n')
            assert AddressBook().similar("Маме") == ["Мама"]

    def test_find_unknown_returns_none(self):
        with isolated_home():
            write_contacts()
            assert AddressBook().find("Никого") is None

    def test_broken_file_gives_clear_error(self):
        with isolated_home():
            write_contacts("[[contact]\nname =")
            with pytest.raises(ConfigError) as exc:
                AddressBook().find("Мама")
            assert "contacts.toml" in str(exc.value)

    def test_public_dict_has_no_secrets(self):
        with isolated_home():
            write_contacts()
            public = AddressBook().to_public()
            assert public[0]["channels"] == ["telegram", "mail", "whatsapp"]
            assert "artem@example.com" not in json.dumps(public, ensure_ascii=False)

    def test_target_per_channel(self):
        contact = Contact(name="X", telegram="1", email="a@b.c", whatsapp="+7")
        assert contact.target("telegram") == "1"
        assert contact.target("mail") == "a@b.c"
        assert contact.target("whatsapp") == "+7"
        assert contact.target("sms") == ""


# --------------------------------------------------------------------- Telegram
class FakeHttp:
    """Подменяет HttpClient: отвечает заготовками и помнит запросы."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, url, payload, **kwargs):
        self.calls.append((url, payload))
        name = url.rsplit("/", 1)[-1]
        answer = self.responses.get(name, {"ok": False, "description": "нет ответа"})
        if isinstance(answer, Exception):
            raise answer
        body = json.dumps(answer).encode("utf-8")

        class Result:
            status = 200

            def __init__(self, body):
                self.body = body

        return Result(body)


def telegram_with(responses) -> tuple[TelegramChannel, FakeHttp]:
    channel = TelegramChannel("токен")
    http = FakeHttp(responses)
    channel.client = http
    return channel, http


class TelegramTests(IsolatedTestCase):
    def test_missing_token(self):
        ready, reason = TelegramChannel("").available()
        assert not ready and "JARVIS_TELEGRAM_TOKEN" in reason

    def test_available_reports_bot_name(self):
        channel, _ = telegram_with({"getMe": {"ok": True, "result": {"username": "mybot"}}})
        ready, reason = channel.available()
        assert ready and "@mybot" in reason

    def test_available_surfaces_telegram_error(self):
        channel, _ = telegram_with({"getMe": {"ok": False, "description": "Unauthorized"}})
        ready, reason = channel.available()
        assert not ready and "Unauthorized" in reason

    def test_network_error_is_named(self):
        channel, _ = telegram_with({"getMe": OSError("сеть недоступна")})
        ready, reason = channel.available()
        assert not ready and "сеть недоступна" in reason

    def test_send_returns_message_id(self):
        channel, http = telegram_with({"sendMessage": {"ok": True, "result": {"message_id": 7}}})
        assert channel.send("123", "привет") == "7"
        url, payload = http.calls[0]
        assert url.endswith("/sendMessage")
        assert payload == {"chat_id": "123", "text": "привет", "disable_web_page_preview": True}

    def test_send_rejects_empty_text(self):
        channel, _ = telegram_with({"sendMessage": {"ok": True, "result": {}}})
        service = make_service(fake_channels())
        service.channels.telegram = channel
        with pytest.raises(ProviderError) as exc:
            service.send("telegram", "123", "   ")
        assert "пустой текст" in str(exc.value)

    def test_poll_returns_messages_and_offset(self):
        channel, _ = telegram_with({"getUpdates": {"ok": True, "result": [
            {"update_id": 5, "message": {"date": 1700000000, "text": "привет",
                                         "chat": {"id": 11}, "from": {"first_name": "Мама"}}},
            {"update_id": 6, "message": {"chat": {"id": 11}, "from": {"first_name": "Мама"}}},
        ]}})
        messages, offset = channel.poll(0)
        assert offset == 7
        assert len(messages) == 1  # сообщение без текста не показываем
        assert messages[0].sender == "Мама" and messages[0].conversation == "11"
        assert messages[0].uid == "tg:5"

    def test_state_without_probe_does_not_touch_network(self):
        channel, http = telegram_with({})
        state = channel.state()
        assert state["available"] and not http.calls


# ------------------------------------------------------------------------ почта
class FakeIMAP:
    """Маленькая подмена imaplib.IMAP4_SSL: два письма в ящике."""

    LETTERS: dict[int, bytes] = {}
    calls: list[tuple] = []
    logged_in: list[tuple] = []

    def __init__(self, host, timeout=None):
        self.host = host

    def login(self, login, password):
        FakeIMAP.logged_in.append((login, password))
        if password == "плохой":
            raise __import__("imaplib").IMAP4.error("Invalid credentials")

    def select(self, mailbox, readonly=False):
        FakeIMAP.calls.append(("select", mailbox, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        FakeIMAP.calls.append(("uid", command, *args))
        if command == "search":
            criteria = args[1]
            uids = [uid for uid in sorted(self.LETTERS)]
            if criteria != "ALL":
                low = int(criteria.split()[1].split(":")[0])
                uids = [uid for uid in uids if uid >= low]
            return "OK", [" ".join(str(uid) for uid in uids).encode()]
        if command == "fetch":
            uid = int(args[0])
            return "OK", [(b"1 (RFC822 {42}", self.LETTERS[uid])]
        return "OK", [b""]

    def logout(self):
        FakeIMAP.calls.append(("logout",))


def letter(subject: str, text: str, sender: str = "Мама <mama@example.com>") -> bytes:
    return (f"From: {sender}\r\nTo: me@example.com\r\nSubject: =?utf-8?B?"
            f"{__import__('base64').b64encode(subject.encode()).decode()}?=\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n{text}\r\n").encode("utf-8")


class MailTests(IsolatedTestCase):
    """Почта: подменяем imaplib/smtplib, сети нет."""

    def setUp(self):
        import imaplib

        FakeIMAP.LETTERS = {1: letter("Привет", "Как дела?"), 2: letter("Отчёт", "Файл во вложении")}
        FakeIMAP.calls = []
        FakeIMAP.logged_in = []
        patcher = mock.patch.object(imaplib, "IMAP4_SSL", FakeIMAP)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.imap = FakeIMAP

    def test_missing_credentials(self):
        ready, reason = MailChannel(login="", password="").available()
        assert not ready and "JARVIS_GMAIL_USER" in reason

    def test_clean_decodes_subject(self):
        assert _clean("=?utf-8?B?0J/RgNC40LLQtdGC?=") == "Привет"

    def test_clean_survives_garbage(self):
        assert _clean("просто текст") == "просто текст"

    def test_poll_reads_new_letters(self):
        channel = MailChannel(login="me@example.com", password="пароль")
        messages, highest = channel.poll(last_uid=0, limit=5)
        assert [message.subject for message in messages] == ["Привет", "Отчёт"]
        assert highest == 2
        assert messages[0].uid == "mail:1" and messages[0].sender.startswith("Мама")
        assert messages[0].text == "Как дела?"

    def test_poll_only_new_uids(self):
        channel = MailChannel(login="me@example.com", password="пароль")
        messages, highest = channel.poll(last_uid=1, limit=5)
        assert [message.subject for message in messages] == ["Отчёт"] and highest == 2

    def test_poll_does_not_mark_read(self):
        channel = MailChannel(login="me@example.com", password="пароль")
        channel.poll(last_uid=0)
        assert ("select", "INBOX", True) in self.imap.calls

    def test_poll_wrong_password(self):
        channel = MailChannel(login="me@example.com", password="плохой")
        with pytest.raises(ProviderError) as exc:
            channel.poll(last_uid=0)
        assert "ошибкой" in str(exc.value)

    def test_doctor_ok(self):
        ok, reason = MailChannel(login="me@example.com", password="пароль").doctor()
        assert ok and "вход выполнен" in reason

    def test_doctor_wrong_password(self):
        ok, reason = MailChannel(login="me@example.com", password="плохой").doctor()
        assert not ok and "Invalid credentials" in reason

    def test_send_builds_letter_and_surfaces_errors(self):
        import smtplib

        sent: list[object] = []

        class FakeSMTP:
            def __init__(self, host, port, timeout=None, context=None):
                self.host, self.port = host, port

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def login(self, login, password):
                if password == "плохой":
                    raise smtplib.SMTPAuthenticationError(535, b"nope")

            def send_message(self, message):
                sent.append(message)

        patcher = mock.patch.object(smtplib, "SMTP_SSL", FakeSMTP)
        patcher.start()
        self.addCleanup(patcher.stop)
        channel = MailChannel(login="me@example.com", password="пароль")
        channel.send("mama@example.com", "я дома", subject="Привет")
        assert sent and sent[0]["To"] == "mama@example.com"
        assert sent[0].get_content().strip() == "я дома"

        broken = MailChannel(login="me@example.com", password="плохой")
        with pytest.raises(ProviderError) as exc:
            broken.send("mama@example.com", "не уйдёт")
        assert "не ушло" in str(exc.value)

    def test_send_without_recipient(self):
        with pytest.raises(ProviderError) as exc:
            MailChannel(login="me@example.com", password="п").send("", "текст")
        assert "не указан получатель" in str(exc.value)


# --------------------------------------------------------------------- WhatsApp
class WhatsAppTests(IsolatedTestCase):
    """WhatsApp: вместо wacli — маленький скрипт, который отдаёт JSON."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wacli-test-")
        self.addCleanup(self.tmp.cleanup)

    def wacli(self, script: str) -> str:
        path = Path(self.tmp.name) / "fake-wacli.py"
        path.write_text("#!/usr/bin/env python3\n" + script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def test_missing_program(self):
        channel = WhatsAppChannel(command="wacli-которого-нет")
        ready, reason = channel.available()
        assert not ready and "не найдена программа" in reason
        assert "wacli" in channel.qr_hint()

    def test_state_reports_program_without_running_it(self):
        channel = WhatsAppChannel(command=self.wacli("print('{}')"))
        state = channel.state()
        assert state["available"] and "найдена" in state["reason"]

    def test_send_parses_json(self):
        command = self.wacli(
            "import sys, json\n"
            "json.dump({'id': 'ABC', 'chat': sys.argv[-1]}, sys.stdout)\n")
        channel = WhatsAppChannel(command=command)
        assert channel.send("+77001234567", "привет") == "ABC"

    def test_send_reads_number_from_argv(self):
        log = Path("/tmp")
        command = self.wacli(
            "import sys, json\n"
            "open('/tmp/wacli-args.txt', 'w').write(' '.join(sys.argv))\n"
            "json.dump({'id': '1'}, sys.stdout)\n")
        WhatsAppChannel(command=command).send("+77001234567", "привет")
        args = Path("/tmp/wacli-args.txt").read_text(encoding="utf-8")
        assert "--json" in args and "send text" in args
        assert "--to +77001234567" in args and "--message" in args

    def test_send_error_from_stderr(self):
        command = self.wacli(
            "import sys\n"
            "print('не связан: сначала wacli auth', file=sys.stderr)\n"
            "sys.exit(1)\n")
        with pytest.raises(ProviderError) as exc:
            WhatsAppChannel(command=command).send("+7", "привет")
        assert "сначала wacli auth" in str(exc.value)

    def test_resolve_exact_name(self):
        command = self.wacli(
            "import sys, json\n"
            "json.dump({'chats': [{'name': 'Мама', 'jid': '1@s.whatsapp.net'},"
            " {'name': 'Мария', 'jid': '2@s.whatsapp.net'}]}, sys.stdout)\n")
        channel = WhatsAppChannel(command=command)
        found, similar = channel.resolve("Мама")
        assert found["jid"] == "1@s.whatsapp.net" and not similar

    def test_resolve_ambiguous_lists_candidates(self):
        command = self.wacli(
            "import sys, json\n"
            "json.dump({'chats': [{'name': 'Мама', 'jid': '1@s.whatsapp.net'},"
            " {'name': 'Мария', 'jid': '2@s.whatsapp.net'}]}, sys.stdout)\n")
        channel = WhatsAppChannel(command=command)
        found, similar = channel.resolve("Мам")
        assert found is None and similar == ["Мама", "Мария"]

    def test_phone_is_used_as_is(self):
        channel = WhatsAppChannel(command=self.wacli("import sys\n"))
        found, similar = channel.resolve("+77001234567")
        assert found["jid"] == "+77001234567" and not similar

    def test_poll_parses_messages(self):
        command = self.wacli(
            "import sys, json\n"
            "json.dump({'messages': [{'id': '9', 'text': 'привет', 'sender_name': 'Мама',"
            " 'chat_name': 'Мама', 'timestamp': '2026-10-08T10:00:00Z'},"
            " {'id': '10', 'text': ''}]}, sys.stdout)\n")
        messages = WhatsAppChannel(command=command).poll(limit=5)
        assert len(messages) == 1
        assert messages[0].sender == "Мама" and messages[0].uid == "9"

    def test_poll_asks_only_for_incoming(self):
        command = self.wacli(
            "import sys, json\n"
            "open('/tmp/wacli-poll.txt', 'w').write(' '.join(sys.argv))\n"
            "json.dump({'messages': []}, sys.stdout)\n")
        WhatsAppChannel(command=command).poll(after="2026-10-01T00:00:00Z")
        args = Path("/tmp/wacli-poll.txt").read_text(encoding="utf-8")
        assert "--from-them" in args and "--after 2026-10-01T00:00:00Z" in args

    def test_non_json_answer_is_named(self):
        command = self.wacli("print('обычный текст')\n")
        with pytest.raises(ProviderError) as exc:
            WhatsAppChannel(command=command).send("+7", "привет")
        assert "не-JSON" in str(exc.value)

    def test_timeout_is_named(self):
        command = self.wacli("import time\ntime.sleep(5)\n")
        channel = WhatsAppChannel(command=command, timeout=0.5)
        with pytest.raises(ProviderError) as exc:
            channel.send("+7", "привет")
        assert "не ответил" in str(exc.value)


# ------------------------------------------------------------------- сам сервис
class ServiceTests(IsolatedTestCase):
    def test_disabled_service_refuses_to_send(self):
        service = MessengerService(config_without_messengers(), channels=fake_channels())
        with pytest.raises(ProviderError) as exc:
            service.send("telegram", "123", "привет")
        assert "выключены" in str(exc.value)

    def test_unknown_channel(self):
        service = make_service(fake_channels())
        with pytest.raises(ProviderError) as exc:
            service.send("смс", "123", "привет")
        assert "не поддерживается" in str(exc.value)

    def test_contact_recipient(self):
        with isolated_home():
            write_contacts()
            service = make_service(fake_channels())
            target, display, problem = service.resolve("telegram", "Мама")
            assert (target, display, problem) == ("987654321", "Мама", "")

    def test_contact_without_channel(self):
        with isolated_home():
            write_contacts()
            service = make_service(fake_channels())
            _target, display, problem = service.resolve("whatsapp", "Бухгалтерия")
            assert "не указан адрес для WhatsApp" in problem

    def test_wrong_recipient_for_telegram(self):
        with isolated_home():
            write_contacts()
            service = make_service(fake_channels())
            _target, _display, problem = service.resolve("telegram", "Пётр")
            assert "chat_id" in problem

    def test_wrong_recipient_for_mail(self):
        with isolated_home():
            write_contacts()
            service = make_service(fake_channels())
            _target, _display, problem = service.resolve("mail", "Пётр")
            assert "не похоже на адрес почты" in problem

    def test_email_used_as_is(self):
        service = make_service(fake_channels())
        target, display, problem = service.resolve("mail", "a@b.c")
        assert (target, display, problem) == ("a@b.c", "a@b.c", "")

    def test_whatsapp_name_search_and_ambiguity(self):
        with isolated_home():
            channels = fake_channels()
            channels.whatsapp.offers = {"Мама": ["Мама"], "Мам": ["Мама", "Мария"]}
            service = make_service(channels)
            target, display, problem = service.resolve("whatsapp", "Мама")
            assert target.endswith("@s.whatsapp.net") and display == "Мама" and not problem
            _target, _display, problem = service.resolve("whatsapp", "Мам")
            assert "несколько похожих чатов" in problem

    def test_send_writes_journal(self):
        with isolated_home():
            write_contacts()
            service = make_service(fake_channels())
            result = service.send("telegram", "Мама", "привет")
            assert result.display == "Мама" and result.message_id == "42"
            assert service.channels.telegram.sent == [("987654321", "привет")]

    def test_channel_error_is_visible(self):
        with isolated_home():
            write_contacts()
            channels = fake_channels()
            channels.telegram.fail_with = "Telegram не отвечает"
            service = make_service(channels)
            with pytest.raises(ProviderError) as exc:
                service.send("telegram", "Мама", "привет")
            assert "Telegram не отвечает" in str(exc.value)

    def test_state_is_cheap_by_default(self):
        service = make_service(fake_channels())
        state = service.state()
        assert set(state["channels"]) == {"telegram", "mail", "whatsapp"}
        assert state["enabled"] is True

    def test_doctor_lists_all_channels(self):
        with isolated_home():
            service = make_service(fake_channels(mail={"available": False, "reason": "нет пароля"}))
            result = dict((name, (ok, reason)) for name, ok, reason in service.doctor())
            assert result["mail"] == (False, "нет пароля") and result["telegram"][0] is True


# ---------------------------------------------------------------- входящие
class IncomingTests(IsolatedTestCase):
    def _telegram_message(self, uid: str = "tg:1") -> IncomingMessage:
        return IncomingMessage(channel="telegram", sender="Мама", text="привет",
                                uid=uid, conversation="11")

    def test_new_message_is_returned_once(self):
        channels = fake_channels()
        channels.telegram.incoming = [self._telegram_message()]
        service = make_service(channels)
        assert len(service.check_new()) == 1
        assert service.check_new() == []          # повторно не показываем
        assert len(service.recent()) == 1

    def test_recent_is_shown_once_until_viewed(self):
        channels = fake_channels()
        channels.telegram.incoming = [self._telegram_message()]
        service = make_service(channels)
        service.check_new()
        assert len(service.recent()) == 1
        assert service.recent() == []             # второй раз — уже «прочитано»

    def test_check_when_disabled_does_nothing(self):
        channels = fake_channels()
        channels.telegram.incoming = [self._telegram_message()]
        service = MessengerService(config_without_messengers(), channels=channels)
        assert service.check_new() == []

    def test_channel_failure_does_not_break_others(self):
        channels = fake_channels()
        channels.telegram.available = lambda: (False, "нет токена")
        channels.mail.incoming = [IncomingMessage(channel="mail", sender="Мама", text="письмо",
                                                  uid="mail:1")]
        service = make_service(channels)
        found = service.check_new()
        assert [message.text for message in found] == ["письмо"]

    def test_seen_list_is_trimmed(self):
        channels = fake_channels()
        service = make_service(channels)
        service._state["seen"] = [str(index) for index in range(400)]
        service.check_new()
        assert len(service._state["seen"]) <= 300

    def test_state_file_is_written_atomically(self):
        channels = fake_channels()
        service = make_service(channels)
        service.check_new()
        assert service.state_path.exists()
        assert not list(service.state_path.parent.glob("*.tmp"))
        assert json.loads(service.state_path.read_text(encoding="utf-8"))

    def test_describe_mentions_subject(self):
        message = IncomingMessage(channel="mail", sender="Мама", text="привет",
                                  subject="Отчёт", uid="mail:1")
        assert message.describe() == "Мама: Отчёт — привет"
        assert message.to_dict()["uid"] == "mail:1"


# ------------------------------------------------------------------ сам сервис: фон
class AssistantTests(IsolatedTestCase):
    def _assistant(self, channels, **settings) -> Assistant:
        config = write_config(**settings)
        return Assistant(config, providers=FakeProviders(), messengers=MessengerService(
            config, channels=channels))

    def test_maintain_notifies_once(self):
        channels = fake_channels()
        channels.telegram.incoming = [IncomingMessage(channel="telegram", sender="Мама",
                                                      text="привет", uid="tg:1")]
        assistant = self._assistant(channels)
        assistant.providers.notifications.clear()
        assistant.maintain()
        assert len(assistant.providers.notifications) == 1
        assistant.maintain()                        # второй раз ничего нового
        assert len(assistant.providers.notifications) == 1

    def test_notification_can_be_switched_off(self):
        channels = fake_channels()
        channels.telegram.incoming = [IncomingMessage(channel="telegram", sender="Мама",
                                                      text="привет", uid="tg:1")]
        assistant = self._assistant(channels, notify=False)
        assistant.maintain()
        assert assistant.providers.notifications == []

    def test_poll_seconds_throttles(self):
        channels = fake_channels()
        service = MessengerService(write_config(poll_seconds=600), channels=channels)
        assistant = Assistant(write_config(poll_seconds=600), providers=FakeProviders(),
                              messengers=service)
        channels.telegram.incoming = [IncomingMessage(channel="telegram", sender="Мама",
                                                      text="один", uid="tg:1")]
        assert len(service.check_new_if_due(now=1000.0)) == 1
        channels.telegram.incoming = [IncomingMessage(channel="telegram", sender="Мама",
                                                      text="два", uid="tg:2")]
        assert service.check_new_if_due(now=1001.0) == []      # рано
        assert len(service.check_new_if_due(now=2000.0)) == 1  # время пришло
        assert assistant.messengers.poll_seconds == 600

    def test_read_aloud_speaks_when_enabled(self):
        channels = fake_channels()
        channels.telegram.incoming = [IncomingMessage(channel="telegram", sender="Мама",
                                                      text="привет", uid="tg:1")]
        providers = FakeProviders(voice_enabled=True)
        config = write_config(read_aloud=True)
        assistant = Assistant(config, providers=providers,
                              messengers=MessengerService(config, channels=channels))
        assistant.maintain()
        assert providers.spoken == ["Мама: привет"]

    def test_status_shows_messengers(self):
        assistant = self._assistant(fake_channels())
        state = assistant.status()["messengers"]
        assert state["enabled"] is True and "contacts_path" in state
        assert set(state["channels"]) == {"telegram", "mail", "whatsapp"}

    def test_maintain_survives_broken_channel(self):
        channels = fake_channels()

        def boom(*args, **kwargs):
            raise RuntimeError("канал сломался")

        channels.telegram.available = boom
        assistant = self._assistant(channels)
        assistant.maintain()                       # не падаем


# ------------------------------------------------------------------- навык
class SkillTests(IsolatedTestCase):
    def _ask(self, text: str, *, answer: bool = True, channels=None, **settings):
        """Спрашивает ядро и возвращает (ответ, подтверждение, каналы)."""
        channels = channels or fake_channels()
        config = write_config(**settings)
        assistant = Assistant(config, providers=FakeProviders(),
                              messengers=MessengerService(config, channels=channels))
        seen: list[str] = []
        reply = assistant.handle_text(text, source="api",
                                      confirm_callback=lambda question: seen.append(question) or answer)
        return reply, seen, channels

    def test_phrases_are_in_registry(self):
        config = write_config()
        assistant = Assistant(config, providers=FakeProviders(),
                              messengers=MessengerService(config, channels=fake_channels()))
        skill = assistant.registry.get("messengers")
        assert skill is not None and assistant.registry.problems == []
        ids = {action.id for action in skill.actions}
        assert ids == {"send", "inbox", "contacts", "channels", "connect"}

    def test_exact_route_to_send(self):
        config = write_config()
        assistant = Assistant(config, providers=FakeProviders(),
                              messengers=MessengerService(config, channels=fake_channels()))
        found = find_exact("отправь в телеграм Артёму: привет", assistant.registry.enabled(), "linux")
        assert [intent.action.id for intent in found] == ["send"]
        assert found[0].args["query"] == "Артёму: привет"

    def test_send_asks_and_sends(self):
        with isolated_home():
            write_contacts()
            reply, seen, channels = self._ask("отправь в телеграм Артёму: привет")
            assert len(seen) == 1
            assert "Telegram" in seen[0] and "Артём" in seen[0] and "привет" in seen[0]
            assert channels.telegram.sent == [("123456789", "привет")]
            assert "Отправил в Telegram" in reply.text

    def test_send_can_be_cancelled(self):
        with isolated_home():
            write_contacts()
            reply, seen, channels = self._ask("отправь в телеграм Артёму: привет", answer=False)
            assert channels.telegram.sent == []
            assert "не отправляю" in reply.text

    def test_dative_name_is_understood(self):
        with isolated_home():
            write_contacts()
            _reply, _seen, channels = self._ask("отправь в ватсап маме: я дома")
            assert channels.whatsapp.sent == [("+77007654321", "я дома")]

    def test_channel_word_from_phrase_without_colon(self):
        with isolated_home():
            write_contacts()
            _reply, _seen, channels = self._ask("напиши в телеграм Артём привет как дела")
            assert channels.telegram.sent == [("123456789", "привет как дела")]

    def test_mail_by_address(self):
        with isolated_home():
            write_contacts()
            _reply, _seen, channels = self._ask("отправь письмо на vasya@example.com: готово")
            assert channels.mail.sent == [("vasya@example.com", "готово")]

    def test_unknown_recipient_asks_clarifying_question(self):
        with isolated_home():
            write_contacts()
            reply, seen, channels = self._ask("отправь в телеграм Пётру привет")
            assert not seen and channels.telegram.sent == []
            assert reply.continue_dialog and "Не понял, кому отправлять" in reply.text
            assert "Артём" in reply.text     # подсказываем, кто есть в книге

    def test_disabled_messengers_replies_with_hint(self):
        with isolated_home():
            write_contacts()
            config = config_without_messengers()
            assistant = Assistant(config, providers=FakeProviders(),
                                  messengers=MessengerService(config, channels=fake_channels()))
            reply = assistant.handle_text("отправь в телеграм Артёму: привет", source="api",
                                          confirm_callback=lambda _q: True)
            assert "Сообщения выключены" in reply.text

    def test_inbox_reports_new(self):
        with isolated_home():
            channels = fake_channels()
            channels.mail.incoming = [IncomingMessage(channel="mail", sender="Мама", subject="Отчёт",
                                                      text="привет", uid="mail:1")]
            reply, _seen, _channels = self._ask("что нового в сообщениях", channels=channels)
            assert "Новых сообщений: 1" in reply.text and "Мама" in reply.text

    def test_inbox_empty(self):
        reply, _seen, _channels = self._ask("проверь почту")
        assert "Новых сообщений нет" in reply.text

    def test_contacts_are_shown(self):
        with isolated_home():
            write_contacts()
            reply, _seen, _channels = self._ask("кому можно написать")
            assert "Артём" in reply.text and "telegram" in reply.text
            assert "artem@example.com" not in reply.text   # адреса не светим в чате

    def test_contacts_empty_hint(self):
        with isolated_home():
            reply, _seen, _channels = self._ask("мои контакты")
            assert "Адресная книга пуста" in reply.text

    def test_channels_report_and_hint(self):
        with isolated_home():
            reply, _seen, _channels = self._ask("какие каналы сообщений")
            assert "telegram: готов" in reply.text and "Сообщения включены" in reply.text

    def test_connect_explains_qr_and_risk(self):
        reply, _seen, _channels = self._ask("свяжи ватсап")
        assert "Связанные устройства" in reply.text and "ограничение" in reply.text

    def test_model_arguments_are_used(self):
        """Модель передала получателя и текст — их и отправляем."""
        with isolated_home():
            write_contacts()
            config = write_config()
            channels = fake_channels()
            assistant = Assistant(config, providers=FakeProviders(),
                                  messengers=MessengerService(config, channels=channels))
            intent = None
            for skill in assistant.registry.enabled():
                if skill.id != "messengers":
                    continue
                for action in skill.actions:
                    if action.id == "send":
                        from jarvis.core.types import Intent

                        intent = Intent(action=action, skill=skill, score=1.0, source="llm",
                                        text="передай маме что я дома",
                                        args={"model_arguments": {"channel": "телеграм",
                                                                  "recipient": "маме",
                                                                  "text": "я дома"}})
            assert intent is not None
            seen: list[str] = []
            from jarvis.core.context import SkillContext

            ctx = SkillContext(config=config, permissions=assistant.policy,
                               providers=FakeProviders(), registry=assistant.registry,
                               journal=assistant.journal,
                               confirm_callback=lambda question: seen.append(question) or True,
                               extra={"skill_id": "messengers", "messengers": assistant.messengers})
            reply = assistant.executor.execute(intent, ctx)
            assert channels.telegram.sent == [("987654321", "я дома")]
            assert "Мама" in seen[0] and "Мама" in reply.text

    def test_send_failure_is_named(self):
        with isolated_home():
            write_contacts()
            channels = fake_channels()
            channels.telegram.fail_with = "Unauthorized"
            reply, _seen, _channels = self._ask("отправь в телеграм Артёму: привет",
                                                channels=channels)
            assert "Сообщение не ушло: Unauthorized" in reply.text


# --------------------------------------------------------------------- секреты
class SecretTests(IsolatedTestCase):
    def test_token_never_leaves_settings(self):
        """Секрет живёт в .env, а в config.toml остаётся только ссылка на него."""
        from jarvis.core import secrets, toml_edit
        from jarvis.core.config import resolve_refs

        Config.load()                                    # создаст config.toml
        secrets.save_env_var("JARVIS_TELEGRAM_TOKEN", "123456:СЕКРЕТНЫЙ-ТОКЕН")
        secrets.load_env_file()
        path = paths.config_path()
        path.write_text(toml_edit.set_value(path.read_text(encoding="utf-8"),
                                            "messengers.telegram_token",
                                            "${JARVIS_TELEGRAM_TOKEN}"), encoding="utf-8")
        raw = path.read_text(encoding="utf-8")
        value, missing = resolve_refs("${JARVIS_TELEGRAM_TOKEN}")
        assert value == "123456:СЕКРЕТНЫЙ-ТОКЕН" and not missing
        assert "СЕКРЕТНЫЙ-ТОКЕН" not in raw
        config = Config.load()
        assert config.get("messengers.telegram_token") == "123456:СЕКРЕТНЫЙ-ТОКЕН"

    def test_window_links_saved_secret_to_config(self):
        """Окно сохраняет ключ в .env и ставит в настройках ссылку ${ИМЯ}."""
        from jarvis.interfaces.api import LocalApi

        Config.load()
        config = Config.load()
        assistant = Assistant(config, providers=FakeProviders(),
                              messengers=MessengerService(config, channels=fake_channels()))
        api = LocalApi(assistant, port=0)
        result = api._set_secret({"name": "JARVIS_TELEGRAM_TOKEN",
                                  "value": "123456:ТОКЕН", "key": "messengers.telegram_token"})
        assert result["name"] == "JARVIS_TELEGRAM_TOKEN"
        raw = paths.config_path().read_text(encoding="utf-8")
        assert 'telegram_token = "${JARVIS_TELEGRAM_TOKEN}"' in raw
        assert "ТОКЕН" not in raw
        assert Config.load().get("messengers.telegram_token") == "123456:ТОКЕН"

    def test_window_does_not_overwrite_other_reference(self):
        """Если ссылка уже указана человеком, окно её не подменяет."""
        from jarvis.core import toml_edit
        from jarvis.interfaces.api import LocalApi

        config = Config.load()
        path = paths.config_path()
        path.write_text(toml_edit.set_value(path.read_text(encoding="utf-8"),
                                            "messengers.gmail_user",
                                            "${MY_OWN_MAIL_VAR}"), encoding="utf-8")
        config = Config.load()
        assistant = Assistant(config, providers=FakeProviders(),
                              messengers=MessengerService(config, channels=fake_channels()))
        LocalApi(assistant, port=0)._set_secret({"name": "JARVIS_GMAIL_USER", "value": "я@мы.ру",
                                                 "key": "messengers.gmail_user"})
        raw = paths.config_path().read_text(encoding="utf-8")
        assert "${MY_OWN_MAIL_VAR}" in raw and "я@мы.ру" not in raw

    def test_config_keeps_secrets_empty_and_names_them(self):
        """В шаблоне настроек нет значений ключей — только имена переменных."""
        text = paths.bundled_config_path().read_text(encoding="utf-8")
        assert 'telegram_token = ""' in text and 'gmail_password = ""' in text
        for name in ("JARVIS_TELEGRAM_TOKEN", "JARVIS_GMAIL_USER", "JARVIS_GMAIL_PASSWORD"):
            assert name in text

    def test_default_config_does_not_demand_messenger_secrets(self):
        """Пока сообщения выключены, Jarvis не требует их ключей."""
        config = Config.load()
        assert not [name for name in config.missing_env if "GMAIL" in name or "TELEGRAM" in name]
        assert config.get("messengers.enabled") is False
        assert config.get("messengers.telegram_token") == ""

    def test_every_secret_field_knows_its_env_name(self):
        """У поля-секрета в окне своё имя переменной, иначе ключ уедет в чужой .env."""
        page = Path(__file__).resolve().parents[1] / "jarvis" / "interfaces" / "webui" / "app.js"
        script = page.read_text(encoding="utf-8")
        rows = re.findall(r'\{ key: "([\w.]+)",[^}]*?kind: "secret"[^}]*?\}', script, re.S)
        assert rows, "не нашёл ни одного поля-секрета"
        for key in rows:
            block = re.search(r'\{ key: "' + re.escape(key) + r'",.*?kind: "secret".*?\}', script, re.S)
            assert 'env: "JARVIS_' in block.group(0), f"у {key} нет env-имени"

    def test_missing_token_names_variable_only(self):
        service = make_service(fake_channels(telegram={"available": False,
                                                       "reason": "не задан токен бота "
                                                                 "(JARVIS_TELEGRAM_TOKEN)"}))
        state = service.state(probe=True)
        assert state["channels"]["telegram"]["available"] is False


# ---------------------------------------------------------------- настройки окна
class SettingsTests(IsolatedTestCase):
    def test_webui_card_shows_channels(self):
        """Карточка «Сообщения» строится по состоянию из ядра (без сети)."""
        page = Path(__file__).resolve().parents[1] / "jarvis" / "interfaces" / "webui"
        html = (page / "index.html").read_text(encoding="utf-8")
        script = (page / "app.js").read_text(encoding="utf-8")
        assert 'id="messengers-list"' in html and 'id="messengers-check"' in html
        assert "renderMessengers(status.messengers)" in script
        for key in ("messengers.off", "messengers.ready", "messengers.contacts"):
            assert f'"{key}"' in script
        keys = json.loads((paths.bundled_config_path().parent / "i18n" / "ru.json")
                          .read_text(encoding="utf-8"))
        for key in ("channel.telegram", "channel.mail", "channel.whatsapp", "messengers.off",
                    "messengers.ready", "messengers.not_ready", "messengers.contacts",
                    "messengers.no_contacts"):
            assert key in keys, f"нет подписи {key}"

    def test_webui_has_messengers_card(self):
        page = Path(__file__).resolve().parents[1] / "jarvis" / "interfaces" / "webui"
        script = (page / "app.js").read_text(encoding="utf-8")
        assert 'title: "Сообщения"' in script
        assert 'key: "messengers.enabled"' in script and "secret" in script
        assert 'key: "messengers.gmail_password", label: "Пароль приложения Google", kind: "secret"' in script


# ------------------------------------------------------------------- CLI
class CommandTests(IsolatedTestCase):
    def _run(self, argv, env=None):
        from jarvis.interfaces import cli

        import contextlib

        out = io.StringIO()
        with contextlib.redirect_stdout(out), isolated_home():
            code = cli.main(argv)
        return code, out.getvalue()

    def test_json_lists_channels(self):
        code, out = self._run(["messengers", "--json"])
        assert code == 0
        payload = json.loads(out)
        assert "state" in payload and "contacts" in payload
        assert set(payload["state"]["channels"]) == {"telegram", "mail", "whatsapp"}

    def test_plain_output_has_hint(self):
        code, out = self._run(["messengers"])
        assert code == 0
        assert "Каналы" in out and "jarvis messengers --inbox" in out

    def test_send_requires_both_keys(self):
        code, out = self._run(["messengers", "--to", "Мама"])
        assert "нужны оба ключа" in out

    def test_send_with_yes_goes_through_channel(self):
        """--yes отправляет без вопроса, но всё равно через канал (подменяем HTTP)."""
        from jarvis.interfaces import cli

        with isolated_home():
            write_contacts()
            write_config(telegram_token="токен")
            sent: list[tuple[str, str]] = []
            patcher = mock.patch.object(cli, "_assistant", lambda debug=False: _CliAssistant(sent))
            patcher.start()
            self.addCleanup(patcher.stop)
            out = io.StringIO()
            import contextlib

            with contextlib.redirect_stdout(out):
                code = cli.main(["messengers", "--channel", "telegram", "--to", "Мама",
                                 "--text", "привет", "--yes"])
            assert code == 0 and sent == [("987654321", "привет")]
            assert "Отправлено: Мама" in out.getvalue()

    def test_qr_hint(self):
        code, out = self._run(["messengers", "--qr"])
        assert "wacli auth" in out and "Связанные устройства" in out

    def test_secret_not_printed(self):
        from jarvis.interfaces import cli

        with isolated_home():
            os.environ["JARVIS_TELEGRAM_TOKEN"] = "123456:ТОКЕН-КАТОРЫЙ-НЕ-ПОКАЗЫВАЕМ"
            out = io.StringIO()
            import contextlib

            with contextlib.redirect_stdout(out):
                cli.main(["messengers"])
            assert "НЕ-ПОКАЗЫВАЕМ" not in out.getvalue()

    def test_json_inbox_runs_check(self):
        code, out = self._run(["messengers", "--json", "--inbox"])
        assert code == 0 and json.loads(out)["incoming"] == []


class _CliAssistant:
    """Ассистент-обманка для CLI: настоящий канал не нужен."""

    def __init__(self, sent: list[tuple[str, str]]):
        self._sent = sent
        self.messengers = _CliService(sent)
        self.status = lambda: {"config_path": "/tmp/config.toml"}

    def shutdown(self):
        pass


class _CliService(MessengerService):
    def __init__(self, sent):
        channels = fake_channels()
        channels.telegram.send = lambda target, text, **kwargs: (sent.append((target, text)), "1")[1]
        super().__init__(write_config(telegram_token="токен"), channels=channels)


class DefaultsTests(IsolatedTestCase):
    """Значения по умолчанию и мелочи, которые видит пользователь."""

    def test_poll_seconds_default_is_sane(self):
        self.assertTrue(10 <= MessengerService(write_config()).poll_seconds <= 3600)

    def test_mailbox_defaults(self):
        service = MessengerService(write_config())
        self.assertEqual(service.mail_limit, 5)
        self.assertEqual(service.channels.mail.mailbox, "INBOX")

    def test_timestamp_of_telegram_message_is_iso(self):
        channel, _ = telegram_with({"getUpdates": {"ok": True, "result": [
            {"update_id": 1, "message": {"date": 1700000000, "text": "x", "chat": {"id": 1}}}]}})
        messages, _offset = channel.poll(0)
        self.assertTrue(messages[0].ts.startswith("2023-11-14"))

    def test_state_does_not_need_network(self):
        """Сборка состояния не должна ходить в сеть (окно открывается сразу)."""
        start = time.monotonic()
        MessengerService(write_config()).state()
        self.assertLess(time.monotonic() - start, 5)

    def test_untouched_config_keeps_messengers_off(self):
        """Чистая установка: сообщения выключены, пока их не включат."""
        service = MessengerService(Config.load())
        self.assertFalse(service.enabled)
