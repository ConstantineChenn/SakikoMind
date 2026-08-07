# SakikoMind Java 版本说明

SakikoMind Java 是 Python 版 SakikoMind 的 Java/Spring 技术栈重构版，目录位于：



当前版本已经覆盖智能客服主链路：对话请求、Redis 工作记忆、知识库检索、多 Agent 路由、Spring AI 模型调用、回答校验、评测、监控、Swagger 文档和 Docker 部署。

## 技术栈

| 类型 | 技术 |
|------|------|
| 语言 | Java 21 |
| Web 框架 | Spring Boot 3.5 |
| AI 框架 | Spring AI 1.1 |
| LLM Provider | Anthropic、DeepSeek |
| 文档处理 | LangChain4j DocumentSplitter |
| 记忆缓存 | Spring Data Redis |
| RAG | BM25 + 本地 hash vector + LLM rerank |
| 持久化 | Redis 工作记忆 + JSON 知识库/长期记忆/用户画像 |
| 监控 | Spring Boot Actuator、Micrometer、Prometheus |
| API 文档 | Springdoc OpenAPI、Swagger UI |
| 部署 | Docker、Docker Compose、Nginx、Prometheus |
| 构建 | Maven Wrapper |

## 核心链路

```text
POST /chat
  -> MemoryManager 读取 Redis 工作记忆、会话摘要、长期记忆、用户画像
  -> KnowledgeToolManager 做查询改写、并行召回、LLM rerank
  -> AgentOrchestrator 做意图识别和 Agent 路由
  -> General / Technical / Billing Agent 生成回复
  -> AnswerVerifier 校验回复是否可信、是否需要转人工
  -> 写入 Redis，并异步更新用户画像
```

相关实现：

- `src/main/java/com/sakikomind/api/SakikoMindController.java`
- `src/main/java/com/sakikomind/memory/MemoryManager.java`
- `src/main/java/com/sakikomind/tool/KnowledgeToolManager.java`
- `src/main/java/com/sakikomind/agent/AgentOrchestrator.java`
- `src/main/java/com/sakikomind/agent/AnswerVerifier.java`

## Python 与 Java 版本对照

| 能力 | Python 版本 | Java 版本 | 当前状态 |
|------|-------------|-----------|----------|
| Web 框架 | FastAPI | Spring Boot MVC | 已对齐 |
| 模型调用 | Anthropic Async SDK | Spring AI ChatModel | 已对齐，调用栈不同 |
| DeepSeek | 未内置 | Spring AI DeepSeek profile | Java 增强 |
| Agent 类型 | General / Technical / Billing | General / Technical / Billing | 已对齐 |
| Agent 路由 | 意图路由 + 性能路由 + 降级 | 意图路由 + 性能路由 + 降级 | 已对齐 |
| 复合问题并行处理 | 支持 | 支持 | 已对齐 |
| 意图识别 | LLM + embedding/hash + pattern | LLM + char n-gram semantic + pattern | 基本对齐 |
| 工作记忆 | Redis | Redis | 已对齐 |
| 情景记忆 | ChromaDB `episodic` collection | JSON 持久化 + 本地向量检索 | 功能对齐，存储不同 |
| 用户画像 | ChromaDB `user_profile` collection | JSON 持久化 | 功能对齐，存储不同 |
| 知识库 | ChromaDB `knowledge_base` collection | JSON 持久化 Hybrid RAG | 功能对齐，主检索实现不同 |
| 查询改写 | LLM 改写 | LLM 改写 | 已对齐 |
| 检索重排 | LLM rerank | LLM rerank，失败回退融合分 | 已对齐 |
| 工具框架 | 通用 MCPToolManager | 专用 KnowledgeToolManager | 部分对齐 |
| 熔断/缓存/超时/fallback | 支持 | 支持 | 已对齐到知识库工具 |
| 评测 | Intent、Macro-F1、LLM Judge、baseline | Intent、Macro-F1、LLM Judge、baseline | 基本对齐 |
| 监控 | Prometheus client、Webhook、Z-score | Actuator、Micrometer、Webhook、阈值告警 | 部分对齐 |
| API 文档 | FastAPI `/docs` | Springdoc Swagger UI `/docs` | 已对齐 |
| Docker | App、Redis、ChromaDB、Prometheus、Nginx | App、Redis、ChromaDB、Prometheus、Nginx | 已对齐 |
| CLI | `api/main.py --cli` | 未实现 | Java 缺失 |

## Java 版已补齐的能力

### DeepSeek 兼容

Java 版通过 Spring profile 切换模型：

