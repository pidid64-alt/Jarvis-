"""Миграция со старой версии Jarvis.

Задача — не потерять то, что уже работает:

* старые файлы копируются в резервную папку (ничего не удаляется);
* ``config.json`` / ``wakeword_config.json`` / ``autonomy.json`` превращаются
  в единственный ``config.toml`` (со ссылками на секреты, без самих секретов);
* ``commands.json`` / ``commands-win.json`` превращаются в навыки-каталоги
  (фразы сохраняются дословно, путь ``$HOME/jarvis`` → ``{legacy}``);
* ``.env`` копируется и переименовывается в единое имя ключа.
"""

from __future__ import annotations

import json
import tomllib
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths, toml_edit
from .logging_setup import get_logger
from .matcher import normalize_text

log = get_logger("core.migrate")

LEGACY_KEY_NAMES = ("JARVIS_LLM_KEY", "OMNIROUTE_API_KEY", "OPENROUTER_API_KEY")

#: Группировка команд старого каталога по смыслу (id/теги → навык)
GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("apps", "Приложения", ("open_", "close_", "start_", "discord", "telegram", "spotify", "firefox",
                            "browser", "code", "editor", "terminal")),
    ("desktop", "Окна и рабочий стол", ("window", "workspace", "screenshot", "clipboard", "brightness",
                                        "wallpaper", "monitor", "zoom", "tab", "panel", "compositor",
                                        "notification", "touchpad", "night_light", "keyboard", "mouse")),
    ("hardware", "Железо и датчики", ("cpu", "temp", "fan", "battery", "disk", "mem", "swap", "zram",
                                      "gpu", "usb", "smart", "governor", "io", "bluetooth", "webcam")),
    ("network", "Сеть", ("network", "wifi", "internet", "dns", "tailscale", "vpn", "ping", "firewall",
                         "port", "ssh", "speed_test")),
    ("media", "Медиа и звук", ("volume", "media", "mute", "audio", "speaker", "player", "music",
                               "microphone", "record", "camera")),
    ("packages", "Пакеты и обновления", ("update", "package", "pacman", "aur", "npm", "pip", "cache",
                                         "orphan", "flatpak", "snap")),
    ("power", "Питание и сессия", ("power", "reboot", "shutdown", "lock", "logout", "suspend", "sleep",
                                   "session", "users")),
    ("services", "Службы и логи", ("service", "journal", "log", "systemd", "docker", "boot", "ollama",
                                   "litvin", "noxy", "earlyoom", "kernel", "failed")),
    ("files", "Файлы", ("file", "trash", "download", "backup", "archive", "find", "directories",
                        "screenshot", "solve", "coding")),
    ("info", "Информация о системе", ("status", "info", "version", "time", "date", "news", "weather",
                                     "health", "process", "top", "count", "calendar", "list")),
    ("misc", "Разное", ()),
]

AUTONOMY_MESSAGES = {
    "cpu_temp_alert": ("cpu_temp", "Температура процессора {temperature} градусов — жарковато."),
    "disk_space_warning": ("disk", "Свободного места на диске: {free_percent}% — стоит почистить."),
    "high_memory": ("memory", "Память занята на {used_percent}%."),
    "battery_low": ("battery", "Заряд батареи {charge_percent}%."),
    "internet_check": ("internet", "Интернета нет."),
}

#: старые проверки, которые выполняются командами перенесённого каталога
AUTONOMY_BY_ACTION = {
    "hourly_failed_services": "failed_services",
    "logs_errors": "journal_errors",
    "morning_updates": "check_updates",
}

_HOME_JARVIS_RE = re.compile(r"\$HOME/jarvis|~/jarvis|%USERPROFILE%\\\\jarvis", re.IGNORECASE)


