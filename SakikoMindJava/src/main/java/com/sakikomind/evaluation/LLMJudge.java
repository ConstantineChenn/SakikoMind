package com.sakikomind.evaluation;

import com.sakikomind.llm.LlmGateway;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class LLMJudge {

    private final LlmGateway llmGateway;
    private final ObjectMapper objectMapper;

    public LLMJudge(LlmGateway llmGateway, ObjectMapper objectMapper) {
        this.llmGateway = llmGateway;
        this.objectMapper = objectMapper;
    }

    public QualityScores judge(String question, String response, String context) {
        String prompt = """
                你是客服质量评估专家。请对以下客服响应进行评分。
                用户问题: %s
                Agent 响应: %s
                背景信息: %s

                从 relevance、accuracy、completeness、helpfulness 四个维度评分，范围 0.0-1.0。
                只返回 JSON，例如 {"relevance":0.9,"accuracy":0.8,"completeness":0.7,"helpfulness":0.85}
                """.formatted(question, response, context == null ? "" : context);
        try {
            String raw = llmGateway.chat("", prompt, 0.0, 256);
            int start = raw.indexOf('{');
            int end = raw.lastIndexOf('}');
            Map<String, Object> data = objectMapper.readValue(raw.substring(start, end + 1), new TypeReference<>() {
            });
            return new QualityScores(
                    asDouble(data.get("relevance"), 0.5),
                    asDouble(data.get("accuracy"), 0.5),
                    asDouble(data.get("completeness"), 0.5),
                    asDouble(data.get("helpfulness"), 0.5),
                    false,
                    null
            );
        } catch (Exception ex) {
            return new QualityScores(0.5, 0.5, 0.5, 0.5, true, ex.getMessage());
        }
    }

    private double asDouble(Object value, double fallback) {
        if (value instanceof Number number) {
            return Math.max(0.0, Math.min(1.0, number.doubleValue()));
        }
        try {
            return Math.max(0.0, Math.min(1.0, Double.parseDouble(String.valueOf(value))));
        } catch (Exception ex) {
            return fallback;
        }
    }
}
