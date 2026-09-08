# 第二阶段架构与安全决策

## 数据与权限模型

SQLAlchemy 2.x 保存 `User / Department / Role / Permission / KnowledgeBase / Document / Conversation / Message`。用户和角色、角色和功能权限、知识库和成员均用关联表表达。SQLite 用于本机验收并强制开启外键；模型没有 SQLite 专属字段，可迁移 PostgreSQL/MySQL。当前阶段只做首次建表，生产升级前应补 Alembic 版本迁移。

知识读取必须同时满足：Token 当前有效、具有 `knowledge:read`、仍是知识库成员、文档状态为 ready、四维 ACL 至少一项命中。ACL 是 OR：global（仍限定租户及 KB 成员）、department、role、user；空 ACL 为拒绝。管理员不绕过文档 ACL。

Qdrant payload 保存 `tenant_id/kb_id/document_id/document_version` 以及 `acl_schema/acl_version/is_global/allowed_departments/allowed_roles/allowed_users`。向量查询先用 Qdrant must/should 过滤；RRF 前、Reranker 前和 LLM token 生成期间再以关系库实时核验。ACL 或文档版本变化会让旧向量副本立即失效。

## Authorized RAG 与引用

数据流：认证 → RBAC → KB 成员 → SQL 当前 ACL → BGE-M3 dense+sparse → Qdrant 预过滤 → SQL 二次复核 → RRF → SQL 再复核 → Reranker → Prompt → 生成期间持续复核 → Citation 校验 → 持久化。

每个 Prompt 来源分配 `[S1]` 等本轮标签；返回的 Citation 由服务端按标签映射实际 Chunk，模型写出的未知标签会被移除。历史消息保存所有参与生成的来源，不只保存模型显式引用；任一来源撤权、删除或换版本后，整轮历史不再返回或进入新 Prompt。

无授权资料时不调用 LLM，直接返回“资料不足”的业务结果。`restricted_sources_detected` 只执行无 payload 的存在性探测，不读取受限标题、正文或 metadata。

## LLM 与 SSE

LLM Adapter 使用 OpenAI-compatible `/chat/completions` 和真实 `stream=true`。密钥只从环境读取；异常不回显提供商正文。流必须同时出现正常 `finish_reason=stop`、`[DONE]` 和至少一个正文增量，否则判为失败，不持久化半截回答。

采用 `sse-starlette` 的 `EventSourceResponse`，利用成熟实现处理客户端断开、心跳、发送超时和取消；未复制其源码。旧项目的全局同步 Queue 方案只借鉴事件命名思路，因多进程隔离、阻塞线程和清理风险未采用。

## 参考与取舍

- 旧 `shopkeeper_brain`：参考 Markdown 表格线性化、BGE-M3 dense+sparse、SSE 事件分层思路；未复用商品识别、旧 Milvus schema、全局 Queue、旧 FastAPI 原型及临时代码。
- [FastAPI OAuth2/JWT 官方教程](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)：采用 Bearer、PyJWT、Argon2/pwdlib；没有引入完整 OAuth 授权服务器。
- [SQLAlchemy Session 官方文档](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) 与 [版本计数器](https://docs.sqlalchemy.org/en/20/orm/versioning.html)：采用短 Session、事务和条件更新；本阶段不堆叠 Unit of Work 框架。
- [Qdrant Filtering](https://qdrant.tech/documentation/search/filtering/) 与 [Hybrid Queries](https://qdrant.tech/documentation/search/hybrid-queries/)：采用 keyword payload、must/should、dense+sparse 和 RRF；保留应用层 RRF，方便解释两路排名并兼容第一阶段。
- [Onyx RBAC/权限说明](https://www.mintlify.com/onyx-dot-app/onyx/administration/permissions-and-rbac)：借鉴“检索和聊天时均按已存 ACL 过滤”；未整体复制其大型连接器/多服务架构。
- [sse-starlette](https://github.com/sysid/sse-starlette)：采用其生产级断流、心跳和发送超时能力，未复制实现。
- [SiliconFlow Chat Completions](https://docs.siliconflow.cn/docs/api/chat-completions-post) 与 [流式模式](https://docs.siliconflow.cn/docs/userguide/capabilities/stream-mode)：按兼容 SSE 协议实现并真实验证配置模型。

没有引入 LangChain/LangGraph：本阶段流程固定、分支少，直接业务服务更清晰；需要复杂 Agent 工作流时再评估。
