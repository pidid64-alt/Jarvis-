"""Тесты прав: что разрешено навыку и когда ядро просит подтверждение."""

from __future__ import annotations

import unittest

from tests.helpers import isolated_home  # noqa: F401

from jarvis.core.errors import PermissionDeniedError
from jarvis.core.permissions import PermissionPolicy
from jarvis.core.types import Action, Permissions, Skill


def skill_with(action: Action, permissions: Permissions, enabled: bool = True) -> Skill:
    return Skill(id="demo", name="Демо", description="", permissions=permissions,
                 actions=[action], enabled=enabled)


class MarkDangerousTests(unittest.TestCase):
    def setUp(self):
        self.policy = PermissionPolicy()

    def test_explicit_confirm(self):
        action = self.policy.mark_dangerous(Action(id="x", phrases=["x"], confirm=True))
        self.assertTrue(action.is_dangerous)

    def test_tag_dangerous(self):
        action = self.policy.mark_dangerous(Action(id="x", phrases=["x"], tags=["dangerous"]))
        self.assertTrue(action.is_dangerous)

    def test_rm_rf_detected_automatically(self):
        action = self.policy.mark_dangerous(Action(id="clean", phrases=["очисти"],
                                                  command="rm -rf /tmp/*"))
        self.assertTrue(action.danger)
        self.assertIn("удаление", action.confirm_prompt)

    def test_sudo_detected(self):
        action = self.policy.mark_dangerous(Action(id="upd", phrases=["обнови"],
                                                   command="sudo pacman -Syu"))
        self.assertTrue(action.danger)

    def test_outgoing_detected(self):
        action = self.policy.mark_dangerous(Action(id="discord_message", phrases=["напиши"],
                                                   command="bash {base}/scripts/discord_message.sh"))
        self.assertTrue(action.danger)
        self.assertIn("outgoing", action.tags)

    def test_safe_command_stays_safe(self):
        action = self.policy.mark_dangerous(Action(id="kernel", phrases=["версия ядра"],
                                                   command="uname -r"))
        self.assertFalse(action.danger)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = PermissionPolicy(mode="restricted")

    def test_shell_not_granted_is_denied(self):
        action = Action(id="run", phrases=["сделай"], command="ls")
        skill = skill_with(action, Permissions(shell=False))
        decision = self.policy.check(skill, action)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "shell_not_granted")
        with self.assertRaises(PermissionDeniedError):
            self.policy.require(skill, action)

    def test_disabled_skill_is_denied(self):
        action = Action(id="say", phrases=["скажи"])
        skill = skill_with(action, Permissions(), enabled=False)
        self.assertFalse(self.policy.check(skill, action).allowed)

    def test_dangerous_action_requires_confirmation(self):
        action = self.policy.mark_dangerous(Action(id="off", phrases=["выключи"], command="systemctl poweroff"))
        skill = skill_with(action, Permissions(shell=True, dangerous=True))
        decision = self.policy.check(skill, action)
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.needs_confirmation)

    def test_safe_action_in_dangerous_skill_is_not_confirmed(self):
        """Навык может быть опасным целиком, но конкретное действие — безобидным."""
        action = Action(id="kernel", phrases=["версия ядра"], command="uname -r")
        skill = skill_with(action, Permissions(shell=True, dangerous=True))
        decision = self.policy.check(skill, action)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.needs_confirmation)

    def test_mode_normal_skips_extra_checks(self):
        policy = PermissionPolicy(mode="normal")
        action = Action(id="kernel", phrases=["версия ядра"], command="uname -r")
        skill = skill_with(action, Permissions(shell=True))
        self.assertFalse(policy.check(skill, action).needs_confirmation)

    def test_unknown_mode_falls_back_to_restricted(self):
        self.assertEqual(PermissionPolicy(mode="что-то").mode, "restricted")


if __name__ == "__main__":
    unittest.main()
