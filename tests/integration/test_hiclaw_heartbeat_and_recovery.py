"""Integration tests for hiClaw heartbeat, stale detection, and recovery.

Follows the same _isolate_queue fixture pattern as test_hiclaw_task_lifecycle.py.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_TQ_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "task-queue" / "server.py"
_WR_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "worker-registry" / "server.py"

from gateway.hiclaw.docker_manager import (
    ContainerStatus,
    DockerManager,
    WorkerContainer,
)


# ---------------------------------------------------------------------------
# Fixtures — same pattern as test_hiclaw_task_lifecycle.py
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_queue(tmp_path, monkeypatch):
    """Redirect ~/.hermes/ to a temp directory for every test."""
    fake_home = tmp_path / ".hermes" / "hiclaw"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(os.path, "expanduser", lambda _: str(fake_home))
    yield str(fake_home)


@pytest.fixture()
def _reload_tq_server(_isolate_queue):
    """Load the task-queue server module with patched expanduser so ~/.hermes/ → tmp."""
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


@pytest.fixture()
def _reload_wr_server(_isolate_queue):
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
def _reload_both_servers(_isolate_queue):
    """Load both task-queue and worker-registry servers together."""
    for mod_name in list(sys.modules):
        if "task_queue" in mod_name or "worker_registry" in mod_name:
            del sys.modules[mod_name]

    fake_home = os.path.expanduser("~")
    with patch.object(os.path, "expanduser", return_value=fake_home):
        import importlib.util

        # Load worker-registry first
        wr_spec = importlib.util.spec_from_file_location(
            "worker_registry", _WR_SERVER_PATH
        )
        wr_mod = importlib.util.module_from_spec(wr_spec)
        sys.modules["worker_registry"] = wr_mod
        wr_spec.loader.exec_module(wr_mod)

        # Then load task-queue
        tq_spec = importlib.util.spec_from_file_location("task_queue", _TQ_SERVER_PATH)
        tq_mod = importlib.util.module_from_spec(tq_spec)
        sys.modules["task_queue"] = tq_mod
        tq_spec.loader.exec_module(tq_mod)

    return {"tq": tq_mod, "wr": wr_mod}


# ---------------------------------------------------------------------------
# TestHeartbeatAndStaleDetection
# ---------------------------------------------------------------------------


class TestHeartbeatAndStaleDetection:
    """Tests for worker heartbeat lifecycle and stale worker detection."""

    def test_worker_sends_heartbeat_stays_alive(self, _reload_wr_server):
        """Register worker, send heartbeat, verify worker still tracked."""
        wr = _reload_wr_server

        # Register worker
        result = wr.wr_register(
            worker_id="worker-1",
            name="Test Worker",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@worker1:hermes.local",
            device_id="device-1",
        )
        data = json.loads(result)
        assert data["status"] == "ok", "Worker registration should succeed"
        assert data["worker"]["worker_id"] == "worker-1"

        # Set worker to ready
        result = wr.wr_update_status(worker_id="worker-1", status="ready")
        data = json.loads(result)
        assert data["status"] == "ok", "Status update should succeed"

        # Get worker last_seen_at before heartbeat
        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        before_heartbeat = data["worker"]["last_seen_at"]
        assert before_heartbeat != ""

        # Send heartbeat
        result = wr.wr_heartbeat(worker_id="worker-1")
        data = json.loads(result)
        assert data["status"] == "ok", "Heartbeat should succeed"
        assert data["worker_id"] == "worker-1"
        assert data["worker_status"] == "ready", (
            "Worker should still be ready after heartbeat"
        )

        # Verify last_seen_at updated
        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        after_heartbeat = data["worker"]["last_seen_at"]
        assert after_heartbeat != "", "last_seen_at should be set after heartbeat"
        assert after_heartbeat >= before_heartbeat, "last_seen_at should be updated"

    def test_worker_stops_sending_heartbeat_marked_stale(self, _reload_wr_server):
        """Register worker, don't send heartbeat, wr_get_stale_workers returns it."""
        wr = _reload_wr_server
        STALE_TIMEOUT = 60  # seconds — use a small timeout for testing

        # Register worker and set to ready
        result = wr.wr_register(
            worker_id="worker-stale",
            name="Stale Worker",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@workerstale:hermes.local",
            device_id="device-stale",
        )
        data = json.loads(result)
        assert data["status"] == "ok"

        wr.wr_update_status(worker_id="worker-stale", status="ready")

        # Manually backdate last_seen_at to simulate no heartbeat
        conn = wr._get_db()
        past_time = (
            datetime.now(timezone.utc) - timedelta(seconds=STALE_TIMEOUT + 10)
        ).isoformat()
        conn.execute(
            "UPDATE workers SET last_seen_at=? WHERE worker_id=?",
            (past_time, "worker-stale"),
        )
        conn.commit()
        conn.close()

        # Get stale workers — should return the worker we just backdated
        result = wr.wr_get_stale_workers(timeout_seconds=STALE_TIMEOUT)
        data = json.loads(result)
        assert data["status"] == "ok", "wr_get_stale_workers should return ok"
        assert data["count"] == 1, "One stale worker should be detected"
        stale_ids = [w["worker_id"] for w in data["workers"]]
        assert "worker-stale" in stale_ids, "worker-stale should be in stale list"

        # Verify worker is now marked offline
        result = wr.wr_get(worker_id="worker-stale")
        data = json.loads(result)
        assert data["worker"]["status"] == "offline", "Worker should be marked offline"

    def test_stale_worker_task_frees_after_stale_detection(self, _reload_both_servers):
        """Register worker, assign+start task, worker goes stale,
        tq_fail_stale_tasks marks task failed AND frees worker to ready."""
        tq = _reload_both_servers["tq"]
        wr = _reload_both_servers["wr"]

        # Register worker → ready
        wr.wr_register(
            worker_id="worker-1",
            name="Test Worker",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@worker1:hermes.local",
            device_id="device-1",
        )
        wr.wr_update_status(worker_id="worker-1", status="ready")

        # Add task → pending
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")

        # Assign → worker=busy
        result = tq.tq_assign(task_id="task-1", worker_id="worker-1")
        data = json.loads(result)
        assert data["success"] is True, "Task assignment should succeed"

        # Verify worker is busy
        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        assert data["worker"]["status"] == "busy", (
            "Worker should be busy after assignment"
        )

        # Start task → running
        tq.tq_start(task_id="task-1")

        # Backdate task updated_at to make it appear stale
        conn = tq._get_db()
        past_time = (datetime.now(timezone.utc) - timedelta(seconds=86400)).isoformat()
        conn.execute(
            "UPDATE tasks SET updated_at=? WHERE id='task-1'",
            (past_time,),
        )
        conn.commit()
        conn.close()

        # Call tq_fail_stale_tasks — should mark task failed AND free worker
        result = tq.tq_fail_stale_tasks(timeout_seconds=86400)
        data = json.loads(result)
        assert data["status"] == "ok", "tq_fail_stale_tasks should return ok"
        assert data["count"] == 1, "One stale task should be detected"
        assert data["tasks"][0]["id"] == "task-1"

        # Verify task is failed
        result = tq.tq_get(task_id="task-1")
        data = json.loads(result)
        assert data["task"]["status"] == "failed", "Task should be marked failed"
        assert data["task"]["error"] == "Task timed out", (
            "Task error should be 'Task timed out'"
        )

        # Verify worker is freed back to ready
        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        assert data["worker"]["status"] == "ready", (
            "Worker should be freed back to ready"
        )


