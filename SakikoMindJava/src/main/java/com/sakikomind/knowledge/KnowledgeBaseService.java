package com.sakikomind.knowledge;

import com.sakikomind.config.SakikoMindProperties;
import dev.langchain4j.data.document.Document;
import dev.langchain4j.data.document.DocumentSplitter;
import dev.langchain4j.data.document.splitter.DocumentSplitters;
import dev.langchain4j.data.segment.TextSegment;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.vectorstore.VectorStore;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;

import jakarta.annotation.PostConstruct;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;

@Service
public class KnowledgeBaseService {

    private static final Logger log = LoggerFactory.getLogger(KnowledgeBaseService.class);

    private final SakikoMindProperties properties;
    private final VectorStore vectorStore;
    private final ObjectMapper objectMapper;
    private final List<KnowledgeDocument> documents = new CopyOnWriteArrayList<>();
    private final DocumentSplitter splitter = DocumentSplitters.recursive(500, 80);

    public KnowledgeBaseService(SakikoMindProperties properties, ObjectProvider<VectorStore> vectorStoreProvider, ObjectMapper objectMapper) {
        this.properties = properties;
        this.vectorStore = vectorStoreProvider.getIfAvailable();
        this.objectMapper = objectMapper;
    }

    @PostConstruct
    public void init() {
        loadPersistedDocuments();
        if (documents.isEmpty()) {
            addDocuments(defaultDocuments());
        }
    }

    public int addDocuments(List<Map<String, String>> inputDocs) {
        int added = 0;
        for (Map<String, String> input : inputDocs) {
            String title = input.getOrDefault("title", "未命名文档");
            String content = input.getOrDefault("content", "");
            List<TextSegment> segments = split(content);
            for (int i = 0; i < segments.size(); i++) {
                String text = segments.get(i).text();
                if (text == null || text.isBlank()) {
                    continue;
                }
                String id = md5(title + "_" + i + "_" + text.substring(0, Math.min(50, text.length())));
                Map<String, Object> metadata = new HashMap<>();
                metadata.put("title", title);
                metadata.put("chunk_index", i);
                metadata.put("source", "sakikomind-java");
                KnowledgeDocument doc = new KnowledgeDocument(id, title, text, i, metadata, embed(text));
                documents.removeIf(existing -> existing.id().equals(id));
                documents.add(doc);
                added++;
                addToSpringAiVectorStore(doc);
            }
        }
        if (added > 0) {
            persistDocuments();
        }
        return added;
    }

    public List<SearchResult> search(String query, int topK) {
        if (query == null || query.isBlank()) {
            return List.of();
        }
        Map<String, Double> bm25 = bm25Scores(query);
        Map<String, Double> vector = vectorScores(query);
        Map<String, Double> fused = new HashMap<>();
        documents.forEach(doc -> {
            double score = properties.getRag().getBm25Weight() * bm25.getOrDefault(doc.id(), 0.0)
                    + properties.getRag().getVectorWeight() * vector.getOrDefault(doc.id(), 0.0);
            fused.put(doc.id(), score);
        });
        return documents.stream()
                .sorted(Comparator.comparingDouble((KnowledgeDocument d) -> fused.getOrDefault(d.id(), 0.0)).reversed())
                .limit(topK)
                .map(doc -> new SearchResult(
                        doc.id(),
                        doc.title(),
                        doc.content(),
                        round(fused.getOrDefault(doc.id(), 0.0)),
                        doc.chunkIndex(),
                        doc.metadata()
                ))
                .filter(result -> result.score() > 0.0)
                .toList();
    }

    public int docCount() {
        return documents.size();
    }

    private List<TextSegment> split(String content) {
        try {
            return splitter.split(Document.from(content == null ? "" : content));
        } catch (Exception ex) {
            log.warn("LangChain4j splitter failed, using simple splitter: {}", ex.getMessage());
            return simpleSplit(content);
        }
    }

