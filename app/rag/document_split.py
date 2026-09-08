"""Markdown 标题优先的语义切分。

设计目标：保留标题路径、代码围栏和自然段；只有单个语义单元本身过长时才退化为字符窗口。
旧的 ``read_markdown``/``split_text`` 函数继续保留，避免破坏现有调用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.models.domain import Chunk, Document


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;\.])")


@dataclass(slots=True)
class MarkdownSection:
    """一个标题路径下的正文。"""

    heading_path: list[str] = field(default_factory=list)
    heading_levels: list[int] = field(default_factory=list)
    body_lines: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.heading_path[-1] if self.heading_path else ""

    def render(self) -> str:
        headings = [f"{'#' * level} {title}" for level, title in zip(self.heading_levels, self.heading_path)]
        body = "\n".join(self.body_lines).strip()
        return "\n".join([*headings, body]).strip()


def read_markdown(file_path: str) -> str:
    """兼容旧入口：按 UTF-8 读取 Markdown。"""

    return Path(file_path).read_text(encoding="utf-8")


def _parse_sections(text: str) -> list[MarkdownSection]:
    sections: list[MarkdownSection] = []
    heading_path: list[str] = []
    heading_levels: list[int] = []
    current = MarkdownSection()
    fence_marker: str | None = None

    def flush() -> None:
        nonlocal current
        if current.render():
            sections.append(current)
        current = MarkdownSection(heading_path.copy(), heading_levels.copy(), [])

    for line in text.splitlines():
        fence_match = FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            current.body_lines.append(line.rstrip())
            continue

        heading_match = HEADING_RE.match(line) if fence_marker is None else None
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip().rstrip("#").strip()
            while heading_levels and heading_levels[-1] >= level:
                heading_levels.pop()
                heading_path.pop()
            heading_levels.append(level)
            heading_path.append(title)
            current = MarkdownSection(heading_path.copy(), heading_levels.copy(), [])
        else:
            current.body_lines.append(line.rstrip())
    flush()
    return sections


def _hard_windows(text: str, size: int) -> list[str]:
    """只作为最后兜底的字符窗口切分。"""

    return [text[index : index + size] for index in range(0, len(text), size) if text[index : index + size]]


def _semantic_units(text: str, max_size: int) -> list[str]:
    """优先按段落、句子、行拆分，单个超长行才硬切。"""

    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_size:
            units.append(paragraph)
            continue
        sentences = [part.strip() for part in SENTENCE_BOUNDARY_RE.split(paragraph) if part.strip()]
        if len(sentences) == 1:
            sentences = [line.strip() for line in paragraph.splitlines() if line.strip()]
        for sentence in sentences:
            if len(sentence) <= max_size:
                units.append(sentence)
            else:
                units.extend(_hard_windows(sentence, max_size))
    return units


def _overlap_tail(text: str, overlap: int) -> str:
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    # 尽量从自然边界开始，避免重叠片段从词语中间截断。
    matches = list(re.finditer(r"[\n。！？!?；;]\s*", tail))
    if matches:
        candidate = tail[matches[-1].end() :].strip()
        if candidate:
            return candidate
    return tail.strip()


def _split_semantically(text: str, max_size: int, overlap: int) -> list[str]:
    units = _semantic_units(text, max_size)
    chunks: list[str] = []
    current = ""
    for unit in units:
        separator = "\n\n" if current else ""
        if len(current) + len(separator) + len(unit) <= max_size:
            current = f"{current}{separator}{unit}"
            continue
        if current:
            chunks.append(current.strip())
            tail = _overlap_tail(current, overlap)
            current = f"{tail}\n\n{unit}" if tail and len(tail) + 2 + len(unit) <= max_size else unit
        else:
            current = unit
    if current:
        chunks.append(current.strip())
    return chunks


def split_text(text: str, max_chunk_size: int = 100, overlap: int = 0) -> list[str]:
    """兼容且增强的切分入口，返回纯文本 Chunk 列表。"""

    if max_chunk_size <= 0:
        raise ValueError("max_chunk_size 必须大于 0")
    if overlap < 0 or overlap >= max_chunk_size:
        raise ValueError("overlap 必须大于等于 0 且小于 max_chunk_size")
    chunks: list[str] = []
    for section in _parse_sections(text):
        chunks.extend(_split_semantically(section.render(), max_chunk_size, overlap))
    return chunks


def build_chunks(document: Document, max_chunk_size: int, overlap: int) -> list[Chunk]:
    """把文档切成带稳定 ID、标题路径和版本信息的 Chunk。"""

    chunks: list[Chunk] = []
    for section in _parse_sections(document.content):
        heading_path = section.heading_path or [document.title]
        for content in _split_semantically(section.render(), max_chunk_size, overlap):
            index = len(chunks)
            content_hash = sha256(content.encode("utf-8")).hexdigest()
            chunk_id = str(uuid5(NAMESPACE_URL, f"{document.document_id}:{document.version}:{index}:{content_hash}"))
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    tenant_id=document.tenant_id,
                    kb_id=document.kb_id,
                    document_id=document.document_id,
                    chunk_index=index,
                    content=content,
                    title=section.title or document.title,
                    source=document.source,
                    document_version=document.version,
                    content_hash=content_hash,
                    metadata={
                        **document.metadata,
                        "document_title": document.title,
                        "heading_path": heading_path,
                    },
                    created_at=document.created_at,
                )
            )
    return chunks
