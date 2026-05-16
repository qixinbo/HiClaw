"""Tests for per-human CoPaw workspace path routing."""

from __future__ import annotations

from pathlib import Path

from copaw_worker.user_paths import (
    bind_human_id,
    build_session_file_path,
    ensure_user_workspace,
    get_current_human_id,
    resolve_user_workspace_dir,
)


def test_resolve_user_workspace_dir_sanitizes_human_id(tmp_path: Path):
    workspace = resolve_user_workspace_dir(
        tmp_path,
        "@alice:hs.local",
    )
    assert workspace == tmp_path / "users" / "@alice--hs.local"


def test_ensure_user_workspace_creates_user_memory_and_shared_assets(
    tmp_path: Path,
):
    (tmp_path / "AGENTS.md").write_text("agent", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("soul", encoding="utf-8")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "demo.txt").write_text("skill", encoding="utf-8")

    user_workspace = ensure_user_workspace(tmp_path, "@alice:hs.local")

    assert user_workspace == tmp_path / "users" / "@alice--hs.local"
    assert (user_workspace / "memory.md").exists()
    assert (user_workspace / "MEMORY.md").exists()
    assert (user_workspace / "sessions").is_dir()
    assert (user_workspace / "memory").is_dir()
    assert (user_workspace / "dialog").is_dir()
    assert (user_workspace / "tool_result").is_dir()
    assert (user_workspace / "AGENTS.md").read_text(encoding="utf-8") == "agent"
    assert (user_workspace / "SOUL.md").read_text(encoding="utf-8") == "soul"
    assert (user_workspace / "skills").exists()


def test_build_session_file_path_routes_to_user_sessions(tmp_path: Path):
    session_path = build_session_file_path(
        workspace_root=tmp_path,
        session_id="matrix:!room:hs.local",
        human_id="@alice:hs.local",
    )

    assert session_path == (
        tmp_path
        / "users"
        / "@alice--hs.local"
        / "sessions"
        / "matrix--!room--hs.local.json"
    )


def test_bind_human_id_is_request_scoped():
    assert get_current_human_id() is None

    with bind_human_id("@alice:hs.local"):
        assert get_current_human_id() == "@alice:hs.local"

    assert get_current_human_id() is None


def test_dm_same_human_reuses_same_personal_paths_across_turns(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("agent", encoding="utf-8")

    first_workspace = ensure_user_workspace(tmp_path, "@alice:hs.local")
    first_memory = first_workspace / "memory.md"
    first_memory.write_text("prefers tea", encoding="utf-8")
    first_session = build_session_file_path(
        workspace_root=tmp_path,
        session_id="matrix:!dm:hs.local",
        human_id="@alice:hs.local",
    )
    first_session.write_text('{"turn": 1}', encoding="utf-8")

    second_workspace = ensure_user_workspace(tmp_path, "@alice:hs.local")
    second_session = build_session_file_path(
        workspace_root=tmp_path,
        session_id="matrix:!dm:hs.local",
        human_id="@alice:hs.local",
    )

    assert second_workspace == first_workspace
    assert second_session == first_session
    assert (second_workspace / "memory.md").read_text(encoding="utf-8") == "prefers tea"
    assert second_session.read_text(encoding="utf-8") == '{"turn": 1}'


def test_group_same_room_different_humans_do_not_share_personal_paths(
    tmp_path: Path,
):
    alice_workspace = ensure_user_workspace(tmp_path, "@alice:hs.local")
    bob_workspace = ensure_user_workspace(tmp_path, "@bob:hs.local")

    alice_session = build_session_file_path(
        workspace_root=tmp_path,
        session_id="matrix:!group:hs.local",
        human_id="@alice:hs.local",
    )
    bob_session = build_session_file_path(
        workspace_root=tmp_path,
        session_id="matrix:!group:hs.local",
        human_id="@bob:hs.local",
    )

    (alice_workspace / "memory.md").write_text("alice-only", encoding="utf-8")
    (bob_workspace / "memory.md").write_text("bob-only", encoding="utf-8")

    assert alice_workspace != bob_workspace
    assert alice_session != bob_session
    assert alice_session.parent == alice_workspace / "sessions"
    assert bob_session.parent == bob_workspace / "sessions"
    assert (alice_workspace / "memory.md").read_text(encoding="utf-8") == "alice-only"
    assert (bob_workspace / "memory.md").read_text(encoding="utf-8") == "bob-only"
