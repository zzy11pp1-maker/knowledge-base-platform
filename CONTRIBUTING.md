# Contributing

感谢你改进企业知识库管理平台。请优先提交小而完整、能够独立验证的改动。

## Before You Start

- 对缺陷、较大功能或行为变更，建议先创建 Issue 说明问题、预期行为和边界。
- 安全漏洞不要发布到公开 Issue；请遵循 [SECURITY.md](SECURITY.md)。
- 不要提交真实业务文档、API Key、Token、密码、Cookie、私有服务器地址或包含这些内容的日志。

## Development Setup

后端需要 Python 3.11+：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Windows PowerShell 使用 `.\.venv\Scripts\Activate.ps1` 和 `Copy-Item .env.example .env`。

前端需要 Node.js `^20.19.0` 或 `>=22.12.0`：

```bash
cd frontend
npm ci
```

## Tests

提交前至少运行：

```bash
python -m unittest discover -s tests/unit -v
cd frontend
npm run build
```

公共 CI 不连接真实 BGE、Reranker、LLM 或远程 Qdrant。只有在你拥有隔离测试环境时，才设置 `RUN_EXTERNAL_INTEGRATION=1` 运行外部集成测试。

## Pull Requests

- 一个 Pull Request 只解决一个清晰问题，避免顺手重构无关代码。
- 描述改动原因、影响范围和实际运行的验证命令。
- 行为变更应增加能够复现问题的测试，并先确认测试在修复前失败。
- 保持租户、知识库、RBAC 和文档 ACL 边界；前端隐藏按钮不能替代后端鉴权。
- 更新相关 README、配置样例或 API 说明，但不要写入无法验证的采用或性能声明。

## Commits

使用清晰的 Conventional Commits，例如：

- `feat: add ...`
- `fix: prevent ...`
- `docs: clarify ...`
- `test: cover ...`
- `chore: ignore ...`
- `ci: verify ...`

提交即表示你同意按照仓库的 [Apache License 2.0](LICENSE) 授权你的贡献。
