"""Change VAULT_MASTER_KEY without losing stored secrets.

Re-encrypts every vault secret under a new key in one transaction, then
writes the new key into .env. Backs up vault.db and .env first. Refuses to
change anything if a secret can't be decrypted with the current key.

Stop Pantheon first (./stop.sh) — a running backend still holds the old key.

Run from the repo root:
    ./.venv/bin/python scripts/rotate_vault_key.py            # generate a random key
    ./.venv/bin/python scripts/rotate_vault_key.py --new-key K
    ./.venv/bin/python scripts/rotate_vault_key.py --no-write-env   # just print it
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    """Mirror start.sh: .env values become environment variables."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# Resolve paths exactly as the running backend does: start.sh sources .env
# and runs uvicorn from backend/.
_load_env(ROOT / ".env")
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from config import get_settings  # noqa: E402
from secrets.vault import SecretsVault  # noqa: E402  (Pantheon's package, not stdlib)


def _write_env(env_path: Path, new_key: str) -> None:
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    line = f"VAULT_MASTER_KEY={new_key}"
    if re.search(r"^VAULT_MASTER_KEY=.*$", text, flags=re.M):
        text = re.sub(r"^VAULT_MASTER_KEY=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    env_path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new-key", help="new VAULT_MASTER_KEY (default: 64 random hex chars)")
    ap.add_argument("--env-file", default=str(ROOT / ".env"), help="env file to update (default: .env)")
    ap.add_argument("--no-write-env", action="store_true", help="don't edit the env file; print the key")
    args = ap.parse_args()

    settings = get_settings()
    new_key = args.new_key or os.urandom(32).hex()
    vault_path = Path(settings.vault_db_path)
    if not vault_path.exists():
        print(f"No vault at {vault_path} — nothing to re-encrypt. Just set VAULT_MASTER_KEY in .env.")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = vault_path.with_name(f"{vault_path.name}.bak-{stamp}")
    shutil.copy2(vault_path, backup)
    print(f"Backed up vault to {backup}")

    vault = SecretsVault(db_path=str(vault_path), master_key=settings.vault_master_key)
    try:
        n = vault.rotate_master_key(new_key)
    except ValueError as e:
        print(f"Refused: {e}")
        return 2
    print(f"Re-encrypted {n} secret(s) under the new key.")

    if args.no_write_env:
        print(f"\nSet this in .env before starting Pantheon:\nVAULT_MASTER_KEY={new_key}")
        return 0
    env_path = Path(args.env_file)
    if env_path.exists():
        shutil.copy2(env_path, env_path.with_name(f"{env_path.name}.bak-{stamp}"))
    _write_env(env_path, new_key)
    print(f"Wrote the new VAULT_MASTER_KEY to {env_path} (previous copy saved alongside).")
    print("Start Pantheon again with ./start.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
