package com.sakikomind.intent;

import com.sakikomind.llm.LlmGateway;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class IntentRecognizer {

    private static final Map<IntentCategory, List<String>> TEMPLATES = Map.of(
            IntentCategory.QUERY, List.of("我的订单状态是什么", "如何重置密码", "快递什么时候到"),
            IntentCategory.COMPLAINT, List.of("服务太差了", "一直没人处理", "等了好几个小时"),
            IntentCategory.REQUEST, List.of("帮我取消订单", "我需要修改地址", "请协助退款"),
            IntentCategory.GREETING, List.of("你好", "嗨有人吗", "hello"),
            IntentCategory.ESCALATION, List.of("转人工客服", "我要投诉", "找你们经理"),
            IntentCategory.TECHNICAL, List.of("应用一直崩溃", "无法登录", "出现500错误"),
            IntentCategory.BILLING, List.of("为什么扣了两次款", "申请退款", "发票问题"),
            IntentCategory.ACCOUNT, List.of("修改邮箱", "注销账户", "更新个人信息"),
            IntentCategory.FEEDBACK, List.of("服务很棒", "非常满意", "给个好评")
    );

    private final LlmGateway llmGateway;
    private final ObjectMapper objectMapper;
    private final Map<String, IntentResult> cache = new ConcurrentHashMap<>();

    public IntentRecognizer(LlmGateway llmGateway, ObjectMapper objectMapper) {
        this.llmGateway = llmGateway;
        this.objectMapper = objectMapper;
    }

    public IntentResult recognize(String message, List<Map<String, String>> history) {
        String key = normalize(message);
        IntentResult cached = cache.get(key);
        if (cached != null) {
            return cached;
        }
        Instant start = Instant.now();
        Map<String, Object> llm = llmRecognize(message, history);
        Map<String, Object> semantic = semanticRecognize(message);
        Map<String, Object> pattern = patternRecognize(message);
        IntentCategory intent = vote(llm, semantic, pattern);
        UrgencyLevel urgency = urgency(message, intent);
        IntentResult result = new IntentResult(
                intent,
                ((Number) llm.getOrDefault("confidence", 0.0)).doubleValue(),
                urgency,
                extractEntities(message),
                String.valueOf(llm.getOrDefault("reasoning", "")),
                Duration.between(start, Instant.now()).toMillis()
        );
        if (cache.size() > 1000) {
            cache.clear();
        }
        cache.put(key, result);
        return result;
    }

    private Map<String, Object> llmRecognize(String message, List<Map<String, String>> history) {
        StringBuilder examples = new StringBuilder();
        TEMPLATES.forEach((intent, samples) ->
                examples.append("消息: \"").append(samples.getFirst()).append("\" -> 意图: ")
                        .append(intent.name().toLowerCase(Locale.ROOT)).append('\n'));
        String prompt = """
                你是客服意图分析专家。根据示例判断用户意图，返回 JSON。
                示例:
                %s
                最近对话:
                %s
                用户消息: "%s"
                返回格式: {"intent":"technical","confidence":0.9,"reasoning":"一句话说明"}
                可选意图: query, complaint, request, greeting, escalation, technical, billing, account, feedback, other
                """.formatted(examples, history == null ? "" : history, message);
        try {
            String raw = llmGateway.chat("", prompt, 0.1, 256);
            String json = sliceJsonObject(raw);
            Map<String, Object> data = objectMapper.readValue(json, new TypeReference<>() {
            });
            data.put("intent", parseIntent(String.valueOf(data.get("intent"))));
            return data;
        } catch (Exception ex) {
            Map<String, Object> fallback = new HashMap<>();
            fallback.put("intent", IntentCategory.OTHER);
            fallback.put("confidence", 0.0);
            fallback.put("reasoning", "LLM recognition failed");
            fallback.put("failed", true);
            return fallback;
        }
    }

    private Map<String, Object> semanticRecognize(String message) {
        IntentCategory best = IntentCategory.OTHER;
        double bestScore = 0.0;
        for (Map.Entry<IntentCategory, List<String>> entry : TEMPLATES.entrySet()) {
            for (String sample : entry.getValue()) {
                double score = jaccard(charNgrams(message), charNgrams(sample));
                if (score > bestScore) {
                    bestScore = score;
                    best = entry.getKey();
                }
            }
        }
        return Map.of("intent", best, "confidence", bestScore);
    }

    private Map<String, Object> patternRecognize(String message) {
        String msg = normalize(message);
        Map<IntentCategory, List<String>> patterns = Map.of(
                IntentCategory.ESCALATION, List.of("投诉", "经理", "转人工", "supervisor"),
                IntentCategory.COMPLAINT, List.of("太差", "糟糕", "horrible", "等了很久"),
                IntentCategory.QUERY, List.of("?", "？", "怎么", "什么", "status"),
                IntentCategory.REQUEST, List.of("帮我", "需要", "please", "help"),
                IntentCategory.GREETING, List.of("你好", "嗨", "hello", "hi"),
                IntentCategory.BILLING, List.of("退款", "扣款", "发票", "refund"),
                IntentCategory.TECHNICAL, List.of("崩溃", "报错", "error", "crash", "500", "401"),
                IntentCategory.ACCOUNT, List.of("密码", "邮箱", "账户", "password")
        );
        IntentCategory best = IntentCategory.OTHER;
        double bestScore = 0.0;
        for (Map.Entry<IntentCategory, List<String>> entry : patterns.entrySet()) {
            long hits = entry.getValue().stream().filter(msg::contains).count();
            if (hits > 0) {
                double score = (double) hits / entry.getValue().size();
                if (score > bestScore) {
                    bestScore = score;
                    best = entry.getKey();
                }
            }
        }
        return Map.of("intent", best, "confidence", bestScore);
    }

    private IntentCategory vote(Map<String, Object> llm, Map<String, Object> semantic, Map<String, Object> pattern) {
        if (Boolean.TRUE.equals(llm.get("failed"))) {
            return (IntentCategory) pattern.getOrDefault("intent", IntentCategory.OTHER);
        }
        Map<IntentCategory, Double> scores = new EnumMap<>(IntentCategory.class);
        addScore(scores, llm, 0.70);
        addScore(scores, semantic, 0.20);
        addScore(scores, pattern, 0.10);
        return scores.entrySet().stream()
                .max(Map.Entry.comparingByValue())
                .filter(e -> e.getValue() >= 0.35)
                .map(Map.Entry::getKey)
                .orElse(IntentCategory.OTHER);
    }

    private void addScore(Map<IntentCategory, Double> scores, Map<String, Object> result, double weight) {
        IntentCategory intent = (IntentCategory) result.getOrDefault("intent", IntentCategory.OTHER);
        double confidence = ((Number) result.getOrDefault("confidence", 0.0)).doubleValue();
        scores.merge(intent, weight * confidence, Double::sum);
    }

    private Map<String, List<String>> extractEntities(String message) {
        Map<String, List<String>> entities = new HashMap<>();
        entities.put("order_id", regexFind(message, "#?\\d{5,}"));
        entities.put("error_code", regexFind(message, "\\b[45]\\d{2}\\b"));
        entities.put("amount", regexFind(message, "\\d+(?:\\.\\d+)?\\s*(?:元|块|rmb|usd|美元)"));
        entities.put("date", regexFind(message, "(今天|明天|昨天|\\d{4}-\\d{1,2}-\\d{1,2})"));
        return entities;
    }

    private List<String> regexFind(String text, String regex) {
        java.util.regex.Matcher matcher = java.util.regex.Pattern.compile(regex, java.util.regex.Pattern.CASE_INSENSITIVE).matcher(text == null ? "" : text);
        List<String> values = new ArrayList<>();
        while (matcher.find()) {
            values.add(matcher.group());
        }
        return values;
    }

    private UrgencyLevel urgency(String message, IntentCategory intent) {
        String msg = normalize(message);
        if (msg.contains("紧急") || msg.contains("urgent") || msg.contains("立刻")) {
            return UrgencyLevel.CRITICAL;
        }
        if (msg.contains("今天") || msg.contains("马上") || msg.contains("尽快") || intent == IntentCategory.ESCALATION) {
            return UrgencyLevel.HIGH;
        }
        if (intent == IntentCategory.COMPLAINT) {
            return UrgencyLevel.MEDIUM;
        }
        return UrgencyLevel.LOW;
    }

    private IntentCategory parseIntent(String value) {
        try {
            return IntentCategory.valueOf(value.toUpperCase(Locale.ROOT));
        } catch (Exception ex) {
            return IntentCategory.OTHER;
        }
    }

    private String sliceJsonObject(String raw) {
        int start = raw.indexOf('{');
        int end = raw.lastIndexOf('}');
        if (start >= 0 && end > start) {
            return raw.substring(start, end + 1);
        }
        return "{}";
    }

    private List<String> charNgrams(String text) {
        String normalized = normalize(text);
        List<String> grams = new ArrayList<>();
        for (int n = 1; n <= 3; n++) {
            for (int i = 0; i + n <= normalized.length(); i++) {
                grams.add(normalized.substring(i, i + n));
            }
        }
        return grams;
    }

    private double jaccard(List<String> left, List<String> right) {
        if (left.isEmpty() || right.isEmpty()) {
            return 0.0;
        }
        java.util.Set<String> a = new java.util.HashSet<>(left);
        java.util.Set<String> b = new java.util.HashSet<>(right);
        long intersection = a.stream().filter(b::contains).count();
        long union = a.size() + b.size() - intersection;
        return union == 0 ? 0.0 : (double) intersection / union;
    }

    private String normalize(String value) {
        return value == null ? "" : value.toLowerCase(Locale.ROOT).trim();
    }
}
