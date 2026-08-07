package com.sakikomind.api.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;

@Schema(description = "知识库文档")
public record DocInput(
        @Schema(description = "文档标题", example = "退款补充政策")
        @NotBlank String title,
        @Schema(description = "文档内容", example = "大促期间退款审核时间可能延长到 3-5 个工作日。")
        @NotBlank String content
) {
}
