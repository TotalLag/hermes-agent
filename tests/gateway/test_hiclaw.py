"""Tests for gateway.hiclaw components."""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from gateway.hiclaw.manager_handler import WorkerMessageParser
from gateway.hiclaw.worker_registry import WorkerRegistry, Worker
from gateway.hiclaw.docker_manager import (
    DockerManager,
    WorkerContainer,
    ContainerStatus,
)


class TestWorkerMessageParser:
    def test_parse_heartbeat_valid(self):
        content = """//heartbeat
Worker: worker-copilot-01
Status: busy
Last completed task: task-42
Current task: task-43
Progress: 75%"""
        result = WorkerMessageParser.parse_heartbeat(content)
        assert result is not None
        assert result["worker_name"] == "worker-copilot-01"
        assert result["status"] == "busy"
        assert result["last_completed_task"] == "task-42"
        assert result["current_task"] == "task-43"
        assert result["progress"] == 75

    def test_parse_heartbeat_minimal(self):
        content = """//heartbeat
Worker: worker-1
Status: ready"""
        result = WorkerMessageParser.parse_heartbeat(content)
        assert result is not None
        assert result["worker_name"] == "worker-1"
        assert result["status"] == "ready"
        assert result["progress"] is None

    def test_parse_heartbeat_missing_worker(self):
        content = """//heartbeat
Status: ready"""
        assert WorkerMessageParser.parse_heartbeat(content) is None

    def test_parse_heartbeat_wrong_prefix(self):
        assert (
            WorkerMessageParser.parse_heartbeat("//status\nWorker: x\nStatus: y")
            is None
        )

    def test_parse_heartbeat_none(self):
        assert WorkerMessageParser.parse_heartbeat(None) is None

    def test_parse_registration_valid(self):
        content = json.dumps(
            {
                "id": "worker-1",
                "name": "worker-copilot-01",
                "capabilities": ["code", "debug"],
                "version": "1.0.0",
                "matrix_user_id": "@worker1:hermes.local",
                "device_id": "device123",
            }
        )
        result = WorkerMessageParser.parse_registration(content)
        assert result is not None
        assert result["id"] == "worker-1"
        assert result["name"] == "worker-copilot-01"
        assert result["capabilities"] == ["code", "debug"]
        assert result["version"] == "1.0.0"
        assert result["matrix_user_id"] == "@worker1:hermes.local"
        assert result["device_id"] == "device123"

    def test_parse_registration_missing_required(self):
        content = json.dumps({"id": "w1", "name": "x"})
        assert WorkerMessageParser.parse_registration(content) is None

    def test_parse_registration_invalid_json(self):
        assert WorkerMessageParser.parse_registration("not json") is None

    def test_parse_registration_none(self):
        assert WorkerMessageParser.parse_registration(None) is None

    def test_parse_status_valid(self):
        content = json.dumps(
            {
                "worker": "worker-1",
                "status": "busy",
                "message": "processing",
            }
        )
        result = WorkerMessageParser.parse_status(content)
        assert result is not None
        assert result == ("worker-1", "busy", "processing")

    def test_parse_status_missing_fields(self):
        content = json.dumps({"worker": "w1"})
        assert WorkerMessageParser.parse_status(content) is None

    def test_parse_status_invalid_json(self):
        assert WorkerMessageParser.parse_status("{bad") is None

    def test_parse_task_assign_valid(self):
        content = """//task-assign
Task ID: task-99
Spec file: /specs/pr-1234.yaml
Write a test for the new feature."""
        result = WorkerMessageParser.parse_task_assign(content)
        assert result is not None
        assert result["task_id"] == "task-99"
        assert result["spec_path"] == "/specs/pr-1234.yaml"
        assert "test for the new feature" in result["description"]

    def test_parse_task_assign_no_spec(self):
        content = """//task-assign
Task ID: task-100
Just do the thing."""
        result = WorkerMessageParser.parse_task_assign(content)
        assert result is not None
        assert result["task_id"] == "task-100"
        assert result["spec_path"] is None
        assert "thing" in result["description"]

    def test_parse_task_assign_missing_task_id(self):
        content = """//task-assign
Spec file: /x.yaml
description"""
        assert WorkerMessageParser.parse_task_assign(content) is None

    def test_parse_task_assign_wrong_prefix(self):
        assert (
            WorkerMessageParser.parse_task_assign("//task-result\nTask ID: x") is None
        )

    def test_parse_task_result_valid(self):
        content = """//task-result
Task ID: task-42
Status: completed
All tests pass. 47 assertions verified."""
        result = WorkerMessageParser.parse_task_result(content)
        assert result is not None
        assert result["task_id"] == "task-42"
        assert result["status"] == "completed"
        assert "tests pass" in result["result_text"]

    def test_parse_task_result_failed(self):
        content = """//task-result
Task ID: task-43
Status: failed
TypeError: cannot read property of undefined"""
        result = WorkerMessageParser.parse_task_result(content)
        assert result is not None
        assert result["task_id"] == "task-43"
        assert result["status"] == "failed"

    def test_parse_task_result_invalid_status(self):
        content = """//task-result
Task ID: x
Status: pending"""
        assert WorkerMessageParser.parse_task_result(content) is None

    def test_parse_task_result_missing_fields(self):
        content = """//task-result
Task ID: x"""
        assert WorkerMessageParser.parse_task_result(content) is None

    def test_detect_message_type_heartbeat(self):
        assert (
            WorkerMessageParser.detect_message_type("//heartbeat\nWorker: x")
            == "heartbeat"
        )

    def test_detect_message_type_task_assign(self):
        assert (
            WorkerMessageParser.detect_message_type("//task-assign\nTask ID: x")
            == "task_assign"
        )

    def test_detect_message_type_task_result(self):
        assert (
            WorkerMessageParser.detect_message_type("//task-result\nTask ID: x")
            == "task_result"
        )

    def test_detect_message_type_registration(self):
        assert (
            WorkerMessageParser.detect_message_type('{"id": "x", "name": "y"}')
            == "registration"
        )

    def test_detect_message_type_status(self):
        assert (
            WorkerMessageParser.detect_message_type('{"worker": "x", "status": "y"}')
            == "status"
        )

    def test_detect_message_type_unknown(self):
        assert WorkerMessageParser.detect_message_type("hello world") == "unknown"

    def test_detect_message_type_none(self):
        assert WorkerMessageParser.detect_message_type(None) == "unknown"

    def test_is_worker_message_heartbeat(self):
        assert WorkerMessageParser.is_worker_message("//heartbeat") is True

    def test_is_worker_message_task_assign(self):
        assert WorkerMessageParser.is_worker_message("//task-assign") is True

    def test_is_worker_message_task_result(self):
        assert WorkerMessageParser.is_worker_message("//task-result") is True

    def test_is_worker_message_json(self):
        assert WorkerMessageParser.is_worker_message('{"id": "x"}') is True

    def test_is_worker_message_regular(self):
        assert WorkerMessageParser.is_worker_message("hello world") is False

    def test_is_worker_message_none(self):
        assert WorkerMessageParser.is_worker_message(None) is False


