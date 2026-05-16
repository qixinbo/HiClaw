# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
# mypy: ignore-errors
"""ReMeLight-backed memory manager for CoPaw agents."""
import asyncio
import importlib.metadata
import json
import logging
import os
import platform
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentscope.message import Msg, TextBlock
from agentscope.tool import Toolkit, ToolResponse

from copaw.agents.memory.base_memory_manager import BaseMemoryManager
from copaw.agents.model_factory import create_model_and_formatter
from copaw.agents.tools import read_file, write_file, edit_file
from copaw.agents.utils import get_copaw_token_counter
from copaw.config import load_config
from copaw.config.config import load_agent_config
from copaw.config.context import (
    set_current_workspace_dir,
    set_current_recent_max_bytes,
)
from copaw.constant import EnvVarLoader
from copaw_worker.user_paths import ensure_user_workspace, get_current_human_id

if TYPE_CHECKING:
    from reme.memory.file_based.reme_in_memory_memory import ReMeInMemoryMemory

logger = logging.getLogger(__name__)

_EXPECTED_REME_VERSION = "0.3.1.8"


class ReMeLightMemoryManager(BaseMemoryManager):
    """Memory manager that lazily creates one ReMeLight per human workspace."""

    def __init__(self, working_dir: str, agent_id: str):
        """Initialize with ReMeLight.

        Args:
            working_dir: Working directory for memory storage.
            agent_id: Agent ID for config loading.

        Embedding priority: config > env var (EMBEDDING_API_KEY /
        EMBEDDING_BASE_URL / EMBEDDING_MODEL_NAME).
        Backend: MEMORY_STORE_BACKEND env var (auto/local/chroma,
        default auto).
        """
        self._base_working_dir = working_dir
        self._user_reme_states: dict[str, dict[str, Any]] = {}
        super().__init__(working_dir=working_dir, agent_id=agent_id)
        self._reme_version_ok: bool = self._check_reme_version()
        self._started = False

        logger.info(
            f"ReMeLightMemoryManager init: "
            f"agent_id={agent_id}, working_dir={working_dir}",
        )

        backend_env = EnvVarLoader.get_str("MEMORY_STORE_BACKEND", "auto")
        if backend_env == "auto":
            if platform.system() == "Windows":
                memory_backend = "local"
            else:
                try:
                    import chromadb  # noqa: F401 pylint: disable=unused-import

                    memory_backend = "chroma"
                except Exception as e:
                    logger.warning(
                        f"""
chromadb import failed, falling back to `local` backend.
This is often caused by an outdated system SQLite (requires >= 3.35).
Please upgrade your system SQLite to >= 3.35.
See: https://docs.trychroma.com/docs/overview/troubleshooting#sqlite
| Error: {e}
                        """,
                    )
                    memory_backend = "local"
        else:
            memory_backend = backend_env

        emb_config = self.get_embedding_config()
        self._vector_enabled = bool(emb_config["base_url"]) and bool(
            emb_config["model_name"],
        )
        self._emb_config = emb_config

        log_cfg = {
            **emb_config,
            "api_key": self._mask_key(emb_config["api_key"]),
        }
        logger.info(
            f"Embedding config: {log_cfg}, vector_enabled={self._vector_enabled}",
        )

        self._fts_enabled = EnvVarLoader.get_bool("FTS_ENABLED", True)

        agent_config = load_agent_config(self.agent_id)
        self._rebuild_on_start = (
            agent_config.running.memory_summary.rebuild_memory_index_on_start
        )
        self._memory_backend = memory_backend

        self.summary_toolkit = Toolkit()
        self.summary_toolkit.register_tool_function(read_file)
        self.summary_toolkit.register_tool_function(write_file)
        self.summary_toolkit.register_tool_function(edit_file)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mask_key(key: str) -> str:
        """Mask API key, showing first 5 chars only."""
        return key[:5] + "*" * (len(key) - 5) if len(key) > 5 else key

    @staticmethod
    def _check_reme_version() -> bool:
        """Return False (and warn) when installed reme-ai version
        mismatches."""
        try:
            installed = importlib.metadata.version("reme-ai")
        except importlib.metadata.PackageNotFoundError:
            return True
        if installed != _EXPECTED_REME_VERSION:
            logger.warning(
                f"reme-ai version mismatch: installed={installed}, "
                f"expected={_EXPECTED_REME_VERSION}. "
                f"Run `pip install reme-ai=={_EXPECTED_REME_VERSION}`"
                " to align.",
            )
            return False
        return True

    def _warn_if_version_mismatch(self) -> None:
        """Warn once per call if the cached version check failed."""
        if not self._reme_version_ok:
            logger.warning(
                "reme-ai version mismatch, "
                f"expected={_EXPECTED_REME_VERSION}. "
                f"Run `pip install reme-ai=={_EXPECTED_REME_VERSION}`"
                " to align.",
            )

    def _prepare_model_formatter(self) -> None:
        """Lazily initialize chat_model and formatter if not already set."""
        self._warn_if_version_mismatch()
        if self.chat_model is None or self.formatter is None:
            self.chat_model, self.formatter = create_model_and_formatter(
                self.agent_id,
            )

    def _resolve_active_working_dir(self) -> str:
        """Resolve the per-user memory root for the current request."""
        human_id = get_current_human_id()
        active_dir = ensure_user_workspace(self._base_working_dir, human_id)
        self.working_dir = str(active_dir)
        return self.working_dir

    def _build_reme(self, working_dir: str):
        """Create a ReMeLight instance bound to *working_dir*."""
        from reme.reme_light import ReMeLight

        return ReMeLight(
            working_dir=working_dir,
            default_embedding_model_config=self._emb_config,
            default_file_store_config={
                "backend": self._memory_backend,
                "store_name": "copaw",
                "vector_enabled": self._vector_enabled,
                "fts_enabled": self._fts_enabled,
            },
            default_file_watcher_config={
                "rebuild_index_on_start": self._rebuild_on_start,
            },
        )

    def _get_user_state(self) -> dict[str, Any]:
        """Return the cached ReMe state for the current request user."""
        working_dir = self._resolve_active_working_dir()
        state = self._user_reme_states.get(working_dir)
        if state is None:
            state = {
                "working_dir": working_dir,
                "reme": self._build_reme(working_dir),
                "started": False,
                "summary_tasks": [],
            }
            self._user_reme_states[working_dir] = state
        return state

    async def _ensure_user_state_started(self) -> dict[str, Any]:
        """Ensure the current user's ReMe instance is initialized and started."""
        state = self._get_user_state()
        if self._started and not state["started"]:
            await state["reme"].start()
            state["started"] = True
        return state

    def _prune_summary_tasks(
        self,
        tasks: list[asyncio.Task],
    ) -> list[asyncio.Task]:
        """Drop finished summary tasks and log their outcomes."""
        remaining_tasks = []
        for task in tasks:
            if task.done():
                if task.cancelled():
                    logger.warning("Summary task was cancelled.")
                    continue
                exc = task.exception()
                if exc is not None:
                    logger.error(f"Summary task failed: {exc}")
                else:
                    result = task.result()
                    logger.info(f"Summary task completed: {result}")
            else:
                remaining_tasks.append(task)
        return remaining_tasks

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_embedding_config(self) -> dict:
        """Return embedding config with priority:
        config > env var > default."""
        self._warn_if_version_mismatch()
        cfg = load_agent_config(self.agent_id).running.embedding_config
        return {
            "backend": cfg.backend,
            "api_key": cfg.api_key
            or EnvVarLoader.get_str("EMBEDDING_API_KEY"),
            "base_url": cfg.base_url
            or EnvVarLoader.get_str("EMBEDDING_BASE_URL"),
            "model_name": cfg.model_name
            or EnvVarLoader.get_str("EMBEDDING_MODEL_NAME"),
            "dimensions": cfg.dimensions,
            "enable_cache": cfg.enable_cache,
            "use_dimensions": cfg.use_dimensions,
            "max_cache_size": cfg.max_cache_size,
            "max_input_length": cfg.max_input_length,
            "max_batch_size": cfg.max_batch_size,
        }

    async def restart_embedding_model(self):
        """Restart the embedding model with current config."""
        self._warn_if_version_mismatch()
        for state in self._user_reme_states.values():
            await state["reme"].restart(
                restart_config={
                    "embedding_models": {"default": self.get_embedding_config()},
                },
            )

    async def prepare_for_user(self) -> str:
        """Initialize the current user's memory workspace before a request."""
        state = await self._ensure_user_state_started()
        return state["working_dir"]

    @property
    def summary_tasks(self) -> list[asyncio.Task]:
        """Return background summary tasks for the current human only."""
        state = self._get_user_state()
        tasks = self._prune_summary_tasks(state["summary_tasks"])
        state["summary_tasks"] = tasks
        return tasks

    @summary_tasks.setter
    def summary_tasks(self, tasks: list[asyncio.Task]) -> None:
        """Store background summary tasks for the current human only."""
        state = self._get_user_state()
        state["summary_tasks"] = list(tasks)

    def add_async_summary_task(self, messages: list[Msg], **kwargs):
        """Queue summary generation only within the current user's scope."""
        state = self._get_user_state()
        tasks = self._prune_summary_tasks(state["summary_tasks"])
        task = asyncio.create_task(
            self.summary_memory(messages=messages, **kwargs),
        )
        tasks.append(task)
        state["summary_tasks"] = tasks

    async def await_summary_tasks(self) -> str:
        """Wait for summary tasks in the current user's queue only."""
        state = self._get_user_state()
        tasks = list(state["summary_tasks"])
        result = ""
        for task in tasks:
            if task.done():
                if task.cancelled():
                    logger.warning("Summary task was cancelled.")
                    result += "Summary task was cancelled.\n"
                else:
                    exc = task.exception()
                    if exc is not None:
                        logger.error(f"Summary task failed: {exc}")
                        result += f"Summary task failed: {exc}\n"
                    else:
                        task_result = task.result()
                        logger.info(f"Summary task completed: {task_result}")
                        result += f"Summary task completed: {task_result}\n"
            else:
                try:
                    task_result = await task
                    logger.info(f"Summary task completed: {task_result}")
                    result += f"Summary task completed: {task_result}\n"
                except asyncio.CancelledError:
                    logger.warning("Summary task was cancelled while waiting.")
                    result += "Summary task was cancelled.\n"
                except Exception as e:
                    logger.exception(f"Summary task failed: {e}")
                    result += f"Summary task failed: {e}\n"
        state["summary_tasks"] = []
        return result

    # ------------------------------------------------------------------
    # BaseMemoryManager interface
    # ------------------------------------------------------------------

    async def start(self):
        """Start the ReMeLight lifecycle."""
        self._warn_if_version_mismatch()
        self._started = True
        for state in self._user_reme_states.values():
            if not state["started"]:
                await state["reme"].start()
                state["started"] = True
        return None

    async def close(self) -> bool:
        """Close ReMeLight and perform cleanup."""
        self._warn_if_version_mismatch()
        logger.info(
            f"ReMeLightMemoryManager closing: agent_id={self.agent_id}",
        )
        result = True
        for state in self._user_reme_states.values():
            for task in state["summary_tasks"]:
                if not task.done():
                    task.cancel()
            result = await state["reme"].close() and result
            state["started"] = False
            state["summary_tasks"] = []
        self._started = False
        logger.info(
            f"ReMeLightMemoryManager closed: "
            f"agent_id={self.agent_id}, result={result}",
        )
        return result

    async def compact_tool_result(self, **kwargs):
        """Compact tool results by truncating large outputs."""
        self._warn_if_version_mismatch()
        state = await self._ensure_user_state_started()
        return await state["reme"].compact_tool_result(**kwargs)

    async def check_context(self, **kwargs):
        """Check context size and determine if compaction is needed."""
        self._warn_if_version_mismatch()
        state = await self._ensure_user_state_started()
        return await state["reme"].check_context(**kwargs)

    async def compact_memory(
        self,
        messages: list[Msg],
        previous_summary: str = "",
        extra_instruction: str = "",
        **_kwargs,
    ) -> str:
        """Compact messages into a condensed summary.

        Returns the compacted string, or empty string on failure.
        """
        self._prepare_model_formatter()

        agent_config = load_agent_config(self.agent_id)
        cc = agent_config.running.context_compact
        state = await self._ensure_user_state_started()
        reme = state["reme"]

        if extra_instruction:
            result = await reme.compact_memory(
                messages=messages,
                as_llm=self.chat_model,
                as_llm_formatter=self.formatter,
                as_token_counter=get_copaw_token_counter(agent_config),
                language=agent_config.language,
                max_input_length=agent_config.running.max_input_length,
                compact_ratio=cc.memory_compact_ratio,
                previous_summary=previous_summary,
                return_dict=True,
                add_thinking_block=cc.compact_with_thinking_block,
                extra_instruction=extra_instruction,
            )
        else:
            # Compatible with older versions of ReMe
            result = await reme.compact_memory(
                messages=messages,
                as_llm=self.chat_model,
                as_llm_formatter=self.formatter,
                as_token_counter=get_copaw_token_counter(agent_config),
                language=agent_config.language,
                max_input_length=agent_config.running.max_input_length,
                compact_ratio=cc.memory_compact_ratio,
                previous_summary=previous_summary,
                return_dict=True,
                add_thinking_block=cc.compact_with_thinking_block,
            )

        if isinstance(result, str):
            logger.error(
                "compact_memory returned str instead of dict, "
                f"result: {result[:200]}... "
                "Please install the latest reme package.",
            )
            return result

        if not result.get("is_valid", True):
            unique_id = uuid.uuid4().hex[:8]
            filepath = os.path.join(
                self.working_dir,
                f"compact_invalid_{unique_id}.json",
            )
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                logger.error(
                    f"Invalid compact result saved to {filepath}. "
                    f"user_msg: {result.get('user_message', '')[:200]}..., "
                    "history_compact: "
                    f"{result.get('history_compact', '')[:200]}...",
                )
                logger.error(
                    "Please upload the log: "
                    "https://github.com/agentscope-ai/CoPaw/issues",
                )
            except Exception as _e:
                logger.error(f"Failed to save invalid compact result: {_e}")
            return ""

        return result.get("history_compact", "")

    async def summary_memory(self, messages: list[Msg], **_kwargs) -> str:
        """Generate a comprehensive summary of the given messages."""
        self._prepare_model_formatter()

        agent_config = load_agent_config(self.agent_id)
        cc = agent_config.running.context_compact
        state = await self._ensure_user_state_started()
        reme = state["reme"]

        set_current_workspace_dir(Path(self.working_dir))
        recent_max_bytes = (
            agent_config.running.tool_result_compact.recent_max_bytes
        )
        set_current_recent_max_bytes(recent_max_bytes)

        return await reme.summary_memory(
            messages=messages,
            as_llm=self.chat_model,
            as_llm_formatter=self.formatter,
            as_token_counter=get_copaw_token_counter(agent_config),
            toolkit=self.summary_toolkit,
            language=agent_config.language,
            max_input_length=agent_config.running.max_input_length,
            compact_ratio=cc.memory_compact_ratio,
            timezone=load_config().user_timezone or None,
            add_thinking_block=cc.compact_with_thinking_block,
        )

    async def memory_search(
        self,
        query: str,
        max_results: int = 5,
        min_score: float = 0.1,
    ) -> ToolResponse:
        """Search stored memories for relevant content."""
        self._warn_if_version_mismatch()
        state = await self._ensure_user_state_started()
        reme = state["reme"]
        if not getattr(reme, "_started", False):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="ReMe is not started, report github issue!",
                    ),
                ],
            )
        return await reme.memory_search(
            query=query,
            max_results=max_results,
            min_score=min_score,
        )

    def get_in_memory_memory(self, **_kwargs) -> "ReMeInMemoryMemory | None":
        """Retrieve the in-memory memory object with token counting support."""
        self._warn_if_version_mismatch()
        state = self._get_user_state()
        agent_config = load_agent_config(self.agent_id)
        return state["reme"].get_in_memory_memory(
            as_token_counter=get_copaw_token_counter(agent_config),
        )
