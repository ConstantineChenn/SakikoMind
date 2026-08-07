package com.sakikomind.tool;

import com.sakikomind.knowledge.KnowledgeBaseService;
import com.sakikomind.knowledge.SearchResult;
import com.sakikomind.llm.LlmGateway;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

@Service
public class KnowledgeToolManager {

    private final KnowledgeBaseService knowledgeBaseService;
    private final LlmGateway llmGateway;
    private final ObjectMapper objectMapper;
    private final ToolStats stats = new ToolStats();
    private final Map<String, CacheEntry> cache = new HashMap<>();
    private final CircuitBreaker breaker = new CircuitBreaker(5, Duration.ofSeconds(60));

    public KnowledgeToolManager(KnowledgeBaseService knowledgeBaseService, LlmGateway llmGateway, ObjectMapper objectMapper) {
        this.knowledgeBaseService = knowledgeBaseService;
        this.llmGateway = llmGateway;
        this.objectMapper = objectMapper;
    }

    public ToolResult<List<SearchResult>> searchWithRewrite(String query, int topK) {
        validate(query, topK);
        if (!breaker.allow()) {
            return new ToolResult<>(true, fallback(query, "工具熔断中，请稍后重试"), "knowledge_search", "circuit open", false, 0, false);
        }
        Instant start = Instant.now();
        try {
            List<String> queries = rewriteQuery(query);
            Map<String, SearchResult> merged = new LinkedHashMap<>();
            int recallK = Math.max(topK, 5);
            List<CompletableFuture<ToolResult<List<SearchResult>>>> futures = queries.stream()
                    .map(subQuery -> CompletableFuture.supplyAsync(() -> search(subQuery, recallK)))
                    .toList();
            for (CompletableFuture<ToolResult<List<SearchResult>>> future : futures) {
                for (SearchResult result : future.get(30, TimeUnit.SECONDS).data()) {
                    merged.putIfAbsent(result.id(), result);
                }
            }
            List<SearchResult> reranked = rerank(query, new ArrayList<>(merged.values()), topK);
            stats.record(true, Duration.between(start, Instant.now()).toMillis());
            breaker.recordSuccess();
            return new ToolResult<>(true, reranked, "knowledge_search", null, false,
                    Duration.between(start, Instant.now()).toMillis(), true);
        } catch (TimeoutException ex) {
            stats.record(false, Duration.between(start, Instant.now()).toMillis());
            breaker.recordFailure();
            return new ToolResult<>(true, fallback(query, "执行超时"), "knowledge_search", "timeout", false,
                    Duration.between(start, Instant.now()).toMillis(), false);
        } catch (Exception ex) {
            stats.record(false, Duration.between(start, Instant.now()).toMillis());
            breaker.recordFailure();
            return new ToolResult<>(true, fallback(query, ex.getMessage()), "knowledge_search", ex.getMessage(), false,
                    Duration.between(start, Instant.now()).toMillis(), false);
        }
    }

    public ToolResult<List<SearchResult>> search(String query, int topK) {
        validate(query, topK);
        String key = query + ":" + topK;
        CacheEntry cached = cache.get(key);
        if (cached != null && !cached.expired()) {
            return new ToolResult<>(true, cached.results(), "knowledge_search", null, true, 0, false);
        }
        Instant start = Instant.now();
        try {
            List<SearchResult> results = knowledgeBaseService.search(query, topK);
            cache.put(key, new CacheEntry(results, Instant.now().plusSeconds(300)));
            return new ToolResult<>(true, results, "knowledge_search", null, false,
                    Duration.between(start, Instant.now()).toMillis(), false);
        } catch (Exception ex) {
            return new ToolResult<>(true, fallback(query, ex.getMessage()), "knowledge_search", ex.getMessage(), false,
                    Duration.between(start, Instant.now()).toMillis(), false);
        }
    }

    public Map<String, Object> stats() {
        return Map.of(
                "knowledge_search", Map.of(
                        "total", stats.total(),
                        "success_rate", round(stats.successRate()),
                        "avg_latency_ms", round(stats.avgLatencyMs()),
                        "consecutive_fails", stats.consecutiveFails(),
                        "circuit_state", breaker.state().name().toLowerCase()
                )
        );
    }

    private List<String> rewriteQuery(String query) {
        String prompt = """
                将以下用户查询改写为 3 个不同角度的知识库搜索子查询，返回 JSON 数组。
                原始查询: "%s"
                """.formatted(query);
        try {
            String raw = llmGateway.chat("", prompt, 0.3, 256);
            int start = raw.indexOf('[');
            int end = raw.lastIndexOf(']');
            List<String> rewritten = objectMapper.readValue(raw.substring(start, end + 1), new TypeReference<>() {
            });
            List<String> queries = new ArrayList<>();
            queries.add(query);
            rewritten.stream().filter(q -> q != null && !q.isBlank()).forEach(queries::add);
            return queries.stream().distinct().toList();
        } catch (Exception ex) {
            return List.of(query);
        }
    }

    private List<SearchResult> rerank(String query, List<SearchResult> results, int topK) {
        if (results.size() <= topK) {
            return results;
        }
        String items = "";
        for (int i = 0; i < results.size(); i++) {
            SearchResult r = results.get(i);
            items += i + ". " + r.title() + " - " + truncate(r.content(), 180) + "\n";
        }
        String prompt = """
                根据用户查询，对以下检索结果按相关性排序，只返回 JSON 索引数组。
                用户查询: "%s"
                检索结果:
                %s
                示例返回: [2,0,1]
                """.formatted(query, items);
        try {
            String raw = llmGateway.chat("", prompt, 0.0, 256);
            int start = raw.indexOf('[');
            int end = raw.lastIndexOf(']');
            List<Integer> order = objectMapper.readValue(raw.substring(start, end + 1), new TypeReference<>() {
            });
            List<SearchResult> reranked = new ArrayList<>();
            for (Integer index : order) {
                if (index != null && index >= 0 && index < results.size()) {
                    SearchResult item = results.get(index);
                    if (reranked.stream().noneMatch(existing -> existing.id().equals(item.id()))) {
                        reranked.add(item);
                    }
                }
            }
            if (!reranked.isEmpty()) {
                return reranked.stream().limit(topK).toList();
            }
        } catch (Exception ignored) {
        }
        return results.stream().sorted(Comparator.comparingDouble(SearchResult::score).reversed()).limit(topK).toList();
    }

    private void validate(String query, int topK) {
        if (query == null || query.isBlank()) {
            throw new IllegalArgumentException("query 不能为空");
        }
        if (topK < 1 || topK > 50) {
            throw new IllegalArgumentException("topK 必须在 1 到 50 之间");
        }
    }

    private String truncate(String value, int max) {
        if (value == null || value.length() <= max) {
            return value == null ? "" : value;
        }
        return value.substring(0, max);
    }

    private List<SearchResult> fallback(String query, String error) {
        return List.of(new SearchResult(
                "fallback",
                "知识库降级结果",
                "知识库暂时不可用，未能完成对“" + query + "”的检索。请稍后重试，或转人工客服确认。",
                0.0,
                0,
                Map.of("fallback", true, "error", error == null ? "" : error)
        ));
    }

    private double round(double value) {
        return Math.round(value * 1000.0) / 1000.0;
    }

    private record CacheEntry(List<SearchResult> results, Instant expiresAt) {
        boolean expired() {
            return Instant.now().isAfter(expiresAt);
        }
    }
}
