# 基础版本设计与参考记录

## 范围

该版本完成：Markdown/TXT 导入、结构化 Chunk、远程 BGE-M3 dense+sparse、Qdrant 入库与隔离过滤、双路检索、RRF、远程 Reranker、文档导入/列表/删除 API、单元测试和真实外部集成测试入口。

该版本不包含：LLM 答案生成、SSE、完整 RBAC、FAQ、知识缺口分析、前端、PDF/DOCX、异步任务队列。

## 完整数据流

```text
Markdown/TXT
  -> 编码/大小校验与清洗
  -> Markdown 标题树和代码围栏识别
  -> 段落/句子优先切分 + overlap
  -> 稳定 Document/Chunk ID 与 metadata
  -> 远程 BGE-M3 批量 dense+sparse
  -> Qdrant named vectors + payload upsert

query
  -> 远程 BGE-M3 query dense+sparse
  -> tenant_id + kb_id + metadata 强制过滤
  -> dense Top-N + sparse Top-N
  -> RRF（保留两路 rank/score）
  -> 远程 Reranker
  -> Top-K Chunk + 来源
```

## 关键架构决策及理由

1. **显式 Service 流水线，不引入 LangGraph。** 当前导入与检索路径是线性的，普通服务编排更容易测试、定位错误和维护。等出现可恢复的多分支长任务后再评估工作流框架。
2. **一个版本化 collection，使用 named vector `dense`/`sparse`。** 同一 Chunk 的两种表示共享 payload，天然避免元数据漂移。collection 名带 `v1`，模型维度或距离度量变化时新建 collection，不破坏旧数据。
3. **`tenant_id + kb_id` 是不可省略的服务端过滤条件。** 当前尚未实现认证，但存取边界已固定；后续接 RBAC 时把 tenant 从请求参数改为认证上下文，不需要改向量 schema。
4. **RRF 在应用层实现。** Qdrant 支持服务端融合，但应用层可完整保留 dense/sparse rank、原始分数和 RRF 分数，便于调试、评估和问题分析。候选规模有限，额外开销可控。
5. **文档 ID 与 Chunk ID 稳定。** 文档默认由租户/知识库/文件名生成稳定 ID，版本由内容哈希生成；Chunk ID 包含文档版本、顺序和内容哈希。重复导入同内容是幂等 upsert，更新内容时先写新版本再删旧版本，避免模型失败先清空线上知识。
6. **Qdrant 不是长期文档主库。** 基础版本为了闭环，从 payload 聚合 `GET /documents`。后续版本应由 PostgreSQL 保存 Document/Version/IngestionJob 状态，Qdrant 只负责检索，并用 outbox/任务状态解决跨库一致性。
7. **语义边界优先、字符数作为预算。** 业务服务不安装 FlagEmbedding/tokenizer，保持 GPU 服务解耦；切分优先标题、段落、句子，仅超长单元才字符兜底。后续可由远程 tokenizer 计数或独立轻量 tokenizer 替换预算策略。
8. **客户端有限重试且不记录正文。** 只对网络错误和可恢复状态重试，日志只记录批量大小、标识和异常类型，避免知识正文或密钥进入日志。
9. **远程与本地 Qdrant 使用同一 Repository。** 生产通过 `QDRANT_URL` 连接独立服务；没有 Docker 的开发机可用 `QDRANT_LOCATION` 运行官方客户端的本地持久化实现。两项互斥，避免误连。
10. **向量分批写入且允许安全重试。** 大文档按 `QDRANT_UPSERT_BATCH_SIZE` 分批 upsert；若中途失败，旧版本仍保留，下一次使用稳定 ID 重试完成后再清理旧版本。

## 本地旧项目参考

- `shopkeeper_brain/knowledge/processor/import_process/nodes/ducment_split.py`：只借鉴标题层级、代码围栏意识；重新实现了空文档、overlap、稳定 ID、元数据和边界测试。
- `shopkeeper_brain/knowledge/utils/embedding_util.py`：只借鉴 dense+sparse 数据契约；新项目改为远程 HTTP 客户端，不加载本地模型。
- `shopkeeper_brain/knowledge/processor/query_process/nodes/rrf.py`：借鉴按排名倒数融合思路；新实现脱离 LangGraph，并保留各路排名和分数。
- `shopkeeper_brain/knowledge/processor/query_process/nodes/rerank.py`：只借鉴候选精排位置；新实现调用远程 API，严格校验条目数量、索引和有限分数。

明确未复用：商品名识别、旧 Milvus schema、旧 FastAPI 原型、内存任务/SSE、临时测试代码及旧项目密钥文件。旧项目源码未被修改。

## 官方文档与开源项目参考

- [Qdrant Hybrid Queries](https://qdrant.tech/documentation/search/hybrid-queries/)：named dense/sparse、双路检索和融合能力。
- [Qdrant Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/)：共享 collection + tenant payload/index 的多租户建议。
- [Qdrant Payload Indexing](https://qdrant.tech/documentation/concepts/indexing/)：tenant/kb/document 精确过滤字段建立 payload index。
- [FastAPI Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/)：路由、依赖、schema、服务职责拆分方式。
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)：环境变量优先、`.env` 和 SecretStr 配置管理。
- [FlagEmbedding BGE-M3](https://github.com/FlagOpen/FlagEmbedding/blob/master/research/BGE_M3/README.md)：BGE-M3 的 dense/lexical sparse 输出及 1024 维模型约束。
- [RAGFlow 知识库配置](https://github.com/infiniflow/ragflow/blob/main/docs/guides/dataset/configure_knowledge_base.md)：知识库、文档、Chunk 分层及 embedding 模型与已入库数据绑定的产品设计。

没有复制上述项目的大段代码；它们只用于校验 API 用法和架构取舍。
