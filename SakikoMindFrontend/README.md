# SakikoMind Frontend

SakikoMind 的 Vue + Vite 产品控制台，唯一连接 Python FastAPI 后端。它把已验证的 Agent 能力组织为适合演示和排障的四个工作区。

## 工作区

- **对话**：发送客服问题，展示意图、Agent、RAG 引用、命中的 Skills、Trace 与人工升级工单。
- **知识库**：检索、添加和上传订阅制 SaaS 政策文档。
- **工单**：查看并更新 SakikoMind 内置人工升级工单；已解决和已关闭工单会从待办列表移除。
- **监控**：展示 Agent/工具统计、告警恢复、优化建议，并跳转到 Prometheus 格式的 `/metrics`。

对话页内置三条“演示捷径”：退款政策、401 排障和高风险转人工。它们只会填入问题，不会自动调用模型，便于录屏前确认内容。

## 本地运行

前提：已启动后端服务 `http://localhost:8000`，并已安装 Node.js。

```powershell
$env:Path = "E:\;$env:Path"
& "E:\npm.cmd" install
& "E:\npm.cmd" run dev
```

打开 `http://127.0.0.1:5173`。如后端地址不同，可在左侧“服务配置”中修改，设置会保存到浏览器本地存储。

## 生产构建

```powershell
$env:Path = "E:\;$env:Path"
& "E:\npm.cmd" run build
```

构建结果位于 `dist/`。当前构建不包含 API Key，也不代理敏感配置；浏览器仅调用已配置的后端 HTTP 接口。

## 后端接口

| 接口 | 前端用途 |
| --- | --- |
| `/chat` | 客服对话、Trace、引用、Skills 和升级工单 |
| `/health` | 服务健康检查 |
| `/skills`、`/skills/reload` | Skills 展示与热更新 |
| `/knowledge/stats`、`/knowledge/add`、`/knowledge/upload`、`/search` | 知识库管理与 RAG 检索 |
| `/handoffs` | 人工工单查询与状态更新 |
| `/monitor`、`/metrics` | 运行监控与 Prometheus 指标 |
