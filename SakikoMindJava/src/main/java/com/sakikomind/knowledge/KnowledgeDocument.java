package com.sakikomind.knowledge;

import java.util.Map;

public record KnowledgeDocument(
        String id,
        String title,
        String content,
        int chunkIndex,
        Map<String, Object> metadata,
        double[] embedding
) {
}
