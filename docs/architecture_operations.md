# 全栈版本设计记录

## 目标与边界

该版本只增加 FAQ 自动沉淀、知识缺口、运营指标、统一多格式导入、文档生命周期和 React 管理控制台。沿用安全增强版本 JWT、RBAC、KB 成员、文档 ACL、SQL 二次鉴权、Reranker 前鉴权、LLM Prompt 前鉴权和 Citation 撤权校验，不引入微调、多 Agent、生产 PostgreSQL、生产 Qdrant Server、TLS、Kubernetes 或压测。

## 关键决策

1. FAQ 缓存保存 `source_message_id`，发布前必须存在历史有效 Citation。命中时逐条核验当前文档版本、租户、KB 成员和 ACL；任一来源失效则放弃缓存，回到正常 RAG 链路。
2. FAQ 聚类只在同一 tenant、同一 KB 内执行。先做 NFKC/空白/结尾标点标准化，再以 BGE dense cosine 相似度聚类；簇向量使用运行均值更新，保证算法可解释。
3. KnowledgeGap 的唯一键包含 tenant、KB、标准问题和缺口类型，重复事件增加次数并重新打开。缺口正文样本不进入 MetricEvent。
4. Dashboard 直接聚合关系数据库业务表和 MetricEvent。文档、FAQ、热门引用均先按当前用户文档 ACL 过滤；缺口明细只向具有 `gap:manage` 的用户返回。
5. 文档解析统一使用 Parser Adapter：文件字节先转换为规范文本或 Markdown，再复用原有 Chunk、BGE、Qdrant 链路。PDF 使用 pypdf 文本层；扫描件明确失败，不用 OCR 假装成功。DOCX 保留标题并线性化表格。
6. 重复文件的索引版本同时绑定正文哈希、切分参数和 embedding 模型。版本未变化时不重复向量化；新版本成功后再删除旧 Qdrant points。
7. 前端只调用受保护后端；隐藏按钮从不被视为授权。Citation 卡片通过受保护的精确 Chunk 接口取正文，而不是使用前端已缓存的任意内容。

## 参考资料与取舍

- FastAPI 文件上传：[Request Files](https://fastapi.tiangolo.com/tutorial/request-files/)。采用 `list[UploadFile]` 的批量上传契约和流式文件对象；未引入单独上传框架。
- Qdrant 多租户：[Multiple Partitions](https://qdrant.tech/documentation/tutorials/multiple-partitions/) 与 [Qdrant Fundamentals](https://qdrant.tech/documentation/faq/qdrant-fundamentals/)。采用 payload tenant/KB 分区和强制过滤；当前版本继续使用本地 Qdrant，不提前部署生产集群。
- Vite：[Getting Started](https://vite.dev/guide/) 与 [Features](https://vite.dev/guide/features.html)。采用 React + TypeScript 官方插件、开发代理和生产构建；没有引入额外元框架。
- Ant Design：[Components Overview](https://ant.design/components/overview/)、[Table](https://ant.design/components/table/) 和 [Form](https://ant.design/components/form/)。采用企业后台通用表格、表单、卡片；没有复制第三方后台模板。
- SiliconFlow：[Chat Completions](https://docs.siliconflow.com/en/api-reference/chat-completions/chat-completions)。按真实返回的 usage 字段记录 Token；提供商未在流式事件返回 usage 时显式记录 `usage_available=false`，不估算冒充计费数据。
- Onyx/Danswer：参考“连接器/解析、索引、检索、权限过滤分层”的架构思想；未复制其大型任务系统、连接器生态或前端实现，因为本项目当前范围更小，且既有安全增强版本 ACL 链路必须保持可审计。
- 旧 `shopkeeper_brain`：复核了 `knowledge/processor/import_process/nodes/pdf_to_md.py` 和 `import_file_service.py` 的 PDF→Markdown→入库思路。保留“先规范化再统一切分”的思想；未复用 MinerU 子进程、临时目录、旧 Milvus schema、商品名逻辑和旧 FastAPI 原型，以避免重量依赖、日志泄漏和架构耦合。

## 数据流

`UploadFile → ParserRegistry → Normalized Text/Markdown → Chunk → BGE dense+sparse → Qdrant`

`Conversation/Message → 问题标准化 → KB 内 embedding 聚类 → FAQCandidate → 审核 → FAQEntry → 运行时 ACL/版本复核 → 命中或回退 RAG`

`检索/重排/拒答事件 → KnowledgeGap 聚合 → 管理员处理或转 FAQ 候选`

`真实业务请求 → MetricEvent → 当前用户授权范围聚合 → Dashboard`
