"""Политика безопасности: какие вызовы инструментов требуют подтверждения."""

from __future__ import annotations

from jarvis.config import ApprovalsConfig
from jarvis.core.tools.base import Tool


class Policy:
    def __init__(self, config: ApprovalsConfig):
        self._config = config

    def decide(self, tool: Tool, args: dict) -> str:
        """Возвращает решение: allow | confirm.

        deny зарезервирован на будущее (чёрные списки); сейчас опасные
        действия не регистрируются вовсе (allowlist MCP-серверов).
        """
        if tool.side_effect == "high_stakes":
            # Никогда не разрешается автоматически, даже через auto-правила.
            return "confirm"
        if tool.side_effect in ("read", "write"):
            return "allow"
        # outbound — только явное разрешение в конфиге, по инструменту и чату.
        for rule in self._config.auto:
            if rule.tool != tool.name:
                continue
            if rule.chat_id is None or str(args.get("chat_id", "")) == str(rule.chat_id):
                return "allow"
        return "confirm"
