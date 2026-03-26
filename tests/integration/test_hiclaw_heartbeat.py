"""Integration tests for hiClaw worker heartbeat.

Uses the same _isolate_registry + _reload_server pattern from test_worker_registry.py.
These tests verify the hiClaw-specific heartbeat behavior including:
- Heartbeat updates last_seen_at
- Offline worker recovery via heartbeat
- Unknown worker heartbeat handling
- WorkerMessageParser.parse_heartbeat() protocol parsing
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


class TestHeartbeat:
    """Test worker heartbeat via wr_heartbeat."""

    def test_worker_sends_heartbeat_last_seen_at_updated(self, _reload_server):
        """Worker sends heartbeat → last_seen_at updated."""
        wr = _reload_server

        # Register a worker first
        wr.wr_register(
            worker_id="worker-hb",
            name="Heartbeat Worker",
            capabilities=["code"],
            version="1.0.0",
            matrix_user_id="@hb:hermes.local",
            device_id="dev-hb",
        )

        # Set a known old last_seen_at
        conn = wr._get_db()
        conn.execute(
            "UPDATE workers SET last_seen_at='2020-01-01T00:00:00' WHERE worker_id='worker-hb'"
        )
        conn.commit()
        conn.close()

        # Verify old timestamp
        get_result_before = wr.wr_get(worker_id="worker-hb")
        get_data_before = json.loads(get_result_before)
        old_last_seen = get_data_before["worker"]["last_seen_at"]
        assert old_last_seen == "2020-01-01T00:00:00"

        # Worker sends heartbeat
        hb_result = wr.wr_heartbeat(worker_id="worker-hb")
        hb_data = json.loads(hb_result)

        # Verify heartbeat succeeded
        assert hb_data["status"] == "ok"
        assert hb_data["worker_id"] == "worker-hb"

        # Verify last_seen_at is updated (not the old value anymore)
        get_result_after = wr.wr_get(worker_id="worker-hb")
        get_data_after = json.loads(get_result_after)
        new_last_seen = get_data_after["worker"]["last_seen_at"]
        assert new_last_seen != "2020-01-01T00:00:00"
        assert new_last_seen == hb_data["last_seen_at"]


class TestHeartbeatOfflineRecovery:
    """Test heartbeat recovery for offline workers."""

    def test_worker_was_offline_heartbeat_status_changes_to_ready(self, _reload_server):
        """Worker was offline, heartbeat → status changes to ready."""
        wr = _reload_server

        # Register a worker and set it to offline
        wr.wr_register(
            worker_id="worker-offline",
            name="Offline Worker",
            capabilities=["code"],
            version="1.0.0",
            matrix_user_id="@offline:hermes.local",
            device_id="dev-offline",
        )

        # Set worker to offline status
        wr.wr_update_status(worker_id="worker-offline", status="offline")

        # Verify worker is offline
        get_result_before = wr.wr_get(worker_id="worker-offline")
        get_data_before = json.loads(get_result_before)
        assert get_data_before["worker"]["status"] == "offline"

        # Worker sends heartbeat - should auto-recover to ready
        hb_result = wr.wr_heartbeat(worker_id="worker-offline")
        hb_data = json.loads(hb_result)

        # Verify heartbeat succeeded and status changed to ready
        assert hb_data["status"] == "ok"
        assert hb_data["worker_id"] == "worker-offline"
        assert hb_data["worker_status"] == "ready"

        # Verify via wr_get
        get_result_after = wr.wr_get(worker_id="worker-offline")
        get_data_after = json.loads(get_result_after)
        assert get_data_after["worker"]["status"] == "ready"


class TestHeartbeatUnknownWorker:
    """Test heartbeat handling for unknown workers."""

    def test_heartbeat_unknown_worker_handled_gracefully(self, _reload_server):
        """Heartbeat for unknown worker → handled gracefully."""
        wr = _reload_server

        # Send heartbeat for unknown worker
        hb_result = wr.wr_heartbeat(worker_id="unknown-worker")
        hb_data = json.loads(hb_result)

        # Should return error status but not crash
        assert hb_data["status"] == "error"
        assert "not found" in hb_data["message"].lower()
        assert hb_data["worker_id"] == "unknown-worker"


class TestHeartbeatProtocolParsing:
    """Test WorkerMessageParser.parse_heartbeat() protocol parsing."""

    def test_parse_heartbeat_correctly_parses_protocol_format(self):
        """Verify WorkerMessageParser.parse_heartbeat() correctly parses //heartbeat\\n\\nWorker: {id}\\nStatus: {status}."""
        from gateway.hiclaw.protocol import WorkerMessageParser

        # Test the exact format: //heartbeat\n\nWorker: {id}\nStatus: {status}
        content = "//heartbeat\n\nWorker: worker-test\nStatus: alive"
        result = WorkerMessageParser.parse_heartbeat(content)

        assert result is not None
        assert result["worker"] == "worker-test"
        assert result["status"] == "alive"
        assert result["message"] == ""

    def test_parse_heartbeat_with_message_body(self):
        """Test parse_heartbeat with additional message body."""
        from gateway.hiclaw.protocol import WorkerMessageParser

        content = "//heartbeat\n\nWorker: worker-test\nStatus: busy\nMessage: processing task 123"
        result = WorkerMessageParser.parse_heartbeat(content)

        assert result is not None
        assert result["worker"] == "worker-test"
        assert result["status"] == "busy"
        assert "processing task 123" in result["message"]

    def test_parse_heartbeat_with_minimal_status(self):
        """Test parse_heartbeat with minimal status values."""
        from gateway.hiclaw.protocol import WorkerMessageParser

        # Test different valid statuses
        for status_value in ["alive", "ok", "ping", "heartbeat"]:
            content = f"//heartbeat\n\nWorker: worker-1\nStatus: {status_value}"
            result = WorkerMessageParser.parse_heartbeat(content)
            assert result is not None, f"Failed for status: {status_value}"
            assert result["worker"] == "worker-1"
            assert result["status"] == status_value

    def test_parse_heartbeat_legacy_json_format(self):
        """Test parse_heartbeat with legacy JSON heartbeat format."""
        from gateway.hiclaw.protocol import WorkerMessageParser

        content = json.dumps(
            {
                "type": "worker.heartbeat",
                "worker_id": "legacy-worker",
                "status": "alive",
                "message": "legacy heartbeat",
            }
        )
        result = WorkerMessageParser.parse_heartbeat(content)

        assert result is not None
        assert result["worker"] == "legacy-worker"
        assert result["status"] == "alive"
        assert result["message"] == "legacy heartbeat"

    def test_parse_heartbeat_invalid_content_returns_none(self):
        """Test parse_heartbeat returns None for invalid content."""
        from gateway.hiclaw.protocol import WorkerMessageParser

        # Empty content
        assert WorkerMessageParser.parse_heartbeat("") is None

        # Non-heartbeat content
        assert (
            WorkerMessageParser.parse_heartbeat("//task-result\n\nTask ID: 123") is None
        )

        # JSON without type field
        content = json.dumps({"worker_id": "w1", "status": "alive"})
        assert WorkerMessageParser.parse_heartbeat(content) is None

        # Missing worker field
        content = json.dumps({"type": "worker.heartbeat", "status": "alive"})
        assert WorkerMessageParser.parse_heartbeat(content) is None

        # Missing status field
        content = json.dumps({"type": "worker.heartbeat", "worker_id": "w1"})
        assert WorkerMessageParser.parse_heartbeat(content) is None
