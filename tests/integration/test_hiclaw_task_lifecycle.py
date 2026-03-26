"""Integration tests for hiClaw task lifecycle and worker scaling.

Uses the same _isolate_queue fixture pattern as tests/mcp/test_task_queue.py.
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

_TQ_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "task-queue" / "server.py"
_WR_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "worker-registry" / "server.py"


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
# TestTaskLifecycleHappyPath
# ---------------------------------------------------------------------------


class TestTaskLifecycleHappyPath:
    """Test the full happy-path lifecycle: pending → assigned → running → completed."""

    def test_happy_path_full_lifecycle(self, _reload_both_servers):
        """Worker registers, task is added, assigned, started, and completed.
        All status transitions are verified.
        """
        tq = _reload_both_servers["tq"]
        wr = _reload_both_servers["wr"]

        # Step 1: Register worker (status=ready)
        result = wr.wr_register(
            worker_id="worker-1",
            name="Test Worker",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@worker1:hermes.local",
            device_id="device-1",
        )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["worker"]["worker_id"] == "worker-1"

        result = wr.wr_update_status(worker_id="worker-1", status="ready")
        data = json.loads(result)
        assert data["status"] == "ok"

        # Step 2: Add task → pending
        result = tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "pending"
        assert data["task"]["id"] == "task-1"

        # Step 3: Assign task → task=assigned, worker=busy
        result = tq.tq_assign(task_id="task-1", worker_id="worker-1")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "assigned"
        assert data["task"]["assigned_worker"] == "worker-1"

        # Verify worker is now busy
        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        assert data["worker"]["status"] == "busy"

        # Step 4: Start task → task=running
        result = tq.tq_start(task_id="task-1")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "running"
        assert data["task"]["started_at"] is not None

        # Step 5: Complete task → task=completed, worker=ready
        result = tq.tq_complete(task_id="task-1", result_path="/path/to/result.json")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "completed"
        assert data["task"]["result_path"] == "/path/to/result.json"
        assert data["task"]["assigned_worker"] is None

        # Verify worker is back to ready
        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        assert data["worker"]["status"] == "ready"


# ---------------------------------------------------------------------------
# TestTaskLifecycleAtomicAssignment
# ---------------------------------------------------------------------------


class TestTaskLifecycleAtomicAssignment:
    """Test that assigning a task twice fails (atomicity)."""

    def test_second_assign_fails(self, _reload_both_servers):
        """Assigning a task that is no longer pending should fail."""
        tq = _reload_both_servers["tq"]
        wr = _reload_both_servers["wr"]

        # Register and set worker to ready
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

        # First assign → succeeds, worker=busy
        result = tq.tq_assign(task_id="task-1", worker_id="worker-1")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "assigned"

        # Second assign same task → fails (not pending)
        result = tq.tq_assign(task_id="task-1", worker_id="worker-1")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Cannot assign task in 'assigned' status" in data["message"]

        # Verify worker is still busy (first assign won)
        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        assert data["worker"]["status"] == "busy"


# ---------------------------------------------------------------------------
# TestTaskLifecycleConcurrentAssign
# ---------------------------------------------------------------------------


class TestTaskLifecycleConcurrentAssign:
    """Simulate two managers assigning the same task (only first should win)."""

    def test_concurrent_assign_only_first_wins(self, _reload_both_servers):
        """First tq_assign succeeds; second fails because task is no longer pending."""
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

        # First assign → succeeds, worker=busy
        result = tq.tq_assign(task_id="task-1", worker_id="worker-1")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "assigned"
        assert data["task"]["assigned_worker"] == "worker-1"

        # Second assign same task → fails
        result = tq.tq_assign(task_id="task-1", worker_id="worker-1")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Cannot assign task in 'assigned' status" in data["message"]

        # Worker status = busy (first assign won)
        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        assert data["worker"]["status"] == "busy"

        # Task is still assigned to worker-1
        result = tq.tq_get(task_id="task-1")
        data = json.loads(result)
        assert data["task"]["status"] == "assigned"
        assert data["task"]["assigned_worker"] == "worker-1"


# ---------------------------------------------------------------------------
# TestTaskLifecycleStaleWorker
# ---------------------------------------------------------------------------


class TestTaskLifecycleStaleWorker:
    """Test stale task detection frees the worker automatically."""

    def test_stale_task_frees_worker(self, _reload_both_servers):
        """tq_fail_stale_tasks marks the task failed AND frees the worker to ready."""
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
        assert data["success"] is True

        # Start task
        tq.tq_start(task_id="task-1")

        conn = tq._get_db()
        conn.execute(
            "UPDATE tasks SET updated_at='2020-01-01T00:00:00' WHERE id='task-1'"
        )
        conn.commit()
        conn.close()

        result = tq.tq_fail_stale_tasks(timeout_seconds=86400)
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 1
        assert data["tasks"][0]["id"] == "task-1"

        result = tq.tq_get(task_id="task-1")
        data = json.loads(result)
        assert data["task"]["status"] == "failed"
        assert data["task"]["error"] == "Task timed out"

        result = wr.wr_get(worker_id="worker-1")
        data = json.loads(result)
        assert data["worker"]["status"] == "ready"

    def test_stale_task_with_zero_timeout_returns_error(self, _reload_both_servers):
        """Verify that timeout=0 is rejected."""
        tq = _reload_both_servers["tq"]

        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_start(task_id="task-1")

        result = tq.tq_fail_stale_tasks(timeout_seconds=0)
        data = json.loads(result)
        assert data["status"] == "error"
        assert "positive integer" in data["message"]
