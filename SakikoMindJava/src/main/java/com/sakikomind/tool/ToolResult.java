package com.sakikomind.tool;

public record ToolResult<T>(
        boolean success,
        T data,
        String toolName,
        String error,
        boolean cached,
        long latencyMs,
        boolean reranked
) {
}
