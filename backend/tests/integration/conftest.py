"""Shared test setup."""
import pytest


@pytest.fixture(autouse=True, scope="session")
def _fast_vault_kdf():
    """Test vaults don't need 600k PBKDF2 rounds (the count is stored per
    vault, so this only affects vaults created during tests)."""
    from secrets.vault import SecretsVault
    original = SecretsVault._V2_ITERATIONS
    SecretsVault._V2_ITERATIONS = 1_000
    yield
    SecretsVault._V2_ITERATIONS = original
