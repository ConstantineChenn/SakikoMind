package com.sakikomind.memory;

import java.time.Instant;
import java.util.Map;

public record ConversationMessage(
        MessageRole role,
        String content,
        Instant timestamp,
        Map<String, Object> metadata
) {
}
