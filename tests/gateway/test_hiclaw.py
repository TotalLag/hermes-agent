"""Tests for gateway.hiclaw components."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from gateway.hiclaw.docker_manager import (
    DockerManager,
    WorkerContainer,
    ContainerStatus,
)


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
