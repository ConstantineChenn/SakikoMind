package com.sakikomind.agent;

import com.sakikomind.intent.IntentCategory;
import com.sakikomind.intent.IntentRecognizer;
import com.sakikomind.intent.IntentResult;
import com.sakikomind.intent.UrgencyLevel;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;

@Service
public class AgentOrchestrator {

    private final IntentRecognizer intentRecognizer;
    private final Map<AgentType, List<BaseAgent>> pool;
    private final Map<IntentCategory, AgentType> routing = new EnumMap<>(IntentCategory.class);

    public AgentOrchestrator(IntentRecognizer intentRecognizer, Map<AgentType, List<BaseAgent>> pool) {
        this.intentRecognizer = intentRecognizer;
        this.pool = pool;
        routing.put(IntentCategory.TECHNICAL, AgentType.TECHNICAL);
        routing.put(IntentCategory.BILLING, AgentType.BILLING);
        routing.put(IntentCategory.ACCOUNT, AgentType.BILLING);
        routing.put(IntentCategory.ESCALATION, AgentType.ESCALATION);
    }

    public OrchestratorResult run(AgentRequest request) {
        Instant start = Instant.now();
        AgentRequest req = request;
        if (req.intent() == null) {
            IntentResult intentResult = intentRecognizer.recognize(req.message(), req.history());
            req = req.withIntent(intentResult.intent(), intentResult.urgency());
        }
        List<AgentType> collaboration = collaborationTargets(req);
        AgentResponse response = collaboration.size() > 1
                ? runParallel(req, collaboration)
                : execute(req, route(req.intent(), req.urgency()));
        boolean escalated = response.escalate()
                || req.urgency() == UrgencyLevel.CRITICAL
                || req.intent() == IntentCategory.ESCALATION;
        return new OrchestratorResult(
                req.requestId(),
                response.content(),
                response.agentType(),
                req.intent(),
                escalated,
                Duration.between(start, Instant.now()).toMillis()
        );
    }

    private AgentResponse runParallel(AgentRequest req, List<AgentType> targets) {
        List<CompletableFuture<AgentResponse>> futures = targets.stream()
                .map(type -> CompletableFuture.supplyAsync(() -> execute(req, type)))
                .toList();
        List<AgentResponse> responses = futures.stream().map(CompletableFuture::join).toList();
        String content = responses.stream()
                .filter(AgentResponse::success)
                .map(r -> "[" + r.agentType().name().toLowerCase(Locale.ROOT) + "]\n" + r.content())
                .reduce((a, b) -> a + "\n\n" + b)
                .orElse("抱歉，所有 Agent 均处理失败。");
        boolean escalate = responses.stream().anyMatch(AgentResponse::escalate);
        long latency = responses.stream().mapToLong(AgentResponse::latencyMs).max().orElse(0);
        return new AgentResponse(targets.getFirst(), content, true, 1.0, latency, escalate);
    }

    private AgentType route(IntentCategory intent, UrgencyLevel urgency) {
        if (urgency == UrgencyLevel.CRITICAL) {
            return AgentType.ESCALATION;
        }
        AgentType target = routing.get(intent);
        if (target != null && pool.containsKey(target)) {
            return target;
        }
        return AgentType.GENERAL;
    }

    private List<AgentType> collaborationTargets(AgentRequest request) {
        String msg = request.message() == null ? "" : request.message().toLowerCase(Locale.ROOT);
        LinkedHashSet<AgentType> targets = new LinkedHashSet<>();
        if (request.intent() == IntentCategory.TECHNICAL || containsAny(msg, "崩溃", "报错", "error", "crash", "无法登录", "500", "401")) {
            targets.add(AgentType.TECHNICAL);
        }
        if (request.intent() == IntentCategory.BILLING || request.intent() == IntentCategory.ACCOUNT
                || containsAny(msg, "退款", "扣款", "发票", "账单", "支付", "订阅", "refund", "invoice")) {
            targets.add(AgentType.BILLING);
        }
        return new ArrayList<>(targets.stream().filter(pool::containsKey).toList());
    }

    private boolean containsAny(String text, String... keywords) {
        for (String keyword : keywords) {
            if (text.contains(keyword)) {
                return true;
            }
        }
        return false;
    }

    private AgentResponse execute(AgentRequest req, AgentType agentType) {
        BaseAgent agent = bestAgent(agentType).orElseGet(() -> bestAgent(AgentType.GENERAL).orElse(null));
        if (agent == null) {
            return new AgentResponse(AgentType.GENERAL, "服务暂时不可用，请稍后重试。", false, 0.0, 0, false);
        }
        AgentResponse response = agent.handle(req);
        if (!response.success() && agentType != AgentType.GENERAL) {
            return bestAgent(AgentType.GENERAL).map(a -> a.handle(req)).orElse(response);
        }
        return response;
    }

    private Optional<BaseAgent> bestAgent(AgentType agentType) {
        return pool.getOrDefault(agentType, List.of()).stream()
                .max(Comparator.comparingDouble(a -> a.stats().routingScore()));
    }

    public Map<String, Object> stats() {
        Map<String, Object> result = new HashMap<>();
        pool.forEach((type, agents) -> {
            for (int i = 0; i < agents.size(); i++) {
                BaseAgent agent = agents.get(i);
                Map<String, Object> data = new HashMap<>();
                data.put("total", agent.stats().total());
                data.put("success_rate", round(agent.stats().successRate()));
                data.put("avg_ms", round(agent.stats().avgLatencyMs()));
                data.put("monitor_penalty", round(agent.stats().monitorPenalty()));
                data.put("routing_score", round(agent.stats().routingScore()));
                result.put(type.name().toLowerCase(Locale.ROOT) + "_" + i, data);
            }
        });
        return result;
    }

    @SuppressWarnings("unchecked")
    public void updateRoutingPenalties(Map<String, Double> penalties) {
        pool.forEach((type, agents) -> {
            for (int i = 0; i < agents.size(); i++) {
                agents.get(i).stats().setMonitorPenalty(penalties.getOrDefault(type.name().toLowerCase(Locale.ROOT) + "_" + i, 0.0));
            }
        });
    }

    private double round(double value) {
        return Math.round(value * 1000.0) / 1000.0;
    }
}
