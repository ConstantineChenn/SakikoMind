import tempfile
import unittest
from pathlib import Path

from core.skill_loader import SkillManager

class SkillLoaderTests(unittest.TestCase):
    def test_skill_escalation_keywords_require_handoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir) / "account_security"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                """---
name: 账号安全规范
keywords: 陌生设备,密码重置
agents: general
escalation_keywords: 陌生设备,密码重置
enabled: true
---

# 账号安全规范
""",
                encoding="utf-8",
            )

            manager = SkillManager(root_dir=temp_dir)
            manager.load()

            message = "我发现账号在陌生设备登录，需要密码重置"
            usage = manager.usage_for(message, "general")

            self.assertEqual(len(usage), 1)
            self.assertEqual(usage[0]["matched_keywords"], ["陌生设备", "密码重置"])
            self.assertTrue(manager.requires_escalation(message, "general"))
            self.assertFalse(manager.requires_escalation("你好，想了解套餐", "general"))