@dataclass
class MigrationReport:
    backup_dir: Path | None = None
    config_written: bool = False
    skills: list[str] = field(default_factory=list)
    actions: int = 0
    #: старые команды, которые не нужны: их фразы уже делает встроенный навык
    superseded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dry_run: bool = False

    def lines(self) -> list[str]:
        head = ["Миграция со старой версии Jarvis" + (" (пробный прогон)" if self.dry_run else "")]
        if self.backup_dir:
            head.append(f"Резервные копии: {self.backup_dir}")
        head.append(f"Настройки: {'записаны' if self.config_written else 'не менялись'} "
                    f"({paths.config_path()})")
        head.append(f"Навыки: {', '.join(self.skills) if self.skills else '—'}")
        head.append(f"Действий перенесено: {self.actions}")
        if self.superseded:
            head.append(f"Заменено встроенными навыками: {len(self.superseded)} "
                        f"({', '.join(self.superseded[:5])}{'…' if len(self.superseded) > 5 else ''})")
        if self.skipped:
            head.append(f"Пропущено: {len(self.skipped)} "
                        f"({', '.join(self.skipped[:5])}{'…' if len(self.skipped) > 5 else ''})")
        head.extend(f"Примечание: {note}" for note in self.notes)
        return head


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("не удалось прочитать %s: %s", path, exc)
        return default


def _backup(src: Path, target_dir: Path, name: str | None = None) -> Path | None:
    if not src.exists():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / (name or src.name)
    if src.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(src, destination)
    else:
        shutil.copy2(src, destination)
    return destination


def _group_for(action_id: str, tags: list[str]) -> str:
    haystack = f"{action_id} {' '.join(tags)}".lower()
    for group_id, _name, keys in GROUPS:
        if any(key in haystack for key in keys):
            return group_id
    return "misc"


def _rewrite_paths(command: str, legacy: Path) -> str:
    """Старый путь ``$HOME/jarvis`` → переносимый ``{legacy}``.

    Абсолютный путь не подставляем: на другой машине репозиторий будет лежать
    в другом месте, а ``{legacy}`` ядро раскрывает при запуске. Обратные слэши
    меняем на прямые — их понимают и bash, и PowerShell, и Python.
    """
    command = _HOME_JARVIS_RE.sub("{legacy}", command)
    command = command.replace("{base}", "{legacy}")
    command = command.replace(str(legacy), "{legacy}")
    return command.replace("{legacy}\\", "{legacy}/")


def _platform_of(entry: dict[str, Any]) -> str:
    return "windows" if "win" in str(entry.get("_source", "")) else "linux"


def _command_skill(entries: list[dict[str, Any]], platform: str, legacy: Path,
                   reserved: set[str] | None = None, seen: set[str] | None = None
                   ) -> tuple[dict[str, Any], list[str]]:
    """Собирает навык из старых команд, выбрасывая фразы, которые уже заняты.

    ``reserved`` — фразы встроенных навыков, ``seen`` — фразы, уже занятые
    другими перенесёнными командами (дубликаты в старом каталоге).
    """
    reserved = reserved or set()
    seen = reserved if seen is None else seen
    actions = []
    superseded: list[str] = []
    for entry in entries:
        if not entry.get("command"):
            continue
        phrases = []
        for phrase in entry.get("phrases", []):
            normalized = normalize_text(str(phrase))
            if not normalized or normalized in reserved or normalized in seen:
                continue
            seen.add(normalized)
            phrases.append(str(phrase))
        if not phrases:
            # Все фразы команды уже заняты встроенным навыком: старую копию
            # не переносим, но и не прячем — она попадёт в отчёт миграции.
            superseded.append(str(entry.get("id", "?")))
            continue
        tags = [str(tag) for tag in entry.get("tags", [])]
        actions.append({
            "id": str(entry["id"]),
            "phrases": phrases,
            "description": str(entry.get("description", "")),
            "command": _rewrite_paths(str(entry["command"]), legacy),
            "response": str(entry.get("response", "")),
            "speak_output": bool(entry.get("speak_output", False)),
            "speak_before": bool(entry.get("speak_before", False)),
            "background": bool(entry.get("background", False)),
            "confirm": bool(entry.get("confirm", False)),
            "confirm_prompt": str(entry.get("confirm_prompt", "")),
            "timeout": float(entry.get("timeout", 15)),
            "min_score": entry.get("min_score"),
            "done_message": str(entry.get("done_message", "")),
            "tags": tags,
            "platforms": [platform],
            "autonomy_safe": bool(entry.get("autonomy_safe", False)),
        })
    permissions = {"shell": True, "notify": False, "network": any("network" in a["tags"] for a in actions),
                   "files": any("files" in a["tags"] for a in actions),
                   "dangerous": any("dangerous" in a["tags"] or a["confirm"] for a in actions)}
    return {
        "id": "legacy_legacy",
        "name": "Старые команды",
        "description": "Команды, перенесённые из предыдущей версии Jarvis.",
        "version": "1.0",
        "permissions": permissions,
        "examples": [],
        "actions": actions,
    }, superseded


