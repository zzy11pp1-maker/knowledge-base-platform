"""统一文档读取器：验证文件后交给 Parser Adapter，输出同一种 Document。"""

from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.core.exceptions import DocumentReadError
from app.models.domain import Document
from app.ingestion.parsers import ParserRegistry


SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf", ".docx"}


class TextDocumentReader:
    """读取 Markdown 和纯文本，生成稳定文档标识。"""

    def __init__(self, max_bytes: int, registry: ParserRegistry | None = None) -> None:
        self.max_bytes = max_bytes
        self.registry = registry or ParserRegistry()

    def read_path(
        self,
        file_path: str | Path,
        *,
        tenant_id: str,
        kb_id: str,
        document_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Document:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise DocumentReadError(f"文档不存在或不是文件：{path}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DocumentReadError(f"读取文档失败：{path.name}") from exc
        return self.read_bytes(
            filename=path.name,
            data=data,
            tenant_id=tenant_id,
            kb_id=kb_id,
            document_id=document_id,
            metadata=metadata,
        )

    def read_bytes(
        self,
        *,
        filename: str,
        data: bytes,
        tenant_id: str,
        kb_id: str,
        document_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Document:
        if not tenant_id.strip() or not kb_id.strip():
            raise DocumentReadError("tenant_id 和 kb_id 不能为空")
        # 同时处理 Linux/Windows 路径分隔符，拒绝客户端文件名中的目录部分。
        safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not safe_name:
            raise DocumentReadError("文件名不能为空")
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise DocumentReadError("仅支持 PDF、DOCX、Markdown 和 TXT 文件")
        if not data:
            raise DocumentReadError("文档内容为空")
        if len(data) > self.max_bytes:
            raise DocumentReadError(f"文档超过大小限制：{self.max_bytes} bytes")

        text, parser_id = self.registry.parse(safe_name, data)
        text = self._clean_text(text)
        if not text:
            raise DocumentReadError("文档清洗后没有有效文本")

        content_hash = sha256(text.encode("utf-8")).hexdigest()
        stable_source = f"{tenant_id}/{kb_id}/{safe_name}"
        resolved_id = document_id or str(uuid5(NAMESPACE_URL, stable_source))
        parser_metadata = {"parser": parser_id}
        # 第一、二阶段调用方仍会读取 encoding；文本解析器保留该兼容字段。
        if parser_id.startswith("text:"):
            parser_metadata["encoding"] = parser_id.split(":", 1)[1]
        return Document(
            document_id=resolved_id,
            tenant_id=tenant_id,
            kb_id=kb_id,
            title=Path(safe_name).stem,
            source=safe_name,
            content=text,
            content_hash=content_hash,
            version=content_hash[:16],
            metadata={
                **(metadata or {}),
                "filename": safe_name,
                "suffix": suffix,
                **parser_metadata,
                "size_bytes": len(data),
            },
        )

    @staticmethod
    def _clean_text(text: str) -> str:
        # 统一换行并去除 NUL；保留段落空行和 Markdown 结构。
        return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
