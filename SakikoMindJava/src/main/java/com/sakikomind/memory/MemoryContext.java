package com.sakikomind.memory;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;

public record MemoryContext(
        List<ConversationMessage> recentMessages,
        List<String> relevantHistory,
        Map<String, Object> userProfile,
        String summary
) {
    public String toPromptText(ObjectMapper objectMapper) {
        StringBuilder prompt = new StringBuilder();
        if (summary != null && !summary.isBlank()) {
            prompt.append("[会话摘要]\n").append(summary).append("\n\n");
        }
        if (relevantHistory != null && !relevantHistory.isEmpty()) {
            prompt.append("[相关历史]\n");
            relevantHistory.stream().limit(3).forEach(item -> prompt.append("- ").append(item).append('\n'));
            prompt.append('\n');
        }
        if (userProfile != null && !userProfile.isEmpty()) {
            prompt.append("[用户画像]\n");
            try {
                prompt.append(objectMapper.writeValueAsString(userProfile));
            } catch (JsonProcessingException ex) {
                prompt.append(userProfile);
            }
            prompt.append("\n\n");
        }
        if (recentMessages != null && !recentMessages.isEmpty()) {
            prompt.append("[最近对话]\n");
            for (ConversationMessage message : recentMessages) {
                prompt.append(message.role().name().toLowerCase()).append(": ").append(message.content()).append('\n');
            }
        }
        return prompt.toString().trim();
    }
}
