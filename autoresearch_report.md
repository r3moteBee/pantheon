# Autoresearch Optimization Report

## Summary
- **Target File**: `backend/memory/chunker.py`
- **Optimization Metric**: `time` (Direction: `min`)
- **Initial Baseline Metric**: `0.037534`
- **Final Best Metric**: `0.037534`
- **Total Iterations**: 5
- **Successful Mutations**: 0

## Evolutionary Run Log

### Iteration 1 - `REGRESSION`
- **Metric**: 0.0952
- **Result**: Regression. Metric: 0.095167 (Best: 0.037534).
- **Mutation Diff Snippet**:
```diff
--- before

+++ after

@@ -10,4 +10,8 @@

 DEFAULT_CHUNK_OVERLAP = 50    # tokens (~200 chars)
 CHARS_PER_TOKEN = 4           # rough estimate
+
+# Pre-compiled regex patterns
+HEADING_PATTERN = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
+PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
 
 
@@ -49,18 +53,17 @@

     idx = 0
     start = 0
-    while start < len(text):
... (120 lines truncated)
```

### Iteration 2 - `REGRESSION`
- **Metric**: 0.1115
- **Result**: Regression. Metric: 0.111533 (Best: 0.037534).
- **Mutation Diff Snippet**:
```diff
--- before

+++ after

@@ -4,4 +4,8 @@

 import re
 from typing import Any
+
+# Pre-compiled regex patterns
+HEADING_PATTERN = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
+PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
 
 # ── Chunking defaults ────────────────────────────────────────────────────────
@@ -23,5 +27,5 @@

     Returns list of dicts: {content, chunk_index, heading}
     """
-    if not text.strip():
... (130 lines truncated)
```

### Iteration 3 - `REGRESSION`
- **Metric**: 0.1031
- **Result**: Regression. Metric: 0.103063 (Best: 0.037534).
- **Mutation Diff Snippet**:
```diff
--- before

+++ after

@@ -4,4 +4,8 @@

 import re
 from typing import Any
+
+# Pre-compiled regex patterns
+HEADING_PATTERN = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
+PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
 
 # ── Chunking defaults ────────────────────────────────────────────────────────
@@ -23,5 +27,5 @@

     Returns list of dicts: {content, chunk_index, heading}
     """
-    if not text.strip():
... (140 lines truncated)
```

### Iteration 4 - `REGRESSION`
- **Metric**: 0.1053
- **Result**: Regression. Metric: 0.105266 (Best: 0.037534).
- **Mutation Diff Snippet**:
```diff
--- before

+++ after

@@ -4,4 +4,8 @@

 import re
 from typing import Any
+
+# Pre-compiled regex patterns
+HEADING_PATTERN = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
+PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
 
 # ── Chunking defaults ────────────────────────────────────────────────────────
@@ -33,13 +37,11 @@

 
     if strategy == "headings":
-        chunks = _chunk_by_headings(text, max_chars, overlap_chars)
... (167 lines truncated)
```

### Iteration 5 - `REGRESSION`
- **Metric**: 0.1010
- **Result**: Regression. Metric: 0.100956 (Best: 0.037534).
- **Mutation Diff Snippet**:
```diff
--- before

+++ after

@@ -10,4 +10,8 @@

 DEFAULT_CHUNK_OVERLAP = 50    # tokens (~200 chars)
 CHARS_PER_TOKEN = 4           # rough estimate
+
+# Pre-compiled regex patterns
+HEADING_PATTERN = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
+PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
 
 
@@ -23,5 +27,5 @@

     Returns list of dicts: {content, chunk_index, heading}
     """
-    if not text.strip():
... (133 lines truncated)
```


## Final Optimized Code
Here is the final version of the code that achieved the best performance:

```py
"""Text chunking strategies for embedding and retrieval."""
from __future__ import annotations

import re
from typing import Any

# ── Chunking defaults ────────────────────────────────────────────────────────

DEFAULT_CHUNK_SIZE = 500      # tokens (~2000 chars)
DEFAULT_CHUNK_OVERLAP = 50    # tokens (~200 chars)
CHARS_PER_TOKEN = 4           # rough estimate


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    respect_headings: bool | None = None,
    strategy: str = "headings",
) -> list[dict[str, Any]]:
    """Split text into overlapping chunks for embedding.

    Returns list of dicts: {content, chunk_index, heading}
    """
    if not text.strip():
        return []

    if respect_headings is not None:
        strategy = "headings" if respect_headings else "paragraphs"

    max_chars = chunk_size * CHARS_PER_TOKEN
    overlap_chars = chunk_overlap * CHARS_PER_TOKEN

    if strategy == "headings":
        chunks = _chunk_by_headings(text, max_chars, overlap_chars)
    elif strategy == "paragraphs":
        chunks = _chunk_by_paragraphs(text, max_chars, overlap_chars)
    elif strategy == "fixed":
        chunks = _chunk_fixed(text, max_chars, overlap_chars)
    else:
        chunks = _chunk_by_headings(text, max_chars, overlap_chars)

    return chunks


def _chunk_fixed(text: str, max_chars: int, overlap_chars: int) -> list[dict]:
    """Split text into fixed-size chunks strictly by characters with overlap."""
    chunks = []
    idx = 0
    start = 0
    while start < len(text):
        end = start + max_chars
        content = text[start:end]
        chunks.append({
            "content": content,
            "chunk_index": idx,
            "heading": "",
        })
        idx += 1
        step = max_chars - overlap_chars
        if step <= 0:
            step = max_chars // 2 or 1
        start += step
        if end >= len(text):
            break
    return chunks


def _chunk_by_headings(text: str, max_chars: int, overlap_chars: int) -> list[dict]:
    """Split on Markdown headings first, then by paragraphs within sections."""
    # Split on heading lines (## Heading)
    heading_pattern = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    sections: list[tuple[str, str]] = []  # (heading, content)

    last_end = 0
    current_heading = ""
    for match in heading_pattern.finditer(text):
        if last_end > 0 or match.start() > 0:
            section_text = text[last_end:match.start()].strip()
            if section_text:
                sections.append((current_heading, section_text))
        current_heading = match.group(2).strip()
        last_end = match.end()

    # Remaining text after last heading
    remaining = text[last_end:].strip()
    if remaining:
        sections.append((current_heading, remaining))

    # If no headings found, fall back to paragraph chunking
    if not sections:
        return _chunk_by_paragraphs(text, max_chars, overlap_chars)

    # Now chunk each section
    chunks = []
    idx = 0
    for heading, section_text in sections:
        if len(section_text) <= max_chars:
            chunks.append({
                "content": section_text,
                "chunk_index": idx,
                "heading": heading,
            })
            idx += 1
        else:
            # Sub-chunk by paragraphs
            sub_chunks = _chunk_by_paragraphs(section_text, max_chars, overlap_chars)
            for sc in sub_chunks:
                sc["heading"] = heading
                sc["chunk_index"] = idx
                chunks.append(sc)
                idx += 1

    return chunks


def _chunk_by_paragraphs(text: str, max_chars: int, overlap_chars: int) -> list[dict]:
    """Split text into chunks by paragraph boundaries with overlap."""
    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    current = ""
    idx = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append({
                "content": current.strip(),
                "chunk_index": idx,
                "heading": "",
            })
            idx += 1
            # Keep overlap from end of previous chunk
            if overlap_chars > 0 and len(current) > overlap_chars:
                current = current[-overlap_chars:] + "\n\n" + para
            else:
                current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append({
            "content": current.strip(),
            "chunk_index": idx,
            "heading": "",
        })

    return chunks

```