    private List<TextSegment> simpleSplit(String content) {
        String text = content == null ? "" : content;
        List<TextSegment> segments = new ArrayList<>();
        for (int start = 0; start < text.length(); start += 500) {
            segments.add(TextSegment.from(text.substring(start, Math.min(text.length(), start + 500))));
        }
        return segments;
    }

    private void addToSpringAiVectorStore(KnowledgeDocument doc) {
        if (vectorStore == null) {
            return;
        }
        try {
            org.springframework.ai.document.Document springDoc = new org.springframework.ai.document.Document(doc.id(), doc.content(), doc.metadata());
            vectorStore.add(List.of(springDoc));
        } catch (Exception ex) {
            log.debug("Spring AI VectorStore write skipped: {}", ex.getMessage());
        }
    }

    private void loadPersistedDocuments() {
        Path path = Path.of(properties.getStorage().getKnowledgePath());
        if (!Files.exists(path)) {
            return;
        }
        try {
            List<StoredKnowledgeDocument> stored = objectMapper.readValue(path.toFile(), new TypeReference<>() {
            });
            for (StoredKnowledgeDocument item : stored) {
                if (item.content() == null || item.content().isBlank()) {
                    continue;
                }
                KnowledgeDocument doc = new KnowledgeDocument(
                        item.id() == null || item.id().isBlank() ? md5(item.title() + item.chunkIndex() + item.content()) : item.id(),
                        item.title() == null || item.title().isBlank() ? "未命名文档" : item.title(),
                        item.content(),
                        item.chunkIndex(),
                        item.metadata() == null ? Map.of() : item.metadata(),
                        embed(item.content())
                );
                documents.removeIf(existing -> existing.id().equals(doc.id()));
                documents.add(doc);
                addToSpringAiVectorStore(doc);
            }
            log.info("Loaded {} persisted knowledge chunks from {}", documents.size(), path);
        } catch (Exception ex) {
            log.warn("Failed to load persisted knowledge store {}: {}", path, ex.getMessage());
        }
    }

    private void persistDocuments() {
        Path path = Path.of(properties.getStorage().getKnowledgePath());
        try {
            if (path.getParent() != null) {
                Files.createDirectories(path.getParent());
            }
            List<StoredKnowledgeDocument> stored = documents.stream()
                    .map(doc -> new StoredKnowledgeDocument(doc.id(), doc.title(), doc.content(), doc.chunkIndex(), doc.metadata()))
                    .toList();
            objectMapper.writerWithDefaultPrettyPrinter().writeValue(path.toFile(), stored);
        } catch (Exception ex) {
            log.warn("Failed to persist knowledge store {}: {}", path, ex.getMessage());
        }
    }

    private Map<String, Double> bm25Scores(String query) {
        List<String> queryTerms = tokenize(query);
        Map<String, Double> scores = new HashMap<>();
        if (queryTerms.isEmpty() || documents.isEmpty()) {
            return scores;
        }
        double avgDl = documents.stream().mapToInt(d -> tokenize(d.content()).size()).average().orElse(1.0);
        double k1 = 1.5;
        double b = 0.75;
        for (KnowledgeDocument doc : documents) {
            List<String> terms = tokenize(doc.content());
            double score = 0.0;
            for (String term : queryTerms) {
                long tf = terms.stream().filter(term::equals).count();
                if (tf == 0) {
                    continue;
                }
                long df = documents.stream().filter(d -> tokenize(d.content()).contains(term)).count();
                double idf = Math.log(1 + (documents.size() - df + 0.5) / (df + 0.5));
                double denom = tf + k1 * (1 - b + b * terms.size() / avgDl);
                score += idf * (tf * (k1 + 1)) / denom;
            }
            scores.put(doc.id(), score);
        }
        return normalize(scores);
    }

    private Map<String, Double> vectorScores(String query) {
        double[] queryVec = embed(query);
        Map<String, Double> scores = new HashMap<>();
        for (KnowledgeDocument doc : documents) {
            scores.put(doc.id(), Math.max(0.0, cosine(queryVec, doc.embedding())));
        }
        return normalize(scores);
    }

