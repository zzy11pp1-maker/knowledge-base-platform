"""文档读取与 Markdown 切分测试。"""

import tempfile
import unittest
from pathlib import Path

from app.core.exceptions import DocumentReadError
from app.ingestion.readers import TextDocumentReader
from app.rag.document_split import build_chunks, split_text


class ReaderAndChunkingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reader = TextDocumentReader(max_bytes=10_000)

    def test_reads_markdown_and_builds_stable_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "guide.md"
            path.write_text("# 总览\n\n正文。\n\n## 安装\n\n安装说明。", encoding="utf-8")
            first = self.reader.read_path(path, tenant_id="t1", kb_id="kb1")
            second = self.reader.read_path(path, tenant_id="t1", kb_id="kb1")
        first_chunks = build_chunks(first, max_chunk_size=50, overlap=5)
        second_chunks = build_chunks(second, max_chunk_size=50, overlap=5)
        self.assertEqual(first.document_id, second.document_id)
        self.assertEqual([item.chunk_id for item in first_chunks], [item.chunk_id for item in second_chunks])
        self.assertEqual(first_chunks[-1].metadata["heading_path"], ["总览", "安装"])

    def test_reads_gb18030_txt(self) -> None:
        document = self.reader.read_bytes(
            filename="说明.txt",
            data="中文内容".encode("gb18030"),
            tenant_id="t1",
            kb_id="kb1",
        )
        self.assertEqual(document.content, "中文内容")
        self.assertEqual(document.metadata["encoding"], "gb18030")

    def test_rejects_unsupported_file(self) -> None:
        with self.assertRaises(DocumentReadError):
            self.reader.read_bytes(filename="bad.pdf", data=b"content", tenant_id="t1", kb_id="kb1")

    def test_overlap_and_max_size(self) -> None:
        text = "abcdefghijklmnopqrstuvwx\n\nyzabcdefghijklmnopqrstuv"
        chunks = split_text(text, max_chunk_size=40, overlap=10)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 40 for chunk in chunks))
        self.assertIn(chunks[0][-10:], chunks[1])

    def test_heading_inside_code_fence_is_not_section(self) -> None:
        text = "# 标题\n\n```python\n# 这是注释\nprint('ok')\n```\n\n正文"
        chunks = split_text(text, max_chunk_size=200)
        self.assertEqual(len(chunks), 1)
        self.assertIn("# 这是注释", chunks[0])


if __name__ == "__main__":
    unittest.main()
