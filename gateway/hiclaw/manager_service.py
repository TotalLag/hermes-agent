import argparse
import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any, Dict, Optional

from gateway.hiclaw.lifecycle_logger import LifecycleLogger
from gateway.hiclaw.manager_handler import WorkerMessageParser
from gateway.hiclaw.manager_state import ManagerMode, ManagerState
from gateway.hiclaw.worker_registry import WorkerInfo, WorkerRegistry

logger = logging.getLogger(__name__)


class HiclawManager:
    def __init__(self, config_path: Optional[str] = None):
        self.config = self._load_config(config_path)
        state_dir = self.config["state_dir"]
        self.worker_registry = WorkerRegistry(state_dir=state_dir)
        self.manager_state = ManagerState(state_dir=state_dir)
        self.lifecycle_logger = LifecycleLogger(state_dir=state_dir)
        self.message_parser = WorkerMessageParser()
        self._running = False
        self._tasks = []
        self._stop_event = asyncio.Event()
        self._rr_index = 0
        self._matrix_client = None

    def _load_config(self, config_path: Optional[str]) -> Dict[str, Any]:
        state_dir = os.getenv("HICLAW_STATE_DIR", "~/.hermes/hiclaw")
        state_root = Path(state_dir).expanduser()
        default_sync_script = (
            Path(__file__).resolve().parents[2]
            / "hiclaw"
            / "scripts"
            / "hiclaw-sync.sh"
        )

        config: Dict[str, Any] = {
            "state_dir": str(state_root),
            "manager_id": os.getenv("HICLAW_MANAGER_ID", "hermes-manager"),
            "matrix_homeserver": os.getenv(
                "HICLAW_MATRIX_HOMESERVER", os.getenv("MATRIX_HOMESERVER", "")
            ),
            "matrix_access_token": os.getenv(
                "HICLAW_MATRIX_ACCESS_TOKEN", os.getenv("MATRIX_ACCESS_TOKEN", "")
            ),
            "matrix_user_id": os.getenv(
                "HICLAW_MATRIX_USER_ID", os.getenv("MATRIX_USER_ID", "")
            ),
            "matrix_password": os.getenv(
                "HICLAW_MATRIX_PASSWORD", os.getenv("MATRIX_PASSWORD", "")
            ),
            "manager_room_id": os.getenv("HICLAW_MANAGER_ROOM_ID", ""),
            "sync_script": str(default_sync_script),
            "task_specs_dir": str(state_root / "task-specs"),
            "task_results_dir": str(state_root / "task-results"),
            "dispatcher_interval": int(os.getenv("HICLAW_DISPATCHER_INTERVAL", "30")),
            "minio_sync_interval": int(os.getenv("HICLAW_MINIO_SYNC_INTERVAL", "60")),
        }

        if config_path:
            p = Path(config_path).expanduser()
            if p.exists():
                if p.suffix.lower() == ".json":
                    try:
                        file_config = json.loads(p.read_text())
                        if isinstance(file_config, dict):
                            config.update(file_config)
                    except Exception as exc:
                        logger.warning("Failed to read manager config %s: %s", p, exc)
                else:
                    try:
                        for raw_line in p.read_text().splitlines():
                            line = raw_line.strip()
                            if not line or line.startswith("#") or "=" not in line:
                                continue
                            key, value = line.split("=", 1)
                            env_key = key.strip()
                            env_val = value.strip().strip('"').strip("'")
                            os.environ.setdefault(env_key, env_val)
                    except Exception as exc:
                        logger.warning("Failed to load env file %s: %s", p, exc)

        env_overrides = {
            "state_dir": os.getenv("HICLAW_STATE_DIR", config["state_dir"]),
            "manager_id": os.getenv("HICLAW_MANAGER_ID", config["manager_id"]),
            "matrix_homeserver": os.getenv(
                "HICLAW_MATRIX_HOMESERVER",
                os.getenv("MATRIX_HOMESERVER", config["matrix_homeserver"]),
            ),
            "matrix_access_token": os.getenv(
                "HICLAW_MATRIX_ACCESS_TOKEN",
                os.getenv("MATRIX_ACCESS_TOKEN", config["matrix_access_token"]),
            ),
            "matrix_user_id": os.getenv(
                "HICLAW_MATRIX_USER_ID",
                os.getenv("MATRIX_USER_ID", config["matrix_user_id"]),
            ),
            "matrix_password": os.getenv(
                "HICLAW_MATRIX_PASSWORD",
                os.getenv("MATRIX_PASSWORD", config["matrix_password"]),
            ),
            "manager_room_id": os.getenv(
                "HICLAW_MANAGER_ROOM_ID", config["manager_room_id"]
            ),
            "sync_script": os.getenv("HICLAW_SYNC_SCRIPT", config["sync_script"]),
            "task_specs_dir": os.getenv(
                "HICLAW_TASK_SPECS_DIR", config["task_specs_dir"]
            ),
            "task_results_dir": os.getenv(
                "HICLAW_TASK_RESULTS_DIR", config["task_results_dir"]
            ),
        }
        config.update(env_overrides)

        try:
            config["dispatcher_interval"] = int(
                os.getenv(
                    "HICLAW_DISPATCHER_INTERVAL",
                    str(config["dispatcher_interval"]),
                )
            )
        except ValueError:
            pass
        try:
            config["minio_sync_interval"] = int(
                os.getenv(
                    "HICLAW_MINIO_SYNC_INTERVAL",
                    str(config["minio_sync_interval"]),
                )
            )
        except ValueError:
            pass

        config["state_dir"] = str(Path(config["state_dir"]).expanduser())
        config["task_specs_dir"] = str(Path(config["task_specs_dir"]).expanduser())
        config["task_results_dir"] = str(Path(config["task_results_dir"]).expanduser())
        config["sync_script"] = str(Path(config["sync_script"]).expanduser())
        return config

    async def start(self):
        if self._running:
            return

        self._running = True
        self._stop_event.clear()
        await self.manager_state.set_mode(ManagerMode.DISPATCHING)
        self.lifecycle_logger.log_event(
            self.config["manager_id"],
            "manager",
            "manager_started",
            {"mode": ManagerMode.DISPATCHING.value},
        )

        self._tasks = [
            asyncio.create_task(self._matrix_listener(), name="hiclaw-matrix-listener"),
            asyncio.create_task(
                self._task_dispatcher(self.config["dispatcher_interval"]),
                name="hiclaw-task-dispatcher",
            ),
            asyncio.create_task(
                self._minio_sync(self.config["minio_sync_interval"]),
                name="hiclaw-minio-sync",
            ),
        ]

        try:
            await self._stop_event.wait()
        finally:
            if self._running:
                await self.stop()

    async def stop(self):
        if not self._running and not self._tasks:
            return

        self._running = False
        self._stop_event.set()
        await self.manager_state.set_mode(ManagerMode.IDLE)
        self.lifecycle_logger.log_event(
            self.config["manager_id"],
            "manager",
            "manager_stopped",
            {"mode": ManagerMode.IDLE.value},
        )

        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

        if self._matrix_client is not None:
            try:
                await self._matrix_client.close()
            except Exception as exc:
                logger.warning("Error closing Matrix client: %s", exc)
            finally:
                self._matrix_client = None

    async def _matrix_listener(self):
        try:
            nio = __import__("nio")
        except Exception as exc:
            logger.warning("matrix-nio unavailable for manager listener: %s", exc)
            while self._running:
                await self._sleep_or_stop(10)
            return

        homeserver = self.config.get("matrix_homeserver", "").rstrip("/")
        if not homeserver:
            logger.warning("Matrix listener disabled: missing matrix_homeserver")
            while self._running:
                await self._sleep_or_stop(10)
            return

        manager_user = self.config.get("matrix_user_id", "")
        client = nio.AsyncClient(homeserver, manager_user)
        self._matrix_client = client

        try:
            if self.config.get("matrix_access_token"):
                client.access_token = self.config["matrix_access_token"]
                if not manager_user:
                    whoami = await client.whoami()
                    if isinstance(whoami, nio.WhoamiResponse):
                        manager_user = whoami.user_id
                        client.user_id = whoami.user_id
                        self.config["matrix_user_id"] = whoami.user_id
                else:
                    client.user_id = manager_user
            elif self.config.get("matrix_user_id") and self.config.get(
                "matrix_password"
            ):
                login = await client.login(
                    self.config["matrix_password"],
                    device_name="Hiclaw Manager",
                )
                if not isinstance(login, nio.LoginResponse):
                    logger.error(
                        "Matrix login failed: %s",
                        getattr(login, "message", str(login)),
                    )
                    return
            else:
                logger.warning("Matrix listener disabled: no token or user/password")
                while self._running:
                    await self._sleep_or_stop(10)
                return

            async def on_message(room, event):
                if not self._running:
                    return
                if getattr(event, "sender", "") == self.config.get("matrix_user_id"):
                    return

                manager_room = self.config.get("manager_room_id")
                if manager_room and getattr(room, "room_id", "") != manager_room:
                    return

                body = getattr(event, "body", "") or ""
                if not body:
                    return

                parsed = self._parse_worker_message(body)
                if not parsed:
                    return
                await self._handle_worker_message(
                    parsed, getattr(room, "room_id", None)
                )

            client.add_event_callback(on_message, nio.RoomMessageText)

            while self._running:
                try:
                    await client.sync(timeout=30000)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Matrix sync error: %s", exc)
                    await self._sleep_or_stop(5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Matrix listener crashed: %s", exc)
        finally:
            if self._matrix_client is client:
                self._matrix_client = None
            try:
                await client.close()
            except Exception:
                pass

    def _parse_worker_message(self, content: str) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None

        msg_type = payload.get("type", "")

        if msg_type == "worker_register" or (
            ("worker_id" in payload or "id" in payload)
            and "capabilities" in payload
            and "matrix_user_id" in payload
        ):
            reg_payload = {
                "worker_id": payload.get("worker_id") or payload.get("id"),
                "name": payload.get("name") or payload.get("worker"),
                "capabilities": payload.get("capabilities", []),
                "version": payload.get("version", "unknown"),
                "matrix_user_id": payload.get("matrix_user_id", ""),
                "device_id": payload.get("device_id", ""),
                "status": payload.get("status", "registered"),
            }
            return {"event": "worker_register", "payload": reg_payload}

        reg = self.message_parser.parse_registration(content)
        if reg:
            return {
                "event": "worker_register",
                "payload": {
                    "worker_id": reg.get("id"),
                    "name": reg.get("name"),
                    "capabilities": reg.get("capabilities", []),
                    "version": reg.get("version", "unknown"),
                    "matrix_user_id": reg.get("matrix_user_id", ""),
                    "device_id": reg.get("device_id", ""),
                    "status": reg.get("status", "registered"),
                },
            }

        if msg_type == "heartbeat" or payload.get("heartbeat") is True:
            return {
                "event": "heartbeat",
                "payload": {
                    "worker_id": payload.get("worker_id") or payload.get("id"),
                    "worker": payload.get("worker"),
                },
            }

        if msg_type in {"task_result", "task_completed"} or (
            "task_id" in payload and ("result_path" in payload or "result" in payload)
        ):
            return {
                "event": "task_result",
                "payload": {
                    "task_id": payload.get("task_id"),
                    "worker_id": payload.get("worker_id"),
                    "worker": payload.get("worker"),
                    "result_path": payload.get("result_path")
                    or payload.get("result", {}).get("result_path", ""),
                },
            }

        if "status" in payload and ("worker" in payload or "worker_id" in payload):
            return {
                "event": "status_update",
                "payload": {
                    "worker_id": payload.get("worker_id") or payload.get("id"),
                    "worker": payload.get("worker"),
                    "status": payload.get("status"),
                    "message": payload.get("message"),
                },
            }

        status = self.message_parser.parse_status(content)
        if status:
            status_value, worker_name, message = status
            return {
                "event": "status_update",
                "payload": {
                    "worker": worker_name,
                    "status": status_value,
                    "message": message,
                },
            }

        return None

    async def _handle_worker_message(
        self, message: Dict[str, Any], room_id: Optional[str]
    ):
        event = message["event"]
        payload = message["payload"]

        if event == "worker_register":
            worker_id = payload.get("worker_id")
            if not worker_id:
                logger.debug("Registration without worker_id ignored: %s", payload)
                return
            worker = await self.worker_registry.register_worker(
                worker_id=worker_id,
                name=payload.get("name") or worker_id,
                capabilities=list(payload.get("capabilities", [])),
                version=payload.get("version", "unknown"),
                matrix_user_id=payload.get("matrix_user_id", ""),
                device_id=payload.get("device_id", ""),
                room_id=room_id or "",
            )
            self.lifecycle_logger.log_registration(
                worker.id,
                worker.name,
                worker.capabilities,
                worker.version,
            )
            initial_status = payload.get("status", "registered")
            if initial_status != "registered":
                await self.worker_registry.update_status(worker.id, initial_status)
                self.lifecycle_logger.log_status_change(
                    worker.id,
                    worker.name,
                    "registered",
                    initial_status,
                )
            return

        worker_id = payload.get("worker_id")
        if not worker_id and payload.get("worker"):
            worker_id = await self._resolve_worker_id_from_name(payload.get("worker"))

        if event == "status_update":
            if not worker_id:
                logger.debug("Status update for unknown worker: %s", payload)
                return
            worker = await self.worker_registry.get_worker(worker_id)
            old_status = worker.status if worker else "unknown"
            new_status = payload.get("status", old_status)
            updated = await self.worker_registry.update_status(
                worker_id,
                new_status,
                payload.get("message"),
            )
            if updated:
                self.lifecycle_logger.log_status_change(
                    updated.id,
                    updated.name,
                    old_status,
                    updated.status,
                    payload.get("message"),
                )
            return

        if event == "heartbeat":
            if not worker_id:
                return
            worker = await self.worker_registry.heartbeat(worker_id)
            if worker:
                self.lifecycle_logger.log_event(worker.id, worker.name, "heartbeat", {})
            return

        if event == "task_result":
            task_id = payload.get("task_id")
            result_path = payload.get("result_path") or ""
            if not task_id:
                return
            task = await self.manager_state.complete_task(task_id, result_path)
            if task and worker_id:
                await self.worker_registry.update_status(worker_id, "ready")
                worker = await self.worker_registry.get_worker(worker_id)
                if worker:
                    self.lifecycle_logger.log_event(
                        worker.id,
                        worker.name,
                        "task_completed",
                        {"task_id": task_id, "result_path": result_path},
                    )

    async def _resolve_worker_id_from_name(self, worker_name: str) -> Optional[str]:
        for worker in await self.worker_registry.list_workers():
            if worker.name == worker_name or worker.id == worker_name:
                return worker.id
        return None

    async def _task_dispatcher(self, interval: int = 30):
        while self._running:
            try:
                await self._discover_task_specs()

                pending_tasks = await self.manager_state.list_tasks(status="pending")
                ready_workers = await self.worker_registry.list_workers(status="ready")

                if pending_tasks and ready_workers:
                    available_workers = list(ready_workers)
                    for task in pending_tasks:
                        if not available_workers:
                            break

                        worker = available_workers[
                            self._rr_index % len(available_workers)
                        ]
                        self._rr_index += 1

                        assigned = await self.manager_state.assign_task(
                            task.id, worker.id
                        )
                        if not assigned:
                            continue

                        sent = await self.send_message_to_worker(
                            worker.id,
                            {
                                "type": "task_assign",
                                "task_id": task.id,
                                "spec_path": task.spec_path,
                            },
                        )
                        if sent:
                            available_workers = [
                                w for w in available_workers if w.id != worker.id
                            ]
                            await self.worker_registry.update_status(
                                worker.id,
                                "busy",
                                f"Assigned task {task.id}",
                            )
                            self.lifecycle_logger.log_event(
                                worker.id,
                                worker.name,
                                "task_assigned",
                                {"task_id": task.id, "spec_path": task.spec_path},
                            )
                        else:
                            await self.manager_state.fail_task(
                                task.id,
                                f"Failed to send task to worker {worker.id}",
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Task dispatcher error: %s", exc)

            await self._sleep_or_stop(interval)

    async def _discover_task_specs(self):
        specs_dir = Path(self.config["task_specs_dir"]).expanduser()
        if not specs_dir.exists():
            return

        for spec_file in specs_dir.rglob("spec.json"):
            task_id = spec_file.parent.name
            try:
                data = json.loads(spec_file.read_text())
                task_id = data.get("task_id") or task_id
            except Exception:
                pass

            existing = await self.manager_state.get_task(task_id)
            if existing is None:
                await self.manager_state.add_task(task_id, str(spec_file))
                self.lifecycle_logger.log_event(
                    self.config["manager_id"],
                    "manager",
                    "task_discovered",
                    {"task_id": task_id, "spec_path": str(spec_file)},
                )

    async def _minio_sync(self, interval: int = 60):
        while self._running:
            try:
                await self._run_sync_command("pull-specs")
                await self._discover_task_specs()
                await self._run_sync_command("push-results")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("MinIO sync error: %s", exc)

            await self._sleep_or_stop(interval)

    async def _run_sync_command(self, action: str):
        script = Path(self.config["sync_script"]).expanduser()
        if not script.exists():
            logger.warning("Sync script not found: %s", script)
            return

        process = await asyncio.create_subprocess_exec(
            "bash",
            str(script),
            action,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning(
                "Sync command failed (%s): %s",
                action,
                stderr.decode().strip() or stdout.decode().strip(),
            )
        else:
            log_line = stdout.decode().strip()
            if log_line:
                logger.info("Sync command success (%s): %s", action, log_line)

    async def send_message_to_worker(self, worker_id: str, message: dict) -> bool:
        worker = await self.worker_registry.get_worker(worker_id)
        if worker is None:
            logger.warning("Cannot send message: unknown worker %s", worker_id)
            return False

        if self._matrix_client is None:
            logger.warning("Cannot send message: Matrix client not connected")
            return False

        room_id = await self._resolve_room_for_worker(worker)
        if not room_id:
            logger.warning(
                "Cannot send message: no room available for worker %s", worker_id
            )
            return False

        try:
            nio = __import__("nio")

            content = {
                "msgtype": "m.text",
                "body": json.dumps(message),
            }
            resp = await self._matrix_client.room_send(
                room_id, "m.room.message", content
            )
            if isinstance(resp, nio.RoomSendResponse):
                return True
            logger.warning(
                "Failed to send Matrix message to %s: %s",
                worker_id,
                getattr(resp, "message", str(resp)),
            )
            return False
        except Exception as exc:
            logger.warning("Error sending Matrix message to %s: %s", worker_id, exc)
            return False

    async def _resolve_room_for_worker(self, worker: WorkerInfo) -> Optional[str]:
        if worker.room_id:
            return worker.room_id

        manager_room_id = self.config.get("manager_room_id")
        if manager_room_id:
            return manager_room_id

        if self._matrix_client is None or not worker.matrix_user_id:
            return None

        try:
            nio = __import__("nio")

            create_resp = await self._matrix_client.room_create(
                is_direct=True,
                invite=[worker.matrix_user_id],
                preset="trusted_private_chat",
            )
            if isinstance(create_resp, nio.RoomCreateResponse):
                worker.room_id = create_resp.room_id
                await self.worker_registry.update_status(worker.id, worker.status)
                return create_resp.room_id
        except Exception as exc:
            logger.warning("Failed to create DM with worker %s: %s", worker.id, exc)

        return None

    async def get_status(self) -> dict:
        mode = await self.manager_state.get_mode()
        workers = await self.worker_registry.list_workers()
        tasks = await self.manager_state.list_tasks()
        stats = await self.manager_state.get_stats()

        worker_counts: Dict[str, int] = {}
        for worker in workers:
            worker_counts[worker.status] = worker_counts.get(worker.status, 0) + 1

        task_counts: Dict[str, int] = {}
        for task in tasks:
            task_counts[task.status] = task_counts.get(task.status, 0) + 1

        return {
            "mode": mode.value,
            "running": self._running,
            "workers": {
                "total": len(workers),
                "by_status": worker_counts,
            },
            "tasks": {
                "total": len(tasks),
                "by_status": task_counts,
            },
            "stats": stats,
        }

    async def _sleep_or_stop(self, seconds: int):
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return


class ManagerCLI:
    def __init__(self):
        parser = argparse.ArgumentParser(prog="hiclaw-manager")
        parser.add_argument(
            "action",
            choices=["start", "stop", "status", "workers", "tasks"],
            help="Manager action",
        )
        parser.add_argument(
            "--config",
            default=None,
            help="Optional path to JSON or env-style config file",
        )
        parser.add_argument("--status", default=None, help="Optional status filter")
        self.parser = parser

    async def run(self, argv=None):
        args = self.parser.parse_args(argv)
        manager = HiclawManager(config_path=args.config)

        if args.action == "start":
            loop = asyncio.get_running_loop()
            for sig_name in ("SIGINT", "SIGTERM"):
                sig = getattr(signal, sig_name, None)
                if sig is None:
                    continue
                try:
                    loop.add_signal_handler(
                        sig,
                        lambda m=manager: asyncio.create_task(m.stop()),
                    )
                except NotImplementedError:
                    pass
            await manager.start()
            return 0

        if args.action == "stop":
            await manager.stop()
            print(json.dumps({"success": True, "message": "manager stop requested"}))
            return 0

        if args.action == "status":
            print(json.dumps(await manager.get_status(), indent=2))
            return 0

        if args.action == "workers":
            workers = await manager.worker_registry.list_workers(status=args.status)
            print(
                json.dumps(
                    {
                        "workers": [
                            {
                                "id": w.id,
                                "name": w.name,
                                "status": w.status,
                                "capabilities": w.capabilities,
                                "matrix_user_id": w.matrix_user_id,
                                "last_seen_at": w.last_seen_at,
                            }
                            for w in workers
                        ],
                        "count": len(workers),
                    },
                    indent=2,
                )
            )
            return 0

        if args.action == "tasks":
            tasks = await manager.manager_state.list_tasks(status=args.status)
            print(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": t.id,
                                "status": t.status,
                                "assigned_worker": t.assigned_worker,
                                "spec_path": t.spec_path,
                                "result_path": t.result_path,
                                "updated_at": t.updated_at,
                            }
                            for t in tasks
                        ],
                        "count": len(tasks),
                    },
                    indent=2,
                )
            )
            return 0

        return 1


async def cli(argv=None):
    logging.basicConfig(
        level=os.getenv("HICLAW_MANAGER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    manager_cli = ManagerCLI()
    return await manager_cli.run(argv=argv)


async def main():
    await cli()


if __name__ == "__main__":
    asyncio.run(main())
