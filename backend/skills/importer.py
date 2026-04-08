"""Skill importer — fetch, normalize, scan, and install skills from external sources.

Supports multiple hub formats via pluggable adapters:
  - SKILL.md (SkillsMP / SkillsLLM bundles)
  - GitHub repos (auto-detect format)
  - Local upload (.tar.gz / .zip)

NOTE: MCP server registries (e.g. Smithery) are intentionally NOT skill
hubs. MCP servers are long-running processes with their own transport
and lifecycle; they belong in the MCP connector system, not in Skills.
See docs/mcp-registry-protocol.md.

Every imported skill goes through the security scanner before installation.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
import tarfile
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from config import get_settings
from security_log import sec_log
from skills.models import SkillManifest

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Safe archive extraction ────────────────────────────────────────────────

def _safe_extract_zip(zip_path: Path, dest: Path) -> None:
    """Extract a zip file into dest, rejecting path-traversal ("zip-slip")
    and absolute-path members. Refuses symlinks entirely."""
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            name = member.filename
            # Reject absolute paths and drive letters
            if name.startswith("/") or name.startswith("\\") or (len(name) > 1 and name[1] == ":"):
                raise ValueError(f"Zip member has absolute path: {name}")
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest_resolved) + "/") and target != dest_resolved:
                raise ValueError(f"Zip path traversal attempt: {name}")
            # Reject symlinks (external attribute 0xA1ED0000 on Unix)
            mode = (member.external_attr >> 16) & 0xFFFF
            if mode and (mode & 0xF000) == 0xA000:
                raise ValueError(f"Zip member is a symlink (refused): {name}")
        zf.extractall(dest)


def _safe_extract_tar(tar_path: Path, dest: Path, mode: str = "r:*") -> None:
    """Extract a tar file into dest, rejecting path traversal and symlinks."""
    dest_resolved = dest.resolve()
    with tarfile.open(tar_path, mode) as tf:
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                raise ValueError(f"Tar member is a link (refused): {member.name}")
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest_resolved) + "/") and target != dest_resolved:
                raise ValueError(f"Tar path traversal attempt: {member.name}")
        tf.extractall(dest)


# ── Enums and Models ────────────────────────────────────────────────────────

class SkillFormat(str, Enum):
    pantheon = "pantheon"       # Native skill.json + instructions.md
    skill_md = "skill_md"      # SKILL.md frontmatter format
    github = "github"          # GitHub repo (auto-detect)
    unknown = "unknown"


class HubResult(BaseModel):
    """Search result from a hub."""
    name: str
    description: str = ""
    author: str = ""
    version: str = ""
    url: str = ""
    hub: str = ""
    format: SkillFormat = SkillFormat.unknown
    tags: list[str] = Field(default_factory=list)
    download_url: str = ""


class ImportResult(BaseModel):
    """Result of an import operation."""
    success: bool
    skill_name: str = ""
    message: str = ""
    scan_passed: bool | None = None
    scan_risk: float | None = None
    scan_findings: int = 0
    quarantined: bool = False
    installed_path: str = ""
    source: str = ""
    format_detected: SkillFormat = SkillFormat.unknown


# ── Hub Adapter Interface ───────────────────────────────────────────────────

class HubAdapter(ABC):
    """Base class for hub-specific import adapters."""

    @property
    @abstractmethod
    def hub_name(self) -> str:
        """Human-readable name for this hub."""
        ...

    @abstractmethod
    async def search(self, query: str) -> list[HubResult]:
        """Search the hub for skills matching a query."""
        ...

    @abstractmethod
    async def fetch(self, identifier: str) -> Path:
        """Fetch a skill bundle from the hub.

        Returns the path to a temporary directory containing the skill files.
        Caller is responsible for cleanup.
        """
        ...

    def detect_format(self, path: Path) -> SkillFormat:
        """Detect the skill format in a directory."""
        if (path / "skill.json").exists():
            return SkillFormat.pantheon
        # Check for SKILL.md (SkillsMP format)
        for f in path.iterdir():
            if f.name.upper() == "SKILL.MD" or f.suffix == ".md":
                content = f.read_text(encoding="utf-8", errors="replace")
                if content.startswith("---"):
                    return SkillFormat.skill_md
        return SkillFormat.unknown

    @abstractmethod
    def normalize(self, path: Path) -> SkillManifest:
        """Convert hub-specific format to a Pantheon SkillManifest.

        Also writes a normalized skill.json and instructions.md to the path.
        """
        ...


# ── SKILL.md Format Adapter ────────────────────────────────────────────────

class SkillMdAdapter(HubAdapter):
    """Adapter for SKILL.md / SkillsMP / SkillsLLM format.

    SKILL.md format:
    ---
    name: skill-name
    description: What the skill does
    triggers:
      - trigger phrase 1
      - trigger phrase 2
    parameters:
      - name: param1
        type: string
        required: true
        description: What it is
    tags:
      - tag1
    capabilities_required:
      - network
    ---

    # Instructions
    (Markdown instructions below frontmatter)
    """

    @property
    def hub_name(self) -> str:
        return "SKILL.md"

    async def search(self, query: str) -> list[HubResult]:
        # SKILL.md is a format, not a hub — no search capability
        return []

    async def fetch(self, identifier: str) -> Path:
        raise NotImplementedError("SKILL.md adapter is used for local format conversion only")

    def normalize(self, path: Path) -> SkillManifest:
        """Parse SKILL.md frontmatter and body into skill.json + instructions.md."""
        skill_md = self._find_skill_md(path)
        if not skill_md:
            raise ValueError("No SKILL.md file found in directory")

        content = skill_md.read_text(encoding="utf-8")
        frontmatter, instructions = self._parse_frontmatter(content)

        # Build manifest from frontmatter
        manifest_data = {
            "name": frontmatter.get("name", path.name),
            "description": frontmatter.get("description", ""),
            "version": frontmatter.get("version", ""),
            "author": frontmatter.get("author", ""),
            "license": frontmatter.get("license", ""),
            "triggers": frontmatter.get("triggers", []),
            "parameters": frontmatter.get("parameters", []),
            "capabilities_required": frontmatter.get("capabilities_required", []),
            "dependencies": frontmatter.get("dependencies", {}),
            "tags": frontmatter.get("tags", []),
            "source_hub": "skill_md",
        }

        manifest = SkillManifest(**manifest_data)

        # Write normalized files
        (path / "skill.json").write_text(
            json.dumps(manifest_data, indent=2),
            encoding="utf-8",
        )
        (path / "instructions.md").write_text(instructions, encoding="utf-8")

        logger.info("Normalized SKILL.md '%s' → skill.json + instructions.md", manifest.name)
        return manifest

    def _find_skill_md(self, path: Path) -> Path | None:
        """Find the SKILL.md file in a directory (case-insensitive)."""
        for f in path.iterdir():
            if f.name.upper() == "SKILL.MD":
                return f
        # Fall back to any .md file that has frontmatter
        for f in path.iterdir():
            if f.suffix.lower() == ".md" and f.name.lower() != "readme.md":
                content = f.read_text(encoding="utf-8", errors="replace")
                if content.startswith("---"):
                    return f
        return None

    def _parse_frontmatter(self, content: str) -> tuple[dict, str]:
        """Parse YAML frontmatter and body from markdown."""
        if not content.startswith("---"):
            return {}, content

        # Find the closing ---
        end = content.find("---", 3)
        if end == -1:
            return {}, content

        frontmatter_str = content[3:end].strip()
        body = content[end + 3:].strip()

        # Parse YAML frontmatter
        try:
            import yaml
            frontmatter = yaml.safe_load(frontmatter_str) or {}
        except Exception:
            # Fall back to simple key-value parsing
            frontmatter = self._simple_parse(frontmatter_str)

        return frontmatter, body

    def _simple_parse(self, text: str) -> dict:
        """Simple fallback frontmatter parser (no YAML dependency)."""
        result: dict[str, Any] = {}
        current_key = None
        current_list: list[str] | None = None

        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue

            # List item
            if stripped.startswith("- ") and current_key:
                if current_list is None:
                    current_list = []
                    result[current_key] = current_list
                current_list.append(stripped[2:].strip())
                continue

            # Key-value
            if ":" in stripped:
                current_list = None
                key, _, val = stripped.partition(":")
                current_key = key.strip()
                val = val.strip()
                if val:
                    result[current_key] = val
                # If val is empty, might be start of a list — wait for next line

        return result


# ── GitHub Adapter ──────────────────────────────────────────────────────────

class GitHubAdapter(HubAdapter):
    """Adapter for importing skills from GitHub repositories."""

    @property
    def hub_name(self) -> str:
        return "GitHub"

    async def search(self, query: str) -> list[HubResult]:
        """Search GitHub for skill repositories."""
        import httpx

        results: list[HubResult] = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.github.com/search/repositories",
                    params={
                        "q": f"{query} topic:agent-skill OR topic:llm-skill",
                        "per_page": 15,
                        "sort": "stars",
                    },
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if resp.status_code != 200:
                    logger.warning("GitHub search returned %d", resp.status_code)
                    return results

                for repo in resp.json().get("items", []):
                    results.append(HubResult(
                        name=repo.get("name", ""),
                        description=repo.get("description", ""),
                        author=repo.get("owner", {}).get("login", ""),
                        url=repo.get("html_url", ""),
                        hub="github",
                        format=SkillFormat.github,
                        tags=repo.get("topics", []),
                        download_url=repo.get("clone_url", ""),
                    ))
        except Exception as e:
            logger.warning("GitHub search failed: %s", e)

        return results

    async def fetch(self, identifier: str) -> Path:
        """Clone or download a GitHub repo into a temp directory.

        identifier can be:
        - A full GitHub URL (https://github.com/user/repo)
        - A user/repo shorthand
        """
        import httpx

        # Normalize identifier
        if identifier.startswith("https://github.com/"):
            identifier = identifier.replace("https://github.com/", "")
        identifier = identifier.rstrip("/").rstrip(".git")

        tmp_dir = Path(tempfile.mkdtemp(prefix="github_"))

        try:
            # Download as zip (faster than git clone, no git dependency)
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                zip_url = f"https://github.com/{identifier}/archive/refs/heads/main.zip"
                resp = await client.get(zip_url)

                if resp.status_code == 404:
                    # Try master branch
                    zip_url = f"https://github.com/{identifier}/archive/refs/heads/master.zip"
                    resp = await client.get(zip_url)

                if resp.status_code != 200:
                    raise ValueError(f"GitHub download failed with status {resp.status_code} for {identifier}")

            # Extract zip
            zip_path = tmp_dir / "repo.zip"
            zip_path.write_bytes(resp.content)

            _safe_extract_zip(zip_path, tmp_dir)
            zip_path.unlink()

            # The zip extracts to a subdirectory like "repo-main/"
            subdirs = [d for d in tmp_dir.iterdir() if d.is_dir()]
            if len(subdirs) == 1:
                # Move contents up one level
                extracted = subdirs[0]
                for item in extracted.iterdir():
                    shutil.move(str(item), str(tmp_dir / item.name))
                extracted.rmdir()

            return tmp_dir

        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    def normalize(self, path: Path) -> SkillManifest:
        """Auto-detect and normalize a GitHub repo to Pantheon skill format."""
        # If it already has skill.json, parse it directly
        if (path / "skill.json").exists():
            raw = json.loads((path / "skill.json").read_text(encoding="utf-8"))
            return SkillManifest(**raw)

        # Check for SKILL.md format
        skill_md_adapter = SkillMdAdapter()
        if skill_md_adapter._find_skill_md(path):
            return skill_md_adapter.normalize(path)

        # MCP tool definitions are not supported as skills — see
        # docs/mcp-registry-protocol.md for the MCP connector install flow.
        if (path / "mcp.json").exists() or (path / "tool.json").exists():
            raise ValueError(
                "This repository contains an MCP server definition. "
                "MCP servers are not imported as skills — install them via "
                "the MCP connector registry instead."
            )

        # Fall back: generate minimal skill from README
        return self._generate_from_readme(path)

    def _generate_from_readme(self, path: Path) -> SkillManifest:
        """Generate a minimal skill manifest from README and directory contents."""
        readme = ""
        for name in ("README.md", "readme.md", "README.MD", "README"):
            readme_path = path / name
            if readme_path.exists():
                readme = readme_path.read_text(encoding="utf-8", errors="replace")
                break

        # Extract first paragraph as description
        lines = readme.split("\n")
        desc_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped:
                desc_lines.append(stripped)
            elif desc_lines:
                break

        description = " ".join(desc_lines)[:300]

        manifest_data: dict[str, Any] = {
            "name": path.name.lower().replace(" ", "-"),
            "description": description or f"Imported from GitHub: {path.name}",
            "source_hub": "github",
            "tags": ["github-import"],
        }

        manifest = SkillManifest(**manifest_data)

        # Write manifest and use README as instructions
        (path / "skill.json").write_text(
            json.dumps(manifest_data, indent=2),
            encoding="utf-8",
        )
        if readme and not (path / "instructions.md").exists():
            (path / "instructions.md").write_text(readme, encoding="utf-8")

        logger.info("Generated minimal skill manifest from GitHub repo: %s", manifest.name)
        return manifest


# ── Local Upload Adapter ────────────────────────────────────────────────────

class LocalUploadAdapter(HubAdapter):
    """Adapter for importing skills from local file uploads (.tar.gz, .zip)."""

    @property
    def hub_name(self) -> str:
        return "Local Upload"

    async def search(self, query: str) -> list[HubResult]:
        return []

    async def fetch(self, identifier: str) -> Path:
        """Extract an uploaded archive to a temp directory.

        identifier should be the path to the uploaded file.
        """
        upload_path = Path(identifier)
        if not upload_path.exists():
            raise FileNotFoundError(f"Upload file not found: {identifier}")

        tmp_dir = Path(tempfile.mkdtemp(prefix="upload_"))

        try:
            if upload_path.suffix == ".zip" or upload_path.name.endswith(".zip"):
                _safe_extract_zip(upload_path, tmp_dir)
            elif upload_path.name.endswith((".tar.gz", ".tgz")):
                _safe_extract_tar(upload_path, tmp_dir, mode="r:gz")
            elif upload_path.name.endswith(".tar"):
                _safe_extract_tar(upload_path, tmp_dir, mode="r:")
            else:
                raise ValueError(f"Unsupported archive format: {upload_path.name}")

            # If extracted to a single subdirectory, flatten
            subdirs = [d for d in tmp_dir.iterdir() if d.is_dir()]
            files = [f for f in tmp_dir.iterdir() if f.is_file()]
            if len(subdirs) == 1 and not files:
                extracted = subdirs[0]
                for item in extracted.iterdir():
                    shutil.move(str(item), str(tmp_dir / item.name))
                extracted.rmdir()

            return tmp_dir

        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    def normalize(self, path: Path) -> SkillManifest:
        """Normalize a local upload — delegate to format detection."""
        fmt = self.detect_format(path)

        if fmt == SkillFormat.pantheon:
            raw = json.loads((path / "skill.json").read_text(encoding="utf-8"))
            return SkillManifest(**raw)
        elif fmt == SkillFormat.skill_md:
            return SkillMdAdapter().normalize(path)
        else:
            # Try to generate from README or directory name
            return GitHubAdapter()._generate_from_readme(path)


# ── Generic Skill Registry Adapter ──────────────────────────────────────────

class GenericSkillRegistryAdapter(HubAdapter):
    """Adapter for any registry that speaks the Pantheon Skill Registry Protocol.

    See docs/skill-registry-protocol.md. One instance is created per
    configured registry; multiple registries can be active at once.
    """

    DISCOVERY_PATH = "/.well-known/pantheon-skill-registry.json"
    MAX_BUNDLE_BYTES = 5 * 1024 * 1024  # 5 MiB hard cap

    def __init__(self, registry_id: str, base_url: str, *,
                 display_name: str | None = None,
                 auth_token: str | None = None) -> None:
        self.registry_id = registry_id
        self.base_url = base_url.rstrip("/")
        self._display_name = display_name or registry_id
        self.auth_token = auth_token
        self._discovery: dict[str, Any] | None = None

    @property
    def hub_name(self) -> str:
        return self._display_name

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.auth_token:
            h["Authorization"] = f"Bearer {self.auth_token}"
        return h

    async def _ensure_discovery(self, client) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        resp = await client.get(self.base_url + self.DISCOVERY_PATH,
                                headers=self._headers())
        resp.raise_for_status()
        doc = resp.json()
        if doc.get("protocol_version") != "1.0":
            raise ValueError(
                f"Unsupported skill registry protocol_version: "
                f"{doc.get('protocol_version')!r} (expected '1.0')"
            )
        self._discovery = doc
        return doc

    def _resolve(self, template: str, skill_id: str = "") -> str:
        path = template.replace("{id}", skill_id)
        if not path.startswith("http"):
            path = self.base_url + path
        return path

    async def search(self, query: str) -> list[HubResult]:
        import httpx
        results: list[HubResult] = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                disco = await self._ensure_discovery(client)
                search_url = self._resolve(disco["endpoints"]["search"])
                resp = await client.get(search_url,
                                        params={"q": query} if query else None,
                                        headers=self._headers())
                if resp.status_code != 200:
                    logger.warning("%s search returned %d",
                                   self._display_name, resp.status_code)
                    return results
                for entry in resp.json().get("results", []):
                    results.append(HubResult(
                        name=entry.get("name", entry.get("id", "")),
                        description=entry.get("description", ""),
                        author=entry.get("author", ""),
                        version=entry.get("version", ""),
                        url=entry.get("homepage", ""),
                        hub=self.registry_id,
                        format=SkillFormat.pantheon,
                        tags=entry.get("tags", []),
                        download_url=entry.get("id", ""),
                    ))
        except Exception as e:
            logger.warning("%s search failed: %s", self._display_name, e)
        return results

    async def fetch(self, identifier: str) -> Path:
        import hashlib
        import httpx

        tmp_dir = Path(tempfile.mkdtemp(prefix=f"{self.registry_id}_"))
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                disco = await self._ensure_discovery(client)

                # Step 1: fetch detail to get bundle metadata
                detail_url = self._resolve(disco["endpoints"]["get"], identifier)
                detail_resp = await client.get(detail_url, headers=self._headers())
                detail_resp.raise_for_status()
                detail = detail_resp.json()

                bundle_meta = detail.get("bundle") or {}
                fmt = bundle_meta.get("format", "tar.gz")
                size = int(bundle_meta.get("size_bytes", 0))
                expected_sha = bundle_meta.get("sha256", "")
                if size > self.MAX_BUNDLE_BYTES:
                    raise ValueError(
                        f"Bundle too large: {size} > {self.MAX_BUNDLE_BYTES}"
                    )
                if not expected_sha:
                    raise ValueError("Registry did not provide bundle.sha256")

                # Step 2: download bundle
                dl_url = self._resolve(disco["endpoints"]["download"], identifier)
                dl_resp = await client.get(dl_url, headers=self._headers())
                dl_resp.raise_for_status()
                content = dl_resp.content
                if len(content) > self.MAX_BUNDLE_BYTES:
                    raise ValueError(
                        f"Bundle download exceeded cap: {len(content)} bytes"
                    )

                # Step 3: verify sha256
                actual_sha = hashlib.sha256(content).hexdigest()
                if actual_sha.lower() != expected_sha.lower():
                    raise ValueError(
                        f"Bundle sha256 mismatch (expected {expected_sha[:16]}…, "
                        f"got {actual_sha[:16]}…)"
                    )

                # Step 4: write to temp file with proper extension
                if fmt == "zip":
                    bundle_path = tmp_dir / "bundle.zip"
                    bundle_path.write_bytes(content)
                    _safe_extract_zip(bundle_path, tmp_dir)
                elif fmt in ("tar.gz", "tgz"):
                    bundle_path = tmp_dir / "bundle.tar.gz"
                    bundle_path.write_bytes(content)
                    _safe_extract_tar(bundle_path, tmp_dir, mode="r:gz")
                else:
                    raise ValueError(f"Unsupported bundle format: {fmt}")
                bundle_path.unlink()

                # Flatten if extracted into a single subdir
                subdirs = [d for d in tmp_dir.iterdir() if d.is_dir()]
                files = [f for f in tmp_dir.iterdir() if f.is_file()]
                if len(subdirs) == 1 and not files:
                    extracted = subdirs[0]
                    for item in extracted.iterdir():
                        shutil.move(str(item), str(tmp_dir / item.name))
                    extracted.rmdir()

                return tmp_dir
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    def normalize(self, path: Path) -> SkillManifest:
        """Bundles must contain skill.json or SKILL.md at the root."""
        if (path / "skill.json").exists():
            raw = json.loads((path / "skill.json").read_text(encoding="utf-8"))
            return SkillManifest(**raw)
        if SkillMdAdapter()._find_skill_md(path):
            return SkillMdAdapter().normalize(path)
        raise ValueError(
            "Skill bundle must contain skill.json or SKILL.md at the root"
        )


# ── Importer Orchestrator ───────────────────────────────────────────────────

# Static built-in adapters. Configured registries are added at runtime via
# register_skill_registry() based on pantheon.config.json or the Settings UI.
_ADAPTERS: dict[str, HubAdapter] = {
    "skill_md": SkillMdAdapter(),
    "github": GitHubAdapter(),
    "local": LocalUploadAdapter(),
}


def register_skill_registry(
    registry_id: str,
    base_url: str,
    *,
    display_name: str | None = None,
    auth_token: str | None = None,
) -> None:
    """Add (or replace) a configured skill-registry-protocol adapter."""
    if registry_id in {"skill_md", "github", "local"}:
        raise ValueError(f"'{registry_id}' is a reserved built-in hub id")
    _ADAPTERS[registry_id] = GenericSkillRegistryAdapter(
        registry_id, base_url,
        display_name=display_name, auth_token=auth_token,
    )
    logger.info("Registered skill registry '%s' → %s", registry_id, base_url)


def unregister_skill_registry(registry_id: str) -> None:
    if registry_id in {"skill_md", "github", "local"}:
        raise ValueError(f"Cannot unregister built-in hub: {registry_id}")
    _ADAPTERS.pop(registry_id, None)


def load_configured_registries() -> None:
    """Load skill registries from settings.skill_registries (if present).

    Each entry: {id, url, display_name?, auth: {type, token_ref?}}
    Token references like 'vault:my_key' are resolved against the vault.
    """
    configured = getattr(settings, "skill_registries", None) or []
    for entry in configured:
        try:
            registry_id = entry["id"]
            url = entry["url"]
            display_name = entry.get("display_name")
            auth = entry.get("auth") or {}
            token = None
            if auth.get("type") == "bearer":
                token_ref = auth.get("token_ref", "")
                if token_ref.startswith("vault:"):
                    from vault import get_secret  # local import to avoid cycle
                    token = get_secret(token_ref[len("vault:"):])
                else:
                    token = auth.get("token")
            register_skill_registry(
                registry_id, url,
                display_name=display_name, auth_token=token,
            )
        except Exception as e:
            logger.error("Failed to load skill registry %r: %s", entry, e)


def get_adapter(hub: str) -> HubAdapter:
    """Get an adapter by hub name."""
    adapter = _ADAPTERS.get(hub)
    if not adapter:
        raise ValueError(f"Unknown hub: {hub}. Available: {list(_ADAPTERS.keys())}")
    return adapter


def list_hubs() -> list[dict[str, str]]:
    """List available import hubs (built-in + configured)."""
    hubs: list[dict[str, str]] = [
        {"id": "github", "name": "GitHub", "searchable": True},
        {"id": "skill_md", "name": "SKILL.md Format", "searchable": False},
        {"id": "local", "name": "Local Upload", "searchable": False},
    ]
    for rid, adapter in _ADAPTERS.items():
        if isinstance(adapter, GenericSkillRegistryAdapter):
            hubs.append({"id": rid, "name": adapter.hub_name, "searchable": True})
    return hubs


async def search_hubs(query: str, hub: str | None = None) -> list[HubResult]:
    """Search one or all hubs for skills."""
    results: list[HubResult] = []

    if hub:
        adapter = get_adapter(hub)
        results = await adapter.search(query)
    else:
        # Search all searchable hubs
        for hub_id, adapter in _ADAPTERS.items():
            try:
                hub_results = await adapter.search(query)
                results.extend(hub_results)
            except Exception as e:
                logger.warning("Hub search failed for %s: %s", hub_id, e)

    return results


async def import_skill(
    source: str,
    hub: str = "local",
    *,
    run_scan: bool = True,
    ai_review: bool = True,
) -> ImportResult:
    """Import a skill from any source, scan it, and install.

    Args:
        source: URL, identifier, or local file path
        hub: Which hub adapter to use
        run_scan: Whether to run the security scanner
        ai_review: Whether to include AI review in the scan

    Returns:
        ImportResult with status and details
    """
    from skills.registry import get_skill_registry
    from skills.scanner import scan_skill

    registry = get_skill_registry()
    adapter = get_adapter(hub)
    tmp_dir = None

    try:
        # Step 1: Fetch the skill bundle
        logger.info("Importing skill from %s (hub=%s)", source, hub)
        tmp_dir = await adapter.fetch(source)

        # Step 2: Detect format and normalize to Pantheon format
        detected_format = adapter.detect_format(tmp_dir)
        manifest = adapter.normalize(tmp_dir)

        skill_name = manifest.name
        if not skill_name:
            return ImportResult(
                success=False,
                message="Skill manifest has no name",
                source=source,
                format_detected=detected_format,
            )

        # Step 3: Check for name collision with bundled skills
        if registry.is_bundled_name(skill_name):
            sec_log.skill_name_collision_blocked(
                skill=skill_name,
                reason=f"import from {hub} tried to override bundled skill",
            )
            return ImportResult(
                success=False,
                skill_name=skill_name,
                message=f"Cannot import: '{skill_name}' collides with a bundled skill name",
                source=source,
                format_detected=detected_format,
            )

        # Check if already installed
        existing = registry.get(skill_name)
        if existing and not existing.is_bundled:
            # Overwrite existing user-installed skill
            old_dir = Path(existing.skill_dir)
            if old_dir.is_dir():
                shutil.rmtree(old_dir)
            logger.info("Replacing existing user skill '%s'", skill_name)

        # Step 4: Run security scan
        scan_result = None
        if run_scan:
            instructions = ""
            instructions_path = tmp_dir / "instructions.md"
            if instructions_path.exists():
                instructions = instructions_path.read_text(encoding="utf-8")

            scan_result = await scan_skill(
                tmp_dir, manifest, instructions,
                run_ai_review=ai_review,
            )

            logger.info(
                "Import scan for '%s': %s (risk=%.2f, findings=%d)",
                skill_name,
                "PASSED" if scan_result.passed else "FAILED",
                scan_result.risk_score,
                len(scan_result.findings),
            )

        # Step 5: Install to data/skills/
        install_dir = settings.data_dir / "skills" / skill_name
        install_dir.parent.mkdir(parents=True, exist_ok=True)

        if install_dir.exists():
            shutil.rmtree(install_dir)
        shutil.copytree(tmp_dir, install_dir)

        # Step 6: Handle scan failure — quarantine
        quarantined = False
        if scan_result and not scan_result.passed:
            quarantine_dir = settings.data_dir / "skills" / ".quarantine"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            dest = quarantine_dir / skill_name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(install_dir), str(dest))
            quarantined = True
            sec_log.skill_quarantined(skill=skill_name, reason="import_scan_failed")
            logger.info("Quarantined imported skill '%s' (scan failed)", skill_name)
        else:
            if scan_result:
                sec_log.skill_scan_passed(
                    skill=skill_name,
                    risk=scan_result.risk_score,
                    findings=len(scan_result.findings),
                )

        # Step 7: Reload registry to pick up the new skill
        from skills.registry import reload_skill_registry
        reload_skill_registry()

        # Save scan result if we ran one
        if scan_result and not quarantined:
            registry = get_skill_registry()
            skill = registry.get(skill_name)
            if skill:
                skill.manifest.security_scan = scan_result
                registry.save_scan_result(skill_name, scan_result)

        return ImportResult(
            success=True,
            skill_name=skill_name,
            message="Quarantined (scan failed)" if quarantined else "Imported successfully",
            scan_passed=scan_result.passed if scan_result else None,
            scan_risk=scan_result.risk_score if scan_result else None,
            scan_findings=len(scan_result.findings) if scan_result else 0,
            quarantined=quarantined,
            installed_path=str(install_dir) if not quarantined else "",
            source=source,
            format_detected=detected_format,
        )

    except Exception as e:
        logger.error("Import failed from %s (hub=%s): %s", source, hub, e, exc_info=True)
        return ImportResult(
            success=False,
            message=f"Import failed: {e}",
            source=source,
        )

    finally:
        # Clean up temp directory
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
