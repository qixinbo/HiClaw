"""Tests for per-human isolation in the ReMe memory manager overlay."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from copaw_runtime_overlay.agents.memory.reme_light_memory_manager import (
    ReMeLightMemoryManager,
)
from copaw_worker.user_paths import bind_human_id, get_current_human_id


def _fake_agent_config() -> SimpleNamespace:
    embedding_config = SimpleNamespace(
        backend="openai",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model_name="text-embedding-test",
        dimensions=1024,
        enable_cache=False,
        use_dimensions=True,
        max_cache_size=0,
        max_input_length=8192,
        max_batch_size=16,
    )
    memory_summary = SimpleNamespace(
        rebuild_memory_index_on_start=True,
    )
    context_compact = SimpleNamespace(
        memory_compact_ratio=0.5,
        compact_with_thinking_block=False,
    )
    tool_result_compact = SimpleNamespace(
        recent_max_bytes=4096,
    )
    running = SimpleNamespace(
        embedding_config=embedding_config,
        memory_summary=memory_summary,
        context_compact=context_compact,
        tool_result_compact=tool_result_compact,
        max_input_length=8192,
    )
    return SimpleNamespace(running=running, language="zh-CN")


class _FakeReme:
    def __init__(self, working_dir: str):
        self.working_dir = working_dir
        self._started = False
        self.start_calls = 0

    async def start(self):
        self._started = True
        self.start_calls += 1
        return None

    async def close(self):
        self._started = False
        return True

    async def restart(self, restart_config=None):
        return restart_config

    async def memory_search(
        self,
        query: str,
        max_results: int = 5,
        min_score: float = 0.1,
    ) -> ToolResponse:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"query={query} max_results={max_results} "
                        f"min_score={min_score} working_dir={self.working_dir}"
                    ),
                ),
            ],
        )

    async def summary_memory(self, messages, **_kwargs):
        return f"summary:{self.working_dir}:{len(messages)}"

    def get_in_memory_memory(self, **_kwargs):
        return {"working_dir": self.working_dir}


@pytest.fixture
def isolated_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "copaw_runtime_overlay.agents.memory.reme_light_memory_manager.load_agent_config",
        lambda _agent_id: _fake_agent_config(),
    )
    monkeypatch.setattr(
        "copaw_runtime_overlay.agents.memory.reme_light_memory_manager.load_config",
        lambda: SimpleNamespace(user_timezone="UTC"),
    )
    monkeypatch.setattr(
        "copaw_runtime_overlay.agents.memory.reme_light_memory_manager.get_copaw_token_counter",
        lambda _agent_config: object(),
    )

    created: dict[str, _FakeReme] = {}

    def _build_reme(self, working_dir: str):
        fake = _FakeReme(working_dir)
        created[working_dir] = fake
        return fake

    monkeypatch.setattr(
        ReMeLightMemoryManager,
        "_build_reme",
        _build_reme,
    )

    manager = ReMeLightMemoryManager(
        working_dir=str(tmp_path / "workspace"),
        agent_id="default",
    )
    return manager, created


@pytest.mark.asyncio
async def test_memory_search_uses_per_human_reme_and_index_start(
    isolated_manager,
):
    manager, created = isolated_manager
    await manager.start()

    with bind_human_id("@alice:hs.local"):
        alice_result = await manager.memory_search("alice")
        alice_dir = manager.working_dir

    with bind_human_id("@bob:hs.local"):
        bob_result = await manager.memory_search("bob")
        bob_dir = manager.working_dir

    with bind_human_id("@alice:hs.local"):
        await manager.memory_search("alice-again")

    assert alice_dir.endswith("users/@alice--hs.local")
    assert bob_dir.endswith("users/@bob--hs.local")
    assert alice_dir != bob_dir
    assert created[alice_dir].start_calls == 1
    assert created[bob_dir].start_calls == 1
    assert alice_dir in alice_result.content[0]["text"]
    assert bob_dir in bob_result.content[0]["text"]


@pytest.mark.asyncio
async def test_summary_memory_routes_auto_memory_to_current_human(
    isolated_manager,
):
    manager, created = isolated_manager
    await manager.start()
    manager._prepare_model_formatter = lambda: None
    manager.chat_model = object()
    manager.formatter = object()

    with bind_human_id("@alice:hs.local"):
        alice_summary = await manager.summary_memory(messages=["a1", "a2"])
        alice_dir = manager.working_dir

    with bind_human_id("@bob:hs.local"):
        bob_summary = await manager.summary_memory(messages=["b1"])
        bob_dir = manager.working_dir

    assert alice_dir.endswith("users/@alice--hs.local")
    assert bob_dir.endswith("users/@bob--hs.local")
    assert alice_dir != bob_dir
    assert alice_summary == f"summary:{alice_dir}:2"
    assert bob_summary == f"summary:{bob_dir}:1"
    assert alice_dir in created
    assert bob_dir in created


@pytest.mark.asyncio
async def test_summary_tasks_are_bucketed_per_human(isolated_manager):
    manager, _created = isolated_manager

    async def _fake_summary_memory(messages, **_kwargs):
        await asyncio.sleep(0)
        return f"{get_current_human_id()}:{len(messages)}"

    manager.summary_memory = _fake_summary_memory

    with bind_human_id("@alice:hs.local"):
        manager.add_async_summary_task(messages=["a1", "a2"])
        assert len(manager.summary_tasks) == 1

    with bind_human_id("@bob:hs.local"):
        manager.add_async_summary_task(messages=["b1"])
        assert len(manager.summary_tasks) == 1

    with bind_human_id("@alice:hs.local"):
        alice_result = await manager.await_summary_tasks()
        assert "@alice:hs.local:2" in alice_result
        assert "@bob:hs.local" not in alice_result
        assert len(manager.summary_tasks) == 0

    with bind_human_id("@bob:hs.local"):
        bob_result = await manager.await_summary_tasks()
        assert "@bob:hs.local:1" in bob_result
        assert "@alice:hs.local" not in bob_result
        assert len(manager.summary_tasks) == 0
