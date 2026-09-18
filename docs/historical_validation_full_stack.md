# 企业知识库管理平台：全栈版本验证报告

> 这是 2026-09-07 的开发验证记录，用于保留真实技术历史；其中数量和耗时不是当前版本的性能基准或采用情况声明。

验证日期：2026-09-07

验证对象：项目根目录

结论：**全栈版本达到验证标准**。最终有效验证共 128 项通过，0 失败，0 跳过；Python 编译检查和 React/TypeScript 生产构建另行通过。

## 1. 新增与修改文件

新增后端文件：

- `app/api/operations.py`：FAQ、知识缺口、Dashboard、埋点、受保护 Citation API。
- `app/ingestion/parsers.py`：PDF、DOCX、Markdown、TXT Parser Adapter 与统一注册表。
- `app/services/faq.py`：问题标准化、Embedding 聚类、候选审核、发布、ACL 安全命中。
- `app/services/gaps.py`：五类知识缺口聚合、反复未解决识别、关闭与排序。
- `app/services/dashboard.py`：真实业务统计及普通用户可见范围过滤。
- `app/services/metrics.py`：低耦合业务事件写入与安全 metadata 白名单。
- `tests/unit/test_phase3.py`：全栈版本功能、安全与生命周期测试。
- `phase3_end_to_end_test.py`：管理员、技术、销售三类用户真实 HTTP E2E。
- `requirements-dev.txt`：可复现测试依赖入口。
- `docs/architecture_operations.md`：架构设计与参考资料记录。
- `frontend/`：React + TypeScript + Vite + Ant Design 控制台。

修改文件：

- `main.py`、`.env.example`、`README.md`、`requirements.txt`
- `app/api/knowledge.py`
- `app/core/auth.py`、`app/core/config.py`
- `app/db/models.py`、`app/db/session.py`
- `app/ingestion/readers.py`
- `app/integrations/llm_client.py`
- `app/repositories/qdrant_repository.py`
- `app/schemas/business.py`
- `app/services/authorized_knowledge.py`、`app/services/chat.py`
- `app/services/document_service.py`、`app/services/identity_service.py`

正式同步前已备份原源码到工作区 `backups/knowledge_platform_pre_phase3_20260907_150345.zip`；同步未覆盖 `.env`、虚拟环境或原运行数据。

## 2. 数据库新增表和字段

新增 5 张表：

| 表 | 作用 |
|---|---|
| `faq_clusters` | 保存标准问题、真实问题样本、消息来源、Embedding 与出现次数 |
| `faq_candidates` | 保存 pending / approved / rejected / published 审核状态和候选答案 |
| `faq_entries` | 已发布 FAQ、Embedding、来源消息、启用状态和命中次数 |
| `knowledge_gaps` | 按 tenant + KB + 标准问题 + 类型聚合知识缺口 |
| `metric_events` | PV/UV、耗时、Token、权限拒绝等真实业务事件 |

`documents` 新增 `file_type`、`error_message`、`updated_at`，并扩展 `status` 的 processing / ready / failed / deleted / reindex / acl_syncing / acl_failed 语义。版本字段继续承载内容、切分参数和 Embedding 模型共同决定的索引版本。

当前 SQLite 启动时执行向后兼容的增量字段迁移；生产数据库迁移工具留到后续版本 PostgreSQL 迁移时统一引入。

## 3. API 清单

全栈版本新增或扩展的主要 API：

- 文档：`POST /documents/import/batch`、`POST /documents/{id}/reindex`、`DELETE /documents/{id}`。
- FAQ：`POST /knowledge-bases/{kb_id}/faq/mine`、`GET .../faq/candidates`、`PATCH /faq/candidates/{id}`、`POST /faq/candidates/{id}/publish`、`GET .../faqs`、`PATCH /faqs/{id}`。
- 缺口：`GET /knowledge-bases/{kb_id}/gaps`、`GET .../gaps/trend`、`POST /gaps/{id}/resolve`、`POST /gaps/{id}/faq-candidate`。
- 运营：`POST /metrics/page-view`、`GET /dashboard/summary`。
- Citation：`GET /citations/{message_id}/{chunk_id}`，返回前重新执行消息归属、租户、KB、文档 ACL 和版本检查。

