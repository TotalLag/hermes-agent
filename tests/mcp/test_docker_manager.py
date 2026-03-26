"""Tests for the docker-manager MCP server.

Uses a temporary ~/.hermes/ directory so tests never touch the real one.
Mocks the docker library to avoid requiring actual Docker.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_SERVER_PATH = PROJECT_ROOT / "mcp-servers" / "docker-manager" / "server.py"


@pytest.fixture(autouse=True)
def _isolate_docker(tmp_path, monkeypatch):
    """Redirect ~/.hermes/ to a temp directory for every test."""
    fake_home = tmp_path / ".hermes" / "hiclaw"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(os.path, "expanduser", lambda _: str(fake_home))
    yield str(fake_home)


@pytest.fixture()
def _reload_server(_isolate_docker):
    """Load the server module with patched expanduser so ~/.hermes/ → tmp."""
    for mod_name in list(sys.modules):
        if "docker_manager" in mod_name or "docker" in mod_name:
            del sys.modules[mod_name]

    fake_home = os.path.expanduser("~")

    # Create mock docker client and containers
    mock_client = MagicMock()
    mock_ping_result = MagicMock()
    mock_client.ping.return_value = True
    mock_client.version.return_value = {"Version": "24.0.0", "ApiVersion": "1.43"}

    # Create mock containers list
    mock_containers = []
    mock_client.containers.list.return_value = mock_containers

    with patch.object(os.path, "expanduser", return_value=fake_home):
        with patch("docker.DockerClient", return_value=mock_client):
            with patch("docker.from_env", return_value=mock_client):
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    "docker_manager", _SERVER_PATH
                )
                mod = importlib.util.module_from_spec(spec)
                sys.modules["docker_manager"] = mod
                spec.loader.exec_module(mod)

                # Initialize the docker client (normally called in main())
                mod._init_docker_client()

    return mod, mock_client


def _make_mock_container(
    name="test-container",
    container_id="abc123def456",
    status="running",
    image_tag="hermes-worker:latest",
    has_tags=True,
):
    """Create a mock container object."""
    mock_container = MagicMock()
    mock_container.name = name
    mock_container.id = container_id
    mock_container.status = status
    mock_container.short_id = container_id[:12]

    mock_image = MagicMock()
    if has_tags:
        mock_image.tags = [image_tag]
    else:
        mock_image.tags = []
    mock_image.short_id = container_id[:12]
    mock_container.image = mock_image

    mock_container.attrs = {
        "Id": container_id,
        "Name": f"/{name}",
        "Created": "2024-01-01T00:00:00.000000000Z",
        "State": {"Status": status, "Running": True, "Pid": 12345},
        "Config": {
            "Env": [f"HERMES_WORKER_ID={name}"],
            "Image": image_tag,
        },
    }
    return mock_container


class TestDockerListContainers:
    """Tests for docker_list_containers tool."""

    def test_list_containers_empty(self, _reload_server):
        dm, mock_client = _reload_server
        mock_client.containers.list.return_value = []

        result = dm.handle_docker_list_containers({})
        data = json.loads(result)

        assert "containers" in data
        assert data["containers"] == []
        mock_client.containers.list.assert_called_once()

    def test_list_containers_with_hermes_workers(self, _reload_server):
        dm, mock_client = _reload_server
        mock_container = _make_mock_container(
            name="hermes-worker-test1", status="running"
        )
        mock_container2 = _make_mock_container(
            name="hermes-worker-test2", status="stopped"
        )
        mock_client.containers.list.return_value = [mock_container, mock_container2]

        result = dm.handle_docker_list_containers({})
        data = json.loads(result)

        assert len(data["containers"]) == 2
        assert data["containers"][0]["name"] == "hermes-worker-test1"
        assert data["containers"][0]["status"] == "running"
        assert data["containers"][1]["name"] == "hermes-worker-test2"
        assert data["containers"][1]["status"] == "stopped"

    def test_list_containers_filter_running(self, _reload_server):
        dm, mock_client = _reload_server
        mock_running = _make_mock_container(
            name="hermes-worker-running", status="running"
        )
        mock_stopped = _make_mock_container(
            name="hermes-worker-stopped", status="exited"
        )
        mock_client.containers.list.return_value = [mock_running, mock_stopped]

        result = dm.handle_docker_list_containers({"status": "running"})
        data = json.loads(result)

        assert len(data["containers"]) == 1
        assert data["containers"][0]["name"] == "hermes-worker-running"
        assert data["containers"][0]["status"] == "running"

    def test_list_containers_filter_stopped(self, _reload_server):
        dm, mock_client = _reload_server
        mock_running = _make_mock_container(
            name="hermes-worker-running", status="running"
        )
        mock_stopped = _make_mock_container(
            name="hermes-worker-stopped", status="exited"
        )
        mock_client.containers.list.return_value = [mock_running, mock_stopped]

        result = dm.handle_docker_list_containers({"status": "stopped"})
        data = json.loads(result)

        assert len(data["containers"]) == 1
        assert data["containers"][0]["name"] == "hermes-worker-stopped"
        assert data["containers"][0]["status"] == "exited"

    def test_list_containers_filter_all(self, _reload_server):
        dm, mock_client = _reload_server
        mock_running = _make_mock_container(
            name="hermes-worker-running", status="running"
        )
        mock_stopped = _make_mock_container(
            name="hermes-worker-stopped", status="exited"
        )
        mock_client.containers.list.return_value = [mock_running, mock_stopped]

        result = dm.handle_docker_list_containers({"status": "all"})
        data = json.loads(result)

        assert len(data["containers"]) == 2

    def test_list_containers_docker_exception(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import DockerException

        mock_client.containers.list.side_effect = DockerException("Connection failed")

        result = dm.handle_docker_list_containers({})
        data = json.loads(result)

        assert "error" in data
        assert "Failed to list containers" in data["error"]

    def test_list_containers_no_tags(self, _reload_server):
        dm, mock_client = _reload_server
        mock_container = _make_mock_container(has_tags=False)
        mock_client.containers.list.return_value = [mock_container]

        result = dm.handle_docker_list_containers({})
        data = json.loads(result)

        assert len(data["containers"]) == 1
        # Should use short_id when no tags
        assert data["containers"][0]["image"] == mock_container.short_id


class TestDockerCreateWorker:
    """Tests for docker_create_worker tool."""

    def test_create_worker_success(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("Not found")

        mock_container = _make_mock_container(name="hermes-worker-testworker")
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = mock_container

        result = dm.handle_docker_create_worker(
            {
                "name": "testworker",
                "capabilities": ["code", "file"],
                "matrix_user_id": "@worker:localhost",
            }
        )
        data = json.loads(result)

        assert data["status"] == "created"
        assert data["id"] == mock_container.id
        assert data["name"] == mock_container.name
        mock_client.containers.run.assert_called_once()

    def test_create_worker_already_exists(self, _reload_server):
        dm, mock_client = _reload_server
        existing_container = _make_mock_container()
        mock_client.containers.get.return_value = existing_container

        result = dm.handle_docker_create_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "already exists" in data["error"]

    def test_create_worker_missing_name(self, _reload_server):
        dm, mock_client = _reload_server

        result = dm.handle_docker_create_worker({})
        data = json.loads(result)

        assert "error" in data
        assert "name is required" in data["error"]

    def test_create_worker_pulls_image_if_not_found(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("Not found")
        mock_client.images.get.side_effect = NotFound("Image not found")

        mock_container = _make_mock_container()
        mock_client.containers.run.return_value = mock_container

        result = dm.handle_docker_create_worker(
            {"name": "testworker", "image": "custom-image:latest"}
        )
        data = json.loads(result)

        assert data["status"] == "created"
        mock_client.images.pull.assert_called_once_with("custom-image:latest")

    def test_create_worker_with_default_image(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("Not found")

        mock_container = _make_mock_container()
        mock_client.containers.run.return_value = mock_container
        mock_client.images.get.return_value = MagicMock()

        result = dm.handle_docker_create_worker({"name": "testworker"})
        data = json.loads(result)

        assert data["status"] == "created"
        # Check that the run was called with default image
        call_args = mock_client.containers.run.call_args
        assert call_args.kwargs.get("image") == dm.DEFAULT_WORKER_IMAGE

    def test_create_worker_docker_exception(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound, DockerException

        mock_client.containers.get.side_effect = NotFound("Not found")
        mock_client.containers.run.side_effect = DockerException("Failed to create")

        result = dm.handle_docker_create_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Failed to create container" in data["error"]

    def test_create_worker_check_existing_exception(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import DockerException

        mock_client.containers.get.side_effect = DockerException("Connection error")

        result = dm.handle_docker_create_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Failed to check existing container" in data["error"]

    def test_create_worker_sets_correct_env_vars(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("Not found")

        mock_container = _make_mock_container()
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = mock_container

        result = dm.handle_docker_create_worker(
            {
                "name": "testworker",
                "capabilities": ["code", "file"],
                "matrix_user_id": "@worker:localhost",
            }
        )
        data = json.loads(result)

        assert data["status"] == "created"
        call_args = mock_client.containers.run.call_args
        env_vars = call_args.kwargs.get("env", [])

        assert "HERMES_WORKER_ID=testworker" in env_vars
        assert "HERMES_WORKER_NAME=testworker" in env_vars
        assert "HERMES_WORKER_CAPABILITIES=code,file" in env_vars
        assert "HERMES_WORKER_VERSION=1.0.0" in env_vars
        assert "MATRIX_USER_ID=@worker:localhost" in env_vars


class TestDockerRemoveWorker:
    """Tests for docker_remove_worker tool."""

    def test_remove_worker_success_stopped(self, _reload_server):
        dm, mock_client = _reload_server
        mock_container = _make_mock_container(status="exited")
        mock_client.containers.get.return_value = mock_container

        result = dm.handle_docker_remove_worker({"name": "testworker"})
        data = json.loads(result)

        assert data["status"] == "removed"
        assert data["name"] == "hermes-worker-testworker"
        mock_container.stop.assert_not_called()
        mock_container.remove.assert_called_once()

    def test_remove_worker_success_running(self, _reload_server):
        dm, mock_client = _reload_server
        mock_container = _make_mock_container(status="running")
        mock_client.containers.get.return_value = mock_container

        result = dm.handle_docker_remove_worker({"name": "testworker"})
        data = json.loads(result)

        assert data["status"] == "removed"
        mock_container.stop.assert_called_once_with(timeout=10)
        mock_container.remove.assert_called_once()

    def test_remove_worker_not_found(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("Container not found")

        result = dm.handle_docker_remove_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"]

    def test_remove_worker_missing_name(self, _reload_server):
        dm, mock_client = _reload_server

        result = dm.handle_docker_remove_worker({})
        data = json.loads(result)

        assert "error" in data
        assert "name is required" in data["error"]

    def test_remove_worker_docker_exception(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import DockerException

        mock_client.containers.get.side_effect = DockerException("Docker error")

        result = dm.handle_docker_remove_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Failed to remove container" in data["error"]


class TestDockerInspectWorker:
    """Tests for docker_inspect_worker tool."""

    def test_inspect_worker_success(self, _reload_server):
        dm, mock_client = _reload_server
        mock_container = _make_mock_container()
        mock_client.containers.get.return_value = mock_container

        result = dm.handle_docker_inspect_worker({"name": "testworker"})
        data = json.loads(result)

        assert data["id"] == mock_container.attrs["Id"]
        assert data["name"] == "test-container"  # Name is lstripped of "/"
        assert "state" in data
        assert "config" in data
        assert "created" in data

    def test_inspect_worker_not_found(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("Container not found")

        result = dm.handle_docker_inspect_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"]

    def test_inspect_worker_missing_name(self, _reload_server):
        dm, mock_client = _reload_server

        result = dm.handle_docker_inspect_worker({})
        data = json.loads(result)

        assert "error" in data
        assert "name is required" in data["error"]

    def test_inspect_worker_docker_exception(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import DockerException

        mock_client.containers.get.side_effect = DockerException("Docker error")

        result = dm.handle_docker_inspect_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Failed to inspect container" in data["error"]


class TestDockerGetWorkerLogs:
    """Tests for docker_get_worker_logs tool."""

    def test_get_logs_success(self, _reload_server):
        dm, mock_client = _reload_server
        mock_container = _make_mock_container()
        mock_container.logs.return_value = b"2024-01-01T00:00:00.000000000Z Log line 1\n2024-01-01T00:00:01.000000000Z Log line 2\n"
        mock_client.containers.get.return_value = mock_container

        result = dm.handle_docker_get_worker_logs({"name": "testworker"})
        data = json.loads(result)

        assert data["name"] == "hermes-worker-testworker"
        assert data["lines"] == 100
        assert "Log line 1" in data["logs"]
        assert "Log line 2" in data["logs"]
        mock_container.logs.assert_called_once_with(tail=100, timestamps=True)

    def test_get_logs_custom_lines(self, _reload_server):
        dm, mock_client = _reload_server
        mock_container = _make_mock_container()
        mock_container.logs.return_value = b"line\n"
        mock_client.containers.get.return_value = mock_container

        result = dm.handle_docker_get_worker_logs({"name": "testworker", "lines": 50})
        data = json.loads(result)

        assert data["lines"] == 50
        mock_container.logs.assert_called_once_with(tail=50, timestamps=True)

    def test_get_logs_not_found(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("Container not found")

        result = dm.handle_docker_get_worker_logs({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"]

    def test_get_logs_missing_name(self, _reload_server):
        dm, mock_client = _reload_server

        result = dm.handle_docker_get_worker_logs({})
        data = json.loads(result)

        assert "error" in data
        assert "name is required" in data["error"]

    def test_get_logs_docker_exception(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import DockerException

        mock_client.containers.get.side_effect = DockerException("Docker error")

        result = dm.handle_docker_get_worker_logs({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Failed to get logs" in data["error"]


class TestDockerRestartWorker:
    """Tests for docker_restart_worker tool."""

    def test_restart_worker_success(self, _reload_server):
        dm, mock_client = _reload_server
        mock_container = _make_mock_container(status="running")
        mock_client.containers.get.return_value = mock_container

        result = dm.handle_docker_restart_worker({"name": "testworker"})
        data = json.loads(result)

        assert data["status"] == "restarted"
        assert data["name"] == "hermes-worker-testworker"
        mock_container.restart.assert_called_once_with(timeout=10)

    def test_restart_worker_not_running(self, _reload_server):
        dm, mock_client = _reload_server
        mock_container = _make_mock_container(status="exited")
        mock_client.containers.get.return_value = mock_container

        result = dm.handle_docker_restart_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "not running" in data["error"]
        assert "exited" in data["error"]
        mock_container.restart.assert_not_called()

    def test_restart_worker_not_found(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("Container not found")

        result = dm.handle_docker_restart_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "not found" in data["error"]

    def test_restart_worker_missing_name(self, _reload_server):
        dm, mock_client = _reload_server

        result = dm.handle_docker_restart_worker({})
        data = json.loads(result)

        assert "error" in data
        assert "name is required" in data["error"]

    def test_restart_worker_docker_exception(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import DockerException

        mock_client.containers.get.side_effect = DockerException("Docker error")

        result = dm.handle_docker_restart_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Failed to restart container" in data["error"]


class TestDockerIsAvailable:
    """Tests for docker_is_available tool."""

    def test_is_available_true(self, _reload_server):
        dm, mock_client = _reload_server
        mock_client.ping.return_value = True
        mock_client.version.return_value = {"Version": "24.0.0", "ApiVersion": "1.43"}

        result = dm.handle_docker_is_available({})
        data = json.loads(result)

        assert data["available"] is True
        assert data["status"] == "available"
        assert "info" in data
        assert data["info"]["version"] == "24.0.0"
        assert data["info"]["api_version"] == "1.43"

    def test_is_available_false_when_client_none(self, _reload_server):
        dm, mock_client = _reload_server
        # Simulate client being None
        dm._client = None

        result = dm.handle_docker_is_available({})
        data = json.loads(result)

        assert data["available"] is False
        assert data["status"] == "unavailable"

    def test_is_available_false_when_ping_fails(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import DockerException

        mock_client.ping.side_effect = DockerException("Ping failed")

        result = dm.handle_docker_is_available({})
        data = json.loads(result)

        assert data["available"] is False
        assert data["status"] == "unavailable"

    def test_is_available_handles_version_exception(self, _reload_server):
        dm, mock_client = _reload_server
        mock_client.ping.return_value = True
        mock_client.version.side_effect = Exception("Version error")

        result = dm.handle_docker_is_available({})
        data = json.loads(result)

        # Should still return available=true but info may be empty
        assert data["available"] is True
        assert data["status"] == "available"
        # info may be empty due to exception
        assert "info" in data


class TestDockerNotAvailable:
    """Tests behavior when docker is not available."""

    def test_list_containers_when_unavailable(self, _reload_server):
        dm, mock_client = _reload_server
        dm._client = None

        result = dm.handle_docker_list_containers({})
        data = json.loads(result)

        assert "error" in data
        assert "Docker is not available" in data["error"]

    def test_create_worker_when_unavailable(self, _reload_server):
        dm, mock_client = _reload_server
        dm._client = None

        result = dm.handle_docker_create_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Docker is not available" in data["error"]

    def test_remove_worker_when_unavailable(self, _reload_server):
        dm, mock_client = _reload_server
        dm._client = None

        result = dm.handle_docker_remove_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Docker is not available" in data["error"]

    def test_inspect_worker_when_unavailable(self, _reload_server):
        dm, mock_client = _reload_server
        dm._client = None

        result = dm.handle_docker_inspect_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Docker is not available" in data["error"]

    def test_get_logs_when_unavailable(self, _reload_server):
        dm, mock_client = _reload_server
        dm._client = None

        result = dm.handle_docker_get_worker_logs({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Docker is not available" in data["error"]

    def test_restart_worker_when_unavailable(self, _reload_server):
        dm, mock_client = _reload_server
        dm._client = None

        result = dm.handle_docker_restart_worker({"name": "testworker"})
        data = json.loads(result)

        assert "error" in data
        assert "Docker is not available" in data["error"]


class TestToolDefinitions:
    """Tests for TOOL_DEFINITIONS and handle_tool_call."""

    def test_all_tools_defined(self, _reload_server):
        dm, mock_client = _reload_server
        names = {t["name"] for t in dm.TOOL_DEFINITIONS}
        expected = {
            "docker_list_containers",
            "docker_create_worker",
            "docker_remove_worker",
            "docker_inspect_worker",
            "docker_get_worker_logs",
            "docker_restart_worker",
            "docker_is_available",
        }
        assert expected.issubset(names)

    def test_handle_tool_call_unknown_tool(self, _reload_server):
        dm, mock_client = _reload_server
        result = dm.handle_tool_call("nonexistent_tool", {})
        data = json.loads(result)
        assert "error" in data
        assert "Unknown tool" in data["error"]

    def test_handle_tool_call_delegates_correctly(self, _reload_server):
        dm, mock_client = _reload_server
        mock_client.containers.list.return_value = []

        result = dm.handle_tool_call("docker_list_containers", {"status": "running"})
        data = json.loads(result)

        assert "containers" in data

    def test_tool_definitions_have_required_fields(self, _reload_server):
        dm, mock_client = _reload_server

        for tool in dm.TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            schema = tool["inputSchema"]
            assert "type" in schema
            assert "properties" in schema

    def test_list_containers_schema(self, _reload_server):
        dm, mock_client = _reload_server
        schema = next(
            t["inputSchema"]
            for t in dm.TOOL_DEFINITIONS
            if t["name"] == "docker_list_containers"
        )
        assert "status" in schema["properties"]
        assert schema["properties"]["status"]["enum"] == ["running", "stopped", "all"]

    def test_create_worker_schema(self, _reload_server):
        dm, mock_client = _reload_server
        schema = next(
            t["inputSchema"]
            for t in dm.TOOL_DEFINITIONS
            if t["name"] == "docker_create_worker"
        )
        props = schema["properties"]
        assert "name" in props
        assert "image" in props
        assert "capabilities" in props
        assert "matrix_user_id" in props
        assert "name" in schema.get("required", [])

    def test_remove_worker_schema(self, _reload_server):
        dm, mock_client = _reload_server
        schema = next(
            t["inputSchema"]
            for t in dm.TOOL_DEFINITIONS
            if t["name"] == "docker_remove_worker"
        )
        assert "name" in schema["properties"]
        assert "name" in schema.get("required", [])

    def test_inspect_worker_schema(self, _reload_server):
        dm, mock_client = _reload_server
        schema = next(
            t["inputSchema"]
            for t in dm.TOOL_DEFINITIONS
            if t["name"] == "docker_inspect_worker"
        )
        assert "name" in schema["properties"]
        assert "name" in schema.get("required", [])

    def test_get_logs_schema(self, _reload_server):
        dm, mock_client = _reload_server
        schema = next(
            t["inputSchema"]
            for t in dm.TOOL_DEFINITIONS
            if t["name"] == "docker_get_worker_logs"
        )
        props = schema["properties"]
        assert "name" in props
        assert "lines" in props
        assert "name" in schema.get("required", [])

    def test_restart_worker_schema(self, _reload_server):
        dm, mock_client = _reload_server
        schema = next(
            t["inputSchema"]
            for t in dm.TOOL_DEFINITIONS
            if t["name"] == "docker_restart_worker"
        )
        assert "name" in schema["properties"]
        assert "name" in schema.get("required", [])

    def test_is_available_schema(self, _reload_server):
        dm, mock_client = _reload_server
        schema = next(
            t["inputSchema"]
            for t in dm.TOOL_DEFINITIONS
            if t["name"] == "docker_is_available"
        )
        # is_available takes no required arguments
        assert schema["properties"] == {}


class TestWorkerNamePrefix:
    """Tests for worker name prefix functionality."""

    def test_worker_name_prefix_constant(self, _reload_server):
        dm, mock_client = _reload_server
        assert dm.WORKER_NAME_PREFIX == "hermes-worker-"

    def test_internal_worker_name_function(self, _reload_server):
        dm, mock_client = _reload_server
        assert dm._worker_name("test") == "hermes-worker-test"

    def test_container_name_in_result(self, _reload_server):
        dm, mock_client = _reload_server
        from docker.errors import NotFound

        mock_client.containers.get.side_effect = NotFound("Not found")

        mock_container = _make_mock_container(name="hermes-worker-testworker")
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = mock_container

        result = dm.handle_docker_create_worker({"name": "testworker"})
        data = json.loads(result)

        # The full name should have the prefix
        assert data["name"] == "hermes-worker-testworker"


class TestToolHandlers:
    """Tests that TOOL_HANDLERS maps correctly."""

    def test_tool_handlers_complete(self, _reload_server):
        dm, mock_client = _reload_server
        assert len(dm.TOOL_HANDLERS) == 7
        assert (
            dm.TOOL_HANDLERS["docker_list_containers"]
            == dm.handle_docker_list_containers
        )
        assert (
            dm.TOOL_HANDLERS["docker_create_worker"] == dm.handle_docker_create_worker
        )
        assert (
            dm.TOOL_HANDLERS["docker_remove_worker"] == dm.handle_docker_remove_worker
        )
        assert (
            dm.TOOL_HANDLERS["docker_inspect_worker"] == dm.handle_docker_inspect_worker
        )
        assert (
            dm.TOOL_HANDLERS["docker_get_worker_logs"]
            == dm.handle_docker_get_worker_logs
        )
        assert (
            dm.TOOL_HANDLERS["docker_restart_worker"] == dm.handle_docker_restart_worker
        )
        assert dm.TOOL_HANDLERS["docker_is_available"] == dm.handle_docker_is_available
