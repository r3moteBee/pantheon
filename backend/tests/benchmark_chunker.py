"""Benchmark and correctness verification script for chunk_text."""
import sys
import time
from pathlib import Path

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from memory.chunker import chunk_text


def generate_large_markdown() -> str:
    """Generate a large Markdown document with nested headings and paragraphs."""
    lines = []
    lines.append("# Document Title\nThis is the document introduction.")
    
    # Generate 50 sections
    for i in range(1, 51):
        lines.append(f"\n## Section Heading {i}\n")
        lines.append(f"This is the introduction paragraph for section {i}. It contains some basic text.")
        
        # 4 subsections per section
        for j in range(1, 5):
            lines.append(f"\n### Subsection Heading {i}.{j}\n")
            # 3 paragraphs per subsection
            for k in range(1, 4):
                lines.append(
                    f"Paragraph {k} under subsection {i}.{j}. "
                    "This paragraph contains a moderate amount of text to simulate realistic "
                    "workspace documentation content. We want to ensure the chunker splits "
                    "paragraphs properly and respects token limits. "
                    "Adding more text to ensure we hit limits: "
                    "the quick brown fox jumps over the lazy dog repeatedly. "
                    "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
                )
    return "\n\n".join(lines)


def verify_chunks_correctness(chunks: list[dict], text: str) -> None:
    """Verify that the chunking output is correct."""
    if not chunks:
        raise ValueError("No chunks returned.")
        
    expected_keys = {"content", "chunk_index", "heading"}
    for idx, chunk in enumerate(chunks):
        # 1. Key validation
        keys = set(chunk.keys())
        if keys != expected_keys:
            raise ValueError(f"Chunk at index {idx} has invalid keys: {keys}")
            
        # 2. Sequential indexing check
        if chunk["chunk_index"] != idx:
            raise ValueError(f"Chunk index mismatch: expected {idx}, got {chunk['chunk_index']}")
            
        # 3. Content existence
        if not isinstance(chunk["content"], str) or not chunk["content"].strip():
            raise ValueError(f"Chunk at index {idx} has empty or non-string content")
            
        # 4. Heading correctness
        heading = chunk["heading"]
        if heading:
            # Heading should be one of the headings in the text
            if heading not in text:
                raise ValueError(f"Chunk at index {idx} has unrecognized heading: '{heading}'")


def main():
    print("Generating large test Markdown document...")
    markdown_text = generate_large_markdown()
    print(f"Generated text length: {len(markdown_text)} characters.")
    
    # Do a dry run to verify correctness
    print("Running dry run verification...")
    dry_run_chunks = chunk_text(markdown_text, chunk_size=300, chunk_overlap=30, strategy="headings")
    print(f"Dry run produced {len(dry_run_chunks)} chunks.")
    verify_chunks_correctness(dry_run_chunks, markdown_text)
    print("Correctness check passed successfully.")
    
    # Benchmark execution
    iterations = 50
    print(f"Benchmarking chunk_text over {iterations} iterations...")
    start_time = time.perf_counter()
    for _ in range(iterations):
        _ = chunk_text(markdown_text, chunk_size=300, chunk_overlap=30, strategy="headings")
    end_time = time.perf_counter()
    
    duration = end_time - start_time
    # Print the exact metric key 'time' to be parsed by the autoresearcher
    print(f"Benchmark completed. time: {duration:.6f}")


if __name__ == "__main__":
    main()
