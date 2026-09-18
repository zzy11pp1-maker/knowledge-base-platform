# 安全增强版本最终验证报告

> 这是 2026-09-07 的开发验证记录，用于保留真实技术历史；其中数量和耗时不是当前版本的性能基准或采用情况声明。

验证日期：2026-09-07（Asia/Shanghai）

验证位置：项目根目录

## 1. 实际测试结果

- AutoDL `/health`、`/v1/embedding`、`/v1/rerank` 均返回 HTTP 200；dense=1024 维，dense+sparse 同时返回。
- 当时在项目根目录执行的完整 unittest：71/71 通过，0 失败，0 跳过，耗时 29.875 秒。其中 70 项本地单元/组件测试，1 项真实外部管线集成测试。
- SiliconFlow LLM：5/5 通过；模型存在、真实多增量、资料落地答案、Citation 标签均通过。10 个增量，首片段 3.735 秒，总计 3.922 秒。
- 权限强制测试：8/8 通过。技术用户的 `Apollo-77` 确实进入真实 Reranker 和 LLM 并得到正确引用；销售用户的响应、Citation、Reranker 输入、LLM Prompt 均未出现该标记。
- 正式 Uvicorn 端到端：10/10 通过。SSE 收到 start=1、token=17、citation=1、done=1、error=0。
- 正式验证累计 94 项检查通过，0 失败，0 跳过（71 unittest + 5 LLM + 8 权限 + 10 E2E）。

## 2. 端到端链路

管理员登录 → 部门/角色/用户 → 知识库/成员 → Markdown 导入 → AutoDL BGE-M3 dense+sparse → Qdrant payload+ACL → 授权 Hybrid Search → RRF → SQL 二次 ACL → AutoDL Reranker → SiliconFlow DeepSeek-V4-Pro → SSE token → Citation → Conversation 持久化，完整真实跑通。

技术用户只命中技术文档并得到 `Apollo-77` 与真实 Chunk Citation；销售用户不能在搜索、回答、Citation、Reranker 输入、LLM Prompt 或历史中获得技术文档信息。

## 3. 新增与修改文件

新增：

- `app/api/{chat,identity,knowledge}.py`
- `app/core/{auth,business_dependencies}.py`
- `app/db/{__init__,models,session}.py`
- `app/integrations/llm_client.py`
- `app/repositories/authorized_qdrant.py`
- `app/schemas/business.py`
- `app/services/{access_control,authorized_knowledge,chat,conversations,identity_service}.py`
- `bootstrap_admin.py`、`llm_external_test.py`、`permission_security_test.py`、`phase2_end_to_end_test.py`
- `tests/unit/{test_llm_stream,test_phase2}.py`
- `docs/{architecture_security,historical_validation_security}.md`、`pyproject.toml`

修改：`.env.example`、`.env.example.txt`、`.gitignore`、`README.md`、`requirements.txt`、`main.py`、`app/core/{config,logging}.py`、`tests/unit/{test_api,test_qdrant_repository}.py`。基础版本核心 BGE、Reranker、Chunk、Qdrant、RRF 和文档服务实现未整体改写。

## 4. 项目目录树

```text
knowledge_platform/
├─ app/
│  ├─ api/                 chat.py, identity.py, knowledge.py, router.py(基础版本回归)
│  ├─ core/                auth.py, config.py, dependencies.py, business_dependencies.py
│  ├─ db/                  models.py, session.py
│  ├─ ingestion/           readers.py
│  ├─ integrations/        bge_client.py, reranker_client.py, llm_client.py, http_client.py
│  ├─ models/              domain.py
│  ├─ rag/                 document_split.py, rrf.py
│  ├─ repositories/        qdrant_repository.py, authorized_qdrant.py
│  ├─ schemas/             api.py, business.py
│  └─ services/            document_service.py, search_service.py, access_control.py,
│                          authorized_knowledge.py, identity_service.py, chat.py, conversations.py
├─ data/test.md
├─ docs/                   architecture_ingestion_search.md, architecture_security.md,
│                          historical_validation_security.md
├─ tests/integration/      test_external_pipeline.py
├─ tests/unit/             8 个测试模块
├─ bootstrap_admin.py
├─ end_to_end_test.py
├─ llm_external_test.py
├─ permission_security_test.py
├─ phase2_end_to_end_test.py
├─ main.py
├─ pyproject.toml
├─ README.md
└─ requirements.txt
```

`.env`、`.env.txt`、`.venv`、`.local`、日志和缓存均为本机私有/运行时内容，不进入源码树。

## 5. 数据库 Schema 与权限关系

13 张表：users、departments、roles、permissions、user_roles、role_permissions、knowledge_bases、knowledge_base_members、documents、document_permissions、conversations、messages、message_citations。

- Department 使用 parent_id 自关联并检测循环。
- User–Role、Role–Permission 多对多；Token 每次请求重新加载当前角色和权限。
- KBMember 角色为 owner/editor/viewer；非成员统一拒绝。
- 文档 ACL 采用规范化行，global/department/role/user 为 OR；空 ACL 拒绝，管理员不绕过。
- SQLite 开发环境强制外键；模型使用通用 SQLAlchemy 类型，可迁移 PostgreSQL/MySQL。