class TestWorkerRegistry:
    @pytest.fixture
    def registry_path(self, tmp_path):
        return tmp_path / "workers.json"

    @pytest.fixture
    def registry(self, registry_path):
        return WorkerRegistry(registry_path=str(registry_path))

    @pytest.mark.asyncio
    async def test_register_worker(self, registry):
        worker = await registry.register_worker(
            worker_id="worker-1",
            name="worker-copilot-01",
            capabilities=["code"],
            version="1.0.0",
            matrix_user_id="@w1:hermes.local",
            device_id="dev1",
        )
        assert worker is not None
        assert worker.id == "worker-1"
        assert worker.status == "registered"

    @pytest.mark.asyncio
    async def test_reregister_existing_worker(self, registry):
        await registry.register_worker(
            "worker-1", "x", [], "1.0.0", "@w1:h.local", "d1"
        )
        worker = await registry.register_worker(
            "worker-1", "x", ["code"], "1.0.0", "@w1:h.local", "d1"
        )
        assert worker is not None
        assert worker.status == "ready"

    @pytest.mark.asyncio
    async def test_heartbeat_updates_last_seen(self, registry):
        await registry.register_worker(
            "worker-1", "x", [], "1.0.0", "@w1:h.local", "d1"
        )
        first_seen = (await registry.get_worker("worker-1")).last_seen_at
        await asyncio.sleep(0.05)
        result = await registry.heartbeat("worker-1")
        assert result is not None
        assert result.last_seen_at >= first_seen

    @pytest.mark.asyncio
    async def test_heartbeat_nonexistent(self, registry):
        assert await registry.heartbeat("nonexistent") is None

    @pytest.mark.asyncio
    async def test_update_status_existing(self, registry):
        await registry.register_worker(
            "worker-1", "x", [], "1.0.0", "@w1:h.local", "d1"
        )
        result = await registry.update_status("worker-1", "busy", "working on it")
        assert result is not None
        assert result.status == "busy"
        assert result.metadata.get("last_message") == "working on it"

    @pytest.mark.asyncio
    async def test_update_status_nonexistent(self, registry):
        assert await registry.update_status("x", "busy", "") is None

    @pytest.mark.asyncio
    async def test_remove_worker_existing(self, registry):
        await registry.register_worker(
            "worker-1", "x", [], "1.0.0", "@w1:h.local", "d1"
        )
        result = await registry.remove_worker("worker-1")
        assert result is True
        assert len(await registry.list_workers()) == 0

    @pytest.mark.asyncio
    async def test_remove_worker_nonexistent(self, registry):
        assert await registry.remove_worker("x") is False

    @pytest.mark.asyncio
    async def test_list_workers_multiple(self, registry):
        for i in range(3):
            await registry.register_worker(
                f"worker-{i}", f"w{i}", [], "1.0.0", f"@w{i}:h.local", f"d{i}"
            )
        workers = await registry.list_workers()
        assert len(workers) == 3

    @pytest.mark.asyncio
    async def test_list_workers_filtered(self, registry):
        await registry.register_worker("w1", "x", [], "1.0.0", "@w1:h.local", "d1")
        await registry.register_worker("w2", "x", [], "1.0.0", "@w2:h.local", "d2")
        await registry.update_status("w1", "busy", "")
        busy = await registry.list_workers(status="busy")
        assert len(busy) == 1
        assert busy[0].id == "w1"

    @pytest.mark.asyncio
    async def test_list_workers_empty(self, registry):
        assert await registry.list_workers() == []

    @pytest.mark.asyncio
    async def test_get_worker_existing(self, registry):
        await registry.register_worker(
            "worker-1", "x", ["code"], "1.0.0", "@w1:h.local", "d1"
        )
        worker = await registry.get_worker("worker-1")
        assert worker is not None
        assert worker.id == "worker-1"
        assert worker.capabilities == ["code"]

    @pytest.mark.asyncio
    async def test_get_worker_nonexistent(self, registry):
        assert await registry.get_worker("nonexistent") is None

    @pytest.mark.asyncio
    async def test_mark_stale_workers(self, registry):
        import gateway.hiclaw.worker_registry as wr

        orig_threshold = wr.STALE_THRESHOLD_SECONDS
        wr.STALE_THRESHOLD_SECONDS = 0
        try:
            for i in range(3):
                await registry.register_worker(
                    f"worker-{i}", f"w{i}", [], "1.0.0", f"@w{i}:h.local", f"d{i}"
                )
            marked = await registry.mark_stale_workers()
            assert len(marked) == 3
            for i in range(3):
                w = await registry.get_worker(f"worker-{i}")
                assert w.status == "offline"
        finally:
            wr.STALE_THRESHOLD_SECONDS = orig_threshold

    @pytest.mark.asyncio
    async def test_persistence(self, registry_path, registry):
        await registry.register_worker(
            "worker-1", "x", [], "1.0.0", "@w1:h.local", "d1"
        )
        new_registry = WorkerRegistry(registry_path=str(registry_path))
        workers = await new_registry.list_workers()
        assert len(workers) == 1
        assert workers[0].id == "worker-1"


