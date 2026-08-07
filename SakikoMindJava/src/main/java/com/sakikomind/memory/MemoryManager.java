package com.sakikomind.memory;

import com.sakikomind.config.SakikoMindProperties;
import com.sakikomind.llm.LlmGateway;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

@Service
public class MemoryManager {

    private static final Logger log = LoggerFactory.getLogger(MemoryManager.class);

    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final SakikoMindProperties properties;
    private final LlmGateway llmGateway;
    private final List<EpisodicEntry> episodicStore = new CopyOnWriteArrayList<>();
    private final Map<String, Map<String, Object>> profileStore = new ConcurrentHashMap<>();

    public MemoryManager(StringRedisTemplate redisTemplate, ObjectMapper objectMapper, SakikoMindProperties properties, LlmGateway llmGateway) {
        this.redisTemplate = redisTemplate;
        this.objectMapper = objectMapper;
        this.properties = properties;
        this.llmGateway = llmGateway;
    }

    @PostConstruct
    public void init() {
        loadPersistentMemory();
    }

    public MemoryContext getContext(String userId, String conversationId, String query) {
        List<ConversationMessage> recent = getWorkingMemory(userId, conversationId);
        List<String> relevantHistory = searchEpisodic(userId, query);
        Map<String, Object> profile = profileStore.getOrDefault(userId, Map.of());
        String summary = safeRedisGet(summaryKey(userId, conversationId));
        return new MemoryContext(recent, relevantHistory, profile, summary == null ? "" : summary);
    }

    public void addMessage(String userId, String conversationId, MessageRole role, String content) {
        ConversationMessage message = new ConversationMessage(role, safe(content), Instant.now(), Map.of());
        String key = wmKey(userId, conversationId);
        try {
            redisTemplate.opsForList().leftPush(key, objectMapper.writeValueAsString(message));
            redisTemplate.expire(key, Duration.ofSeconds(properties.getMemory().getTtlSeconds()));
            Long size = redisTemplate.opsForList().size(key);
            if (size != null && size >= properties.getMemory().getCompressAt()) {
                compress(userId, conversationId);
            }
        } catch (Exception ex) {
            log.warn("Failed to write working memory: {}", ex.getMessage());
        }
    }

    @Async
    public void updateProfile(String userId, String conversationId) {
        List<ConversationMessage> messages = getWorkingMemory(userId, conversationId);
        if (messages.isEmpty()) {
            return;
        }
        String text = tail(messages, 10).stream()
                .map(m -> m.role().name().toLowerCase() + ": " + m.content())
                .reduce((a, b) -> a + "\n" + b)
                .orElse("");
        String prompt = """
                从以下客服对话中提炼用户画像，返回 JSON：
                %s
                格式: {"preferences":["..."],"entities":{"产品":[],"问题类型":[]}}
                """.formatted(text);
        try {
            String raw = llmGateway.chat("", prompt, 0.0, 512);
            Map<String, Object> profile = objectMapper.readValue(sliceJson(raw), new TypeReference<>() {
            });
            profileStore.put(userId, profile);
            persistMemory();
        } catch (Exception ex) {
            log.warn("Profile update failed: {}", ex.getMessage());
        }
    }

    public List<ConversationMessage> getWorkingMemory(String userId, String conversationId) {
        try {
            String key = wmKey(userId, conversationId);
            List<String> raw = redisTemplate.opsForList().range(key, 0, properties.getMemory().getWorkingMax() - 1);
            if (raw == null) {
                return List.of();
            }
            List<ConversationMessage> messages = new ArrayList<>();
            for (int i = raw.size() - 1; i >= 0; i--) {
                messages.add(objectMapper.readValue(raw.get(i), ConversationMessage.class));
            }
            return messages;
        } catch (Exception ex) {
            log.warn("Failed to read working memory: {}", ex.getMessage());
            return List.of();
        }
    }

    private void compress(String userId, String conversationId) {
        List<ConversationMessage> messages = getWorkingMemory(userId, conversationId);
        if (messages.size() < properties.getMemory().getCompressAt()) {
            return;
        }
        int keep = Math.min(5, messages.size());
        List<ConversationMessage> oldMessages = messages.subList(0, messages.size() - keep);
        List<ConversationMessage> keepMessages = messages.subList(messages.size() - keep, messages.size());
        String text = oldMessages.stream()
                .map(m -> m.role().name().toLowerCase() + ": " + m.content())
                .reduce((a, b) -> a + "\n" + b)
                .orElse("");
        String summary;
        try {
            summary = llmGateway.chat("", "用 2-3 句话总结以下对话关键信息：\n" + text, 0.0, 256);
        } catch (Exception ex) {
            summary = "对话包含 " + oldMessages.size() + " 条历史消息。";
        }
        episodicStore.add(new EpisodicEntry(userId, conversationId, summary, text, Instant.now(), embed(summary)));
        persistMemory();
        String key = wmKey(userId, conversationId);
        try {
            String oldSummary = safeRedisGet(summaryKey(userId, conversationId));
            redisTemplate.opsForValue().set(summaryKey(userId, conversationId), ((oldSummary == null ? "" : oldSummary + "\n") + summary).trim(),
                    Duration.ofSeconds(properties.getMemory().getTtlSeconds()));
            redisTemplate.delete(key);
            for (int i = keepMessages.size() - 1; i >= 0; i--) {
                redisTemplate.opsForList().leftPush(key, objectMapper.writeValueAsString(keepMessages.get(i)));
            }
            redisTemplate.expire(key, Duration.ofSeconds(properties.getMemory().getTtlSeconds()));
        } catch (Exception ex) {
            log.warn("Memory compression failed: {}", ex.getMessage());
        }
    }

