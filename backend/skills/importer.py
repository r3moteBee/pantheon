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
                        "q": f"{query} topic:agent-skill OR topic:llm-skill OR topic:mcp-server",
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

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_dir)
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
                with zipfile.ZipFile(upload_path, "r") as zf:
                    zf.extractall(tmp_dir)
            elif upload_path.name.endswith((".tar.gz", ".tgz")):
                with tarfile.open(upload_path, "r:gz") as tf:
                    # Security: prevent path traversal in tar
                    for member in tf.getmembers():
                        member_path = (tmp_dir / member.name).resolve()
                        if not str(member_path).startswith(str(tmp_dir.resolve())):
                            raise ValueError(f"Tar path traversal attempt: {member.name}")
                    tf.extractall(tmp_dir)
            elif upload_path.name.endswith(".tar"):
                with tarfile.open(upload_path, "r:") as tf:
                    for member in tf.getmembers():
                        member_path = (tmp_dir / member.name).resolve()
                        if not str(member_path).startswith(str(tmp_dir.resolve())):
                            raise ValueError(f"Tar path traversal attempt: {member.name}")
                    tf.extractall(tmp_dir)
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


# ── Importer Orchestrator ───────────────────────────────────────────────────

# Adapter registry
_ADAPTERS: dict[str, HubAdapter] = {
    "skill_md": SkillMdAdapter(),
    "github": GitHubAdapter(),
    "local": LocalUploadAdapter(),
}


def get_adapter(hub: str) -> HubAdapter:
    """Get an adapter by hub name."""
    adapter = _ADAPTERS.get(hub)
    if not adapter:
        raise ValueError(f"Unknown hub: {hub}. Available: {list(_ADAPTERS.keys())}")
    return adapter


def list_hubs() -> list[dict[str, str]]:
    """List available import hubs."""
    return [
        {"id": "github", "name": "GitHub", "searchable": True},
        {"id": "skill_md", "name": "SKILL.md Format", "searchable": False},
        {"id": "local", "name": "Local Upload", "searchable": False},
    ]


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
