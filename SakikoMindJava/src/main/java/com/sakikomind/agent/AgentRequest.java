package com.sakikomind.agent;

import com.sakikomind.intent.IntentCategory;
import com.sakikomind.intent.UrgencyLevel;

import java.util.List;
import java.util.Map;
import java.util.UUID;

public record AgentRequest(
        String message,
        String userId,
        String conversationId,
        String context,
        List<Map<String, String>> history,
        IntentCategory intent,
        UrgencyLevel urgency,
        String requestId
) {
    public AgentRequest withIntent(IntentCategory intent, UrgencyLevel urgency) {
        return new AgentRequest(message, userId, conversationId, context, history, intent, urgency, requestId);
    }

    public static AgentRequest of(String message, String userId, String conversationId, String context, List<Map<String, String>> history) {
        return new AgentRequest(message, userId, conversationId, context, history, null, null, UUID.randomUUID().toString().substring(0, 8));
    }
}
