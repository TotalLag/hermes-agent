"""Integration tests for hiClaw error recovery and graceful degradation.

Uses the same _isolate_registry / _isolate_queue fixture patterns from
tests/mcp/test_worker_registry.py and tests/mcp/test_task_queue.py.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_WR_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "worker-registry" / "server.py"
_TQ_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "task-queue" / "server.py"


# ---------------------------------------------------------------------------
# Fixtures — mirror the patterns from tests/mcp/test_worker_registry.py
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path, monkeypatch):
    """Redirect ~/.hermes/ to a temp directory for every test."""
    fake_home = tmp_path / ".hermes" / "hiclaw"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(os.path, "expanduser", lambda _: str(fake_home))
    yield str(fake_home)


@pytest.fixture(autouse=True)
def _isolate_queue(tmp_path, monkeypatch):
    """Redirect ~/.hermes/ to a temp directory for every test."""
    fake_home = tmp_path / ".hermes" / "hiclaw"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(os.path, "expanduser", lambda _: str(fake_home))
    yield str(fake_home)


@pytest.fixture()
def _reload_wr_server(_isolate_registry):
    """Load the worker-registry server module with patched expanduser."""
    for mod_name in list(sys.modules):
        if "worker_registry" in mod_name:
            del sys.modules[mod_name]

    fake_home = os.path.expanduser("~")
    with patch.object(os.path, "expanduser", return_value=fake_home):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "worker_registry", _WR_SERVER_PATH
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["worker_registry"] = mod
        spec.loader.exec_module(mod)

    return mod


@pytest.fixture()
def _reload_tq_server(_isolate_queue):
    """Load the task-queue server module with patched expanduser."""
    for mod_name in list(sys.modules):
        if "task_queue" in mod_name:
            del sys.modules[mod_name]

    fake_home = os.path.expanduser("~")
    with patch.object(os.path, "expanduser", return_value=fake_home):
        import importlib.util

        spec = importlib.util.spec_from_file_location("task_queue", _TQ_SERVER_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["task_queue"] = mod
        spec.loader.exec_module(mod)

    return mod


# ---------------------------------------------------------------------------
# DockerManager graceful degradation tests
# ---------------------------------------------------------------------------


class TestDockerUnavailableGraceful:
    """DockerManager should degrade gracefully when Docker socket is unavailable."""

    def test_is_docker_available_returns_false_without_socket(self):
        """DockerManager with no docker socket reports unavailable, not crashes."""
        from gateway.hiclaw.docker_manager import DockerManager

        # Pass an invalid socket path so DockerClient fails to connect
        dm = DockerManager(docker_host="unix:///nonexistent/docker.sock")
        assert dm.is_docker_available() is False

    def test_list_workers_returns_empty_when_docker_unavailable(self):
        """list_workers() returns [] instead of crashing when Docker is down."""
        from gateway.hiclaw.docker_manager import DockerManager

        dm = DockerManager(docker_host="unix:///nonexistent/docker.sock")
        result = dm.list_workers()
        assert result == []

    def test_launch_worker_handles_error_gracefully(self):
        """launch_worker() returns an error WorkerContainer, not an exception."""
        from gateway.hiclaw.docker_manager import (
            ContainerStatus,
            DockerManager,
        )

        dm = DockerManager(docker_host="unix:///nonexistent/docker.sock")

        # Should not raise — should handle the error gracefully
        result = dm.launch_worker(
            worker_name="test-worker",
            manager_room_id="!room:example.com",
            manager_mxid="@manager:example.com",
        )

        # Should return a WorkerContainer indicating the failure
        assert result.name == "hermes-worker-test-worker"
        assert result.status == ContainerStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Worker crash mid-task tests
# ---------------------------------------------------------------------------


class TestWorkerCrashMidTask:
    """Simulate a worker crashing mid-task and verify recovery workflow."""

    def test_stale_detection_frees_worker(self, _reload_wr_server, _reload_tq_server):
        """tq_fail_stale_tasks marks task=failed AND frees worker to ready.

        Simulates: worker crashes, task is stuck running, stale detection
        fails the task AND auto-frees the worker.
        """
        wr = _reload_wr_server
        tq = _reload_tq_server

        # 1. Register worker and set to ready
        result = wr.wr_register(
            worker_id="w1",
            name="Worker 1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        assert json.loads(result)["status"] == "ok"

        result = wr.wr_update_status(worker_id="w1", status="ready")
        assert json.loads(result)["status"] == "ok"

        # 2. Add task and assign to worker
        result = tq.tq_add_task(task_id="task-1", spec_path="/spec.json")
        assert json.loads(result)["success"] is True

        result = tq.tq_assign(task_id="task-1", worker_id="w1")
        assert json.loads(result)["success"] is True

        # Verify task is assigned and worker is busy
        task_data = json.loads(tq.tq_get(task_id="task-1"))
        assert task_data["task"]["status"] == "assigned"

        worker_data = json.loads(wr.wr_get(worker_id="w1"))
        assert worker_data["worker"]["status"] == "busy"

        # 3. Start the task (now it's 'running')
        result = tq.tq_start(task_id="task-1")
        assert json.loads(result)["success"] is True

        task_data = json.loads(tq.tq_get(task_id="task-1"))
        assert task_data["task"]["status"] == "running"

        # 4. Simulate worker crash: set updated_at to old time so task is stale
        conn = tq._get_db()
        conn.execute(
            "UPDATE tasks SET updated_at='2020-01-01T00:00:00' WHERE id='task-1'"
        )
        conn.commit()
        conn.close()

        # 5. tq_fail_stale_tasks should fail the task
        result = tq.tq_fail_stale_tasks(timeout_seconds=1)
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 1
        assert data["tasks"][0]["id"] == "task-1"

        # Verify task is now failed
        task_data = json.loads(tq.tq_get(task_id="task-1"))
        assert task_data["task"]["status"] == "failed"
        assert task_data["task"]["error"] == "Task timed out"

        # 6. Worker is freed to ready by stale detection
        worker_data = json.loads(wr.wr_get(worker_id="w1"))
        assert worker_data["worker"]["status"] == "ready"

        # 7. Manual tq_fail frees the worker back to ready
        result = tq.tq_fail(task_id="task-1", error="Manual failure after crash")
        data = json.loads(result)
        assert data["success"] is True

        # Now worker should be ready again
        worker_data = json.loads(wr.wr_get(worker_id="w1"))
        assert worker_data["worker"]["status"] == "ready"


# ---------------------------------------------------------------------------
# Manager restart / state recovery tests
# ---------------------------------------------------------------------------


class TestManagerRestartStateRecovery:
    """Verify state persists across server module reloads (simulate manager restart)."""

    def test_worker_persists_across_server_reload(self, _reload_wr_server):
        """wr_registered worker is still in registry after module reload."""
        wr = _reload_wr_server

        # Register a worker
        result = wr.wr_register(
            worker_id="w1",
            name="Worker 1",
            capabilities=["code"],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        assert json.loads(result)["status"] == "ok"

        # Get the path to the existing DB
        db_path = os.path.join(os.path.expanduser("~"), "workers-registry.db")
        assert os.path.exists(db_path)

        # Reload the module (simulate manager restart)
        for mod_name in list(sys.modules):
            if "worker_registry" in mod_name:
                del sys.modules[mod_name]

        fake_home = os.path.expanduser("~")
        with patch.object(os.path, "expanduser", return_value=fake_home):
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "worker_registry", _WR_SERVER_PATH
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules["worker_registry"] = mod
            spec.loader.exec_module(mod)

        # Worker should still be there
        result = mod.wr_list()
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 1
        assert data["workers"][0]["worker_id"] == "w1"

    def test_task_persists_across_server_reload(self, _reload_tq_server):
        """Added tasks remain in queue after task-queue module reload."""
        tq = _reload_tq_server

        # Add a task
        result = tq.tq_add_task(task_id="task-1", spec_path="/spec.json")
        assert json.loads(result)["success"] is True

        # Get the path to the existing DB
        db_path = os.path.join(os.path.expanduser("~"), "task-queue.db")
        assert os.path.exists(db_path)

        # Reload the module (simulate manager restart)
        for mod_name in list(sys.modules):
            if "task_queue" in mod_name:
                del sys.modules[mod_name]

        fake_home = os.path.expanduser("~")
        with patch.object(os.path, "expanduser", return_value=fake_home):
            import importlib.util

            spec = importlib.util.spec_from_file_location("task_queue", _TQ_SERVER_PATH)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["task_queue"] = mod
            spec.loader.exec_module(mod)

        # Task should still be there
        result = mod.tq_list()
        data = json.loads(result)
        assert data["count"] == 1
        assert data["tasks"][0]["id"] == "task-1"


# ---------------------------------------------------------------------------
# Stale worker detection tests
# ---------------------------------------------------------------------------


class TestStaleWorkerDetection:
    """wr_get_stale_workers should detect workers that have not sent heartbeats."""

    def test_no_stale_workers_when_all_healthy(self, _reload_wr_server):
        """wr_get_stale_workers(timeout=1) returns [] when no worker is stale."""
        wr = _reload_wr_server

        result = wr.wr_register(
            worker_id="w1",
            name="Worker 1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        assert json.loads(result)["status"] == "ok"

        wr.wr_update_status(worker_id="w1", status="ready")

        # Very short timeout, but worker just registered — should return empty
        result = wr.wr_get_stale_workers(timeout_seconds=1)
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 0
        assert data["workers"] == []

    def test_wr_get_stale_workers_with_invalid_timeout_returns_error(
        self, _reload_wr_server
    ):
        """wr_get_stale_workers rejects non-positive timeout with proper error."""
        wr = _reload_wr_server

        result = wr.wr_get_stale_workers(timeout_seconds=0)
        data = json.loads(result)
        assert data["status"] == "error"
        assert "positive integer" in data["message"]

        result2 = wr.wr_get_stale_workers(timeout_seconds=-1)
        data2 = json.loads(result2)
        assert data2["status"] == "error"


# ---------------------------------------------------------------------------
# Database error handling tests
# ---------------------------------------------------------------------------


class TestDatabaseErrorHandling:
    """Verify tools return structured JSON errors for invalid inputs."""

    def test_tq_get_with_none_task_id(self, _reload_tq_server):
        """tq_get(task_id=None) should return a structured error."""
        tq = _reload_tq_server
        result = tq.tq_get(task_id=None)
        data = json.loads(result)
        assert data.get("status") == "error" or data.get("error") is not None
        assert (
            "task_id" in data["message"].lower()
            or "required" in data["message"].lower()
        )

    def test_tq_get_with_empty_task_id(self, _reload_tq_server):
        """tq_get(task_id='') should return a structured error."""
        tq = _reload_tq_server
        result = tq.tq_get(task_id="")
        data = json.loads(result)
        assert data.get("status") == "error" or data.get("error") is not None
        assert (
            "task_id" in data["message"].lower()
            or "required" in data["message"].lower()
        )

    def test_tq_assign_with_empty_ids(self, _reload_tq_server):
        """tq_assign(task_id='', worker_id='') should return a structured error."""
        tq = _reload_tq_server
        result = tq.tq_assign(task_id="", worker_id="")
        data = json.loads(result)
        assert data.get("status") == "error" or data.get("error") is not None

    def test_error_responses_are_structured_json(self, _reload_tq_server):
        """Error responses must be valid JSON with 'error' or status='error'."""
        tq = _reload_tq_server

        # All of these should return valid JSON strings
        error_results = [
            tq.tq_get(task_id=None),
            tq.tq_get(task_id=""),
            tq.tq_assign(task_id="", worker_id=""),
        ]

        for result in error_results:
            # Must be parseable JSON
            parsed = json.loads(result)
            # Must have error structure
            assert parsed.get("status") == "error" or parsed.get("error") is not None, (
                f"Unexpected non-error response: {result}"
            )
