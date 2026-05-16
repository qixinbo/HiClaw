"""Tests for Worker sync and restored per-user workspace recovery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from copaw_worker.sync import FileSync, push_local
from copaw_worker.worker import Worker
from copaw_worker.user_paths import reconcile_existing_user_workspaces


def test_push_local_uploads_users_workspace_files(
    tmp_path: Path,
    monkeypatch,
):
    uploaded: list[str] = []

    def _fake_mc(*args, **_kwargs):
        if args[0] == "cp":
            uploaded.append(args[1])
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    sync = FileSync(
        endpoint="http://minio.local",
        access_key="ak",
        secret_key="sk",
        bucket="bucket",
        worker_name="worker-a",
        local_dir=tmp_path,
    )

    user_file = (
        tmp_path
        / ".copaw"
        / "workspaces"
        / "default"
        / "users"
        / "@alice--hs.local"
        / "sessions"
        / "matrix--!room--hs.local.json"
    )
    user_file.parent.mkdir(parents=True, exist_ok=True)
    user_file.write_text('{"hello":"world"}', encoding="utf-8")

    monkeypatch.setattr("copaw_worker.sync._mc", _fake_mc)
    monkeypatch.setattr(sync, "_ensure_alias", lambda: None)
    monkeypatch.setattr(sync, "_cat", lambda _key: None)

    pushed = push_local(sync, since=0)

    rel_path = (
        ".copaw/workspaces/default/users/"
        "@alice--hs.local/sessions/matrix--!room--hs.local.json"
    )
    assert rel_path in pushed
    assert str(user_file) in uploaded


def test_reconcile_existing_user_workspaces_repairs_restored_users(
    tmp_path: Path,
):
    workspace_dir = tmp_path / ".copaw" / "workspaces" / "default"
    users_root = workspace_dir / "users"
    restored_user = users_root / "@alice--hs.local"

    restored_user.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "AGENTS.md").write_text("agent", encoding="utf-8")
    (workspace_dir / "SOUL.md").write_text("soul", encoding="utf-8")

    restored = reconcile_existing_user_workspaces(workspace_dir)

    assert restored == [restored_user]
    assert (restored_user / "memory.md").exists()
    assert (restored_user / "sessions").is_dir()
    assert (restored_user / "memory").is_dir()
    assert (restored_user / "AGENTS.md").read_text(encoding="utf-8") == "agent"
    assert (restored_user / "SOUL.md").read_text(encoding="utf-8") == "soul"


def test_worker_restore_user_workspaces_uses_default_workspace(tmp_path: Path):
    worker = Worker(SimpleNamespace(worker_name="worker-a"))
    worker._copaw_working_dir = tmp_path / ".copaw"

    workspace_dir = worker._copaw_working_dir / "workspaces" / "default"
    restored_user = workspace_dir / "users" / "@alice--hs.local"
    restored_user.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "AGENTS.md").write_text("agent", encoding="utf-8")
    (workspace_dir / "SOUL.md").write_text("soul", encoding="utf-8")

    worker._restore_user_workspaces()

    assert (restored_user / "memory.md").exists()
    assert (restored_user / "sessions").is_dir()
    assert (restored_user / "AGENTS.md").read_text(encoding="utf-8") == "agent"
