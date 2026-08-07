import unittest

from agents.agent_orchestrator import AgentOrchestrator, AgentType, BaseAgent
from core.intent_recognizer import IntentCategory, UrgencyLevel


class OrchestratorRuleTests(unittest.TestCase):
    def test_account_intent_routes_to_general_agent(self):
        orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        orchestrator._pool = {
            AgentType.GENERAL: [object()],
            AgentType.TECHNICAL: [object()],
            AgentType.BILLING: [object()],
        }

        agent_type = orchestrator._route(IntentCategory.ACCOUNT, UrgencyLevel.LOW)

        self.assertEqual(agent_type, AgentType.GENERAL)

    def test_human_service_text_does_not_create_false_escalation(self):
        agent = BaseAgent.__new__(BaseAgent)

        self.assertFalse(agent._needs_escalation("如需修改发票信息，请联系人工客服核验。"))
        self.assertTrue(agent._needs_escalation("[ESCALATE] 需要人工处理"))

    def test_critical_technical_incident_stays_with_technical_agent(self):
        orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        orchestrator._pool = {
            AgentType.GENERAL: [object()],
            AgentType.TECHNICAL: [object()],
            AgentType.BILLING: [object()],
        }

        agent_type = orchestrator._route(IntentCategory.TECHNICAL, UrgencyLevel.CRITICAL)

        self.assertEqual(agent_type, AgentType.TECHNICAL)
