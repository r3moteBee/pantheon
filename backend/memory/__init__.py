"""Multi-tier memory system with active curation.

Tiers:
  1. Working   — in-context conversation buffer (ephemeral)
  2. Episodic  — SQLite conversation history with semantic search
  3. Semantic  — ChromaDB vector store for knowledge retrieval
  4. Graph     — SQLite associative concept network
  5. Archival  — file-based long-term personality and project notes

Active components:
  - Extraction  — LLM-powered post-conversation knowledge extraction
  - FileIndexer — workspace file ingestion into semantic + graph memory
  - ContextBudget — token-aware recall limiting
"""
