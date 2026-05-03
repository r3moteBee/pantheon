"""Unified jobs system.

Single source of truth for every agent-initiated background work item.
Handlers register at import time. The worker (jobs.worker) polls the
jobs table for queued rows and dispatches them.
"""
from jobs.store import JobStore, get_store, JobStatus, JobNotFound
from jobs.context import JobContext

__all__ = ["JobStore", "get_store", "JobContext", "JobStatus", "JobNotFound"]
