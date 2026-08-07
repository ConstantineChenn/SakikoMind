package com.sakikomind.api.dto;

import io.swagger.v3.oas.annotations.media.Schema;

@Schema(description = "智能客服对话响应")
public record ChatResponse(
        @Schema(description = "会话 ID", example = "conv-001")
        String conversationId,
        @Schema(description = "客服回复")
        String response,
        @Schema(description = "识别出的意图", example = "billing")
        String intent,
        @Schema(description = "实际处理请求的 Agent 类型", example = "billing")
        String agentType,
        @Schema(description = "是否需要转人工")
        boolean escalated,
        @Schema(description = "处理耗时，单位毫秒", example = "253")
        long latencyMs,
        @Schema(description = "是否使用了知识库")
        boolean knowledgeUsed,
        @Schema(description = "回答校验是否通过")
        boolean verified,
        @Schema(description = "回答是否基于上下文")
        boolean grounded
) {
}
