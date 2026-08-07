package com.sakikomind.agent;

import com.sakikomind.llm.LlmGateway;

import java.time.Duration;
import java.time.Instant;

public abstract class BaseAgent {

    private final LlmGateway llmGateway;
    private final AgentStats stats = new AgentStats();

    protected BaseAgent(LlmGateway llmGateway) {
        this.llmGateway = llmGateway;
    }

    public abstract AgentType type();

    protected abstract String systemPrompt();

    public AgentResponse handle(AgentRequest request) {
        Instant start = Instant.now();
        try {
            String prompt = buildPrompt(request);
            String content = llmGateway.chat(systemPrompt(), prompt, 0.2, 1024);
            long latency = Duration.between(start, Instant.now()).toMillis();
            boolean escalate = needsEscalation(content);
            stats.record(true, latency);
            return new AgentResponse(type(), content, true, 1.0, latency, escalate);
        } catch (Exception ex) {
            long latency = Duration.between(start, Instant.now()).toMillis();
            stats.record(false, latency);
            return new AgentResponse(type(), "抱歉，处理您的请求时出现问题，请稍后重试。", false, 0.0, latency, false);
        }
    }

    public AgentStats stats() {
        return stats;
    }

    private String buildPrompt(AgentRequest request) {
        StringBuilder prompt = new StringBuilder();
        if (request.context() != null && !request.context().isBlank()) {
            prompt.append("[背景信息]\n").append(request.context()).append("\n\n");
        }
        prompt.append("[用户问题]\n").append(request.message());
        return prompt.toString();
    }

    private boolean needsEscalation(String content) {
        String text = content == null ? "" : content.toLowerCase();
        return text.contains("转人工")
                || text.contains("人工客服")
                || text.contains("escalate")
                || text.contains("specialist")
                || text.contains("无法处理");
    }
}
