from app.rag.document_split import read_markdown, split_text


file_path = "data/test.md"

text = read_markdown(file_path)

chunks = split_text(text, max_chunk_size=100)


print("原文长度：", len(text))
print("切片数量：", len(chunks))

for index, chunk in enumerate(chunks, start=1):
    print(f"\n--- Chunk {index} ---")
    print(chunk)