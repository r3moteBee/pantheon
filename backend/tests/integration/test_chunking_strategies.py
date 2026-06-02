import pytest
from memory.file_indexer import chunk_text

def test_fixed_chunking_strategy():
    text = "abcdefghijklmnopqrstuvwxyz"
    # max_chars = chunk_size * CHARS_PER_TOKEN = 2 * 4 = 8
    # overlap_chars = chunk_overlap * CHARS_PER_TOKEN = 1 * 4 = 4
    chunks = chunk_text(text, chunk_size=2, chunk_overlap=1, strategy="fixed")
    assert len(chunks) > 0
    # verify chunks are sequential slices
    for i, c in enumerate(chunks):
        assert c["chunk_index"] == i
        assert len(c["content"]) <= 8

def test_paragraphs_chunking_strategy():
    text = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3."
    chunks = chunk_text(text, chunk_size=5, chunk_overlap=0, strategy="paragraphs")
    # Should split on paragraph boundaries
    assert len(chunks) == 3
    assert chunks[0]["content"] == "Paragraph 1."
    assert chunks[1]["content"] == "Paragraph 2."
    assert chunks[2]["content"] == "Paragraph 3."

def test_headings_chunking_strategy():
    text = "# Title\nSection content.\n## Subtitle\nMore content."
    chunks = chunk_text(text, chunk_size=50, chunk_overlap=5, strategy="headings")
    assert len(chunks) >= 2
    # First chunk should have heading Title
    assert chunks[0]["heading"] == "Title"
