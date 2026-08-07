package com.sakikomind.monitor;

import com.sakikomind.agent.AgentOrchestrator;
import com.sakikomind.config.SakikoMindProperties;
import com.sakikomind.tool.KnowledgeToolManager;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.annotation.PostConstruct;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Service
public class PerformanceMonitor {

    private final AgentOrchestrator orchestrator;
    private final KnowledgeToolManager toolManager;
    private final SakikoMindProperties properties;
    private final List<Map<String, Object>> alerts = new ArrayList<>();
    private final List<Map<String, Object>> suggestions = new ArrayList<>();
    private final Set<String> alertKeys = new HashSet<>();
    private final Set<String> suggestionKeys = new HashSet<>();
    private final RestClient restClient = RestClient.create();
    private final Counter monitorCollections;
    private final Timer monitorCollectionTimer;
    private volatile double agentSuccessRate = 1.0;

    public PerformanceMonitor(AgentOrchestrator orchestrator, KnowledgeToolManager toolManager, SakikoMindProperties properties, MeterRegistry meterRegistry) {
        this.orchestrator = orchestrator;
        this.toolManager = toolManager;
        this.properties = properties;
        Gauge.builder("sakikomind_agent_success_rate", () -> agentSuccessRate).register(meterRegistry);
        this.monitorCollections = Counter.builder("sakikomind_monitor_collections_total").register(meterRegistry);
        this.monitorCollectionTimer = Timer.builder("sakikomind_monitor_collection_duration").register(meterRegistry);
    }

    @PostConstruct
    public void init() {
        collect();
    }

    @Scheduled(fixedDelay = 10000)
    public void collect() {
        monitorCollectionTimer.record(() -> {
            monitorCollections.increment();
            Map<String, Object> agentStats = orchestrator.stats();
            Map<String, Double> penalties = new HashMap<>();
            double minSuccessRate = 1.0;
            agentStats.forEach((key, value) -> {
                if (value instanceof Map<?, ?> map) {
                    double sr = asDouble(map.get("success_rate"), 1.0);
                    double avg = asDouble(map.get("avg_ms"), 0.0);
                    if (sr < minSuccessRate) {
                        agentSuccessRate = sr;
                    }
                    if (sr < properties.getMonitor().getSuccessRateThreshold()) {
                        addAlert("agent_success_rate:" + key, sr, properties.getMonitor().getSuccessRateThreshold());
                    }
                    if (avg > properties.getMonitor().getLatencyMsThreshold()) {
                        addAlert("agent_avg_ms:" + key, avg, properties.getMonitor().getLatencyMsThreshold());
                    }
                    penalties.put(key, routingPenalty(sr, avg));
                }
            });
            orchestrator.updateRoutingPenalties(penalties);
            if (!penalties.isEmpty()) {
                addSuggestion("路由权重已根据在线表现调整", "检查 /monitor 中低成功率或高延迟 Agent，必要时优化 prompt 或增加实例。", 7);
            }
        });
    }

    public Map<String, Object> summary() {
        return Map.of(
                "agent_stats", orchestrator.stats(),
                "tool_stats", toolManager.stats(),
                "active_alerts", tail(alerts, 10),
                "suggestions", tail(suggestions, 5)
        );
    }

    private double routingPenalty(double successRate, double avgMs) {
        double penalty = 0.0;
        if (successRate < 0.90) {
            penalty += Math.min(0.5, (0.90 - successRate) * 2);
        }
        if (avgMs > 3000) {
            penalty += Math.min(0.4, (avgMs - 3000) / 10000);
        }
        return Math.min(0.9, penalty);
    }

    private void addAlert(String metric, double value, double threshold) {
        if (alertKeys.add(metric)) {
            Map<String, Object> alert = Map.of("metric", metric, "value", value, "threshold", threshold, "resolved", false);
            alerts.add(alert);
            sendWebhook(alert);
        }
    }

    private void addSuggestion(String title, String action, int priority) {
        if (suggestionKeys.add(title)) {
            suggestions.add(Map.of("title", title, "action", action, "priority", priority));
        }
    }

    private void sendWebhook(Map<String, Object> alert) {
        String url = properties.getMonitor().getWebhookUrl();
        if (url == null || url.isBlank()) {
            return;
        }
        try {
            restClient.post().uri(url).body(alert).retrieve().toBodilessEntity();
        } catch (Exception ignored) {
        }
    }

    private double asDouble(Object value, double fallback) {
        return value instanceof Number number ? number.doubleValue() : fallback;
    }

    private List<Map<String, Object>> tail(List<Map<String, Object>> values, int n) {
        return values.subList(Math.max(0, values.size() - n), values.size());
    }
}
