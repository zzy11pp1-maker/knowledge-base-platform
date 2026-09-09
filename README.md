# 2.9 企业知识库管理平台（第三阶段）

当前版本在第二阶段授权 RAG 闭环上增加真实 FAQ 自动沉淀、知识缺口、运营看板、PDF/DOCX/Markdown/TXT 批量导入、文档重建/删除生命周期和 React 管理控制台。FAQ、Citation、Dashboard 与 Gap 均继续执行后端租户/KB/文档 ACL。

## 验收截图（老师请点这里）

[查看部署验收截图与说明](docs/截图/README.md)

## 本地准备

```powershell
cd E:\ai_projects\knowledge_platform
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在 `.env` 配置数据库、随机 `JWT_SECRET`、BGE/Reranker、Qdrant 和 LLM。密钥文件已被 Git 忽略。无 Docker 时可使用 `QDRANT_LOCATION=./.local/qdrant`；生产建议独立 Qdrant 服务与 PostgreSQL。

首次启动前以隐藏输入初始化管理员：

```powershell
python bootstrap_admin.py --tenant your-tenant --username admin
```

## 启动与健康检查

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8080
```

- Swagger：`http://127.0.0.1:8080/docs`
- 存活检查：`GET /health`
- 登录后管理员依赖检查：`GET /health/dependencies`

先调用 `POST /auth/login`，在后续请求中发送 `Authorization: Bearer <token>`。租户身份只取自 Token；请求体中的 tenant 不能切换租户。

## 核心 API

- 身份与组织：`/auth/login`、`/auth/me`、`/users`、`/roles`、`/permissions`、`/departments`
- 知识库与成员：`/knowledge-bases`、`/knowledge-bases/{id}/members`
- 文档与权限：`/documents/import`、`/documents`、`/documents/{id}/permissions`
- 批量与生命周期：`/documents/import/batch`、`/documents/{id}/reindex`、`DELETE /documents/{id}`
- 检索问答：`/search`、`/chat`、`/chat/stream`
- 会话：`/conversations`、`/conversations/{id}/messages`
- FAQ：`/knowledge-bases/{id}/faq/mine`、`/faq/candidates/{id}`、`/faqs`
- 缺口与看板：`/knowledge-bases/{id}/gaps`、`/dashboard/summary`、`/metrics/page-view`
- 受保护引用正文：`/citations/{message_id}/{chunk_id}`

SSE 事件顺序为 `start → token* → citation* → done`；失败只发送 `error`，不会再发送 `done`。Citation 只来自本轮已授权且实际送入模型的 Chunk。

## 测试

```powershell
python -m unittest discover -s tests/unit -v
$env:RUN_EXTERNAL_INTEGRATION="1"
python -m unittest discover -s tests -v
python llm_external_test.py
python permission_security_test.py
python phase2_end_to_end_test.py --base-url http://127.0.0.1:8080 --tenant your-acceptance-tenant
python phase3_end_to_end_test.py --base-url http://127.0.0.1:8080 --tenant your-acceptance-tenant
```

前端启动：

```powershell
cd frontend
npm install
npm run dev
```

开发服务器把 `/api` 代理到 `http://127.0.0.1:8080`。外部和端到端测试必须连接真实 BGE-M3、Reranker、Qdrant/本地持久化存储及 LLM，不能用单元测试替身代替验收。第三阶段决策与参考依据见 `docs/phase3_design.md`。
