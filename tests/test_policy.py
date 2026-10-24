"""Тесты политики подтверждений: чтение/запись проходят, исходящие ждут подтверждения."""


from jarvis.config import ApprovalsConfig, AutoRule
from jarvis.core.tools.base import Tool, ToolResult
from jarvis.core.tools.policy import Policy


def _tool(name: str, side_effect: str) -> Tool:
    async def handler(args: dict) -> ToolResult:
        return ToolResult(output="ок")

    return Tool(
        name=name,
        description="тест",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        side_effect=side_effect,  # type: ignore[arg-type]
    )


def test_read_and_write_allowed_without_confirmation():
    policy = Policy(ApprovalsConfig())
    assert policy.decide(_tool("read_url", "read"), {}) == "allow"
    assert policy.decide(_tool("memory_save", "write"), {}) == "allow"


def test_outbound_requires_confirmation_by_default():
    policy = Policy(ApprovalsConfig())
    assert policy.decide(_tool("telegram_send_message", "outbound"), {}) == "confirm"


def test_outbound_auto_rule_by_chat():
    policy = Policy(
        ApprovalsConfig(auto=[AutoRule(tool="telegram_send_message", chat_id="123456")])
    )
    args_match = {"chat_id": 123456}  # тип не важен — сравнение строковое
    assert policy.decide(_tool("telegram_send_message", "outbound"), args_match) == "allow"
    assert policy.decide(_tool("telegram_send_message", "outbound"), {"chat_id": "999"}) == "confirm"
    # Правило не распространяется на другие инструменты.
    assert policy.decide(_tool("vk_post", "outbound"), args_match) == "confirm"


def test_high_stakes_never_auto_approved():
    policy = Policy(
        ApprovalsConfig(auto=[AutoRule(tool="bank_transfer", chat_id=None)])
    )
    assert policy.decide(_tool("bank_transfer", "high_stakes"), {}) == "confirm"
