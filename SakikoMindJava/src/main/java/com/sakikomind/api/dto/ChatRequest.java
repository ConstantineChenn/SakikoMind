package com.sakikomind.api.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;

@Schema(description = "智能客服对话请求")
public record ChatRequest(
        @Schema(description = "用户消息", example = "我想申请退款，订单号是 #12345")
        @NotBlank String message,
        @Schema(description = "用户 ID，未传时默认为 anonymous", example = "u1001")
        String userId,
        @Schema(description = "会话 ID，未传时服务端自动生成", example = "conv-001")
        String conversationId
) {
    public String userIdOrDefault() {
        return userId == null || userId.isBlank() ? "anonymous" : userId;
    }
}
