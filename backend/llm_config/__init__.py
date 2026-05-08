"""LLM endpoints registry + role-to-endpoint mapping.

Replaces Pantheon's flat per-role config (llm_*, prefill_*, ...) with
a saved-endpoints registry + a role mapping that points at endpoints
by name. See docs/superpowers/plans/2026-05-08-llm-endpoints-role-mapping.md.
"""
