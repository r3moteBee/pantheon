"""Multi-tier memory system with active curation.

Tiers (see memory/README.md):
  Episodic  — SQLite conversation history with semantic search
  Semantic  — ChromaDB vector store for knowledge retrieval
  Graph     — SQLite associative concept network
  Archival  — file-based project notes
Working memory is AgentCore.working_memory (in-process, not persisted).

Active components:
  - Extraction  — LLM-powered post-conversation knowledge extraction
  - FileIndexer — workspace file ingestion into semantic + graph memory
  - ContextBudget — token-aware recall limiting
"""
