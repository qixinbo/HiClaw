from __future__ import annotations

import os
import re
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

_UNSAFE_PATH_RE = re.compile(r'[\\/:*?"<>|]')
_USER_PRIVATE_NAMES = frozenset(
    {
        "users",
        "sessions",
        "memory",
        "dialog",
        "tool_result",
        "memory.md",
        "MEMORY.md",
    },
)
_SHARED_DIR_NAMES = ("skills", "active_skills", "customized_skills")

_current_human_id: ContextVar[str | None] = ContextVar(
    "copaw_current_human_id",
    default=None,
)


def sanitize_path_component(value: str) -> str:
    """Make a user/session identifier safe to store as a path component."""
    value = (value or "").strip()
    if not value:
        return ""
    return _UNSAFE_PATH_RE.sub("--", value)


def get_current_human_id() -> str | None:
    """Return the human id bound to the current request context."""
    return _current_human_id.get()


@contextmanager
def bind_human_id(human_id: str | None) -> Iterator[None]:
    """Bind *human_id* to the current request so runtime helpers can resolve it."""
    normalized = (human_id or "").strip() or None
    token = _current_human_id.set(normalized)
    try:
        yield
    finally:
        _current_human_id.reset(token)


def resolve_user_workspace_dir(
    workspace_root: str | Path,
    human_id: str | None,
) -> Path:
    """Return the per-user workspace directory for *human_id*."""
    root = Path(workspace_root)
    safe_human_id = sanitize_path_component(human_id or "")
    if not safe_human_id:
        return root
    return root / "users" / safe_human_id


def build_session_file_path(
    workspace_root: str | Path,
    session_id: str,
    human_id: str | None,
) -> Path:
    """Return the on-disk session JSON path for the current user."""
    safe_session_id = sanitize_path_component(session_id)
    if not safe_session_id:
        safe_session_id = "session"

    user_workspace = resolve_user_workspace_dir(workspace_root, human_id)
    if user_workspace == Path(workspace_root):
        session_dir = Path(workspace_root) / "sessions"
    else:
        session_dir = ensure_user_workspace(workspace_root, human_id) / "sessions"

    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / f"{safe_session_id}.json"


def ensure_user_workspace(
    workspace_root: str | Path,
    human_id: str | None,
) -> Path:
    """Create and initialize the per-user workspace tree if needed."""
    root = Path(workspace_root)
    user_workspace = resolve_user_workspace_dir(root, human_id)
    if user_workspace == root:
        return root

    user_workspace.mkdir(parents=True, exist_ok=True)
    (user_workspace / "sessions").mkdir(parents=True, exist_ok=True)
    (user_workspace / "memory").mkdir(parents=True, exist_ok=True)
    (user_workspace / "dialog").mkdir(parents=True, exist_ok=True)
    (user_workspace / "tool_result").mkdir(parents=True, exist_ok=True)

    memory_md = user_workspace / "memory.md"
    if not memory_md.exists():
        memory_md.write_text("", encoding="utf-8")

    memory_md_compat = user_workspace / "MEMORY.md"
    if not memory_md_compat.exists():
        try:
            memory_md_compat.symlink_to("memory.md")
        except OSError:
            memory_md_compat.write_text(
                memory_md.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

    for entry in root.iterdir():
        if entry.name in _USER_PRIVATE_NAMES:
            continue
        if entry.is_file() and entry.suffix.lower() == ".md":
            _ensure_shared_entry(entry, user_workspace / entry.name)

    for dir_name in _SHARED_DIR_NAMES:
        source = root / dir_name
        if source.exists():
            _ensure_shared_entry(source, user_workspace / dir_name)

    return user_workspace


def reconcile_existing_user_workspaces(workspace_root: str | Path) -> list[Path]:
    """Repair restored per-user workspaces after they are mirrored back locally."""
    root = Path(workspace_root)
    users_root = root / "users"
    if not users_root.is_dir():
        return []

    restored: list[Path] = []
    for child in users_root.iterdir():
        if not child.is_dir():
            continue
        restored.append(ensure_user_workspace(root, child.name))
    return restored


def _ensure_shared_entry(source: Path, destination: Path) -> None:
    """Expose shared agent assets inside a per-user workspace."""
    if destination.exists() or destination.is_symlink():
        return

    rel_target = os.path.relpath(source, start=destination.parent)
    try:
        destination.symlink_to(
            rel_target,
            target_is_directory=source.is_dir(),
        )
        return
    except OSError:
        pass

    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
