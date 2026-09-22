# Knowledge Base Platform

[简体中文](README.md)

A multi-tenant knowledge management application with a FastAPI backend and a React administration console. It supports document ingestion, hybrid retrieval, access-controlled RAG chat, citations, FAQ mining, knowledge-gap analysis, and an operations dashboard. The screenshots and dated validation records in this repository describe local demonstrations, not production adoption or performance benchmarks.

## Features

- Import PDF, DOCX, Markdown, and TXT documents, individually or in batches, with persisted ingestion jobs and document reindex/delete operations.
- Generate dense and sparse vectors with an external BGE-M3 service; search Qdrant, fuse results with RRF, and rerank them with an external Reranker service.
- Use JWT authentication, RBAC, knowledge-base membership, and document ACLs for global, department, role, and user access. The backend rechecks SQL permissions before content reaches the Reranker, LLM, or citation endpoint.
- Offer synchronous and SSE-streamed chat, conversation history, and protected citations.
- Mine FAQ candidates for human review, aggregate knowledge gaps, and display metrics based on recorded application events.

## Stack and data flow

Python 3.11+, FastAPI, Pydantic, SQLAlchemy, and Qdrant power the backend. The frontend uses React 19, TypeScript, Vite, Ant Design, and ECharts.

Documents are parsed and chunked, embedded by BGE-M3, and indexed in Qdrant. For a query, the backend applies tenant and knowledge-base context, retrieves candidates, rechecks document ACLs in SQL, then sends authorized content to the Reranker and optional OpenAI-compatible LLM. Citation access is checked again when the source is read. See the [ingestion/search](docs/architecture_ingestion_search.md), [security](docs/architecture_security.md), and [operations](docs/architecture_operations.md) design records.

## Quick start

You need Python 3.11+ and Node.js `^20.19.0` or `>=22.12.0`. Document ingestion requires a BGE-M3 HTTP service; retrieval also requires a Reranker HTTP service. Chat generation requires an OpenAI-compatible LLM. Qdrant can run as a server or use the client's local persistent mode for development.

```bash
git clone https://github.com/zzy11pp1-maker/knowledge-base-platform.git
cd knowledge-base-platform
python -m venv .venv
```

Activate the environment for your shell:

```bash
# Linux / macOS
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

Then install dependencies and create a local configuration file:

```bash
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env` instead of `cp` if preferred. In `.env`, replace `JWT_SECRET` with a random secret of at least 32 characters and configure your BGE-M3 and Reranker URLs. For local Qdrant, clear `QDRANT_URL` and set `QDRANT_LOCATION=./.local/qdrant`. Configure `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` to enable generated answers. Keep `.env` and real credentials out of Git. See [`.env.example`](.env.example) for all settings.

Create the first administrator; the script reads the password without echoing it:

```bash
python bootstrap_admin.py --tenant your-tenant --username admin
```

Start the API:

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8080
```

Swagger UI is at `http://127.0.0.1:8080/docs`, OpenAPI JSON at `/openapi.json`, and the basic health endpoint at `/health`. After signing in, administrators can check `/health/dependencies`.

From a second terminal, start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Vite serves the console on `http://127.0.0.1:5173` and proxies `/api` to the backend on port 8080. Set `VITE_API_BASE` only if you need another API base. Run `npm run build` from `frontend` for a production build.

## Tests

From the repository root, run the backend unit/component suite without external model services:

```bash
python -m unittest discover -s tests/unit -v
```

External integration tests require your own isolated services and credentials; they are not part of public CI. GitHub Actions runs this backend suite plus `npm ci` and `npm run build` for the frontend.

## API and limitations

The generated Swagger/OpenAPI schema is the authoritative API reference. It covers authentication, organizations, knowledge bases, documents, search/chat, conversations/citations, FAQ, gaps, dashboard metrics, and model configuration.

This repository does not include model weights, hosted BGE-M3/Reranker/LLM services, OCR for image-only PDFs, Docker Compose, or production TLS/proxy deployment. The default SQLite database and local Qdrant mode are for development; limited SQLite compatibility migrations exist, but formal versioned migrations and a separate ingestion worker are not yet provided. The administration UI is primarily in Chinese.

Maintenance directions include database migrations, reproducible containerized development, more resilient background ingestion, frontend code splitting/accessibility/internationalization, and reproducible retrieval evaluations. These are plans, not delivery commitments.

## Community

See [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), and the [Apache License 2.0](LICENSE). For local demonstration screenshots, see [Screenshots](docs/截图/README.md). Do not disclose vulnerabilities or credentials in public issues.