    private List<String> searchEpisodic(String userId, String query) {
        if (query == null || query.isBlank()) {
            return List.of();
        }
        double[] queryVec = embed(query);
        return episodicStore.stream()
                .filter(e -> e.userId().equals(userId))
                .sorted(Comparator.comparingDouble((EpisodicEntry e) -> cosine(queryVec, e.embedding())).reversed())
                .limit(5)
                .map(EpisodicEntry::summary)
                .toList();
    }

    private String safeRedisGet(String key) {
        try {
            return redisTemplate.opsForValue().get(key);
        } catch (Exception ex) {
            return null;
        }
    }

    private List<ConversationMessage> tail(List<ConversationMessage> messages, int n) {
        return messages.subList(Math.max(0, messages.size() - n), messages.size());
    }

    private String sliceJson(String raw) {
        int start = raw.indexOf('{');
        int end = raw.lastIndexOf('}');
        return start >= 0 && end > start ? raw.substring(start, end + 1) : "{}";
    }

    private String wmKey(String userId, String conversationId) {
        return "wm:" + safe(userId) + ":" + safe(conversationId);
    }

    private String summaryKey(String userId, String conversationId) {
        return "summary:" + safe(userId) + ":" + safe(conversationId);
    }

    private String safe(String value) {
        return value == null ? "" : value;
    }

    private double[] embed(String text) {
        double[] vector = new double[256];
        String normalized = text == null ? "" : text.toLowerCase();
        for (int n = 1; n <= 3; n++) {
            for (int i = 0; i + n <= normalized.length(); i++) {
                String gram = normalized.substring(i, i + n);
                int hash = gram.hashCode();
                int idx = Math.floorMod(hash, vector.length);
                vector[idx] += (hash & 1) == 0 ? 1.0 : -1.0;
            }
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

    private void loadPersistentMemory() {
        Path path = Path.of(properties.getStorage().getMemoryPath());
        if (!Files.exists(path)) {
            return;
        }
        try {
            StoredMemory stored = objectMapper.readValue(path.toFile(), StoredMemory.class);
            if (stored.episodic() != null) {
                stored.episodic().forEach(entry -> {
                    if (entry.summary() != null && !entry.summary().isBlank()) {
                        episodicStore.add(new EpisodicEntry(
                                safe(entry.userId()),
                                safe(entry.conversationId()),
                                safe(entry.summary()),
                                safe(entry.fullText()),
                                entry.timestamp() == null ? Instant.now() : entry.timestamp(),
                                embed(entry.summary())
                        ));
                    }
                });
            }
            if (stored.profiles() != null) {
                profileStore.putAll(stored.profiles());
            }
            log.info("Loaded persisted memory: episodic={}, profiles={}", episodicStore.size(), profileStore.size());
        } catch (Exception ex) {
            log.warn("Failed to load persisted memory store {}: {}", path, ex.getMessage());
        }
    }

    private synchronized void persistMemory() {
        Path path = Path.of(properties.getStorage().getMemoryPath());
        try {
            if (path.getParent() != null) {
                Files.createDirectories(path.getParent());
            }
            List<StoredEpisodicEntry> episodic = episodicStore.stream()
                    .map(entry -> new StoredEpisodicEntry(entry.userId(), entry.conversationId(), entry.summary(), entry.fullText(), entry.timestamp()))
                    .toList();
            objectMapper.writerWithDefaultPrettyPrinter()
                    .writeValue(path.toFile(), new StoredMemory(episodic, Map.copyOf(profileStore)));
        } catch (Exception ex) {
            log.warn("Failed to persist memory store {}: {}", path, ex.getMessage());
        }
    }

    private record EpisodicEntry(String userId, String conversationId, String summary, String fullText, Instant timestamp, double[] embedding) {
    }

    private record StoredMemory(List<StoredEpisodicEntry> episodic, Map<String, Map<String, Object>> profiles) {
        private StoredMemory {
            episodic = episodic == null ? List.of() : episodic;
            profiles = profiles == null ? Map.of() : profiles;
        }
    }

    private record StoredEpisodicEntry(String userId, String conversationId, String summary, String fullText, Instant timestamp) {
    }
}
