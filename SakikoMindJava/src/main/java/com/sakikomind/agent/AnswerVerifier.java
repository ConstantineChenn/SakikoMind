package com.sakikomind.agent;

import com.sakikomind.llm.LlmGateway;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class AnswerVerifier {

    private final LlmGateway llmGateway;
    private final ObjectMapper objectMapper;

    public AnswerVerifier(LlmGateway llmGateway, ObjectMapper objectMapper) {
        this.llmGateway = llmGateway;
        this.objectMapper = objectMapper;
    }

    public VerificationResult verify(String question, String answer, String context) {
        String prompt = """
                你是客服回答质量校验器。判断回答是否解决用户问题、是否基于上下文、是否需要转人工。
                用户问题: %s
                回答: %s
                上下文: %s
                返回 JSON: {"pass":true,"grounded":true,"need_escalation":false,"reason":"..."}
                """.formatted(question, answer, context == null ? "" : context);
        try {
            String raw = llmGateway.chat("", prompt, 0.0, 256);
            int start = raw.indexOf('{');
            int end = raw.lastIndexOf('}');
            Map<String, Object> data = objectMapper.readValue(raw.substring(start, end + 1), new TypeReference<>() {
            });
            return new VerificationResult(
                    Boolean.TRUE.equals(data.get("pass")),
                    Boolean.TRUE.equals(data.get("grounded")),
                    Boolean.TRUE.equals(data.get("need_escalation")),
                    String.valueOf(data.getOrDefault("reason", ""))
            );
        } catch (Exception ex) {
            boolean grounded = context != null && !context.isBlank();
            return new VerificationResult(true, grounded, answer != null && answer.contains("转人工"), "verifier fallback");
        }
    }

    public record VerificationResult(boolean pass, boolean grounded, boolean needEscalation, String reason) {
    }
}