    private Map<String, Double> normalize(Map<String, Double> scores) {
        double max = scores.values().stream().mapToDouble(Double::doubleValue).max().orElse(0.0);
        if (max <= 0.0) {
            return scores;
        }
        Map<String, Double> normalized = new HashMap<>();
        scores.forEach((id, score) -> normalized.put(id, score / max));
        return normalized;
    }

    private List<String> tokenize(String text) {
        String normalized = text == null ? "" : text.toLowerCase(Locale.ROOT).replaceAll("[\\p{Punct}\\s]+", " ");
        List<String> tokens = new ArrayList<>();
        for (String part : normalized.split("\\s+")) {
            if (!part.isBlank()) {
                tokens.add(part);
            }
        }
        for (int n = 2; n <= 3; n++) {
            for (int i = 0; i + n <= normalized.length(); i++) {
                String gram = normalized.substring(i, i + n).trim();
                if (!gram.isBlank()) {
                    tokens.add(gram);
                }
            }
        }
        return tokens;
    }

    private double[] embed(String text) {
        double[] vector = new double[256];
        Set<String> grams = new HashSet<>(tokenize(text));
        for (String gram : grams) {
            int hash = gram.hashCode();
            int idx = Math.floorMod(hash, vector.length);
            vector[idx] += (hash & 1) == 0 ? 1.0 : -1.0;
        }
        return vector;
    }

    private double cosine(double[] a, double[] b) {
        double dot = 0.0;
        double na = 0.0;
        double nb = 0.0;
        for (int i = 0; i < a.length; i++) {
            dot += a[i] * b[i];
            na += a[i] * a[i];
            nb += b[i] * b[i];
        }
        return na == 0 || nb == 0 ? 0.0 : dot / (Math.sqrt(na) * Math.sqrt(nb));
    }

    private String md5(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("MD5").digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : digest) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (Exception ex) {
            return Integer.toHexString(value.hashCode());
        }
    }

    private double round(double value) {
        return Math.round(value * 10000.0) / 10000.0;
    }

    private List<Map<String, String>> defaultDocuments() {
        List<Map<String, String>> docs = new ArrayList<>();
        docs.add(doc("退款政策", "用户在购买后 7 天内可以申请无理由退款。退款申请提交后，系统会在 1-3 个工作日内审核。审核通过后，款项将在 5-7 个工作日内退回原支付账户。商品已发货时，需要先完成退货流程。"));
        docs.add(doc("订单查询", "用户可以通过订单号查询订单状态。订单状态包括待支付、已支付、已发货、运输中、已签收、已完成。物流信息通常在发货后 24 小时内更新。"));
        docs.add(doc("账户安全", "建议用户定期修改密码，密码长度至少 8 位，包含字母和数字。如果忘记密码，可以通过绑定手机号或邮箱重置。发现异常登录时系统会锁定账户。"));
        docs.add(doc("技术故障排查", "应用崩溃请尝试清除缓存后重启应用。登录失败 401 表示认证失败。500 服务器错误通常是服务端问题，请稍后重试或联系技术支持。"));
        docs.add(doc("会员与积分", "每消费 1 元累积 1 积分。100 积分可抵扣 1 元。会员等级包括普通会员、银卡会员和金卡会员，积分有效期为 1 年。"));
        docs.add(doc("配送说明", "标准配送 3-5 个工作日送达，订单满 99 元免运费。加急配送 1-2 个工作日送达。同城配送可当日达或次日达。"));
        return docs;
    }

    private Map<String, String> doc(String title, String content) {
        Map<String, String> map = new LinkedHashMap<>();
        map.put("title", title);
        map.put("content", content);
        return map;
    }

    private record StoredKnowledgeDocument(
            String id,
            String title,
            String content,
            int chunkIndex,
            Map<String, Object> metadata
    ) {
    }
}