```env
SPRING_PROFILES_ACTIVE=deepseek
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

当前还做了启动容错：缺少真实 API Key 时使用 `sakikomind-local-placeholder` 避免 Spring AI 自动配置阶段直接失败。真实调用时如果没有配置真实 key，并且：

```env
LLM_FALLBACK_ENABLED=true
```

系统会返回本地降级回复。

Anthropic 配置：

```env
SPRING_PROFILES_ACTIVE=anthropic
ANTHROPIC_API_KEY=your_key
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

### 记忆和知识库持久化

Python 版：

- Redis 保存工作记忆。
- ChromaDB 保存情景记忆、用户画像和知识库。

Java 版：

- Redis 保存工作记忆。
- `data/java/memory-store.json` 保存情景记忆和用户画像。
- `data/java/knowledge-store.json` 保存知识库片段。
- Docker 中保存到 `/app/data/java`，由 `app-data` volume 持久化。

配置项：

```env
SAKIKOMIND_DATA_DIR=data/java
KNOWLEDGE_STORE_PATH=data/java/knowledge-store.json
MEMORY_STORE_PATH=data/java/memory-store.json
EVAL_BASELINE_PATH=data/eval/baseline.json
```

当前用户可见效果已经对齐：应用重启后，导入的知识库、长期记忆和画像可以恢复。底层差异仍然存在：Java 没有直接写入 ChromaDB collection，而是 JSON 持久化 + 本地 hash vector 检索。

### Hybrid RAG

Python 版知识库主要通过 ChromaDB 做语义检索。

Java 版当前检索链路：

```text
文档导入
  -> LangChain4j recursive splitter
  -> JSON 持久化
  -> 本地 documents 索引

查询
  -> LLM 查询改写
  -> 多子查询并行召回
  -> BM25 关键词得分
  -> 本地 hash vector 语义得分
  -> 加权融合
  -> LLM rerank
  -> fallback 到融合分排序
```

这比 Python 版多了 BM25 + vector 融合检索，但没有把 ChromaDB 作为主召回源。

### 评测和监控

Java 版已经实现：

- Intent Accuracy
- Macro-F1
- per-class Precision / Recall / F1
- LLM-as-Judge 四维评分
- baseline 保存和回归检测
- `/monitor`
- `/metrics`
- `/actuator/prometheus`
- Webhook 告警
- Agent 路由惩罚反馈

相关实现：

- `src/main/java/com/sakikomind/evaluation/EndToEndEvaluator.java`
- `src/main/java/com/sakikomind/evaluation/LLMJudge.java`
- `src/main/java/com/sakikomind/monitor/PerformanceMonitor.java`

### Swagger / OpenAPI

Python 版 FastAPI 默认提供 `/docs`。

Java 版现在通过 Springdoc OpenAPI 提供同名入口：

- Swagger UI：`http://localhost:8080/docs`
- Nginx 代理：`http://localhost:8081/docs`
- OpenAPI JSON：`http://localhost:8080/v3/api-docs`

相关实现：

- `pom.xml`
- `src/main/resources/application.yml`
- `src/main/java/com/sakikomind/config/OpenApiConfig.java`
- `src/main/java/com/sakikomind/api/SwaggerDocsController.java`
- `src/main/java/com/sakikomind/api/SakikoMindController.java`

`/docs` 页面会从 jsdelivr CDN 加载 Swagger UI 静态资源；如果部署在不能访问外网的环境，需要把 Swagger UI 静态资源放到项目本地。

## 当前仍然不同的地方

### 通用 MCPToolManager 未完整迁移

Python 版 `MCPToolManager` 可以注册任意 Tool，并统一处理：

- register / unregister
- JSON Schema 参数校验
- timeout
- circuit breaker
- TTL cache
- fallback
- rerank
- 工具统计

Java 版当前是专用 `KnowledgeToolManager`，只服务 `knowledge_search`，但已经具备查询改写、并行召回、缓存、超时、熔断、fallback、rerank 和统计。

要完全对齐，可以继续新增：

- `ToolDefinition`
- `ToolRegistry`
- `ToolExecutor`
- JSON Schema validator

### ChromaDB 不是 Java 版主检索源

Java 版保留 ChromaDB 容器，并引入了 Spring AI Chroma VectorStore starter，但当前 profile 中排除了 Chroma VectorStore 自动配置，主检索仍然走本地 Hybrid RAG。

原因：

- DeepSeek Chat starter 不提供 EmbeddingModel。
- 当前实现优先保证 DeepSeek / Anthropic 都能启动和运行。

后续可增强为：

```text
ChromaDB VectorStore semantic search
  + BM25 keyword recall
  + RRF / weighted fusion
  + LLM rerank
```

### CLI 模式未迁移

Python 版支持：

```bash
python api/main.py --cli
```

Java 版当前没有 CLI。如果需要对齐，可以使用 Spring Shell 或 `CommandLineRunner` 实现。

### 监控算法仍有差异

Python 版监控包含 Z-score 异常检测。