基础版本与安全增强版本的身份、RBAC、KB 成员、文档 ACL、检索、Chat/SSE、Conversation API 全部保留。

## 4. 前端页面清单

前端包含 13 个路由页面，覆盖要求中的 20 类功能交互：登录、Dashboard、用户、部门、角色、功能权限、知识库、知识库成员、批量上传与文档列表、文档 ACL/重建/删除、AI 问答、SSE、Markdown、Citation 卡片、历史 Conversation 查看与恢复、FAQ 候选审核、已发布 FAQ、知识缺口。

浏览器烟雾验证真实执行了登录、Dashboard、创建/选择知识库、文档与 ACL、SSE 回答显示、历史消息查看和继续问答。前端只控制展示；所有授权决定仍由后端执行。

## 5. FAQ 流程

完成回答并保存 Citation 后，系统异步增量观察真实 Conversation/Message。问题经规则标准化，再用 BGE dense Embedding 余弦相似度聚类；Cluster 累计样本、次数和最后出现时间。达到阈值且有完整高质量回答时创建 Candidate，管理员执行 pending → approved/rejected → published。发布项保存来源消息，不复制一个脱离来源的“万能答案”。

FAQ 命中前重新检查候选来源消息、所有 used Citation、文档版本和当前 ACL；任何来源被删除、重建或撤权都会导致该 FAQ 对当前用户不可见，随后回落到普通 RAG。FAQ 不能绕开 ACL，也不会把无权内容送入 Reranker 或 LLM。

## 6. Knowledge Gap 流程

当前支持五类：`empty_retrieval`、`low_retrieval_score`、`low_rerank_score`、`evidence_refusal`、`repeated_unresolved`。前三类在检索/精排阶段记录，证据不足拒答在生成阶段记录；同一标准问题跨失败类型累计三次后形成 `repeated_unresolved`。

缺口按 tenant、KB、标准问题和类型聚合，保留最多 10 条原始样本、首次/最近时间、次数、状态和建议动作。只有具备 `gap:manage` 且是 KB owner 的管理员可以查看、关闭或生成 FAQ Candidate。

## 7. Dashboard 指标来源

- PV、UV：前端页面访问产生的 `page_view` 事件，UV 按当前窗口内用户去重。
- KB、文档、Chunk、FAQ：SQL 业务表实时聚合；文档与热门文档先做 ACL 过滤。
- 高频 FAQ、热门文档、Gap Top N：FAQ 命中、有效 Citation 与 Gap 实表聚合。
- Token、LLM 调用：OpenAI-compatible 流响应真实 usage；提供商未返回 usage 时记为不可用，不估算冒充。
- 平均/P50/P95、检索、Rerank、首 Token、LLM 总耗时：对应真实服务阶段埋点。
- 权限拒绝：统一 HTTP 异常处理记录 401/403 业务拒绝。
- 7/30 天趋势：按事件日期聚合；所有查询先限制 tenant，再限制 KB 和当前用户可见文档。

## 8. 文档生命周期

所有 PDF、DOCX、Markdown、TXT 与批量上传统一进入：Document → Parser Adapter → Normalized Text → Chunk → BGE dense+sparse → Qdrant。

导入先写 `processing`；解析、Embedding、Qdrant 成功后原子更新为 `ready`；异常保存脱敏错误并标为 `failed`。内容、切分配置和模型标识未变化时幂等返回，避免重复向量化。重建使用 `reindex` 并在成功后替换旧 Chunk；删除先标记 `deleted`，同步删除 Qdrant Chunk 并将 SQL `chunk_count` 归零。ACL 变更失败时保留 `acl_failed`，避免 SQL 和向量 Payload 静默失配。