# ---------------------------------------------------------------------------
# TestWorkerFailureRecovery
# ---------------------------------------------------------------------------


class TestWorkerFailureRecovery:
    """Tests for worker crash scenarios and recovery via task reassignment."""

    def test_worker_crash_task_gets_reassigned(self, _reload_both_servers):
        """Register 2 workers, assign task to worker-1, simulate worker-1 going offline,
        stale cleanup frees worker-1, then task is reassigned to worker-2."""
        tq = _reload_both_servers["tq"]
        wr = _reload_both_servers["wr"]

        # Register worker-1 → ready
        wr.wr_register(
            worker_id="worker-1",
            name="Worker One",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@worker1:hermes.local",
            device_id="device-1",
        )
        wr.wr_update_status(worker_id="worker-1", status="ready")

        # Register worker-2 → ready
        wr.wr_register(
            worker_id="worker-2",
            name="Worker Two",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@worker2:hermes.local",
            device_id="device-2",
        )
        wr.wr_update_status(worker_id="worker-2", status="ready")

        # Add task → pending
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")

        # Assign task to worker-1
        result = tq.tq_assign(task_id="task-1", worker_id="worker-1")
        data = json.loads(result)
        assert data["success"] is True, "Task should be assigned to worker-1"
        assert data["task"]["assigned_worker"] == "worker-1"

        # Start task
        tq.tq_start(task_id="task-1")

        # Simulate worker-1 going offline (backdate last_seen_at)
        STALE_TIMEOUT = 60
        conn = wr._get_db()
        past_time = (
            datetime.now(timezone.utc) - timedelta(seconds=STALE_TIMEOUT + 10)
        ).isoformat()
        conn.execute(
            "UPDATE workers SET last_seen_at=? WHERE worker_id='worker-1'",
            (past_time,),
        )
        conn.commit()
        conn.close()

        # Stale detection marks worker-1 offline and frees the task
        result = wr.wr_get_stale_workers(timeout_seconds=STALE_TIMEOUT)
        data = json.loads(result)
        assert data["count"] == 1, "worker-1 should be detected as stale"
        stale_ids = [w["worker_id"] for w in data["workers"]]
        assert "worker-1" in stale_ids, "worker-1 should be stale"

        # Verify worker-1 is offline
        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        assert data["worker"]["status"] == "offline", "worker-1 should be offline"

        # Backdate task updated_at and call tq_fail_stale_tasks to free task
        conn = tq._get_db()
        past_time = (datetime.now(timezone.utc) - timedelta(seconds=86400)).isoformat()
        conn.execute(
            "UPDATE tasks SET updated_at=? WHERE id='task-1'",
            (past_time,),
        )
        conn.commit()
        conn.close()

        result = tq.tq_fail_stale_tasks(timeout_seconds=86400)
        data = json.loads(result)
        assert data["count"] == 1, "One task should be marked failed"

        # Verify worker-2 is still ready
        result = wr.wr_get(worker_id="worker-2")
        data = json.loads(result)
        assert data["worker"]["status"] == "ready", "worker-2 should still be ready"

    def test_manager_restart_state_recovered_from_sqlite(self, _reload_both_servers):
        """Register worker + add task, close/reopen SQLite DB (simulating restart),
        verify state restored correctly."""
        tq = _reload_both_servers["tq"]
        wr = _reload_both_servers["wr"]

        # Register worker → ready
        result = wr.wr_register(
            worker_id="worker-persist",
            name="Persistent Worker",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@workerp:hermes.local",
            device_id="device-p",
        )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["worker"]["worker_id"] == "worker-persist"

        wr.wr_update_status(worker_id="worker-persist", status="ready")

        # Add task → pending
        result = tq.tq_add_task(task_id="task-persist", spec_path="/path/to/spec.json")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["id"] == "task-persist"
        assert data["task"]["status"] == "pending"

        # Close both DB connections (simulating manager crash)
        wr_conn = wr._get_db()
        wr_conn.close()
        tq_conn = tq._get_db()
        tq_conn.close()

        # Reload both servers — this reopens the SQLite databases
        for mod_name in list(sys.modules):
            if "task_queue" in mod_name or "worker_registry" in mod_name:
                del sys.modules[mod_name]

        fake_home = os.path.expanduser("~")
        with patch.object(os.path, "expanduser", return_value=fake_home):
            import importlib.util

            wr_spec = importlib.util.spec_from_file_location(
                "worker_registry", _WR_SERVER_PATH
            )
            wr_mod = importlib.util.module_from_spec(wr_spec)
            sys.modules["worker_registry"] = wr_mod
            wr_spec.loader.exec_module(wr_mod)

            tq_spec = importlib.util.spec_from_file_location(
                "task_queue", _TQ_SERVER_PATH
            )
            tq_mod = importlib.util.module_from_spec(tq_spec)
            sys.modules["task_queue"] = tq_mod
            tq_spec.loader.exec_module(tq_mod)

        # Verify worker persisted
        result = wr_mod.wr_get(worker_id="worker-persist")
        data = json.loads(result)
        assert data["status"] == "ok", "Worker should be recoverable after restart"
        assert data["worker"]["worker_id"] == "worker-persist"
        assert data["worker"]["status"] == "ready", "Worker status should be 'ready'"

        # Verify task persisted
        result = tq_mod.tq_get(task_id="task-persist")
        data = json.loads(result)
        assert data["task"]["id"] == "task-persist", (
            "Task should be recoverable after restart"
        )
        assert data["task"]["status"] == "pending", "Task should still be 'pending'"