## 6. Qdrant 与 Authorized RAG

payload 新增 `acl_schema=2`、`acl_version`、`is_global`、`allowed_departments`、`allowed_roles`、`allowed_users`。查询以 tenant+kb+当前 document/version/acl_version 为 must，以四维 ACL 为 should。Qdrant 过滤后仍在 RRF 前、Reranker 前、LLM 生成期间用 SQL 复核，实现 fail-closed。

本机 Qdrant persistent mode 功能通过；其警告说明本地模式 payload index 不产生性能收益。生产应使用 Qdrant Server 才能获得 payload 索引和并发性能。

## 7. LLM、Prompt、SSE、Citation、Conversation

- LLM Adapter 只从环境读取 base URL/model/key；不打印密钥或提供商错误正文。强制 HTTPS（本机例外），验证 content-type、正常 stop、`[DONE]` 和非空增量。
- Prompt 将 Chunk 标为不可信资料，要求仅据资料回答并使用 `[Sx]`；未知 Citation 标签在服务端过滤。
- SSE 使用成熟 `sse-starlette`：`start → token* → citation* → done`；异常/取消为 error 或 aborted，半截回答不持久化。
- Citation 持久化 document_id、chunk_id、title、source、chunk_index、document_version。
- 历史保存所有参与生成的来源；任一来源撤权、删除或换版本，整轮历史隐藏且不再进入新 Prompt。

## 8. 外部服务依赖

- AutoDL RTX 3080 Ti：`http://127.0.0.1:18000`（SSH 隧道），BGE-M3 `/v1/embedding`，Reranker `/v1/rerank`。
- SiliconFlow OpenAI-compatible API：配置模型 `deepseek-ai/DeepSeek-V4-Pro`。
- Qdrant：验证使用本地持久化模式；生产建议 Qdrant Server。
- 业务数据库：验证 SQLite；生产建议 PostgreSQL。

## 9. 启动与测试命令

```powershell
cd <project-root>
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python bootstrap_admin.py --tenant your-tenant --username admin
python -m uvicorn main:app --host 0.0.0.0 --port 8080
```

```powershell
python -m unittest discover -s tests/unit -v
$env:RUN_EXTERNAL_INTEGRATION="1"
python -m unittest discover -s tests -v
python llm_external_test.py
python permission_security_test.py
python phase2_end_to_end_test.py --base-url http://127.0.0.1:8080 --tenant your-tenant
```

## 10. 代码审查与已修复问题

- Ruff：通过；compileall：通过；pip check：无损坏依赖。
- TODO/FIXME/NotImplemented/pass：生产代码未发现。
- Mock：只存在显式单元测试；Integration、权限安全、LLM 和 E2E 均调用真实服务。
- 未发现硬编码 API Key/IP/密码；验证密码只存在测试脚本，生产密钥不进入源码或日志。
- 已覆盖：Token 缺失/伪造/停用、RBAC、部门循环、KB 成员、四维 ACL、空 ACL、跨租户、metadata/title/content 零泄漏、上游断流、非正常 finish、无效引用、历史撤权和并发会话锁。
- 中途出现的受限网络导致 LLM 502、旧进程锁住 Qdrant、Windows 目录嵌套同步均已定位修复；最终正式结果为 0 失败。
- 临时副本与项目共比较 71 个源码/文档文件，哈希不一致数量为 0。

## 11. 参考情况与取舍

- 旧 shopkeeper_brain：借鉴 Markdown 表格、dense+sparse、事件分层；未复用商品名识别、旧 Milvus schema、旧 FastAPI 原型、全局 Queue 和临时代码。
- GitHub：Onyx 的检索/聊天 ACL 原则；sse-starlette 的断流、心跳、发送超时。未整体复制大项目代码。
- 官方：FastAPI JWT、SQLAlchemy Session/并发版本、Qdrant Filtering/Hybrid/RRF、SiliconFlow Chat/SSE。
- 没有引入 LangChain/LangGraph：当前固定管线直接实现更清晰；出现复杂 Agent 工作流需求时再评估。

## 12. 未完成项、验证结论与后续版本建议

安全增强版本代码与真实链路达到验证标准。当前项目 `.env` 已能自动加载现有 LLM 配置，但日常启动前仍需由部署者补充随机 `JWT_SECRET`、BGE/Reranker 地址，以及 Qdrant URL 或 location；验证时这些值通过环境变量注入，未硬编码。

该版本按要求未开发前端、FAQ、知识缺口、LangGraph、多 Agent 或生产容器。生产上线前应补 Alembic 迁移、PostgreSQL/Qdrant Server、登录限流/审计日志、密钥管理、反向代理 TLS 与多实例并发压力测试。后续版本建议先做 API 契约冻结和生产数据库迁移，再进入 LLM 策略/前端等全栈版本内容。