#: разделы, которые могли появиться уже в новой версии: при повторном
#: переносе их значения важнее шаблона (там могут быть и ссылки на пароли)
KEEP_SECTIONS = ("messengers", "mcp")


def _keep_current(text: str, current_text: str | None,
                  notes: list[str]) -> str:
    """Возвращает настройки, которые пользователь уже поставил в новой версии.

    Миграцию иногда запускают повторно (``--force``). Тогда настройки, которых
    не было в старой версии, — например почта и телеграм в ``[messengers]`` —
    должны остаться, иначе человек потеряет уже сделанное.
    """
    if not current_text:
        return text
    try:
        current = tomllib.loads(current_text)
    except tomllib.TOMLDecodeError:
        return text
    for section in KEEP_SECTIONS:
        values = current.get(section)
        if not isinstance(values, dict):
            continue
        kept: list[str] = []
        for key, value in values.items():
            if isinstance(value, dict) or isinstance(value, list):
                continue
            default = _value_of(text, (section, key))
            if value == default:
                continue
            text = toml_edit.set_value(text, (section, key), value)
            kept.append(str(key))
        if kept:
            notes.append(f"сохранены прежние настройки [{section}]: {', '.join(kept)}")
    return text


def _value_of(text: str, path: tuple[str, ...]) -> Any:
    """Значение из текста конфигурации (для сравнения с шаблоном)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    current: Any = data
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _build_config(legacy: Path, base_text: str,
                  current_text: str | None = None) -> tuple[str, list[str]]:
    notes: list[str] = []
    old_config = _load_json(legacy / "config.json", {}) or {}
    wake = _load_json(legacy / "wakeword_config.json", {}) or {}
    text = base_text

    def set_value(path: tuple[str, ...], value: Any) -> None:
        nonlocal text
        text = toml_edit.set_value(text, path, value)

    llm = old_config.get("llm") or {}
    if llm:
        set_value(("llm", "base_url"), llm.get("base_url", "http://localhost:20128/v1"))
        set_value(("llm", "model"), llm.get("model", ""))
        set_value(("llm", "enabled"), bool(llm.get("enabled", True)))
        set_value(("llm", "timeout_seconds"), float(llm.get("timeout_seconds", 8)))
        set_value(("llm", "max_retries"), int(llm.get("max_retries", 1)))
        set_value(("llm", "max_tokens"), int(llm.get("max_tokens", 400)))
        set_value(("llm", "api_key"), "${JARVIS_LLM_KEY}")
        notes.append("ключ LLM теперь хранится как JARVIS_LLM_KEY в .env")

    for key in ("record_seconds", "max_record_seconds", "silence_seconds", "wait_speech_seconds",
                "start_speech_seconds", "min_speech_seconds", "vad_aggressiveness", "sample_rate"):
        if key in old_config:
            set_value(("voice", key), old_config[key])
    if old_config.get("vad_silence_seconds") is not None:
        set_value(("voice", "silence_seconds"), float(old_config["vad_silence_seconds"]))
    if old_config.get("vad_wait_speech_seconds") is not None:
        set_value(("voice", "wait_speech_seconds"), float(old_config["vad_wait_speech_seconds"]))
    if old_config.get("vad_aggressiveness") is not None:
        set_value(("voice", "vad_aggressiveness"), int(old_config["vad_aggressiveness"]))
    if old_config.get("match_threshold") is not None:
        set_value(("assistant", "match_threshold"), float(old_config["match_threshold"]))
    if old_config.get("match_ambiguity_margin") is not None:
        set_value(("assistant", "ambiguity_margin"), float(old_config["match_ambiguity_margin"]))
    if old_config.get("exact_max_extra_words") is not None:
        set_value(("assistant", "max_extra_words"), int(old_config["exact_max_extra_words"]))

    if wake.get("model_path"):
        model = legacy / "models" / "wakeword" / str(wake["model_path"])
        set_value(("voice", "wakeword", "model"), str(model))
        set_value(("voice", "wakeword", "threshold"), float(wake.get("threshold", 0.25)))
        set_value(("voice", "wakeword", "cooldown_seconds"), float(wake.get("cooldown_seconds", 3.0)))
        if model.exists():
            notes.append(f"модель слова-активатора найдена: {model.name}")

    stt_model = legacy / "models" / f"ggml-{old_config.get('whisper_model', 'base-q5_1')}.bin"
    if stt_model.exists():
        set_value(("stt", "model"), str(stt_model))
        server = legacy / "whisper.cpp" / "build" / "bin" / "whisper-server"
        if server.exists():
            set_value(("stt", "server_command"),
                      f"{server} --host 127.0.0.1 --port 8081 -m {stt_model} -l ru")
        else:
            notes.append("не нашёл собранный whisper-server: распознавание нужно настроить заново")
    win_server = legacy / "whisper-win" / "Release" / "whisper-server.exe"
    if win_server.exists():
        set_value(("stt", "server_command"),
                  f"{win_server} --host 127.0.0.1 --port 8081 -m {stt_model} -l ru")

    voice_dir = legacy / "models"
    if (voice_dir / "ru_RU-dmitri-medium.onnx").exists():
        set_value(("tts", "data_dir"), str(voice_dir))
        notes.append("голос Piper найден и переиспользуется (не нужно качать заново)")

    text = _keep_current(text, current_text, notes)
    return text, notes


def _autonomy_rules(legacy: Path) -> tuple[list[dict[str, Any]], list[str]]:
    data = _load_json(legacy / "autonomy.json", {}) or {}
    rules = data.get("rules") or []
    result: list[dict[str, Any]] = []
    skipped: list[str] = []
    for rule in rules:
        rule_id = str(rule.get("id", ""))
        check = str(rule.get("check", ""))
        base: dict[str, Any] = {"id": rule_id, "repeat_seconds": float(rule.get("repeat_seconds", 21600))}
        if rule_id in AUTONOMY_MESSAGES:
            metric, message = AUTONOMY_MESSAGES[rule_id]
            base.update({
                "check": metric,
                "metric": {"cpu_temp_alert": "temperature", "disk_space_warning": "free_percent",
                           "high_memory": "used_percent", "battery_low": "charge_percent",
                           "internet_check": "text"}.get(rule_id, "value"),
                "message": message,
            })
            if rule_id == "internet_check":
                base = {"id": rule_id, "check": "internet", "if_equals": "Интернета нет.",
                        "message": message, "repeat_seconds": base["repeat_seconds"]}
            for key in ("if_above", "if_below", "if_below_percent", "if_above_percent", "if_equals",
                        "only_if_on_battery"):
                if key in rule:
                    base[key] = rule[key]
            result.append(base)
        elif rule_id in AUTONOMY_BY_ACTION and check in {v for v in AUTONOMY_BY_ACTION.values()}:
            action_id = AUTONOMY_BY_ACTION[rule_id]
            base.update({"check": f"action:{action_id}", "if_not_empty": True,
                         "message": "{text}"})
            result.append(base)
        else:
            skipped.append(f"{rule_id} (нет локальной проверки)")
    return result, skipped


def _builtin_phrases() -> set[str]:
    """Фразы встроенных навыков — старые дубликаты переносить не нужно."""

    phrases: set[str] = set()
    for manifest in paths.bundled_skills_dir().glob("*/skill.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for action in data.get("actions", []) or []:
            for phrase in action.get("phrases", []) or []:
                normalized = normalize_text(str(phrase))
                if normalized:
                    phrases.add(normalized)
    return phrases


def migrate(*, dry_run: bool = False, force: bool = False) -> MigrationReport:
    """Переносит данные старой версии в новую. Идемпотентно."""
    report = MigrationReport(dry_run=dry_run)
    legacy = paths.legacy_dir()
    if not legacy.exists():
        report.notes.append("папка legacy/ не найдена — переносить нечего")
        return report

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = paths.state_dir() / f"migration-backup-{stamp}"

    for name in ("config.json", "wakeword_config.json", "autonomy.json", "commands.json",
                 "commands-win.json", "discord_contacts.json"):
        if not dry_run:
            _backup(legacy / name, backup_dir, name)
    old_env = paths.env_file_path()
    if old_env.exists() and not dry_run:
        _backup(old_env, backup_dir, "env")
    current_config = paths.config_path()
    current_text = (current_config.read_text(encoding="utf-8")
                    if current_config.exists() else None)
    if current_text is not None and not dry_run:
        _backup(current_config, backup_dir, "config.toml")

    # ----------------------------------------------------------------- config
    # (шаблон новой версии + значения из старой; своё из новой версии — важнее)

    report.backup_dir = None if dry_run else backup_dir

    template = paths.bundled_config_path().read_text(encoding="utf-8")
    config_text, notes = _build_config(legacy, template, current_text)
    report.notes.extend(notes)

    rules, skipped_rules = _autonomy_rules(legacy)
    report.skipped.extend(skipped_rules)
    if rules:
        # В шаблоне настроек стоит пустой массив; массив таблиц [[autonomy.rules]]
        # рядом с ним TOML запрещает, поэтому пустой ключ убираем.
        config_text = toml_edit.remove_key(config_text, ("autonomy", "rules"))
        config_text += "\n" + _rules_to_toml(rules)
        report.notes.append(f"автономных правил перенесено: {len(rules)}")

    if paths.config_path().exists() and not force:
        report.notes.append("config.toml уже существует — оставил как есть (используйте --force)")
    else:
        report.config_written = True
        if not dry_run:
            paths.config_dir().mkdir(parents=True, exist_ok=True)
            paths.config_path().write_text(config_text, encoding="utf-8")

    # ----------------------------------------------------------------- skills
    for filename, platform in (("commands.json", "linux"), ("commands-win.json", "windows")):
        data = _load_json(legacy / filename, {}) or {}
        entries = data.get("commands") or []
        for entry in entries:
            entry["_source"] = filename
        builtin = _builtin_phrases()
        grouped: dict[str, list[dict[str, Any]]] = {}
        skipped_builtin = 0
        for entry in entries:
            if not entry.get("command") or not entry.get("phrases"):
                report.skipped.append(str(entry.get("id", "?")))
                continue
            grouped.setdefault(_group_for(str(entry.get("id", "")), list(entry.get("tags") or [])), []).append(entry)
        if not grouped:
            continue
        seen_phrases: set[str] = set(builtin)
        for group_id, group_entries in grouped.items():
            skill_id = f"legacy_{group_id}" if platform == "linux" else f"legacy_win_{group_id}"
            skill, superseded = _command_skill(group_entries, platform, legacy,
                                               reserved=builtin, seen=seen_phrases)
            report.superseded.extend(superseded)
            skill["id"] = skill_id
            group_title = dict((g[0], g[1]) for g in GROUPS).get(group_id, group_id)
            if platform == "windows":
                group_title += " (Windows)"
            skill["name"] = f"Старые команды: {group_title}"
            skill["description"] = ("Команды из прежней версии Jarvis "
                                    f"({platform}, {len(group_entries)} шт.), перенесены автоматически.")
            report.skills.append(skill_id)
            report.actions += len(skill["actions"])
            if not dry_run:
                folder = paths.user_skills_dir() / skill_id
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "skill.json").write_text(
                    json.dumps(skill, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                report.notes.append(f"навык {skill_id}: {len(skill['actions'])} команд → {folder}")

    # ------------------------------------------------------------------ .env
    env_values = _read_env(old_env)
    if env_values and not dry_run:
        from . import secrets

        for name in LEGACY_KEY_NAMES:
            if env_values.get(name):
                if name != "JARVIS_LLM_KEY":
                    secrets.save_env_var("JARVIS_LLM_KEY", env_values[name], old_env)
                    report.notes.append("ключ доступа перенесён в JARVIS_LLM_KEY (значение не печаталось)")
                break
    return report


def _rules_to_toml(rules: list[dict[str, Any]]) -> str:
    lines = ["# Правила автономных проверок (перенесены из старой версии)"]
    for rule in rules:
        lines.append("")
        lines.append("[[autonomy.rules]]")
        for key, value in rule.items():
            lines.append(f"{key} = {toml_edit.format_value(value)}")
    return "\n".join(lines) + "\n"


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    from .secrets import parse_env

    try:
        return parse_env(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
