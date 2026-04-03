"""First-run setup — migrate secrets from .env into the encrypted vault.

Usage:
    python -m secrets.setup              # Interactive — prompts for each secret
    python -m secrets.setup --migrate    # Auto-migrate from current .env values
    python -m secrets.setup --check      # Check which secrets are configured

This script writes sensitive values into the vault so they can be removed
from .env. After running, .env should only contain non-sensitive configuration.
"""
from __future__ import annotations
import argparse
import getpass
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Secrets that should live in the vault, not .env
# Format: (vault_key, env_var_name, description, is_required)
MANAGED_SECRETS = [
    ("llm_api_key", "LLM_API_KEY", "LLM provider API key", True),
    ("secret_key", "SECRET_KEY", "Application secret key (JWT signing)", True),
    ("embedding_api_key", "EMBEDDING_API_KEY", "Embedding provider API key (if separate)", False),
    ("prefill_api_key", "PREFILL_API_KEY", "Prefill/summarization provider API key (if separate)", False),
    ("reranker_api_key", "RERANKER_API_KEY", "Reranker provider API key (if separate)", False),
    ("search_api_key", "SEARCH_API_KEY", "Search backend API key", False),
    ("telegram_bot_token", "TELEGRAM_BOT_TOKEN", "Telegram bot token", False),
]


def _check_secrets():
    """Report which secrets are configured in the vault vs .env."""
    from config import get_settings
    from secrets.vault import get_vault

    settings = get_settings()
    vault = get_vault()

    print("\n  Secret Status")
    print("  " + "=" * 60)

    for vault_key, env_var, desc, required in MANAGED_SECRETS:
        in_vault = vault.has_secret(vault_key)
        env_val = getattr(settings, vault_key.replace("-", "_"), "") or ""
        # Some env vars map differently
        if vault_key == "secret_key":
            env_val = settings.secret_key
        in_env = bool(env_val) and env_val not in (
            "dev-secret-key-change-in-production",
            "",
        )

        if in_vault:
            status = "✅ vault"
        elif in_env:
            status = "⚠️  .env (should migrate)"
        elif required:
            status = "❌ missing"
        else:
            status = "—  not set (optional)"

        req_tag = " *" if required else ""
        print(f"  {vault_key:<30} {status:<30} {desc}{req_tag}")

    # Check vault master key source
    import os
    from secrets.vault import _SYSTEM_KEY_FILE
    env_key = os.environ.get("VAULT_MASTER_KEY", "").strip()
    if env_key and env_key != "dev-key-change-in-production-32x":
        key_src = "✅ environment variable"
    elif _SYSTEM_KEY_FILE.exists():
        key_src = "✅ /etc/pantheon/vault.key"
    else:
        key_src = "⚠️  default dev key (insecure)"

    print(f"\n  {'vault_master_key':<30} {key_src}")
    print(f"\n  * = required\n")


def _migrate_from_env():
    """Auto-migrate secrets from .env/config into the vault."""
    from config import get_settings
    from secrets.vault import get_vault

    settings = get_settings()
    vault = get_vault()
    migrated = 0

    env_field_map = {
        "llm_api_key": settings.llm_api_key,
        "secret_key": settings.secret_key,
        "embedding_api_key": settings.embedding_api_key,
        "prefill_api_key": settings.prefill_api_key,
        "reranker_api_key": settings.reranker_api_key,
        "search_api_key": settings.search_api_key,
        "telegram_bot_token": settings.telegram_bot_token,
    }

    skip_defaults = {
        "secret_key": "dev-secret-key-change-in-production",
    }

    for vault_key, env_var, desc, required in MANAGED_SECRETS:
        # Skip if already in vault
        if vault.has_secret(vault_key):
            logger.info("  %-30s already in vault — skipped", vault_key)
            continue

        value = env_field_map.get(vault_key, "")
        default = skip_defaults.get(vault_key, "")
        if not value or value == default:
            if required:
                logger.warning("  %-30s not set in .env — skipped (required!)", vault_key)
            continue

        vault.set_secret(vault_key, value)
        migrated += 1
        logger.info("  %-30s migrated to vault ✅", vault_key)

    if migrated:
        logger.info("\n  %d secret(s) migrated. You can now remove them from .env.", migrated)
    else:
        logger.info("\n  No secrets to migrate.")


def _interactive_setup():
    """Walk through each secret interactively."""
    from secrets.vault import get_vault

    vault = get_vault()

    print("\n  Pantheon — Vault Setup")
    print("  " + "=" * 40)
    print("  Enter values for each secret. Press Enter to skip optional ones.\n")

    for vault_key, env_var, desc, required in MANAGED_SECRETS:
        existing = vault.has_secret(vault_key)
        req_tag = " (required)" if required else " (optional)"
        existing_tag = " [already set — Enter to keep]" if existing else ""

        prompt = f"  {desc}{req_tag}{existing_tag}: "
        value = getpass.getpass(prompt) if "key" in vault_key or "token" in vault_key or "secret" in vault_key else input(prompt)
        value = value.strip()

        if not value:
            if existing:
                logger.info("    → kept existing value")
            elif required:
                logger.warning("    → skipped (will need to be set before use)")
            continue

        vault.set_secret(vault_key, value)
        logger.info("    → saved to vault ✅")

    print("\n  Setup complete. Secrets are encrypted in the vault.\n")


def main():
    parser = argparse.ArgumentParser(description="Pantheon vault setup")
    parser.add_argument("--migrate", action="store_true", help="Auto-migrate secrets from .env into vault")
    parser.add_argument("--check", action="store_true", help="Check secret configuration status")
    args = parser.parse_args()

    if args.check:
        _check_secrets()
    elif args.migrate:
        logger.info("\n  Migrating secrets from .env to vault...\n")
        _migrate_from_env()
    else:
        _interactive_setup()


if __name__ == "__main__":
    main()