## 9. 权限安全设计

- 每个查询都携带 tenant_id 与 kb_id；Qdrant 先执行 payload 过滤，SQL 再做权威 ACL 二次鉴权。
- 未授权 Chunk 在进入 Reranker 前被剔除，因此也不会进入 LLM Prompt。
- FAQ Candidate 审核、发布、缓存命中均校验来源 Citation ACL 与文档版本。
- Citation 内容端点从消息关系反查，不接受前端自报权限，返回前再次鉴权。
- Dashboard 文档明细和 FAQ 对普通用户按 ACL 过滤；Gap 仅管理员可见。
- 密钥使用 `SecretStr` 和环境变量；测试与报告未打印 `.env` 内容。

权限边界真实审计 8/8：技术用户可将 Apollo-77 资料送入 Reranker/LLM；销售用户的同一资料未进入 Reranker、未进入 LLM、未出现在检索响应或 Citation。

## 10. 测试命令

在项目根目录执行：

```powershell
cd <project-root>
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests\unit -q

$env:RUN_EXTERNAL_INTEGRATION='1'
$env:BGE_BASE_URL='http://127.0.0.1:18000'
$env:RERANKER_BASE_URL='http://127.0.0.1:18000'
$env:QDRANT_URL=''
$env:QDRANT_LOCATION='.local/qdrant-external-test'
.\.venv\Scripts\python.exe -m unittest tests.integration.test_external_pipeline -v

.\.venv\Scripts\python.exe llm_external_test.py --env-file .env
.\.venv\Scripts\python.exe permission_security_test.py
.\.venv\Scripts\python.exe phase2_end_to_end_test.py --base-url http://127.0.0.1:8080 --tenant phase2-acceptance
.\.venv\Scripts\python.exe phase3_end_to_end_test.py --base-url http://127.0.0.1:8080 --tenant phase3-acceptance

cd frontend
npm ci
npm run build
```

验证用 JWT、数据库和 Qdrant 路径应通过当前 PowerShell 会话的环境变量设置，不写入源码；正式运行使用 `.env`。

## 11. 每组测试通过数量

| 测试组 | 通过 | 失败 | 跳过 |
|---|---:|---:|---:|
| Python 单元/组件回归 | 80 | 0 | 0 |
| 真实 AutoDL + Qdrant 外部集成 | 1 | 0 | 0 |
| SiliconFlow LLM 外部检查 | 5 | 0 | 0 |
| 权限边界审计 | 8 | 0 | 0 |
| 基础版本原始 E2E 步骤 | 5 | 0 | 0 |
| 安全增强版本正式 Uvicorn E2E | 10 | 0 | 0 |
| 全栈版本正式 Uvicorn E2E | 13 | 0 | 0 |
| 前端浏览器核心烟雾检查 | 6 | 0 | 0 |
| **合计** | **128** | **0** | **0** |

另：Python `compileall` 通过；前端 `tsc -b && vite build` 通过。Vite 仅提示主包约 1.23 MB，属于后续按路由拆包的性能优化项，不影响正确性。

## 12. 真实 E2E 结果

AutoDL 服务 `http://127.0.0.1:18000` 实际返回 BGE-M3 dense+sparse，并完成本地持久化 Qdrant 写入、Hybrid Search、RRF 和真实 Reranker。SiliconFlow `deepseek-ai/DeepSeek-V4-Pro` 实际产生 10 个流增量，首增量 4.250 秒，总计 4.437 秒，回答包含 Cedar-28 和 `[S1]`。

安全增强版本链路 10/10：JWT → RBAC → KB 成员 → ACL → BGE → Qdrant → RRF → SQL 二次鉴权 → Reranker → LLM → SSE → Citation → Conversation。SSE 收到 start 1、token 14、citation 1、done 1。

全栈版本 13/13：批量导入、技术用户 SSE/Citation/历史恢复、FAQ 自动沉淀/发布、管理员 Dashboard、销售用户 FAQ/Chat/Dashboard 隔离、Gap 权限边界均通过。

