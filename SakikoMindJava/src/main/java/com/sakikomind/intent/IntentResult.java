package com.sakikomind.intent;

import java.util.List;
import java.util.Map;

public record IntentResult(
        IntentCategory intent,
        double confidence,
        UrgencyLevel urgency,
        Map<String, List<String>> entities,
        String reasoning,
        long latencyMs
) {
}