class TestDockerManager:
    @pytest.fixture
    def mock_client(self):
        return MagicMock()

    @pytest.fixture
    def dm(self, mock_client):
        with patch(
            "gateway.hiclaw.docker_manager.docker.DockerClient",
            return_value=mock_client,
        ):
            return DockerManager()

    def test_launch_worker(self, dm, mock_client):
        mock_container = MagicMock()
        mock_container.name = "hermes-worker-test-worker"
        mock_container.status = "running"
        mock_container.id = "abc123"
        mock_container.attrs = {
            "Config": {"Image": "hermes-worker:latest"},
            "State": {},
        }
        mock_client.containers.run.return_value = mock_container

        result = dm.launch_worker(
            worker_name="test-worker",
            manager_room_id="!room:hermes.local",
            manager_mxid="@manager:hermes.local",
        )

        assert isinstance(result, WorkerContainer)
        assert result.status == ContainerStatus.RUNNING
        mock_client.containers.run.assert_called_once()

    def test_launch_worker_with_extra_env(self, dm, mock_client):
        mock_container = MagicMock()
        mock_container.name = "hermes-worker-special"
        mock_container.status = "running"
        mock_container.id = "def456"
        mock_container.attrs = {
            "Config": {"Image": "hermes-worker:latest"},
            "State": {},
        }
        mock_client.containers.run.return_value = mock_container

        dm.launch_worker(
            worker_name="special",
            manager_room_id="!room:h.local",
            manager_mxid="@m:h.local",
            extra_env={"CUSTOM_KEY": "custom_val"},
        )

        call_kwargs = mock_client.containers.run.call_args.kwargs
        assert "CUSTOM_KEY" in call_kwargs["environment"]

    def test_is_docker_available_true(self, dm, mock_client):
        mock_client.ping.return_value = True
        assert dm.is_docker_available() is True

    def test_is_docker_available_false(self):
        with patch(
            "gateway.hiclaw.docker_manager.docker.DockerClient",
            side_effect=Exception("no docker"),
        ):
            dm = DockerManager()
            assert dm.is_docker_available() is False

    def test_stop_worker(self, dm, mock_client):
        mock_container = MagicMock()
        mock_client.containers.get.return_value = mock_container

        result = dm.stop_worker("worker-1")

        assert result is True
        mock_container.stop.assert_called_once()

    def test_stop_worker_not_found(self, dm, mock_client):
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("not found")

        result = dm.stop_worker("nonexistent")

        assert result is False

    def test_remove_worker(self, dm, mock_client):
        mock_container = MagicMock()
        mock_client.containers.get.return_value = mock_container

        result = dm.remove_worker("worker-1")

        assert result is True
        mock_container.remove.assert_called_once()

    def test_get_worker(self, dm, mock_client):
        mock_container = MagicMock()
        mock_container.name = "hermes-worker-1"
        mock_container.status = "running"
        mock_container.attrs = {
            "Config": {"Image": "hermes-worker:latest"},
            "State": {},
        }
        mock_client.containers.get.return_value = mock_container

        result = dm.get_worker("worker-1")

        assert isinstance(result, WorkerContainer)
        assert result.status == ContainerStatus.RUNNING

    def test_get_worker_not_found_returns_container(self, dm, mock_client):
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("not found")

        result = dm.get_worker("nonexistent")

        assert isinstance(result, WorkerContainer)
        assert result.status == ContainerStatus.NOT_FOUND

    def test_list_workers(self, dm, mock_client):
        mock_containers = []
        for i in range(3):
            c = MagicMock()
            c.name = f"hermes-worker-{i}"
            c.status = "running"
            c.attrs = {"Config": {"Image": "hermes-worker:latest"}, "State": {}}
            mock_containers.append(c)

        mock_client.containers.list.return_value = mock_containers

        result = dm.list_workers()

        assert len(result) == 3

    def test_get_worker_logs(self, dm, mock_client):
        mock_container = MagicMock()
        mock_container.logs.return_value = b"line1\nline2\n"
        mock_client.containers.get.return_value = mock_container

        result = dm.get_worker_logs("worker-1", tail=50)

        assert "line1" in result
        assert "line2" in result

    def test_restart_worker(self, dm, mock_client):
        mock_container = MagicMock()
        mock_client.containers.get.return_value = mock_container

        result = dm.restart_worker("worker-1")

        assert result is True
        mock_container.restart.assert_called_once()

    def test_get_manager(self, dm, mock_client):
        mock_container = MagicMock()
        mock_container.name = "hermes-manager"
        mock_container.status = "running"
        mock_container.attrs = {
            "Config": {"Image": "hermes-manager:latest"},
            "State": {},
        }
        mock_client.containers.get.return_value = mock_container

        result = dm.get_manager()

        assert isinstance(result, WorkerContainer)
        assert result.name == "hermes-manager"
