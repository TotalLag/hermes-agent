"""Tests for the task-queue MCP server.

Uses a temporary ~/.hermes/ directory so tests never touch the real one.
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

_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "task-queue" / "server.py"


@pytest.fixture(autouse=True)
def _isolate_queue(tmp_path, monkeypatch):
    """Redirect ~/.hermes/ to a temp directory for every test."""
    fake_home = tmp_path / ".hermes" / "hiclaw"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(os.path, "expanduser", lambda _: str(fake_home))
    yield str(fake_home)


@pytest.fixture()
def _reload_server(_isolate_queue):
    """Load the server module with patched expanduser so ~/.hermes/ → tmp."""
    for mod_name in list(sys.modules):
        if "task_queue" in mod_name:
            del sys.modules[mod_name]

    fake_home = os.path.expanduser("~")
    with patch.object(os.path, "expanduser", return_value=fake_home):
        import importlib.util

        spec = importlib.util.spec_from_file_location("task_queue", _SERVER_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["task_queue"] = mod
        spec.loader.exec_module(mod)

    return mod


# ---------------------------------------------------------------------------
# tq_add_task tests
# ---------------------------------------------------------------------------


class TestTqAddTask:
    def test_add_task_success(self, _reload_server):
        tq = _reload_server
        result = tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["id"] == "task-1"
        assert data["task"]["status"] == "pending"
        assert data["task"]["spec_path"] == "/path/to/spec.json"

    def test_add_task_duplicate_rejected(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        result = tq.tq_add_task(task_id="task-1", spec_path="/path/to/other.json")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "already exists" in data["message"]

    def test_add_task_missing_task_id(self, _reload_server):
        tq = _reload_server
        result = tq.tq_add_task(task_id="", spec_path="/path/to/spec.json")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "task_id is required" in data["message"]

    def test_add_task_missing_spec_path(self, _reload_server):
        tq = _reload_server
        result = tq.tq_add_task(task_id="task-1", spec_path="")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "spec_path is required" in data["message"]

    def test_add_task_whitespace_trimmed(self, _reload_server):
        tq = _reload_server
        result = tq.tq_add_task(task_id="  task-1  ", spec_path="  /path/to/spec.json  ")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["id"] == "task-1"
        assert data["task"]["spec_path"] == "/path/to/spec.json"


# ---------------------------------------------------------------------------
# tq_assign tests
# ---------------------------------------------------------------------------


class TestTqAssign:
    def test_assign_success(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        result = tq.tq_assign(task_id="task-1", worker_id="worker-1")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "assigned"
        assert data["task"]["assigned_worker"] == "worker-1"

    def test_assign_task_not_found(self, _reload_server):
        tq = _reload_server
        result = tq.tq_assign(task_id="nonexistent", worker_id="worker-1")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["message"]

    def test_assign_task_not_pending(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        result = tq.tq_assign(task_id="task-1", worker_id="worker-2")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Cannot assign task in 'assigned' status. Must be 'pending'" in data["message"]

    def test_assign_missing_task_id(self, _reload_server):
        tq = _reload_server
        result = tq.tq_assign(task_id="", worker_id="worker-1")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "task_id is required" in data["message"]

    def test_assign_missing_worker_id(self, _reload_server):
        tq = _reload_server
        result = tq.tq_assign(task_id="task-1", worker_id="")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "worker_id is required" in data["message"]

    def test_assign_same_worker_twice_standalone_mode(self, _reload_server):
        """In standalone mode (no worker-registry), assigning the same worker
        to multiple tasks succeeds because there's no busy tracking."""
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_add_task(task_id="task-2", spec_path="/path/to/spec.json")
        result = tq.tq_assign(task_id="task-2", worker_id="worker-1")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "assigned"


