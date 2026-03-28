from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from gateway.config import Platform
from gateway.hiclaw.protocol import WorkerMessageParser
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path.home() / ".hermes" / "hiclaw" / "task-results"
_SERVER_FILES = {
    "worker_registry": Path(__file__).resolve().parents[2]
    / "mcp-servers"
    / "worker-registry"
    / "server.py",
    "task_queue": Path(__file__).resolve().parents[2]
    / "mcp-servers"
    / "task-queue"
    / "server.py",
}
_TOOL_SERVER_MAP = {
    "wr_register": "worker_registry",
    "wr_heartbeat": "worker_registry",
    "wr_update_status": "worker_registry",
    "tq_start": "task_queue",
    "tq_complete": "task_queue",
    "tq_fail": "task_queue",
}
_HEARTBEAT_STATUSES = {"heartbeat", "alive", "ok", "ping"}


if TYPE_CHECKING:
    from gateway.run import GatewayRunner as Gateway


class HiClawManagerError(RuntimeError):
    pass


class HiClawManagerHandler:
    def __init__(self, app: "Gateway") -> None:
        self.app = app
        self._loaded_servers: dict[str, Any] = {}

    @staticmethod
    def is_hiclaw_message(content: str) -> bool:
        return bool(
            WorkerMessageParser.is_worker_message(content)
            or WorkerMessageParser.parse_task_result(content) is not None
        )

    async def handle_worker_message(
        self, content: str, sender: str, room_id: str
    ) -> bool:
        if not self.is_hiclaw_message(content):
            return False

        stripped = (content or "").strip()
        try:
            if WorkerMessageParser.parse_registration(content) is not None:
                await self._handle_registration(content, sender)
                return True

            if WorkerMessageParser.parse_status(content) is not None:
                await self._handle_status_update(content, sender)
                return True

            if stripped.startswith("//heartbeat"):
                await self._handle_heartbeat(content, sender)
                return True

            if stripped.startswith("//task-result"):
                await self._handle_task_result(content, sender, room_id)
                return True

            logger.warning(
                "hiClaw: recognized but unroutable worker payload from %s", sender
            )
            return True
        except Exception as exc:
            logger.exception("hiClaw: failed handling worker message from %s", sender)
            await self._send_worker_notice(
                room_id,
                f"⚠️ hiClaw manager failed to process a worker message from {sender}: {exc}",
            )
            return True

    async def _handle_registration(self, content: str, sender: str) -> None:
        registration = WorkerMessageParser.parse_registration(content)
        if registration is None:
            raise HiClawManagerError("Malformed worker registration payload")

        worker_id = str(registration["id"]).strip()
        if (
            registration.get("matrix_user_id")
            and registration["matrix_user_id"] != sender
        ):
            logger.warning(
                "hiClaw: registration sender mismatch for %s (payload=%s sender=%s)",
                worker_id,
                registration["matrix_user_id"],
                sender,
            )

        result = await self._call_mcp_tool(
            "wr_register",
            {
                "worker_id": worker_id,
                "name": registration["name"],
                "capabilities": registration["capabilities"],
                "version": registration["version"],
                "matrix_user_id": registration["matrix_user_id"],
                "device_id": registration["device_id"],
            },
        )

        message = str(result.get("message", ""))
        if result.get("status") == "error" and "already registered" in message.lower():
            logger.info(
                "hiClaw: worker %s already registered, refreshing heartbeat", worker_id
            )
            await self._call_mcp_tool("wr_heartbeat", {"worker_id": worker_id})
            return

        self._ensure_tool_success("wr_register", result)

    async def _handle_status_update(self, content: str, sender: str) -> None:
        parsed = WorkerMessageParser.parse_status(content)
        if parsed is None:
            raise HiClawManagerError("Malformed worker status payload")

        status, worker_id, message = parsed
        normalized_status = status.strip().lower()
        if normalized_status in _HEARTBEAT_STATUSES:
            result = await self._call_mcp_tool("wr_heartbeat", {"worker_id": worker_id})
            self._ensure_tool_success("wr_heartbeat", result)
            return

        result = await self._call_mcp_tool(
            "wr_update_status",
            {
                "worker_id": worker_id,
                "status": normalized_status,
                "message": message or f"status update from {sender}",
            },
        )
        self._ensure_tool_success("wr_update_status", result)

    async def _handle_heartbeat(self, content: str, sender: str) -> None:
        heartbeat = WorkerMessageParser.parse_heartbeat(content)
        if heartbeat is None:
            raise HiClawManagerError("Malformed worker heartbeat payload")

        worker_id = str(heartbeat["worker"]).strip() or sender
        result = await self._call_mcp_tool("wr_heartbeat", {"worker_id": worker_id})
        self._ensure_tool_success("wr_heartbeat", result)

    async def _handle_task_result(
        self, content: str, sender: str, room_id: str
    ) -> None:
        task_result = WorkerMessageParser.parse_task_result(content)
        if task_result is None:
            raise HiClawManagerError("Malformed task result payload")

        task_id = str(task_result["task_id"]).strip()
        status = str(task_result["status"]).strip().lower()
        body = str(task_result.get("body", "")).strip()

        if status == "completed":
            result_path = await asyncio.to_thread(
                self._write_task_result_file,
                task_id,
                sender,
                room_id,
                body,
            )
            result = await self._call_task_queue_completion(task_id, result_path)
            self._ensure_tool_success("tq_complete", result)
        elif status == "failed":
            result = await self._call_task_queue_failure(
                task_id, body or "Worker reported failure"
            )
            self._ensure_tool_success("tq_fail", result)
        else:
            raise HiClawManagerError(f"Unsupported task-result status: {status}")

        await self._forward_task_result_to_agent(
            task_id=task_id,
            status=status,
            body=body,
            sender=sender,
            room_id=room_id,
        )

    async def _call_task_queue_completion(
        self, task_id: str, result_path: str
    ) -> dict[str, Any]:
        result = await self._call_mcp_tool(
            "tq_complete", {"task_id": task_id, "result_path": result_path}
        )
        if self._needs_task_start(result):
            await self._call_mcp_tool("tq_start", {"task_id": task_id})
            result = await self._call_mcp_tool(
                "tq_complete", {"task_id": task_id, "result_path": result_path}
            )
        return result

    async def _call_task_queue_failure(
        self, task_id: str, error: str
    ) -> dict[str, Any]:
        result = await self._call_mcp_tool(
            "tq_fail", {"task_id": task_id, "error": error}
        )
        if self._needs_task_start(result):
            await self._call_mcp_tool("tq_start", {"task_id": task_id})
            result = await self._call_mcp_tool(
                "tq_fail", {"task_id": task_id, "error": error}
            )
        return result

    async def _forward_task_result_to_agent(
        self,
        *,
        task_id: str,
        status: str,
        body: str,
        sender: str,
        room_id: str,
    ) -> None:
        message_text = self._format_forwarded_task_result(
            task_id=task_id,
            status=status,
            body=body,
            sender=sender,
        )
        event = MessageEvent(
            text=message_text,
            message_type=MessageType.TEXT,
            source=SessionSource(
                platform=Platform.MATRIX,
                chat_id=room_id,
                chat_type="dm",
                user_id=sender,
                user_name=sender,
            ),
            raw_message={
                "hiclaw": True,
                "forwarded": True,
                "worker_id": sender,
                "task_id": task_id,
                "task_status": status,
                "original_content": body,
            },
        )

        handler = cast(Callable[..., Any], getattr(self.app, "_handle_message", None))
        if callable(handler):
            await handler(event)
            return

        session_store = getattr(self.app, "session_store", None)
        if session_store is not None:
            session_entry = session_store.get_or_create_session(event.source)
            session_store.append_to_transcript(
                session_entry.session_id,
                {"role": "user", "content": message_text},
            )
            return

        logger.warning(
            "hiClaw: unable to forward task result for %s to the agent", task_id
        )

    async def _call_mcp_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        tool_caller = cast(Callable[..., Any], getattr(self.app, "mcp_tool", None))
        if callable(tool_caller):
            try:
                result = await tool_caller(
                    tool_name=tool_name, arguments=arguments, task_id=None
                )
                return self._coerce_tool_result(tool_name, result)
            except TypeError:
                logger.debug(
                    "hiClaw: app.mcp_tool signature mismatch, falling back to local server"
                )
            except Exception:
                logger.exception(
                    "hiClaw: app.mcp_tool failed for %s; falling back to local server",
                    tool_name,
                )

        server_name = _TOOL_SERVER_MAP.get(tool_name)
        if not server_name:
            raise HiClawManagerError(
                f"No hiClaw MCP server mapping for tool {tool_name}"
            )

        module = self._load_server_module(server_name)
        handler = getattr(module, tool_name, None)
        if not callable(handler):
            raise HiClawManagerError(
                f"Tool {tool_name} not found in {server_name} server"
            )

        result = await asyncio.to_thread(handler, **arguments)
        return self._coerce_tool_result(tool_name, result)

    def _load_server_module(self, server_name: str) -> Any:
        cached = self._loaded_servers.get(server_name)
        if cached is not None:
            return cached

        path = _SERVER_FILES[server_name]
        spec = importlib.util.spec_from_file_location(
            f"gateway.hiclaw.{server_name}_server", path
        )
        if spec is None or spec.loader is None:
            raise HiClawManagerError(f"Unable to load hiClaw server module from {path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._loaded_servers[server_name] = module
        return module

    async def _send_worker_notice(self, room_id: str, message: str) -> None:
        adapters = getattr(self.app, "adapters", {}) or {}
        matrix_adapter = adapters.get(Platform.MATRIX)
        if matrix_adapter is None:
            return
        try:
            await matrix_adapter.send(room_id, message)
        except Exception:
            logger.exception("hiClaw: failed sending Matrix notice to %s", room_id)

    @staticmethod
    def _coerce_tool_result(tool_name: str, result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError as exc:
                raise HiClawManagerError(
                    f"Tool {tool_name} returned non-JSON: {result}"
                ) from exc
            if isinstance(parsed, dict):
                return parsed
            raise HiClawManagerError(
                f"Tool {tool_name} returned non-object JSON: {parsed!r}"
            )
        raise HiClawManagerError(
            f"Tool {tool_name} returned unsupported type: {type(result).__name__}"
        )

    @staticmethod
    def _ensure_tool_success(tool_name: str, result: dict[str, Any]) -> None:
        if result.get("error"):
            raise HiClawManagerError(f"{tool_name} failed: {result['error']}")
        if result.get("status") == "error":
            raise HiClawManagerError(
                f"{tool_name} failed: {result.get('message', 'unknown error')}"
            )

    @staticmethod
    def _needs_task_start(result: dict[str, Any]) -> bool:
        error = str(result.get("error", ""))
        return "must be 'running'" in error.lower()

    @staticmethod
    def _write_task_result_file(
        task_id: str, sender: str, room_id: str, body: str
    ) -> str:
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _RESULTS_DIR / f"{task_id}.md"
        timestamp = datetime.now(timezone.utc).isoformat()
        contents = (
            f"# hiClaw Task Result\n\n"
            f"- Task ID: {task_id}\n"
            f"- Worker: {sender}\n"
            f"- Room: {room_id}\n"
            f"- Received At: {timestamp}\n\n"
            f"## Result\n\n{body or '_No body provided._'}\n"
        )
        path.write_text(contents, encoding="utf-8")
        return str(path)

    @staticmethod
    def _format_forwarded_task_result(
        *, task_id: str, status: str, body: str, sender: str
    ) -> str:
        heading = "completed" if status == "completed" else "failed"
        details = body.strip() or "(no details provided)"
        return (
            f"hiClaw worker result from {sender}\n\n"
            f"Task ID: {task_id}\n"
            f"Status: {heading}\n\n"
            f"{details}"
        )
