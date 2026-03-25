"""Tests for the worker-registry MCP server.

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

_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "worker-registry" / "server.py"


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path, monkeypatch):
    """Redirect ~/.hermes/ to a temp directory for every test."""
    fake_home = tmp_path / ".hermes" / "hiclaw"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(os.path, "expanduser", lambda _: str(fake_home))
    yield str(fake_home)


@pytest.fixture()
def _reload_server(_isolate_registry):
    """Load the server module with patched expanduser so ~/.hermes/ → tmp."""
    for mod_name in list(sys.modules):
        if "worker_registry" in mod_name:
            del sys.modules[mod_name]

    fake_home = os.path.expanduser("~")
    with patch.object(os.path, "expanduser", return_value=fake_home):
        import importlib.util

        spec = importlib.util.spec_from_file_location("worker_registry", _SERVER_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["worker_registry"] = mod
        spec.loader.exec_module(mod)

    return mod


class TestWrRegister:
    def test_register_success(self, _reload_server):
        wr = _reload_server
        result = wr.wr_register(
            worker_id="w1",
            name="Test Worker",
            capabilities=["code", "file"],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["worker"]["worker_id"] == "w1"
        assert data["worker"]["status"] == "registered"

    def test_register_duplicate_rejected(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_register(
            worker_id="w1",
            name="W1 Again",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "already registered" in data["message"]

    def test_register_missing_worker_id(self, _reload_server):
        wr = _reload_server
        result = wr.wr_register(
            worker_id="",
            name="No ID",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        data = json.loads(result)
        assert data["status"] == "error"

    def test_register_missing_name(self, _reload_server):
        wr = _reload_server
        result = wr.wr_register(
            worker_id="w1",
            name="",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        data = json.loads(result)
        assert data["status"] == "error"

    def test_register_invalid_capabilities(self, _reload_server):
        wr = _reload_server
        result = wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities="not-a-list",
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        data = json.loads(result)
        assert data["status"] == "error"


class TestWrHeartbeat:
    def test_heartbeat_success(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_heartbeat(worker_id="w1")
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["worker_id"] == "w1"

    def test_heartbeat_unknown_worker(self, _reload_server):
        wr = _reload_server
        result = wr.wr_heartbeat(worker_id="unknown")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["message"]

    def test_heartbeat_recovers_offline(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_update_status(worker_id="w1", status="offline")
        result = wr.wr_heartbeat(worker_id="w1")
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["worker_status"] == "ready"


class TestWrUpdateStatus:
    def test_update_status_valid_transition(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_update_status(worker_id="w1", status="ready")
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["worker_status"] == "ready"

    def test_update_status_invalid_transition(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_update_status(worker_id="w1", status="busy")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Invalid status transition" in data["message"]

    def test_update_status_unknown_worker(self, _reload_server):
        wr = _reload_server
        result = wr.wr_update_status(worker_id="unknown", status="ready")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["message"]

    def test_update_status_invalid_status_value(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_update_status(worker_id="w1", status="not_a_status")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Invalid status" in data["message"]

    def test_update_status_with_message(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_update_status(worker_id="w1", status="ready", message="all good")
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["message"] == "all good"


class TestWrRemove:
    def test_remove_success(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_remove(worker_id="w1")
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["removed"] is True

    def test_remove_unknown_worker(self, _reload_server):
        wr = _reload_server
        result = wr.wr_remove(worker_id="unknown")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["message"]

    def test_remove_then_get(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_remove(worker_id="w1")
        result = wr.wr_get(worker_id="w1")
        data = json.loads(result)
        assert data["status"] == "error"


class TestWrList:
    def test_list_empty(self, _reload_server):
        wr = _reload_server
        result = wr.wr_list()
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 0
        assert data["workers"] == []

    def test_list_all(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_register(
            worker_id="w2",
            name="W2",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w2:h.local",
            device_id="dev2",
        )
        result = wr.wr_list()
        data = json.loads(result)
        assert data["count"] == 2

    def test_list_filtered_by_status(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_register(
            worker_id="w2",
            name="W2",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w2:h.local",
            device_id="dev2",
        )
        wr.wr_update_status(worker_id="w1", status="ready")
        result = wr.wr_list(status="ready")
        data = json.loads(result)
        assert data["count"] == 1
        assert data["workers"][0]["worker_id"] == "w1"


class TestWrGet:
    def test_get_success(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=["code"],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_get(worker_id="w1")
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["worker"]["worker_id"] == "w1"
        assert data["worker"]["capabilities"] == ["code"]

    def test_get_unknown_worker(self, _reload_server):
        wr = _reload_server
        result = wr.wr_get(worker_id="unknown")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not found" in data["message"]


class TestWrGetStaleWorkers:
    def test_no_stale_workers(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_update_status(worker_id="w1", status="ready")
        result = wr.wr_get_stale_workers(timeout_seconds=300)
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 0
        assert data["workers"] == []

    def test_stale_worker_marked_offline(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        conn = wr._get_db()
        conn.execute(
            "UPDATE workers SET last_seen_at='2020-01-01T00:00:00' WHERE worker_id='w1'"
        )
        conn.commit()
        conn.close()

        result = wr.wr_get_stale_workers(timeout_seconds=300)
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["count"] == 1
        assert data["workers"][0]["worker_id"] == "w1"

        get_result = wr.wr_get(worker_id="w1")
        get_data = json.loads(get_result)
        assert get_data["worker"]["status"] == "offline"

    def test_stale_workers_excludes_already_offline(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        conn = wr._get_db()
        conn.execute(
            "UPDATE workers SET status='offline', last_seen_at='2020-01-01T00:00:00' "
            "WHERE worker_id='w1'"
        )
        conn.commit()
        conn.close()

        result = wr.wr_get_stale_workers(timeout_seconds=300)
        data = json.loads(result)
        assert data["count"] == 0

    def test_stale_workers_invalid_timeout(self, _reload_server):
        wr = _reload_server
        result = wr.wr_get_stale_workers(timeout_seconds=0)
        data = json.loads(result)
        assert data["status"] == "error"
        assert "positive integer" in data["message"]

        result2 = wr.wr_get_stale_workers(timeout_seconds=-1)
        data2 = json.loads(result2)
        assert data2["status"] == "error"


class TestStatusTransitions:
    def test_registered_to_ready(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_update_status(worker_id="w1", status="ready")
        assert json.loads(result)["status"] == "ok"

    def test_registered_to_error(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_update_status(worker_id="w1", status="error")
        assert json.loads(result)["status"] == "ok"

    def test_registered_to_offline(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_update_status(worker_id="w1", status="offline")
        assert json.loads(result)["status"] == "ok"

    def test_ready_to_busy(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_update_status(worker_id="w1", status="ready")
        result = wr.wr_update_status(worker_id="w1", status="busy")
        assert json.loads(result)["status"] == "ok"

    def test_ready_to_offline(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_update_status(worker_id="w1", status="ready")
        result = wr.wr_update_status(worker_id="w1", status="offline")
        assert json.loads(result)["status"] == "ok"

    def test_busy_back_to_ready(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_update_status(worker_id="w1", status="ready")
        wr.wr_update_status(worker_id="w1", status="busy")
        result = wr.wr_update_status(worker_id="w1", status="ready")
        assert json.loads(result)["status"] == "ok"

    def test_busy_to_error(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_update_status(worker_id="w1", status="ready")
        wr.wr_update_status(worker_id="w1", status="busy")
        result = wr.wr_update_status(worker_id="w1", status="error")
        assert json.loads(result)["status"] == "ok"

    def test_error_to_ready(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_update_status(worker_id="w1", status="error")
        result = wr.wr_update_status(worker_id="w1", status="ready")
        assert json.loads(result)["status"] == "ok"

    def test_error_to_offline(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_update_status(worker_id="w1", status="error")
        result = wr.wr_update_status(worker_id="w1", status="offline")
        assert json.loads(result)["status"] == "ok"

    def test_offline_to_ready(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        wr.wr_update_status(worker_id="w1", status="offline")
        result = wr.wr_update_status(worker_id="w1", status="ready")
        assert json.loads(result)["status"] == "ok"

    def test_noop_transition_allowed(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        result = wr.wr_update_status(worker_id="w1", status="registered")
        assert json.loads(result)["status"] == "ok"


class TestToolDefinitions:
    def test_all_tools_defined(self, _reload_server):
        wr = _reload_server
        names = {t["name"] for t in wr.TOOL_DEFINITIONS}
        expected = {
            "wr_register",
            "wr_heartbeat",
            "wr_update_status",
            "wr_remove",
            "wr_list",
            "wr_get",
            "wr_get_stale_workers",
        }
        assert expected.issubset(names)

    def test_wr_register_schema_required_fields(self, _reload_server):
        wr = _reload_server
        schema = next(
            t["input_schema"] for t in wr.TOOL_DEFINITIONS if t["name"] == "wr_register"
        )
        required = schema["required"]
        assert "worker_id" in required
        assert "name" in required
        assert "capabilities" in required
        assert "version" in required
        assert "matrix_user_id" in required
        assert "device_id" in required

    def test_handle_tool_call_unknown_tool(self, _reload_server):
        wr = _reload_server
        result = wr.handle_tool_call("nonexistent_tool", {})
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Unknown tool" in data["message"]


class TestSQLiteSchema:
    def test_workers_table_exists(self, _reload_server):
        wr = _reload_server
        conn = wr._get_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workers'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None

    def test_status_index_exists(self, _reload_server):
        wr = _reload_server
        conn = wr._get_db()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_workers_status'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None

    def test_heartbeat_updates_last_seen_at(self, _reload_server):
        wr = _reload_server
        wr.wr_register(
            worker_id="w1",
            name="W1",
            capabilities=[],
            version="1.0.0",
            matrix_user_id="@w1:h.local",
            device_id="dev1",
        )
        conn = wr._get_db()
        conn.execute(
            "UPDATE workers SET last_seen_at='2020-01-01T00:00:00' WHERE worker_id='w1'"
        )
        conn.commit()
        conn.close()

        result = wr.wr_heartbeat(worker_id="w1")
        data = json.loads(result)
        assert data["last_seen_at"] != "2020-01-01T00:00:00"