class TestTqAssignAtomicity:
    """Test atomicity of tq_assign with worker-registry integration."""

    def test_assign_atomicity_with_worker_registry(self, _reload_server):
        """When WR DB exists and worker is ready, assigning a task should atomically
        update task status to 'assigned' AND worker status to 'busy'."""
        tq = _reload_server

        wr_server_path = PROJECT_ROOT / "mcp-servers" / "worker-registry" / "server.py"
        for mod_name in list(sys.modules):
            if "worker_registry" in mod_name:
                del sys.modules[mod_name]

        fake_home = os.path.expanduser("~")
        with patch.object(os.path, "expanduser", return_value=fake_home):
            import importlib.util

            spec = importlib.util.spec_from_file_location("worker_registry", wr_server_path)
            wr_mod = importlib.util.module_from_spec(spec)
            sys.modules["worker_registry"] = wr_mod
            spec.loader.exec_module(wr_mod)

        wr_mod.wr_register(
            worker_id="w1",
            name="Worker 1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr_mod.wr_update_status(worker_id="w1", status="ready")

        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        result = tq.tq_assign(task_id="task-1", worker_id="w1")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "assigned"

        task_result = tq.tq_get(task_id="task-1")
        task_data = json.loads(task_result)
        assert task_data["task"]["status"] == "assigned"
        assert task_data["task"]["assigned_worker"] == "w1"

        worker_result = wr_mod.wr_get(worker_id="w1")
        worker_data = json.loads(worker_result)
        assert worker_data["worker"]["status"] == "busy"

    def test_assign_standalone_no_worker_registry_db(self, _reload_server):
        """When workers-registry.db doesn't exist, tq_assign should still work
        (assign the task without updating any worker status)."""
        tq = _reload_server

        wr_db_path = os.path.join(os.path.expanduser("~"), "workers-registry.db")
        assert not os.path.exists(wr_db_path)

        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        result = tq.tq_assign(task_id="task-1", worker_id="any-worker")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "assigned"
        assert data["task"]["assigned_worker"] == "any-worker"

    def test_assign_atomicity_failure_rollback_with_registry(self, _reload_server):
        """Worker registered but not in ready state - assignment should fail
        and task should stay pending."""
        tq = _reload_server

        wr_server_path = PROJECT_ROOT / "mcp-servers" / "worker-registry" / "server.py"
        for mod_name in list(sys.modules):
            if "worker_registry" in mod_name:
                del sys.modules[mod_name]

        fake_home = os.path.expanduser("~")
        with patch.object(os.path, "expanduser", return_value=fake_home):
            import importlib.util

            spec = importlib.util.spec_from_file_location("worker_registry", wr_server_path)
            wr_mod = importlib.util.module_from_spec(spec)
            sys.modules["worker_registry"] = wr_mod
            spec.loader.exec_module(wr_mod)

        wr_mod.wr_register(
            worker_id="w1",
            name="Worker 1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )

        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")

        result = tq.tq_assign(task_id="task-1", worker_id="w1")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not available" in data["message"]

        task_result = tq.tq_get(task_id="task-1")
        task_data = json.loads(task_result)
        assert task_data["task"]["status"] == "pending"
        assert task_data["task"]["assigned_worker"] is None

        worker_result = wr_mod.wr_get(worker_id="w1")
        worker_data = json.loads(worker_result)
        assert worker_data["worker"]["status"] == "registered"

    def test_assign_worker_busy_with_registry(self, _reload_server):
        """Worker is in busy state - assignment should fail."""
        tq = _reload_server

        wr_server_path = PROJECT_ROOT / "mcp-servers" / "worker-registry" / "server.py"
        for mod_name in list(sys.modules):
            if "worker_registry" in mod_name:
                del sys.modules[mod_name]

        fake_home = os.path.expanduser("~")
        with patch.object(os.path, "expanduser", return_value=fake_home):
            import importlib.util

            spec = importlib.util.spec_from_file_location("worker_registry", wr_server_path)
            wr_mod = importlib.util.module_from_spec(spec)
            sys.modules["worker_registry"] = wr_mod
            spec.loader.exec_module(wr_mod)

        wr_mod.wr_register(
            worker_id="w1",
            name="Worker 1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr_mod.wr_update_status(worker_id="w1", status="ready")

        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="w1")

        tq.tq_add_task(task_id="task-2", spec_path="/path/to/spec.json")
        result = tq.tq_assign(task_id="task-2", worker_id="w1")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not available" in data["message"]


# ---------------------------------------------------------------------------
# tq_start tests
# ---------------------------------------------------------------------------


class TestTqStart:
    def test_start_success(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        result = tq.tq_start(task_id="task-1")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "running"
        assert data["task"]["started_at"] is not None

    def test_start_task_not_found(self, _reload_server):
        tq = _reload_server
        result = tq.tq_start(task_id="nonexistent")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["message"]

    def test_start_wrong_status(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        result = tq.tq_start(task_id="task-1")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "assigned" in data["message"]

    def test_start_completed_task(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_start(task_id="task-1")
        tq.tq_complete(task_id="task-1", result_path="/path/to/result.json")
        result = tq.tq_start(task_id="task-1")
        data = json.loads(result)
        assert data["status"] == "error"

    def test_start_missing_task_id(self, _reload_server):
        tq = _reload_server
        result = tq.tq_start(task_id="")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "task_id is required" in data["message"]


# ---------------------------------------------------------------------------
# tq_complete tests
# ---------------------------------------------------------------------------


class TestTqComplete:
    def test_complete_success(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_start(task_id="task-1")
        result = tq.tq_complete(task_id="task-1", result_path="/path/to/result.json")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "completed"
        assert data["task"]["result_path"] == "/path/to/result.json"
        assert data["task"]["assigned_worker"] is None

    def test_complete_task_not_found(self, _reload_server):
        tq = _reload_server
        result = tq.tq_complete(task_id="nonexistent", result_path="/path/to/result.json")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["message"]

    def test_complete_wrong_status(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        result = tq.tq_complete(task_id="task-1", result_path="/path/to/result.json")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "running" in data["message"]

    def test_complete_missing_task_id(self, _reload_server):
        tq = _reload_server
        result = tq.tq_complete(task_id="", result_path="/path/to/result.json")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "task_id is required" in data["message"]


# ---------------------------------------------------------------------------
# tq_fail tests
# ---------------------------------------------------------------------------


class TestTqFail:
    def test_fail_success(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_start(task_id="task-1")
        result = tq.tq_fail(task_id="task-1", error="Something went wrong")
        data = json.loads(result)
        assert data["success"] is True
        assert data["task"]["status"] == "failed"
        assert data["task"]["error"] == "Something went wrong"
        assert data["task"]["assigned_worker"] is None

    def test_fail_task_not_found(self, _reload_server):
        tq = _reload_server
        result = tq.tq_fail(task_id="nonexistent", error="Task failed")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["message"]

    def test_fail_wrong_status(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        result = tq.tq_fail(task_id="task-1", error="Task failed")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "running" in data["message"]

    def test_fail_missing_task_id(self, _reload_server):
        tq = _reload_server
        result = tq.tq_fail(task_id="", error="Task failed")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "task_id is required" in data["message"]


# ---------------------------------------------------------------------------
# tq_fail_stale_tasks tests
# ---------------------------------------------------------------------------


class TestTqFailStaleTasks:
    def test_no_stale_tasks(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_start(task_id="task-1")
        result = tq.tq_fail_stale_tasks(timeout_seconds=300)
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 0
        assert data["tasks"] == []

    def test_with_stale_tasks(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_start(task_id="task-1")

        conn = tq._get_db()
        conn.execute(
            "UPDATE tasks SET updated_at='2020-01-01T00:00:00' WHERE id='task-1'"
        )
        conn.commit()
        conn.close()

        result = tq.tq_fail_stale_tasks(timeout_seconds=300)
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 1
        assert data["tasks"][0]["id"] == "task-1"

        task_result = tq.tq_get(task_id="task-1")
        task_data = json.loads(task_result)
        assert task_data["task"]["status"] == "failed"
        assert task_data["task"]["error"] == "Task timed out"

    def test_fail_stale_tasks_invalid_timeout(self, _reload_server):
        tq = _reload_server
        result = tq.tq_fail_stale_tasks(timeout_seconds=0)
        data = json.loads(result)
        assert data["status"] == "error"
        assert "positive integer" in data["message"]

        result2 = tq.tq_fail_stale_tasks(timeout_seconds=-1)
        data2 = json.loads(result2)
        assert data2["status"] == "error"

    def test_fail_stale_tasks_already_completed_not_affected(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_start(task_id="task-1")
        tq.tq_complete(task_id="task-1", result_path="/path/to/result.json")

        result = tq.tq_fail_stale_tasks(timeout_seconds=300)
        data = json.loads(result)
        assert data["count"] == 0


# ---------------------------------------------------------------------------
# tq_list tests
# ---------------------------------------------------------------------------


class TestTqList:
    def test_list_all(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec1.json")
        tq.tq_add_task(task_id="task-2", spec_path="/path/to/spec2.json")
        tq.tq_add_task(task_id="task-3", spec_path="/path/to/spec3.json")
        result = tq.tq_list()
        data = json.loads(result)
        assert data["count"] == 3
        assert len(data["tasks"]) == 3

    def test_list_empty(self, _reload_server):
        tq = _reload_server
        result = tq.tq_list()
        data = json.loads(result)
        assert data["count"] == 0
        assert data["tasks"] == []

    def test_list_filtered_by_status(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec1.json")
        tq.tq_add_task(task_id="task-2", spec_path="/path/to/spec2.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        result = tq.tq_list(status="pending")
        data = json.loads(result)
        assert data["count"] == 1
        assert data["tasks"][0]["id"] == "task-2"
        assert data["tasks"][0]["status"] == "pending"

    def test_list_filtered_by_assigned_status(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec1.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        result = tq.tq_list(status="assigned")
        data = json.loads(result)
        assert data["count"] == 1
        assert data["tasks"][0]["id"] == "task-1"
        assert data["tasks"][0]["status"] == "assigned"


# ---------------------------------------------------------------------------
# tq_get tests
# ---------------------------------------------------------------------------


class TestTqGet:
    def test_get_success(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        result = tq.tq_get(task_id="task-1")
        data = json.loads(result)
        assert data["task"]["id"] == "task-1"
        assert data["task"]["status"] == "pending"
        assert data["task"]["spec_path"] == "/path/to/spec.json"

    def test_get_not_found(self, _reload_server):
        tq = _reload_server
        result = tq.tq_get(task_id="nonexistent")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["message"]

    def test_get_missing_task_id(self, _reload_server):
        tq = _reload_server
        result = tq.tq_get(task_id="")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "task_id is required" in data["message"]


# ---------------------------------------------------------------------------
# tq_stats tests
# ---------------------------------------------------------------------------


class TestTqStats:
    def test_stats_empty(self, _reload_server):
        tq = _reload_server
        result = tq.tq_stats()
        data = json.loads(result)
        assert data["total_tasks"] == 0
        assert data["completed_tasks"] == 0
        assert data["failed_tasks"] == 0
        assert data["pending_tasks"] == 0
        assert data["assigned_tasks"] == 0
        assert data["running_tasks"] == 0

    def test_stats_with_tasks(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec1.json")
        tq.tq_add_task(task_id="task-2", spec_path="/path/to/spec2.json")
        tq.tq_add_task(task_id="task-3", spec_path="/path/to/spec3.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_start(task_id="task-1")
        tq.tq_complete(task_id="task-1", result_path="/path/to/result.json")
        tq.tq_assign(task_id="task-2", worker_id="worker-1")
        tq.tq_start(task_id="task-2")
        tq.tq_fail(task_id="task-2", error="Failed")
        result = tq.tq_stats()
        data = json.loads(result)
        assert data["total_tasks"] == 3
        assert data["completed_tasks"] == 1
        assert data["failed_tasks"] == 1
        assert data["pending_tasks"] == 1
        assert data["assigned_tasks"] == 0
        assert data["running_tasks"] == 0


# ---------------------------------------------------------------------------
# tq_set_mode / tq_get_mode tests
# ---------------------------------------------------------------------------


class TestTqMode:
    def test_set_mode_accept(self, _reload_server):
        tq = _reload_server
        result = tq.tq_set_mode(mode="dispatching")
        data = json.loads(result)
        assert data["success"] is True
        assert data["mode"] == "dispatching"

    def test_set_mode_drain(self, _reload_server):
        tq = _reload_server
        result = tq.tq_set_mode(mode="monitoring")
        data = json.loads(result)
        assert data["success"] is True
        assert data["mode"] == "monitoring"

    def test_set_mode_invalid(self, _reload_server):
        tq = _reload_server
        result = tq.tq_set_mode(mode="invalid_mode")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Invalid mode" in data["message"]

    def test_get_mode_default(self, _reload_server):
        tq = _reload_server
        result = tq.tq_get_mode()
        data = json.loads(result)
        assert data["mode"] == "idle"

    def test_set_and_get_mode(self, _reload_server):
        tq = _reload_server
        tq.tq_set_mode(mode="dispatching")
        result = tq.tq_get_mode()
        data = json.loads(result)
        assert data["mode"] == "dispatching"


# ---------------------------------------------------------------------------
# handle_tool_call tests
# ---------------------------------------------------------------------------


class TestHandleToolCall:
    def test_unknown_tool_returns_error(self, _reload_server):
        tq = _reload_server
        result = tq.handle_tool_call("nonexistent_tool", {})
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Unknown tool" in data["message"]


# ---------------------------------------------------------------------------
# TOOL_DEFINITIONS tests
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    def test_all_tools_defined(self, _reload_server):
        tq = _reload_server
        names = {t["name"] for t in tq.TOOL_DEFINITIONS}
        expected = {
            "tq_add_task",
            "tq_assign",
            "tq_start",
            "tq_complete",
            "tq_fail",
            "tq_fail_stale_tasks",
            "tq_list",
            "tq_get",
            "tq_stats",
            "tq_set_mode",
            "tq_get_mode",
        }
        assert expected.issubset(names)

    def test_tq_add_task_schema_required_fields(self, _reload_server):
        tq = _reload_server
        schema = next(
            t["input_schema"] for t in tq.TOOL_DEFINITIONS if t["name"] == "tq_add_task"
        )
        required = schema["required"]
        assert "task_id" in required
        assert "spec_path" in required

    def test_tq_assign_schema_required_fields(self, _reload_server):
        tq = _reload_server
        schema = next(
            t["input_schema"] for t in tq.TOOL_DEFINITIONS if t["name"] == "tq_assign"
        )
        required = schema["required"]
        assert "task_id" in required
        assert "worker_id" in required

    def test_tq_complete_schema_required_fields(self, _reload_server):
        tq = _reload_server
        schema = next(
            t["input_schema"] for t in tq.TOOL_DEFINITIONS if t["name"] == "tq_complete"
        )
        required = schema["required"]
        assert "task_id" in required
        assert "result_path" in required

    def test_tq_fail_schema_required_fields(self, _reload_server):
        tq = _reload_server
        schema = next(
            t["input_schema"] for t in tq.TOOL_DEFINITIONS if t["name"] == "tq_fail"
        )
        required = schema["required"]
        assert "task_id" in required
        assert "error" in required


# ---------------------------------------------------------------------------
# SQLite schema tests
# ---------------------------------------------------------------------------


class TestSQLiteSchema:
    def test_tasks_table_exists(self, _reload_server):
        tq = _reload_server
        conn = tq._get_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None

    def test_tasks_table_schema(self, _reload_server):
        tq = _reload_server
        conn = tq._get_db()
        cursor = conn.execute("PRAGMA table_info(tasks)")
        columns = {row["name"] for row in cursor.fetchall()}
        conn.close()
        expected_columns = {
            "id", "spec_path", "assigned_worker", "status", "result_path",
            "error", "started_at", "created_at", "updated_at"
        }
        assert expected_columns.issubset(columns)

    def test_status_index_exists(self, _reload_server):
        tq = _reload_server
        conn = tq._get_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tasks_status'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None

    def test_assigned_worker_index_exists(self, _reload_server):
        tq = _reload_server
        conn = tq._get_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tasks_assigned_worker'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None

    def test_updated_at_index_exists(self, _reload_server):
        tq = _reload_server
        conn = tq._get_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tasks_updated_at'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None


# ---------------------------------------------------------------------------
# Full lifecycle tests
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_pending_assigned_running_completed(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        task = json.loads(tq.tq_get(task_id="task-1"))
        assert task["task"]["status"] == "pending"

        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        task = json.loads(tq.tq_get(task_id="task-1"))
        assert task["task"]["status"] == "assigned"

        tq.tq_start(task_id="task-1")
        task = json.loads(tq.tq_get(task_id="task-1"))
        assert task["task"]["status"] == "running"

        tq.tq_complete(task_id="task-1", result_path="/path/to/result.json")
        task = json.loads(tq.tq_get(task_id="task-1"))
        assert task["task"]["status"] == "completed"

    def test_pending_assigned_running_failed(self, _reload_server):
        tq = _reload_server
        tq.tq_add_task(task_id="task-1", spec_path="/path/to/spec.json")
        tq.tq_assign(task_id="task-1", worker_id="worker-1")
        tq.tq_start(task_id="task-1")
        tq.tq_fail(task_id="task-1", error="Task failed")
        task = json.loads(tq.tq_get(task_id="task-1"))
        assert task["task"]["status"] == "failed"
        assert task["task"]["error"] == "Task failed"