浏览器实测中发现并修复了 CRLF SSE 分帧兼容问题；修复后页面真实显示证据不足回答并持久化 Conversation，历史页可加载消息并返回工作台继续问答。

## 13. 基础版本与安全增强版本回归结果

- 基础版本 `end_to_end_test.py`：读取 1 个 Markdown，切分 4 个 Chunk，Qdrant 计数 4，Hybrid TopK 4 均有真实 Rerank 分数，5/5 步骤通过。
- 安全增强版本 `permission_security_test.py`：8/8。
- 安全增强版本 `llm_external_test.py`：5/5。
- 安全增强版本 `phase2_end_to_end_test.py`：10/10。
- 全部 80 个单元/组件测试包含基础版本与安全增强版本原测试，最终再次通过。

## 14. 前后端启动方法

先保证本机隧道 `127.0.0.1:18000` 可访问，并在 `.env` 配置数据库、随机 JWT Secret、BGE/Reranker、Qdrant 和 LLM。不要把密钥写进启动命令或日志。

```powershell
cd <project-root>
.\.venv\Scripts\Activate.ps1
python bootstrap_admin.py --tenant your-tenant --username admin
python -m uvicorn main:app --host 127.0.0.1 --port 8080
```

另开终端：

```powershell
cd <project-root>\frontend
npm ci
npm run dev -- --host 127.0.0.1
```

浏览器访问 `http://127.0.0.1:5173`。生产构建使用 `npm run build`。

## 15. 仍未完成事项

以下均按需求留到后续版本：PostgreSQL 生产迁移、Qdrant Server 正式部署、Docker Compose、TLS、压力测试、Kubernetes、LoRA/QLoRA、Multi-Agent。

非阻塞优化项：前端按路由懒加载以缩小首包；引入 Alembic 替代当前 SQLite 增量迁移；生产级异步任务队列与失败补偿；独立 Qdrant Server 验证 payload index 性能。Windows 终端中文日志存在代码页显示乱码，但 UTF-8 API/数据库/页面内容均正常。

## 16. 验证结论

全栈版本目标均已有可运行实现，且曾在项目根目录使用真实 AutoDL、Qdrant、SiliconFlow、Uvicorn 和浏览器完成验证。FAQ、Gap、Dashboard、Citation 与文档生命周期均保留 tenant/KB/RBAC/ACL 安全边界，没有使用 Mock 冒充外部验证，也未提前开发后续版本内容。

**结论：全栈版本通过验证，可以进入演示与后续版本规划。**

## 参考资料与取舍

- FastAPI 文件上传：<https://fastapi.tiangolo.com/tutorial/request-files/>。采用多 `UploadFile` 和统一 Parser 流程；未为每种格式复制一套 RAG。
- Qdrant 多租户分区：<https://qdrant.tech/documentation/tutorials/multiple-partitions/>；基础设计：<https://qdrant.tech/documentation/faq/qdrant-fundamentals/>。采用单 collection + tenant/kb/document payload 过滤和 SQL 二次鉴权；未延续旧 Milvus schema。
- Vite：<https://vite.dev/guide/>；Ant Design：<https://ant.design/components/overview/>。采用轻量 React/Vite 企业控制台；未引入低代码或重型微前端。
- SiliconFlow Chat Completions：<https://docs.siliconflow.com/en/api-reference/chat-completions/chat-completions>。采用 OpenAI-compatible SSE 与提供商真实 usage；未自行估算 Token。
- Onyx/Danswer：参考企业 RAG 的连接器、权限同步与 Citation 分层思想；未复制其大型任务系统和部署架构。
- 旧 `shopkeeper_brain`：参考 `knowledge/processor/import_process/nodes/pdf_to_md.py` 和 `import_file_service.py` 的“先归一化再统一切分”思想；未复用 MinerU 子进程、商品名识别、旧 Milvus schema、旧 FastAPI 原型和临时代码。