Java 版当前是：

- 成功率阈值告警
- 平均延迟阈值告警
- Webhook
- Micrometer 指标
- 路由惩罚反馈

可继续补：

- Z-score 异常检测
- 告警 resolved 状态
- 请求 latency histogram
- tool success rate gauge

## 主要接口

默认端口：`8080`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/chat` | 主对话接口 |
| POST | `/search` | 知识库检索 |
| POST | `/knowledge/add` | 批量添加知识文档 |
| POST | `/knowledge/upload` | 上传 `.txt` / `.md` / `.json` 文件 |
| GET | `/knowledge/stats` | 知识库统计 |
| GET | `/monitor` | 监控摘要 |
| GET | `/metrics` | Prometheus 指标，兼容 Python 版路径 |
| GET | `/actuator/prometheus` | Spring Actuator Prometheus 指标 |
| POST | `/eval/run` | 运行评测 |
| GET | `/docs` | Swagger UI，可在线调用接口 |
| GET | `/v3/api-docs` | OpenAPI JSON |

Java 版配置了 Jackson `SNAKE_CASE`，响应字段会输出为：

```json
{
  "conversation_id": "...",
  "response": "...",
  "intent": "...",
  "agent_type": "...",
  "escalated": false,
  "latency_ms": 123,
  "knowledge_used": true,
  "verified": true,
  "grounded": true
}
```

和 Python 版主要差异：

- Python 使用 `conv_id`。
- Java 使用 `conversation_id`。
- Java 额外返回 `verified` 和 `grounded`。

推荐请求字段：

```json
{
  "message": "我想申请退款",
  "user_id": "u1001",
  "conversation_id": "optional-conversation-id"
}
```

## 本地运行

### macOS / Linux

准备环境：

- JDK 21 或更高版本
- Docker Desktop 或 Docker Engine

启动依赖：

```bash
docker compose up -d redis chromadb
```

DeepSeek 启动：

```bash
export SPRING_PROFILES_ACTIVE=deepseek
export DEEPSEEK_API_KEY=your_key
./mvnw spring-boot:run
```

Anthropic 启动：

```bash
export SPRING_PROFILES_ACTIVE=anthropic
export ANTHROPIC_API_KEY=your_key
./mvnw spring-boot:run
```

健康检查：

```bash
curl http://localhost:8080/health
```

Swagger：

```text
http://localhost:8080/docs
```

### Windows PowerShell

启动依赖：

```powershell
docker compose up -d redis chromadb
```

DeepSeek 启动：

```powershell
$env:SPRING_PROFILES_ACTIVE="deepseek"
$env:DEEPSEEK_API_KEY="your_key"
.\mvnw.cmd spring-boot:run
```

Anthropic 启动：

```powershell
$env:SPRING_PROFILES_ACTIVE="anthropic"
$env:ANTHROPIC_API_KEY="your_key"
.\mvnw.cmd spring-boot:run
```

健康检查：

```powershell
curl http://localhost:8080/health
```

Swagger：

```text
http://localhost:8080/docs
```

## Docker 部署

复制配置：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，选择模型 profile 并填写对应 API Key。

启动：

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f sakikomind-java
```

停止：

```bash
docker compose down
```

Compose 服务和端口：

| 服务 | 容器名 | 地址 |
|------|--------|------|
| Java App | `sakikomind-java-app` | `http://localhost:8080` |
| Nginx | `sakikomind-java-nginx` | `http://localhost:8081` |
| Prometheus | `sakikomind-java-prometheus` | `http://localhost:9091` |
| ChromaDB | `sakikomind-java-chromadb` | `http://localhost:8002` |
| Redis | `sakikomind-java-redis` | `localhost:6380` |

常用验证：

```bash
curl http://localhost:8080/health
curl http://localhost:8081/health
curl http://localhost:8080/metrics
```

Swagger：

```text
http://localhost:8080/docs
http://localhost:8081/docs
```

## API 示例

### 对话

```bash
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "我想申请退款，订单号是 #12345",
    "user_id": "u1001"
  }'
```

也可以打开 Swagger UI，通过页面直接调用：

```text
http://localhost:8080/docs
```

### 检索

```bash
curl -X POST "http://localhost:8080/search?query=退款多久能到账&topK=3"
```

### 添加知识库

```bash
curl -X POST http://localhost:8080/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "title": "退款补充政策",
        "content": "大促期间退款审核时间可能延长到 3-5 个工作日。"
      }
    ]
  }'
```

### 上传知识库文件

```bash
curl -X POST http://localhost:8080/knowledge/upload \
  -F "file=@docs.md"
```

### 运行评测

```bash
curl -X POST http://localhost:8080/eval/run \
  -H "Content-Type: application/json" \
  -d '{}'
```