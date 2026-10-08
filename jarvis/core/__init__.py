"""Ядро Jarvis: логика ассистента, без привязки к интерфейсу.

Слои и правило зависимостей:

* ``core``  — решения, маршрутизация, права, журнал. Ничего не знает про GUI.
* ``providers`` — внешний мир: модель, распознавание, синтез, поиск, звук.
* ``skills`` — плагины: «одна папка = один навык», объявляют действия и права.
* ``interfaces`` — терминал, демон, GUI; содержат только ввод/вывод.
* ``platform`` — различия Linux/Windows.

Всё, что можно отложить, создаётся лениво: на слабой машине важен каждый
мегабайт (см. README, раздел про память).
"""

from __future__ import annotations

from .assistant import Assistant
from .config import Config
from .errors import (
    ConfigError,
    JarvisError,
    ProviderError,
    SecretMissingError,
    SkillError,
)
from .types import Action, Intent, Permissions, Reply, Skill, State

__all__ = [
    "Action",
    "Assistant",
    "Config",
    "ConfigError",
    "Intent",
    "JarvisError",
    "Permissions",
    "ProviderError",
    "Reply",
    "SecretMissingError",
    "Skill",
    "SkillError",
    "State",
]
