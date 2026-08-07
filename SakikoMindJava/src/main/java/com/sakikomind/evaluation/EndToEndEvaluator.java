package com.sakikomind.evaluation;

import com.sakikomind.agent.AgentOrchestrator;
import com.sakikomind.agent.AgentRequest;
import com.sakikomind.api.dto.EvalRunRequest;
import com.sakikomind.config.SakikoMindProperties;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sakikomind.intent.IntentRecognizer;
import com.sakikomind.intent.IntentResult;
import org.springframework.stereotype.Service;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class EndToEndEvaluator {

    private final IntentRecognizer intentRecognizer;
    private final AgentOrchestrator orchestrator;
    private final LLMJudge judge;
    private final ObjectMapper objectMapper;
    private final SakikoMindProperties properties;

    public EndToEndEvaluator(IntentRecognizer intentRecognizer, AgentOrchestrator orchestrator, LLMJudge judge, ObjectMapper objectMapper, SakikoMindProperties properties) {
        this.intentRecognizer = intentRecognizer;
        this.orchestrator = orchestrator;
        this.judge = judge;
        this.objectMapper = objectMapper;
        this.properties = properties;
    }

    public Map<String, Object> run(EvalRunRequest request) {
        List<EvalRunRequest.IntentCase> intentCases = request != null && request.intentCases() != null
                ? request.intentCases()
                : defaultIntentCases();
        List<EvalRunRequest.DialogCase> dialogCases = request != null && request.dialogCases() != null
                ? request.dialogCases()
                : defaultDialogCases();
        List<Map<String, Object>> results = new ArrayList<>();
        List<String> predictions = new ArrayList<>();
        List<String> groundTruth = new ArrayList<>();
        long intentCorrect = 0;
        for (EvalRunRequest.IntentCase c : intentCases) {
            IntentResult result = intentRecognizer.recognize(c.message(), null);
            String predicted = result.intent().name().toLowerCase(Locale.ROOT);
            predictions.add(predicted);
            groundTruth.add(c.expectedIntent());
            boolean passed = predicted.equals(c.expectedIntent());
            if (passed) {
                intentCorrect++;
            }
            results.add(Map.of(
                    "test_id", "intent_" + results.size(),
                    "passed", passed,
                    "scores", Map.of("accuracy", passed ? 1.0 : 0.0),
                    "metadata", Map.of("message", c.message(), "expected", c.expectedIntent(), "predicted", predicted)
            ));
        }
        for (EvalRunRequest.DialogCase c : dialogCases) {
            List<String> turns = c.turns() != null && !c.turns().isEmpty() ? c.turns() : List.of(c.question());
            List<Map<String, String>> history = new ArrayList<>();
            String convId = c.conversationId() == null ? "eval_" + UUID.randomUUID() : c.conversationId();
            String userId = c.userId() == null ? "eval_user" : c.userId();
            for (String turn : turns) {
                var response = orchestrator.run(AgentRequest.of(turn, userId, convId, history.toString(), history));
                QualityScores scores = judge.judge(turn, response.response(), history.toString());
                results.add(Map.of(
                        "test_id", "dialog_" + results.size(),
                        "passed", scores.overall() >= 0.75,
                        "scores", Map.of(
                                "overall", round(scores.overall()),
                                "relevance", scores.relevance(),
                                "accuracy", scores.accuracy(),
                                "completeness", scores.completeness(),
                                "helpfulness", scores.helpfulness()
                        ),
                        "metadata", Map.of(
                                "question", turn,
                                "response", response.response(),
                                "agent_type", response.agentType().name().toLowerCase(Locale.ROOT),
                                "judge_failed", scores.judgeFailed(),
                                "judge_error", scores.error() == null ? "" : scores.error()
                        )
                ));
                history.add(Map.of("role", "user", "content", turn));
                history.add(Map.of("role", "assistant", "content", response.response()));
            }
        }
        long passed = results.stream().filter(r -> Boolean.TRUE.equals(r.get("passed"))).count();
        double intentAccuracy = intentCases.isEmpty() ? 0.0 : (double) intentCorrect / intentCases.size();
        Map<String, Object> avgScores = new HashMap<>();
        avgScores.put("intent_accuracy", round(intentAccuracy));
        avgScores.put("macro_f1", round(macroF1(predictions, groundTruth)));
        avgScores.put("dialog_overall", round(avgDialogOverall(results)));
        List<String> regressions = detectRegressions(avgScores);
        Map<String, Object> report = Map.of(
                "pass_rate", results.isEmpty() ? 0.0 : round((double) passed / results.size()),
                "total", results.size(),
                "passed", passed,
                "avg_scores", avgScores,
                "per_class", perClassMetrics(predictions, groundTruth),
                "regressions", regressions,
                "recommendations", recommendations(intentAccuracy, regressions),
                "results", results
        );
        saveBaseline(report);
        return report;
    }

    private List<EvalRunRequest.IntentCase> defaultIntentCases() {
        return List.of(
                new EvalRunRequest.IntentCase("我的订单什么时候到？", "query"),
                new EvalRunRequest.IntentCase("帮我取消订单", "request"),
                new EvalRunRequest.IntentCase("你们服务太差了！", "complaint"),
                new EvalRunRequest.IntentCase("应用一直报500错误", "technical"),
                new EvalRunRequest.IntentCase("为什么扣了两次款？", "billing"),
                new EvalRunRequest.IntentCase("我要投诉，转人工！", "escalation"),
                new EvalRunRequest.IntentCase("你好", "greeting"),
                new EvalRunRequest.IntentCase("修改我的邮箱地址", "account")
        );
    }

    private List<EvalRunRequest.DialogCase> defaultDialogCases() {
        return List.of(
                new EvalRunRequest.DialogCase("我的订单 #12345 还没到，已经超时了", null, null, null),
                new EvalRunRequest.DialogCase("应用登录一直报错 401", null, null, null),
                new EvalRunRequest.DialogCase("为什么这个月多扣了 50 块钱？", null, null, null),
                new EvalRunRequest.DialogCase(null, List.of("你好，我想退款", "订单号是 #12345", "退款多久能到账？"), null, null)
        );
    }

    private double round(double value) {
        return Math.round(value * 10000.0) / 10000.0;
    }

    private double macroF1(List<String> predictions, List<String> groundTruth) {
        Map<String, Map<String, Double>> perClass = perClassMetrics(predictions, groundTruth);
        return perClass.values().stream().mapToDouble(m -> m.getOrDefault("f1", 0.0)).average().orElse(0.0);
    }

    private Map<String, Map<String, Double>> perClassMetrics(List<String> predictions, List<String> groundTruth) {
        Set<String> labels = new HashSet<>();
        labels.addAll(predictions);
        labels.addAll(groundTruth);
        Map<String, Map<String, Double>> metrics = new HashMap<>();
        for (String label : labels) {
            int tp = 0;
            int fp = 0;
            int fn = 0;
            for (int i = 0; i < predictions.size(); i++) {
                boolean p = label.equals(predictions.get(i));
                boolean g = label.equals(groundTruth.get(i));
                if (p && g) tp++;
                if (p && !g) fp++;
                if (!p && g) fn++;
            }
            double precision = tp + fp == 0 ? 0.0 : (double) tp / (tp + fp);
            double recall = tp + fn == 0 ? 0.0 : (double) tp / (tp + fn);
            double f1 = precision + recall == 0 ? 0.0 : 2 * precision * recall / (precision + recall);
            metrics.put(label, Map.of("precision", round(precision), "recall", round(recall), "f1", round(f1)));
        }
        return metrics;
    }

    @SuppressWarnings("unchecked")
    private double avgDialogOverall(List<Map<String, Object>> results) {
        return results.stream()
                .filter(r -> String.valueOf(r.get("test_id")).startsWith("dialog_"))
                .map(r -> (Map<String, Object>) r.get("scores"))
                .mapToDouble(scores -> ((Number) scores.getOrDefault("overall", 0.0)).doubleValue())
                .average()
                .orElse(0.0);
    }

    @SuppressWarnings("unchecked")
    private List<String> detectRegressions(Map<String, Object> currentScores) {
        Path path = Path.of(properties.getEval().getBaselinePath());
        if (!Files.exists(path)) {
            return List.of();
        }
        try {
            Map<String, Object> previous = objectMapper.readValue(path.toFile(), new TypeReference<>() {
            });
            Map<String, Object> prevScores = (Map<String, Object>) previous.getOrDefault("avg_scores", Map.of());
            List<String> regressions = new ArrayList<>();
            for (Map.Entry<String, Object> entry : currentScores.entrySet()) {
                Object prev = prevScores.get(entry.getKey());
                if (prev instanceof Number p && entry.getValue() instanceof Number c && p.doubleValue() > 0) {
                    double delta = (c.doubleValue() - p.doubleValue()) / p.doubleValue();
                    if (delta < -0.05) {
                        regressions.add(entry.getKey() + ": " + round(p.doubleValue()) + " -> " + round(c.doubleValue()));
                    }
                }
            }
            return regressions;
        } catch (Exception ex) {
            return List.of();
        }
    }

    private void saveBaseline(Map<String, Object> report) {
        try {
            Path path = Path.of(properties.getEval().getBaselinePath());
            Files.createDirectories(path.getParent());
            objectMapper.writerWithDefaultPrettyPrinter().writeValue(path.toFile(), report);
        } catch (Exception ignored) {
        }
    }

    private List<String> recommendations(double intentAccuracy, List<String> regressions) {
        List<String> recs = new ArrayList<>();
        if (intentAccuracy < 0.9) {
            recs.add("补充低准确率意图类别的样本和 Few-shot 示例");
        }
        if (!regressions.isEmpty()) {
            recs.add("发现评测回归，请对比 baseline 中退化指标并检查最近 prompt 或检索逻辑变更");
        }
        if (recs.isEmpty()) {
            recs.add("所有指标均达标");
        }
        return recs;
    }
}
