package com.sakikomind.agent;

import com.sakikomind.intent.IntentCategory;

public record OrchestratorResult(
        String requestId,
        String response,
        AgentType agentType,
        IntentCategory intent,
        boolean escalated,
        long latencyMs
) {
}
