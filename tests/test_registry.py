"""Тесты реестра: подтверждения, отклонения, ошибки, аудит."""

import pytest

from jarvis.config import ApprovalsConfig
from jarvis.core.audit import AuditLog
from jarvis.core.tools.base import Tool, ToolError, ToolResult
from jarvis.core.tools.policy import Policy
from jarvis.core.tools.registry import ToolRegistry
from jarvis.memory.db import Database


def _tool(name: str, side_effect: str = "read", fail: bool = False):
    async def handler(args: dict) -> ToolResult:
        if fail:
            raise ToolError("сломалось")
        return ToolResult(output=f"выполнено {name}")

    return Tool(
        name=name,
        description="тест",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        side_effect=side_effect,  # type: ignore[arg-type]
    )


@pytest.fixture
async def registry(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    reg = ToolRegistry(Policy(ApprovalsConfig()), AuditLog(db))
    yield reg, db
    await db.close()


async def test_read_tool_executes_without_confirmation(registry):
    reg, db = registry
    reg.register(_tool("read_url"))
    result = await reg.execute("read_url", {}, confirmer=None)
    assert "выполнено" in result.output
    rows = await db.fetchall("SELECT tool, status FROM audit_log")
    assert rows[-1]["status"] == "ok"


async def test_outbound_denied_when_user_rejects(registry):
    reg, db = registry
    reg.register(_tool("send_message", side_effect="outbound"))

    async def reject(request):
        return False

    result = await reg.execute("send_message", {}, confirmer=reject)
    assert "отклонил" in result.output
    rows = await db.fetchall("SELECT decision, status FROM audit_log")
    assert rows[-1]["status"] == "denied"


async def test_outbound_allowed_when_user_approves(registry):
    reg, _ = registry
    reg.register(_tool("send_message", side_effect="outbound"))

    async def approve(request):
        return True

    result = await reg.execute("send_message", {}, confirmer=approve)
    assert result.output == "выполнено send_message"


async def test_outbound_denied_without_confirmer(registry):
    reg, _ = registry
    reg.register(_tool("send_message", side_effect="outbound"))
    result = await reg.execute("send_message", {}, confirmer=None)
    assert "требует подтверждения" in result.output


async def test_tool_error_returns_observation(registry):
    reg, db = registry
    reg.register(_tool("broken", fail=True))
    result = await reg.execute("broken", {}, confirmer=None)
    assert "Ошибка инструмента" in result.output
    rows = await db.fetchall("SELECT status FROM audit_log")
    assert rows[-1]["status"] == "error"


async def test_unknown_tool(registry):
    reg, _ = registry
    result = await reg.execute("nope", {}, confirmer=None)
    assert "не найден" in result.output


async def test_duplicate_registration(registry):
    reg, _ = registry
    reg.register(_tool("dup"))
    with pytest.raises(ValueError):
        reg.register(_tool("dup"))