# ---------------------------------------------------------------------------
# TestDockerManagerDegradation
# ---------------------------------------------------------------------------


class TestDockerManagerDegradation:
    """Tests for DockerManager graceful degradation when Docker is unavailable."""

    @pytest.fixture
    def mock_docker_client(self):
        return MagicMock()

    @pytest.fixture
    def docker_manager(self, mock_docker_client):
        with patch(
            "gateway.hiclaw.docker_manager.docker.DockerClient",
            return_value=mock_docker_client,
        ):
            dm = DockerManager()
            dm._client = mock_docker_client
            yield dm

    def test_docker_unavailable_is_docker_available_returns_false(
        self, mock_docker_client
    ):
        """Patch docker to throw ConnectionError, verify is_docker_available() returns False."""
        mock_docker_client.ping.side_effect = ConnectionError(
            "Docker socket not available"
        )

        with patch(
            "gateway.hiclaw.docker_manager.docker.DockerClient",
            return_value=mock_docker_client,
        ):
            dm = DockerManager()
            dm._client = mock_docker_client

            result = dm.is_docker_available()
            assert result is False, (
                "is_docker_available should return False when Docker throws ConnectionError"
            )

    def test_docker_unavailable_launch_does_not_crash(self, mock_docker_client):
        """Patch docker to throw ConnectionError, verify launch_worker() handles gracefully."""
        # Simulate Docker client being None after ConnectionError on init
        with patch(
            "gateway.hiclaw.docker_manager.docker.DockerClient",
            side_effect=ConnectionError("Docker daemon unreachable"),
        ):
            dm = DockerManager()
            # When DockerClient raises, _client is set to None
            assert dm._client is None, (
                "DockerManager should set _client to None on connection failure"
            )

        # launch_worker should not raise — it should return a degraded WorkerContainer
        result = dm.launch_worker(
            worker_name="test-worker",
            manager_room_id="!room:hermes.local",
            manager_mxid="@manager:hermes.local",
        )

        assert isinstance(result, WorkerContainer), (
            "launch_worker should return a WorkerContainer"
        )
        assert result.status == ContainerStatus.UNKNOWN, (
            "Should return UNKNOWN status when Docker unavailable"
        )
        assert "hermes-worker-test-worker" in result.name, (
            "Container name should be set correctly"
        )

    def test_docker_reconnect_recovers(self, mock_docker_client):
        """First call succeeds, second throws ConnectionError, third succeeds again,
        verify all handled gracefully."""
        call_count = [0]

        def ping_side_effect():
            call_count[0] += 1
            if call_count[0] == 2:
                raise ConnectionError("Docker temporarily unavailable")

        mock_docker_client.ping.side_effect = ping_side_effect

        with patch(
            "gateway.hiclaw.docker_manager.docker.DockerClient",
            return_value=mock_docker_client,
        ):
            dm = DockerManager()
            dm._client = mock_docker_client

            # First call — available
            result1 = dm.is_docker_available()
            assert result1 is True, "First is_docker_available should return True"

            # Second call — connection error
            result2 = dm.is_docker_available()
            assert result2 is False, (
                "Second is_docker_available should return False after ConnectionError"
            )

            # Third call — recovers
            result3 = dm.is_docker_available()
            assert result3 is True, (
                "Third is_docker_available should return True after recovery"
            )
