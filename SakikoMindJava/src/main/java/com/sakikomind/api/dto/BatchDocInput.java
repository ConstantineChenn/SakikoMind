package com.sakikomind.api.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;

import java.util.List;

@Schema(description = "批量知识库导入请求")
public record BatchDocInput(
        @Schema(description = "待导入文档列表")
        @Valid @NotEmpty List<DocInput> documents
) {
}
