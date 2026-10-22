"""Small JSON files that several code paths read-modify-write (projects.json).

- One process-wide lock per file, so two writers can't interleave a
  read-modify-write and lose each other's change.
- Atomic writes: a temp file in the same directory + fsync + os.replace,
  so a reader never sees a half-written file and a crash leaves the old one.
- The previous good file is kept as ``<name>.bak`` on every write.
- A file that doesn't parse raises ``JsonFileCorrupt``; it is never treated
  as "missing" and overwritten with defaults.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class JsonFileCorrupt(RuntimeError):
    """The file exists but isn't valid JSON (or not the expected type)."""


def lock_for(path: Path | str) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.RLock()
        return lock


def backup_path(path: Path | str) -> Path:
    p = Path(path)
    return p.with_name(p.name + ".bak")


def read_json(path: Path | str, default: Any = None, *, expect: type | None = dict) -> Any:
    """Parsed content of ``path``; ``default`` when it doesn't exist.
    Raises JsonFileCorrupt when it exists but can't be parsed."""
    p = Path(path)
    with lock_for(p):
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default
        try:
            data = json.loads(text)
        except ValueError as e:
            logger.error("%s is not valid JSON (%s) - not touching it; the last good copy is %s",
                         p, e, backup_path(p))
            raise JsonFileCorrupt(f"{p.name} is unreadable: {e}") from e
        if expect is not None and not isinstance(data, expect):
            logger.error("%s holds %s, expected %s - not touching it", p, type(data).__name__, expect.__name__)
            raise JsonFileCorrupt(f"{p.name} holds {type(data).__name__}, expected {expect.__name__}")
        return data


def write_json_atomic(path: Path | str, data: Any) -> None:
    """Replace ``path`` with ``data`` atomically; the old file becomes .bak."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    with lock_for(p):
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            if p.exists():
                try:
                    json.loads(p.read_text(encoding="utf-8"))
                    shutil.copy2(p, backup_path(p))   # only a parseable file replaces the backup
                except (ValueError, OSError):
                    pass
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


@contextmanager
def update_json(path: Path | str, default: Callable[[], Any] | Any = None,
                *, expect: type | None = dict) -> Iterator[Any]:
    """Locked read-modify-write: ``with update_json(p, dict) as data: ...``.
    The file is written only when ``data`` changed. A corrupt file raises
    JsonFileCorrupt before the body runs."""
    p = Path(path)
    with lock_for(p):
        data = read_json(p, None, expect=expect)
        existed = data is not None
        if data is None:
            data = default() if callable(default) else default
        before = json.dumps(data, sort_keys=True)
        yield data
        if not existed or json.dumps(data, sort_keys=True) != before:
            write_json_atomic(p, data)
